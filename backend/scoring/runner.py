"""评分批量执行器。

职责:
1. 从 lhb_stock_daily 读取待评分区间
2. 对每一行: 准备 ScoreInput(kline_window / sentiment_level / hist_stats)
3. 调用 compute_score 计算
4. 批量 UPDATE lhb_stock_daily 的 score / score_* / score_detail
"""

from __future__ import annotations

import json
import logging
from datetime import timedelta
from typing import Iterable, Optional

from psycopg2.extras import RealDictCursor, execute_batch

from backend.db.session import pg_conn
from backend.scoring.lhb_score import ScoreInput, compute_score

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------
# 数据读取
# --------------------------------------------------------------------


def _load_lhb_rows(conn, start: str, end: str, only_empty: bool = False):
    # STO-70 C: 全量参数化,避免 f-string 拼 WHERE(虽然字面量白名单无注入,但不一致)
    s_iso = f"{start[:4]}-{start[4:6]}-{start[6:]}"
    e_iso = f"{end[:4]}-{end[4:6]}-{end[6:]}"
    sql = """
        SELECT id, ts_code, trade_date, name, pct_change, turnover_rate,
               amount, l_buy, l_sell, l_net, reason, reason_type,
               industry, market_cap, float_market_cap
        FROM lhb_stock_daily
        WHERE trade_date BETWEEN %s AND %s
          AND (%s::bool = FALSE OR score = 0 OR score IS NULL)
        ORDER BY trade_date, ts_code
    """
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(sql, (s_iso, e_iso, bool(only_empty)))
        return cur.fetchall()


def _load_kline_window(conn, ts_code: str, trade_date, lookback_days: int = 90):
    """(legacy) 单行取 kline。STO-73 新 run() 走 `_load_kline_windows_bulk`。

    保留此函数:测试直接调用 + 外部可能 import,但主流程不再命中。
    """
    start = trade_date - timedelta(days=lookback_days + 30)  # 多买点缓冲
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT trade_date, open, high, low, close, pct_change,
                   vol, amount, turnover_rate
            FROM kline_daily
            WHERE ts_code = %s
              AND trade_date BETWEEN %s AND %s
            ORDER BY trade_date
            """,
            (ts_code, start, trade_date),
        )
        return cur.fetchall()


def _load_kline_windows_bulk(
    conn, rows: list, lookback_days: int = 90,
) -> dict[tuple, list[dict]]:
    """STO-73: 一次 SQL 预拉全部 (ts_code, trade_date) 的 kline 窗口。

    实现:
      - 取 rows 里涉及的所有 ts_code + trade_date 范围 [min-lookback-30, max]
      - 一次 SQL 拉回,Python 按 ts_code 分组
      - 对每个 (ts_code, trade_date) 在内存切片 [trade_date - lookback-30, trade_date]

    返回:{(ts_code, trade_date) -> list[dict]} 与 _load_kline_window 等价的窗口
    """
    if not rows:
        return {}
    ts_codes = sorted({r["ts_code"] for r in rows})
    min_td = min(r["trade_date"] for r in rows)
    max_td = max(r["trade_date"] for r in rows)
    earliest = min_td - timedelta(days=lookback_days + 30)
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT ts_code, trade_date, open, high, low, close, pct_change,
                   vol, amount, turnover_rate
            FROM kline_daily
            WHERE ts_code = ANY(%s)
              AND trade_date BETWEEN %s AND %s
            ORDER BY ts_code, trade_date
            """,
            (ts_codes, earliest, max_td),
        )
        all_kline = cur.fetchall()
    # 按 ts_code 分组(升序已由 SQL 保证)+ 预计算 trade_date 列表供 bisect 用
    import bisect
    by_code: dict[str, list[dict]] = {}
    dates_by_code: dict[str, list] = {}
    for k in all_kline:
        by_code.setdefault(k["ts_code"], []).append(k)
        dates_by_code.setdefault(k["ts_code"], []).append(k["trade_date"])
    # 对每个 row 切片:[trade_date - lookback-30, trade_date]
    out: dict[tuple, list[dict]] = {}
    for r in rows:
        tc = r["ts_code"]
        td = r["trade_date"]
        cutoff_low = td - timedelta(days=lookback_days + 30)
        dates = dates_by_code.get(tc, [])
        if not dates:
            out[(tc, td)] = []
            continue
        lo = bisect.bisect_left(dates, cutoff_low)
        hi = bisect.bisect_right(dates, td)
        out[(tc, td)] = by_code[tc][lo:hi]
    return out


def _load_sentiment_map(conn, start: str, end: str) -> dict:
    """trade_date -> sentiment_level"""
    s_iso = f"{start[:4]}-{start[4:6]}-{start[6:]}"
    e_iso = f"{end[:4]}-{end[4:6]}-{end[6:]}"
    with conn.cursor() as cur:
        cur.execute(
            "SELECT trade_date, sentiment_level FROM market_sentiment "
            "WHERE trade_date BETWEEN %s AND %s",
            (s_iso, e_iso),
        )
        return {row[0]: row[1] for row in cur.fetchall()}


