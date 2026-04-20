"""市场情绪入库（market_sentiment）。

数据来源策略:
  1. 优先调 manager.get_market_sentiment(date) (当日可用,历史东财已下线)
  2. 若字段为空/0 → 从 DB 的 kline_daily 反算
     (零外网依赖, 历史全日期都可用, 与 K 线同一口径)
"""

from __future__ import annotations

import logging

from backend.db.session import pg_conn
from backend.db.writer import upsert_market_sentiment
from backend.ingest.base import df_to_rows, get_manager, normalize_date
from backend.ingest.lhb import _load_trading_days

logger = logging.getLogger(__name__)

COLMAP = {
    "trade_date": "trade_date",
    "limit_up_count": "limit_up_count",
    "limit_down_count": "limit_down_count",
    "up_down_ratio": "up_down_ratio",
    "avg_pct_change": "avg_pct_change",
    "strong_stock_count": "strong_stock_count",
    "weak_stock_count": "weak_stock_count",
    "sentiment_score": "sentiment_score",
    "sentiment_level": "sentiment_level",
}


def _calc_from_kline(conn, trade_date_str: str) -> dict | None:
    """从 kline_daily 反算情绪字段(trade_date_str: YYYYMMDD)。
    若当日 kline 无数据返回 None; 否则返回一个 dict 供 upsert。

    2026-04-20 P18.13 扩字段:补齐 total_stocks / up_count / down_count /
    median_pct_change —— 这些都是 market_sentiment 表里一直为 NULL 的
    关键衍生字段,Dashboard 前端需要它们才能展示「涨跌家数」「样本数」等。
    """
    d_iso = f"{trade_date_str[:4]}-{trade_date_str[4:6]}-{trade_date_str[6:]}"
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                COUNT(*)                                              AS total_cnt,
                SUM(CASE WHEN pct_change >=  0.095 THEN 1 ELSE 0 END) AS limit_up_cnt,
                SUM(CASE WHEN pct_change <= -0.095 THEN 1 ELSE 0 END) AS limit_down_cnt,
                SUM(CASE WHEN pct_change >  0 THEN 1 ELSE 0 END)      AS up_cnt,
                SUM(CASE WHEN pct_change <  0 THEN 1 ELSE 0 END)      AS down_cnt,
                SUM(CASE WHEN pct_change >  0.05 THEN 1 ELSE 0 END)   AS strong_cnt,
                SUM(CASE WHEN pct_change < -0.05 THEN 1 ELSE 0 END)   AS weak_cnt,
                AVG(pct_change)                                       AS avg_pct,
                -- PostgreSQL percentile_cont(0.5) 实现中位数
                percentile_cont(0.5) WITHIN GROUP (ORDER BY pct_change) AS median_pct
            FROM kline_daily
            WHERE trade_date = %s AND pct_change IS NOT NULL
            """,
            (d_iso,),
        )
        row = cur.fetchone()
    if not row or not row[0]:
        return None
    total_cnt, lu, ld, up_cnt, down_cnt, strong, weak, avg_pct, median_pct = row
    ratio = round(float(up_cnt) / max(int(down_cnt or 0), 1), 4) if up_cnt is not None else None
    avg_f = float(avg_pct) if avg_pct is not None else None
    median_f = float(median_pct) if median_pct is not None else None
    # STO-32 (2026-04-20 P20.6) 方案 A - 温和线性评分公式:
    #   score = 50 + (ratio - 1) * 10 + avg_pct * 300 + (lu - ld) * 0.1
    #   clip 到 [0, 100]
    # 阈值不变: >=70 HOT, >=55 WARM, >=45 NEUTRAL, >=30 COOL, else COLD
    #
    # 旧公式 `(ratio-1)*20 + avg*600 + (lu-ld)*0.5` 的问题:
    #   A 股涨停潮常见 LU=80~140 只, 旧系数 0.5 使 LU 项单独 +40~70 分,
    #   叠加 base 50 + avg*600 后瞬间撞 clip 到 100, 造成严重双峰分布
    #   (历史 538 天中 score=100 占 36%, 中间档稀疏)。
    #
    # 新系数: LU 0.5->0.1, avg 600->300, ratio 20->10, 区分度显著提升,
    # 最近 20 天 simulate 下 score 在 13~100 连续散落, stddev 更高且单峰。
    score = 50.0
    if ratio is not None:
        score += (ratio - 1.0) * 10
    if avg_f is not None:
        score += avg_f * 300
    score += (lu or 0) * 0.1 - (ld or 0) * 0.1
    score = max(0.0, min(100.0, score))
    level = (
        "HOT" if score >= 70 else
        "WARM" if score >= 55 else
        "NEUTRAL" if score >= 45 else
        "COOL" if score >= 30 else
        "COLD"
    )
    return {
        "trade_date": d_iso,
        "limit_up_count": int(lu or 0),
        "limit_down_count": int(ld or 0),
        "up_down_ratio": ratio,
        "avg_pct_change": round(avg_f, 6) if avg_f is not None else None,
        "median_pct_change": round(median_f, 6) if median_f is not None else None,
        "strong_stock_count": int(strong or 0),
        "weak_stock_count": int(weak or 0),
        # P18.13 新增
        "total_stocks": int(total_cnt or 0),
        "up_count": int(up_cnt or 0),
        "down_count": int(down_cnt or 0),
        "sentiment_score": round(score, 2),
        "sentiment_level": level,
    }


# P18.13 从 DB 反算补齐时需要合并的字段(外部源返回 None,DB 反算能给)
_DB_FILL_KEYS = (
    "up_down_ratio", "avg_pct_change", "median_pct_change",
    "strong_stock_count", "weak_stock_count",
    "total_stocks", "up_count", "down_count",
    "sentiment_score", "sentiment_level",
)


def _is_empty_sentiment(r: dict) -> bool:
    """判断外部 adapter 返回的 row 是否算「完全空壳」(所有关键字段全 0/None)。

    语义:True 表示这条数据完全不可用,需要整体走 DB 反算。
    否则(至少有一个关键字段有值)不算空壳,保留外部源数据,
    同时**对缺失字段走 `_merge_db_fill` 兜底**(P18.13)。
    """
    keys = ("limit_up_count", "limit_down_count", "up_down_ratio", "avg_pct_change")
    return all((r.get(k) in (None, 0, 0.0)) for k in keys)


def _merge_db_fill(external_row: dict, db_row: dict | None) -> dict:
    """P18.13: 外部源有部分数据时,从 DB 反算补齐 None 字段,不覆盖已有值。

    典型场景:akshare 东财只返回了 limit_up_count/limit_down_count,
    其余 up_down_ratio/avg_pct_change 等为 None — 本函数会从 db_row 取值填上。
    """
    if db_row is None:
        return external_row
    merged = dict(external_row)
    for k in _DB_FILL_KEYS:
        if merged.get(k) in (None, 0, 0.0) and db_row.get(k) is not None:
            merged[k] = db_row[k]
    return merged


def _load_covered_days(start: str, end: str) -> set[str]:
    """已写入 market_sentiment 的交易日 (YYYYMMDD)"""
    s_iso = f"{start[:4]}-{start[4:6]}-{start[6:]}"
    e_iso = f"{end[:4]}-{end[4:6]}-{end[6:]}"
    with pg_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT to_char(trade_date, 'YYYYMMDD') "
            "FROM market_sentiment WHERE trade_date BETWEEN %s AND %s",
            (s_iso, e_iso),
        )
        return {r[0] for r in cur.fetchall()}


def run(start: str, end: str, skip_existing: bool = True) -> int:
    mgr = get_manager()
    days = _load_trading_days(start, end)
    if not days:
        logger.warning("[ingest.sentiment] trade_calendar 无数据，请先 ingest calendar")
        return 0
    if skip_existing:
        covered = _load_covered_days(start, end)
        if covered:
            days = [d for d in days if d not in covered]
            logger.info(
                f"[ingest.sentiment] 增量模式: {len(covered)} 天已覆盖, 待抓 {len(days)} 天"
            )

    total = 0
    fail = 0
    from_db = 0
    from_api = 0
    with pg_conn() as conn:
        for d in days:
            try:
                rows: list[dict] = []
                # Step 1: 尝试外部 API
                try:
                    df = mgr.get_market_sentiment(d)
                    rows = df_to_rows(df, COLMAP)
                    for r in rows:
                        r["trade_date"] = normalize_date(r.get("trade_date")) or f"{d[:4]}-{d[4:6]}-{d[6:]}"
                except Exception as e:
                    logger.warning(f"[ingest.sentiment] {d} 外部源失败: {e}")

                # Step 2: rows 空 或 '空壳' → 从 kline_daily 反算
                #         rows 有数据但部分字段 None → DB 反算补齐缺失字段
                # P18.13 修复:旧逻辑只在「完全空壳」时走 DB 反算,
                # 导致 akshare 只返回 limit_up/down_count 时,up_down_ratio
                # avg_pct_change 等 4 个衍生字段永久 None。
                if not rows or _is_empty_sentiment(rows[0]):
                    db_row = _calc_from_kline(conn, d)
                    if db_row:
                        rows = [db_row]
                        from_db += 1
                        logger.info(f"[ingest.sentiment] {d} 使用 kline_daily 反算")
                    else:
                        logger.info(f"[ingest.sentiment] {d} kline 亦无数据, 跳过")
                        continue
                else:
                    # P18.13: 外部源有数据,但从 DB 反算补齐 None 字段
                    db_row = _calc_from_kline(conn, d)
                    rows[0] = _merge_db_fill(rows[0], db_row)
                    from_api += 1

                total += upsert_market_sentiment(conn, rows)
            except NotImplementedError:
                logger.info("[ingest.sentiment] 数据源尚未实现，跳出")
                break
            except Exception as e:
                fail += 1
                try:
                    conn.rollback()
                except Exception:
                    pass
                logger.warning(f"[ingest.sentiment] {d} 失败: {e}")
        conn.commit()

    logger.info(
        f"[ingest.sentiment] 完成: {total} 行 (外部源 {from_api} 天 / DB反算 {from_db} 天), 失败 {fail} 天"
    )
    return total
