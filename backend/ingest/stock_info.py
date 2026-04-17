"""股票基础信息入库。"""

from __future__ import annotations

import logging

from backend.db.session import pg_conn
from backend.db.writer import upsert_stock_info
from backend.ingest.base import df_to_rows, get_manager, normalize_date

logger = logging.getLogger(__name__)

COLMAP = {
    "ts_code": "ts_code",
    "symbol": "symbol",
    "name": "name",
    "area": "area",
    "industry": "industry",
    "market": "market",
    "list_date": "list_date",
    "delist_date": "delist_date",
    "is_hs": "is_hs",
    "exchange": "exchange",
}


def run() -> int:
    mgr = get_manager()
    df = mgr.get_stock_info()
    if df is None or df.empty:
        logger.warning("[ingest.stock_info] 数据源返回为空")
        return 0

    rows = df_to_rows(df, COLMAP)
    for r in rows:
        r.setdefault("is_active", True)
        r["list_date"] = normalize_date(r.get("list_date"))
        r["delist_date"] = normalize_date(r.get("delist_date"))

    with pg_conn() as conn:
        n = upsert_stock_info(conn, rows)
    logger.info(f"[ingest.stock_info] upsert {n} 行")
    return n