def _load_hist_stats_bulk(conn, rows: list) -> dict[tuple, dict]:
    """STO-73: 一次 SQL 批量计算所有 (ts_code, trade_date) 的 hist_stats。

    思路:
      1. 把所有 ts_code 在过去 365 天的 lhb 上榜日期 + kline 窗口函数 c3/c5 一次拉回
      2. Python 端按 (ts_code, target_trade_date) 聚合(只取 d0 < target 的历史)

    返回:{(ts_code, trade_date) -> hist_dict} 与 _load_hist_stats 等价
    空值时返回 {\"total_count\":0, ...} 保持向后兼容
    """
    if not rows:
        return {}
    ts_codes = sorted({r["ts_code"] for r in rows})
    max_td = max(r["trade_date"] for r in rows)
    earliest = min(r["trade_date"] for r in rows) - timedelta(days=365)
    # 一次 SQL:把 ts_codes 在 [earliest, max_td] 内的所有历史上榜日 + c0/c3/c5 带出
    # c3/c5 用 LEAD(close, 2) / LEAD(close, 4) OVER (PARTITION BY ts_code ORDER BY trade_date)
    # 注意:LEAD 是该 kline 行之后的第 N 行,与原 LIMIT 1 OFFSET 2/4 (不含 d0 的第 3/5 个) 等价
    with conn.cursor() as cur:
        cur.execute(
            """
            WITH kline_with_future AS (
                SELECT ts_code, trade_date, close,
                       LEAD(close, 3) OVER (PARTITION BY ts_code ORDER BY trade_date) AS c3,
                       LEAD(close, 5) OVER (PARTITION BY ts_code ORDER BY trade_date) AS c5
                FROM kline_daily
                WHERE ts_code = ANY(%s)
            ),
            past_lhb AS (
                SELECT l.ts_code, l.trade_date AS d0, k.close AS c0, k.c3, k.c5
                FROM lhb_stock_daily l
                LEFT JOIN kline_with_future k
                  ON k.ts_code = l.ts_code AND k.trade_date = l.trade_date
                WHERE l.ts_code = ANY(%s)
                  AND l.trade_date BETWEEN %s AND %s
            )
            SELECT ts_code, d0, c0, c3, c5 FROM past_lhb
            ORDER BY ts_code, d0
            """,
            (ts_codes, ts_codes, earliest, max_td),
        )
        history = cur.fetchall()
    # 按 ts_code 分组
    by_code: dict[str, list[tuple]] = {}
    for ts_code, d0, c0, c3, c5 in history:
        by_code.setdefault(ts_code, []).append((d0, c0, c3, c5))
    # 对每个 (ts_code, target_trade_date):找过去 365d 内 d0 < target 的所有记录聚合
    out: dict[tuple, dict] = {}
    for r in rows:
        tc = r["ts_code"]
        td = r["trade_date"]
        cutoff = td - timedelta(days=365)
        total = 0
        s3 = s5 = wr = 0.0
        n3 = n5 = nwr = 0
        for d0, c0, c3, c5 in by_code.get(tc, []):
            if not (cutoff <= d0 < td):
                continue
            total += 1
            if c0 and c0 > 0:
                if c3 is not None:
                    s3 += float(c3 - c0) / float(c0)
                    n3 += 1
                if c5 is not None:
                    s5 += float(c5 - c0) / float(c0)
                    n5 += 1
            if c0 is not None and c5 is not None:
                wr += 1.0 if c5 > c0 else 0.0
                nwr += 1
        out[(tc, td)] = {
            "total_count": total,
            "avg_chg_3d": (s3 / n3) if n3 > 0 else None,
            "avg_chg_5d": (s5 / n5) if n5 > 0 else None,
            "win_rate_5d": (wr / nwr) if nwr > 0 else None,
        }
    return out


