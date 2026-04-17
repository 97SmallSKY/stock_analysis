"""批量 upsert 工具。

约定: 每个表暴露一个 `upsert_xxx(conn, rows: list[dict])`，
内部用 psycopg2 `execute_values` 做批量写入 + ON CONFLICT。
"""

from __future__ import annotations

import logging
from typing import Iterable, Sequence

from psycopg2.extensions import connection as PGConnection
from psycopg2.extras import execute_values

logger = logging.getLogger(__name__)


def _bulk_upsert(
    conn: PGConnection,
    table: str,
    columns: Sequence[str],
    rows: Iterable[dict],
    conflict_cols: Sequence[str],
    update_cols: Sequence[str] | None = None,
    batch_size: int = 1000,
    coalesce_cols: Sequence[str] | None = None,
) -> int:
    """通用 upsert。

    rows: list of dict; 缺失列写 NULL。
    update_cols: ON CONFLICT 时要覆盖的列；None → 不覆盖（DO NOTHING）。
    coalesce_cols: 这些列用 COALESCE(EXCLUDED.c, table.c),
      即新值为 NULL 时保留旧值。常用于"多轮增量补字段"场景。
    """
    rows = list(rows)
    if not rows:
        return 0

    col_list = ",".join(columns)
    conflict = ",".join(conflict_cols)
    coalesce_set = set(coalesce_cols or [])
    if update_cols:
        parts = []
        for c in update_cols:
            if c in coalesce_set:
                parts.append(f"{c}=COALESCE(EXCLUDED.{c}, {table}.{c})")
            else:
                parts.append(f"{c}=EXCLUDED.{c}")
        set_clause = ",".join(parts)
        on_conflict = f"ON CONFLICT ({conflict}) DO UPDATE SET {set_clause}"
    else:
        on_conflict = f"ON CONFLICT ({conflict}) DO NOTHING"

    sql = f"INSERT INTO {table} ({col_list}) VALUES %s {on_conflict}"
    total = 0
    with conn.cursor() as cur:
        for i in range(0, len(rows), batch_size):
            chunk = rows[i : i + batch_size]
            values = [tuple(r.get(c) for c in columns) for r in chunk]
            execute_values(cur, sql, values, page_size=batch_size)
            total += len(chunk)
    return total


# ------------------------------------------------------------------
# 具体表
# ------------------------------------------------------------------

def upsert_stock_info(conn, rows):
    cols = ["ts_code", "symbol", "name", "area", "industry", "market",
            "list_date", "delist_date", "is_hs", "exchange", "is_active"]
    update = [c for c in cols if c not in ("ts_code",)]
    # industry/area/market/list_date/delist_date/is_hs/exchange 等用 COALESCE
    # 保留旧值, 避免多轮拉取时新值缺失覆盖掉已有行业
    coalesce = ["industry", "area", "market", "list_date",
                "delist_date", "is_hs", "exchange"]
    return _bulk_upsert(
        conn, "stock_info", cols, rows, ["ts_code"], update,
        coalesce_cols=coalesce,
    )


def upsert_trade_calendar(conn, rows):
    cols = ["exchange", "cal_date", "is_open", "pretrade_date"]
    update = ["is_open", "pretrade_date"]
    return _bulk_upsert(conn, "trade_calendar", cols, rows, ["exchange", "cal_date"], update)


def upsert_kline_daily(conn, rows):
    cols = ["ts_code", "trade_date", "open", "high", "low", "close",
            "pre_close", "pct_change", "vol", "amount",
            "turnover_rate", "turnover_rate_f", "volume_ratio",
            "pe_ttm", "pb", "total_mv", "circ_mv",
            "is_st", "is_limit_up", "is_limit_down"]
    update = [c for c in cols if c not in ("ts_code", "trade_date")]
    return _bulk_upsert(conn, "kline_daily", cols, rows, ["ts_code", "trade_date"], update)


def upsert_lhb_stock_daily(conn, rows):
    cols = ["ts_code", "trade_date", "name", "close_price", "pct_change",
            "turnover_rate", "amount",
            "l_buy", "l_sell", "l_net",
            "reason", "reason_type",
            "industry", "market_cap", "float_market_cap"]
    update = [c for c in cols if c not in ("ts_code", "trade_date")]
    return _bulk_upsert(conn, "lhb_stock_daily", cols, rows, ["ts_code", "trade_date"], update)


def upsert_market_sentiment(conn, rows):
    """market_sentiment 没有 UNIQUE(trade_date) 约束，
    先删再插保证幂等。"""
    rows = list(rows)
    if not rows:
        return 0
    cols = ["trade_date",
            "limit_up_count", "limit_down_count",
            "up_down_ratio", "avg_pct_change",
            "strong_stock_count", "weak_stock_count",
            "sentiment_score", "sentiment_level"]
    dates = sorted({r["trade_date"] for r in rows})
    with conn.cursor() as cur:
        # 显式 cast, 避免 trade_date(DATE) 与 text[] 类型不匹配
        cur.execute(
            "DELETE FROM market_sentiment WHERE trade_date = ANY(%s::date[])",
            (dates,),
        )
    col_list = ",".join(cols)
    sql = f"INSERT INTO market_sentiment ({col_list}) VALUES %s"
    with conn.cursor() as cur:
        values = [tuple(r.get(c) for c in cols) for r in rows]
        execute_values(cur, sql, values, page_size=500)
    return len(rows)
