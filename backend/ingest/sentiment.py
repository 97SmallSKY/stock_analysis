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
    """
    d_iso = f"{trade_date_str[:4]}-{trade_date_str[4:6]}-{trade_date_str[6:]}"
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                COUNT(*)                                          AS total_cnt,
                SUM(CASE WHEN pct_change >=  0.095 THEN 1 ELSE 0 END) AS limit_up_cnt,
                SUM(CASE WHEN pct_change <= -0.095 THEN 1 ELSE 0 END) AS limit_down_cnt,
                SUM(CASE WHEN pct_change >  0 THEN 1 ELSE 0 END)  AS up_cnt,
                SUM(CASE WHEN pct_change <  0 THEN 1 ELSE 0 END)  AS down_cnt,
                SUM(CASE WHEN pct_change >  0.05 THEN 1 ELSE 0 END)   AS strong_cnt,
                SUM(CASE WHEN pct_change < -0.05 THEN 1 ELSE 0 END)   AS weak_cnt,
                AVG(pct_change)                                   AS avg_pct
            FROM kline_daily
            WHERE trade_date = %s AND pct_change IS NOT NULL
            """,
            (d_iso,),
        )
        row = cur.fetchone()
    if not row or not row[0]:
        return None
    total_cnt, lu, ld, up_cnt, down_cnt, strong, weak, avg_pct = row
    ratio = round(float(up_cnt) / max(int(down_cnt or 0), 1), 4) if up_cnt is not None else None
    avg_f = float(avg_pct) if avg_pct is not None else None
    # 简易评分: 涨跌比 * 40 + 均幅 * 600 + 涨停数 * 0.5, clamp 到 [0, 100]
    score = 50.0
    if ratio is not None:
        score += (ratio - 1.0) * 20
    if avg_f is not None:
        score += avg_f * 600
    score += (lu or 0) * 0.5 - (ld or 0) * 0.5
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
        "strong_stock_count": int(strong or 0),
        "weak_stock_count": int(weak or 0),
        "sentiment_score": round(score, 2),
        "sentiment_level": level,
    }


def _is_empty_sentiment(r: dict) -> bool:
    """判断从外部 adapter 拿到的 row 是否 '空壳'(关键字段全 0/None)。"""
    keys = ("limit_up_count", "limit_down_count", "up_down_ratio", "avg_pct_change")
    return all((r.get(k) in (None, 0, 0.0)) for k in keys)


def run(start: str, end: str) -> int:
    mgr = get_manager()
    days = _load_trading_days(start, end)
    if not days:
        logger.warning("[ingest.sentiment] trade_calendar 无数据，请先 ingest calendar")
        return 0

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
