"""交易日历入库。"""

from __future__ import annotations

import logging

from backend.db.session import pg_conn
from backend.db.writer import upsert_trade_calendar
from backend.ingest.base import df_to_rows, get_manager, normalize_date

logger = logging.getLogger(__name__)

COLMAP = {
    "exchange": "exchange",
    "cal_date": "cal_date",
    "is_open": "is_open",
    "pretrade_date": "pretrade_date",
}


def run(start: str, end: str, exchange: str = "SSE") -> int:
    mgr = get_manager()
    df = mgr.get_trade_calendar(exchange, start, end)
    if df is None or df.empty:
        logger.warning("[ingest.calendar] 空结果")
        return 0

    rows = df_to_rows(df, COLMAP)
    for r in rows:
        r.setdefault("exchange", exchange)
        r["cal_date"] = normalize_date(r.get("cal_date"))
        r["pretrade_date"] = normalize_date(r.get("pretrade_date"))
        if isinstance(r.get("is_open"), (int, str)):
            r["is_open"] = bool(int(r["is_open"]))

    with pg_conn() as conn:
        n = upsert_trade_calendar(conn, rows)
    logger.info(f"[ingest.calendar] upsert {n} 行 ({exchange} {start}~{end})")
    return n
