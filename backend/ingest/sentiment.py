"""市场情绪入库（market_sentiment）。

W2 阶段留一个最小骨架：尝试调用 manager.get_market_sentiment(trade_date)；
若数据源暂未实现则 skip，待 W3 完成情绪汇总后补。
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


def run(start: str, end: str) -> int:
    mgr = get_manager()
    days = _load_trading_days(start, end)
    if not days:
        logger.warning("[ingest.sentiment] trade_calendar 无数据，请先 ingest calendar")
        return 0

    total = 0
    with pg_conn() as conn:
        for d in days:
            try:
                df = mgr.get_market_sentiment(d)
                rows = df_to_rows(df, COLMAP)
                if not rows:
                    continue
                for r in rows:
                    r["trade_date"] = normalize_date(r.get("trade_date")) or f"{d[:4]}-{d[4:6]}-{d[6:]}"
                total += upsert_market_sentiment(conn, rows)
            except NotImplementedError:
                logger.info("[ingest.sentiment] 数据源尚未实现，跳出")
                break
            except Exception as e:
                logger.warning(f"[ingest.sentiment] {d} 失败: {e}")

    logger.info(f"[ingest.sentiment] 完成: {total} 行")
    return total
