"""龙虎榜入库（lhb_stock_daily）。

席位明细 (lhb_seats) 依赖 stock_daily_id 外键，分两步：W3 再做，
当前 W2 只灌主表，保证能做评分。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from backend.db.session import pg_conn
from backend.db.writer import upsert_lhb_stock_daily
from backend.ingest.base import df_to_rows, get_manager, normalize_date

logger = logging.getLogger(__name__)

COLMAP = {
    "ts_code": "ts_code",
    "trade_date": "trade_date",
    "name": "name",
    "close": "close_price",
    "close_price": "close_price",
    "pct_change": "pct_change",
    "pct_chg": "pct_change",
    "turnover_rate": "turnover_rate",
    "amount": "amount",
    "l_buy": "l_buy",
    "l_sell": "l_sell",
    "l_net": "l_net",
    "reason": "reason",
    "reason_type": "reason_type",
    "industry": "industry",
    "market_cap": "market_cap",
    "float_market_cap": "float_market_cap",
}


def _daterange(start: str, end: str):
    s = datetime.strptime(start, "%Y%m%d")
    e = datetime.strptime(end, "%Y%m%d")
    cur = s
    while cur <= e:
        yield cur.strftime("%Y%m%d")
        cur += timedelta(days=1)


def _load_trading_days(start: str, end: str) -> list[str]:
    with pg_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT to_char(cal_date,'YYYYMMDD') FROM trade_calendar "
            "WHERE exchange='SSE' AND is_open=TRUE "
            "AND cal_date BETWEEN %s AND %s ORDER BY cal_date",
            (f"{start[:4]}-{start[4:6]}-{start[6:]}",
             f"{end[:4]}-{end[4:6]}-{end[6:]}"),
        )
        return [r[0] for r in cur.fetchall()]


def run(start: str, end: str) -> int:
    mgr = get_manager()
    days = _load_trading_days(start, end) or list(_daterange(start, end))
    logger.info(f"[ingest.lhb] {len(days)} 天待抓")

    total = 0
    fail = 0
    with pg_conn() as conn:
        for idx, d in enumerate(days, 1):
            try:
                df = mgr.get_lhb_detail(d)
                rows = df_to_rows(df, COLMAP)
                if not rows:
                    continue
                for r in rows:
                    r["trade_date"] = normalize_date(r.get("trade_date")) or f"{d[:4]}-{d[4:6]}-{d[6:]}"
                n = upsert_lhb_stock_daily(conn, rows)
                total += n
                if idx % 20 == 0:
                    conn.commit()
                    logger.info(f"[ingest.lhb] 进度 {idx}/{len(days)}  累计 {total}")
            except Exception as e:
                fail += 1
                logger.warning(f"[ingest.lhb] {d} 失败: {e}")

    logger.info(f"[ingest.lhb] 完成: {total} 行, 失败 {fail} 天")
    return total
