"""龙虎榜领域入库器。

输入: list[LhbRecord]
动作:
  1. 自动补 stock_info 骨架(可转债/退市)
  2. upsert lhb_stock_daily 主表
  3. upsert lhb_seats (如果 record 里有 seats)
  4. 返回 { 'main_rows': int, 'seat_rows': int }

对比 ingest/lhb.py 的区别: 本模块按 domain 分层, 纯"接收 LhbRecord"接口,
不关心数据源 / 没有交易日循环. 交易日循环由调用方(ingest 层或 scheduler)负责.
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Iterable

from psycopg2.extras import execute_values

from backend.domain.lhb.parser import LhbRecord

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------
# 工具: trade_date 归一化
# --------------------------------------------------------------------


def _norm_date(v):
    if v is None:
        return None
    if isinstance(v, date):
        return v
    s = str(v)
    if len(s) == 8 and s.isdigit():
        return date(int(s[:4]), int(s[4:6]), int(s[6:]))
    try:
        return datetime.fromisoformat(s).date()
    except Exception:
        return s  # 传给 DB 让它自己 cast


# --------------------------------------------------------------------
# stock_info 骨架补齐
# --------------------------------------------------------------------


def ensure_stock_info_skel(conn, records: Iterable[LhbRecord]) -> int:
    codes = {r.ts_code for r in records if r.ts_code}
    if not codes:
        return 0
    with conn.cursor() as cur:
        cur.execute(
            "SELECT ts_code FROM stock_info WHERE ts_code = ANY(%s)",
            (list(codes),),
        )
        existed = {r[0] for r in cur.fetchall()}
    missing = codes - existed
    if not missing:
        return 0

    name_by_code: dict[str, str] = {}
    for r in records:
        if r.ts_code in missing and r.ts_code not in name_by_code:
            name_by_code[r.ts_code] = (r.name or r.ts_code)[:32]

    skel_rows = []
    for c in missing:
        symbol = c.split(".")[0] if "." in c else c
        exch = c.split(".")[-1] if "." in c else None
        exchange = {"SH": "SSE", "SZ": "SZSE", "BJ": "BSE"}.get(exch)
        skel_rows.append((
            c, symbol, name_by_code.get(c, symbol),
            exchange, False,
        ))
    with conn.cursor() as cur:
        execute_values(
            cur,
            "INSERT INTO stock_info (ts_code, symbol, name, exchange, is_active)"
            " VALUES %s ON CONFLICT (ts_code) DO NOTHING",
            skel_rows,
            page_size=500,
        )
    logger.info(f"[domain.lhb] 自动补 stock_info 骨架 {len(skel_rows)} 条")
    return len(skel_rows)


# --------------------------------------------------------------------
# upsert 主表, 返回 (id, ts_code, trade_date) 映射以供 seats 写入
# --------------------------------------------------------------------


_MAIN_COLS = [
    "ts_code", "trade_date", "name", "close_price", "pct_change",
    "turnover_rate", "amount", "l_buy", "l_sell", "l_net",
    "reason", "reason_type", "industry", "market_cap",
    "float_market_cap",
]


def upsert_main(conn, records: list[LhbRecord]) -> dict[tuple, int]:
    """写 lhb_stock_daily, 返回 {(ts_code, trade_date) -> id}."""
    if not records:
        return {}
    # STO-72: upsert 前按 (ts_code, trade_date) 兜底去重,避免 parser/merge_same_key 的
    # 边界条件(trade_date 类型混用 / ts_code 前后缀差异)导致 ON CONFLICT DO UPDATE
    # 命中同一行两次而 CardinalityViolation。后入覆盖前入,与 DO UPDATE 语义一致。
    idx_ts = _MAIN_COLS.index("ts_code")
    idx_td = _MAIN_COLS.index("trade_date")
    row_by_key: dict[tuple, tuple] = {}
    for r in records:
        d = r.to_main_row()
        d["trade_date"] = _norm_date(d["trade_date"])
        row = tuple(d.get(c) for c in _MAIN_COLS)
        row_by_key[(row[idx_ts], row[idx_td])] = row
    rows = list(row_by_key.values())

    col_list = ",".join(_MAIN_COLS)
    update_cols = [c for c in _MAIN_COLS if c not in ("ts_code", "trade_date")]
    set_clause = ",".join(f"{c}=EXCLUDED.{c}" for c in update_cols)
    sql = (
        f"INSERT INTO lhb_stock_daily ({col_list}) VALUES %s "
        f"ON CONFLICT (ts_code, trade_date) DO UPDATE SET {set_clause} "
        f"RETURNING id, ts_code, trade_date"
    )
    with conn.cursor() as cur:
        result = execute_values(cur, sql, rows, fetch=True, page_size=500)
    return {(ts, d): i for i, ts, d in result}


# --------------------------------------------------------------------
# upsert seats
# --------------------------------------------------------------------


_SEAT_COLS = [
    "stock_daily_id", "ts_code", "trade_date", "broker_name",
    "broker_type", "is_institution",
    "buy_amount", "sell_amount", "net_amount",
    "direction", "rank",
]


def upsert_seats(conn, records: list[LhbRecord], id_map: dict[tuple, int]) -> int:
    # STO-72: 兜底按 (ts_code, trade_date, broker_name, direction) 去重,
    # 防多个 record 的 seats 合并后同键重复触发 ON CONFLICT CardinalityViolation。
    idx_ts = _SEAT_COLS.index("ts_code")
    idx_td = _SEAT_COLS.index("trade_date")
    idx_br = _SEAT_COLS.index("broker_name")
    idx_dir = _SEAT_COLS.index("direction")
    row_by_key: dict[tuple, tuple] = {}
    for r in records:
        if not r.seats:
            continue
        d = _norm_date(r.trade_date)
        sid = id_map.get((r.ts_code, d))
        if sid is None:
            continue
        for s in r.seats:
            d_row = s.to_db_row(r.ts_code, d, sid)
            row = tuple(d_row.get(c) for c in _SEAT_COLS)
            key = (row[idx_ts], row[idx_td], row[idx_br], row[idx_dir])
            row_by_key[key] = row   # 后入覆盖前入(与 DO UPDATE 语义一致)
    rows = list(row_by_key.values())

    if not rows:
        return 0

    col_list = ",".join(_SEAT_COLS)
    update_cols = ["broker_type", "is_institution",
                   "buy_amount", "sell_amount", "net_amount", "rank"]
    set_clause = ",".join(f"{c}=EXCLUDED.{c}" for c in update_cols)
    sql = (
        f"INSERT INTO lhb_seats ({col_list}) VALUES %s "
        f"ON CONFLICT (ts_code, trade_date, broker_name, direction) "
        f"DO UPDATE SET {set_clause}"
    )
    with conn.cursor() as cur:
        execute_values(cur, sql, rows, page_size=500)
    return len(rows)


# --------------------------------------------------------------------
# 主入口: 一次性写主表 + 席位
# --------------------------------------------------------------------


def ingest_records(conn, records: list[LhbRecord]) -> dict:
    """一站式入库, 返回 {main_rows, seat_rows, skel_rows}."""
    if not records:
        return {"main_rows": 0, "seat_rows": 0, "skel_rows": 0}
    skel_n = ensure_stock_info_skel(conn, records)
    id_map = upsert_main(conn, records)
    seat_n = upsert_seats(conn, records, id_map)
    return {
        "main_rows": len(records),
        "seat_rows": seat_n,
        "skel_rows": skel_n,
    }
