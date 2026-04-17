"""日K线历史数据入库。

全市场灌库思路：
1. 从 stock_info 取所有 ts_code（如果库里还没有，调用者应先 ingest_stock_info）
2. 逐只调 manager.get_daily_kline(ts_code, start, end)
3. 批量 upsert 到 kline_daily
"""

from __future__ import annotations

import logging
from typing import List, Optional

from backend.db.session import pg_conn
from backend.db.writer import upsert_kline_daily
from backend.ingest.base import df_to_rows, get_manager, normalize_date

logger = logging.getLogger(__name__)

COLMAP = {
    "ts_code": "ts_code",
    "trade_date": "trade_date",
    "open": "open",
    "high": "high",
    "low": "low",
    "close": "close",
    "pre_close": "pre_close",
    "pct_chg": "pct_change",
    "pct_change": "pct_change",
    "vol": "vol",
    "amount": "amount",
    "turnover_rate": "turnover_rate",
    "turnover_rate_f": "turnover_rate_f",
    "volume_ratio": "volume_ratio",
    "pe_ttm": "pe_ttm",
    "pb": "pb",
    "total_mv": "total_mv",
    "circ_mv": "circ_mv",
}


def _load_all_ts_codes() -> List[str]:
    with pg_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT ts_code FROM stock_info WHERE is_active = TRUE")
        return [r[0] for r in cur.fetchall()]


def _normalize_pct(rows: list[dict]) -> None:
    """TuShare 的 pct_chg 是百分比（5.23 表示 5.23%），DB 存小数（0.0523）。"""
    for r in rows:
        v = r.get("pct_change")
        if v is not None:
            try:
                fv = float(v)
                if abs(fv) > 1:  # 看起来是百分比
                    r["pct_change"] = fv / 100.0
            except (TypeError, ValueError):
                r["pct_change"] = None
        tr = r.get("turnover_rate")
        if tr is not None:
            try:
                ftr = float(tr)
                if abs(ftr) > 1:
                    r["turnover_rate"] = ftr / 100.0
            except (TypeError, ValueError):
                r["turnover_rate"] = None


def run(start: str, end: str, codes: Optional[List[str]] = None) -> int:
    mgr = get_manager()

    if not codes or codes == ["all"]:
        codes = _load_all_ts_codes()
        if not codes:
            logger.error("[ingest.kline] stock_info 为空，请先 ingest stock-info")
            return 0

    total = 0
    fail = 0
    logger.info(f"[ingest.kline] 开始: {len(codes)} 只股票, {start}~{end}")

    with pg_conn() as conn:
        for idx, code in enumerate(codes, 1):
            try:
                df = mgr.get_daily_kline(code, start, end)
                rows = df_to_rows(df, COLMAP)
                if not rows:
                    continue
                for r in rows:
                    r.setdefault("ts_code", code)
                    r["trade_date"] = normalize_date(r.get("trade_date"))
                _normalize_pct(rows)
                n = upsert_kline_daily(conn, rows)
                total += n
                if idx % 100 == 0:
                    conn.commit()
                    logger.info(f"[ingest.kline] 进度 {idx}/{len(codes)}  累计 {total} 行")
            except Exception as e:
                fail += 1
                logger.warning(f"[ingest.kline] {code} 失败: {e}")

    logger.info(f"[ingest.kline] 完成: 共 {total} 行, 失败 {fail} 只")
    return total