def _load_hist_stats(conn, ts_code: str, trade_date) -> dict:
    """过去 1 年该 ts_code 上榜统计(不含当日)。

    实现上取过去 365 日内、trade_date 之前的所有上榜记录,
    用 kline_daily join 计算 3/5 日后的收益率。
    """
    start = trade_date - timedelta(days=365)
    end = trade_date - timedelta(days=1)
    with conn.cursor() as cur:
        cur.execute(
            """
            WITH past_lhb AS (
                SELECT trade_date AS d0
                FROM lhb_stock_daily
                WHERE ts_code = %s AND trade_date BETWEEN %s AND %s
            ),
            with_close AS (
                SELECT p.d0,
                       (SELECT close FROM kline_daily k
                        WHERE k.ts_code = %s AND k.trade_date = p.d0) AS c0,
                       (SELECT close FROM kline_daily k
                        WHERE k.ts_code = %s AND k.trade_date > p.d0
                        ORDER BY k.trade_date LIMIT 1 OFFSET 2) AS c3,
                       (SELECT close FROM kline_daily k
                        WHERE k.ts_code = %s AND k.trade_date > p.d0
                        ORDER BY k.trade_date LIMIT 1 OFFSET 4) AS c5
                FROM past_lhb p
            )
            SELECT
                COUNT(*)                                        AS total_count,
                AVG(CASE WHEN c0>0 THEN (c3-c0)/c0 END)         AS avg_chg_3d,
                AVG(CASE WHEN c0>0 THEN (c5-c0)/c0 END)         AS avg_chg_5d,
                AVG(CASE WHEN c5 IS NULL OR c0 IS NULL THEN NULL
                         WHEN c5 > c0 THEN 1.0 ELSE 0.0 END)    AS win_rate_5d
            FROM with_close
            """,
            (ts_code, start, end, ts_code, ts_code, ts_code),
        )
        total, a3, a5, wr = cur.fetchone()
    return {
        "total_count": int(total or 0),
        "avg_chg_3d": float(a3) if a3 is not None else None,
        "avg_chg_5d": float(a5) if a5 is not None else None,
        "win_rate_5d": float(wr) if wr is not None else None,
    }


# --------------------------------------------------------------------
# 批量更新
# --------------------------------------------------------------------


def _update_scores(conn, updates: list[dict]) -> int:
    """批量更新评分。updates: [{id, score, score_tech, score_fund,
    score_hist, score_detail_json}]"""
    if not updates:
        return 0
    with conn.cursor() as cur:
        execute_batch(
            cur,
            """
            UPDATE lhb_stock_daily
            SET score = %(score)s,
                score_tech = %(score_tech)s,
                score_fund = %(score_fund)s,
                score_hist = %(score_hist)s,
                score_detail = %(score_detail)s::jsonb,
                updated_at = NOW()
            WHERE id = %(id)s
            """,
            updates,
            page_size=500,
        )
    return len(updates)


# --------------------------------------------------------------------
# 主入口
# --------------------------------------------------------------------


def run(
    start: str,
    end: str,
    only_empty: bool = False,
    limit: Optional[int] = None,
) -> int:
    """对 [start, end] 区间的龙虎榜数据批量打分。

    Args:
        start, end: YYYYMMDD
        only_empty: True 则只处理 score=0 的行(断点续跑)
        limit: 最多处理多少行(调试用)

    Returns:
        更新行数
    """
    with pg_conn() as conn:
        rows = _load_lhb_rows(conn, start, end, only_empty=only_empty)
        if limit:
            rows = rows[:limit]
        logger.info(f"[scoring] 待评分 {len(rows)} 行")
        if not rows:
            return 0

        sentiment_map = _load_sentiment_map(conn, start, end)
        # STO-73: 一次 SQL 预拉全部 kline 窗口 + hist 统计,消除 N+1
        # 原实现:N 行 × (1 kline + 1 hist CTE) = 2N 次往返
        # 新实现:无论 N 多大,常量 2 次 SQL(加 lhb+sentiment 共 4 次总计)
        kline_bulk = _load_kline_windows_bulk(conn, rows)
        hist_bulk = _load_hist_stats_bulk(conn, rows)
        updates: list[dict] = []

        for idx, r in enumerate(rows, 1):
            ts_code = r["ts_code"]
            trade_date = r["trade_date"]
            kline_win = kline_bulk.get((ts_code, trade_date), [])
            hist = hist_bulk.get((ts_code, trade_date), {
                "total_count": 0, "avg_chg_3d": None,
                "avg_chg_5d": None, "win_rate_5d": None,
            })
            inp = ScoreInput(
                lhb_row=dict(r),
                kline_window=list(kline_win),
                sentiment_level=sentiment_map.get(trade_date, "NEUTRAL"),
                hist_stats=hist,
            )
            result = compute_score(inp)
            updates.append({
                "id": r["id"],
                "score": result["score"],
                "score_tech": result["score_tech"],
                "score_fund": result["score_fund"],
                "score_hist": result["score_hist"],
                "score_detail": json.dumps(
                    result["score_detail"], ensure_ascii=False, default=str
                ),
            })
            if idx % 500 == 0:
                # STO-70 B: 不手动 conn.commit(),依赖 pg_conn() 上下文最终原子 commit。
                # 中途失败语义改为:整个区间回滚(与 CLAUDE.md §9.1 一致)。
                _update_scores(conn, updates)
                logger.info(f"[scoring] 进度 {idx}/{len(rows)}")
                updates = []

        if updates:
            _update_scores(conn, updates)

        logger.info(f"[scoring] 完成: {len(rows)} 行已评分")
        return len(rows)
