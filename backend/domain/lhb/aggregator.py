"""龙虎榜聚合统计 -> lhb_agg_stats.

只做 STOCK 维度 7D/30D/90D/ALL 的基础统计(lhb_count, 净买入和等).
BROKER 维度需要 lhb_seats 填充后才能算, 留给 W4-2 之后的迭代.
INDUSTRY 维度依赖 lhb_stock_daily.industry 字段(现有).

胜率字段(win_rate_*)暂留 NULL, 需要 lhb_tn_price 落地后再回填.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Literal

from psycopg2.extras import execute_values

logger = logging.getLogger(__name__)

Period = Literal["7D", "30D", "90D", "ALL"]
PERIOD_DAYS = {"7D": 7, "30D": 30, "90D": 90, "ALL": None}


def _period_start(as_of: date, period: Period) -> date | None:
    days = PERIOD_DAYS[period]
    if days is None:
        return None
    return as_of - timedelta(days=days)


# --------------------------------------------------------------------
# STOCK 维度
# --------------------------------------------------------------------


def _aggregate_stock(conn, as_of: date, period: Period) -> list[tuple]:
    start = _period_start(as_of, period)
    where = "WHERE trade_date <= %s" + (" AND trade_date >= %s" if start else "")
    params = [as_of] + ([start] if start else [])
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT
                ts_code,
                COUNT(*)                                 AS lhb_count,
                SUM(CASE WHEN l_net > 0 THEN 1 ELSE 0 END)  AS buy_count,
                SUM(CASE WHEN l_net < 0 THEN 1 ELSE 0 END)  AS sell_count,
                COALESCE(SUM(l_buy),  0)                 AS total_buy,
                COALESCE(SUM(l_sell), 0)                 AS total_sell,
                COALESCE(SUM(l_net),  0)                 AS total_net,
                AVG(NULLIF(score, 0))                    AS avg_score
            FROM lhb_stock_daily
            {where}
            GROUP BY ts_code
            """,
            params,
        )
        rows = cur.fetchall()
    return [
        ("STOCK", ts, period, cnt, buy, sell, tb, ts2, tn, avg_sc)
        for ts, cnt, buy, sell, tb, ts2, tn, avg_sc in rows
    ]


# --------------------------------------------------------------------
# INDUSTRY 维度
# --------------------------------------------------------------------


def _aggregate_industry(conn, as_of: date, period: Period) -> list[tuple]:
    start = _period_start(as_of, period)
    where = "WHERE industry IS NOT NULL AND trade_date <= %s" + (
        " AND trade_date >= %s" if start else ""
    )
    params = [as_of] + ([start] if start else [])
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT
                industry,
                COUNT(*)                                 AS lhb_count,
                SUM(CASE WHEN l_net > 0 THEN 1 ELSE 0 END)  AS buy_count,
                SUM(CASE WHEN l_net < 0 THEN 1 ELSE 0 END)  AS sell_count,
                COALESCE(SUM(l_buy),  0)                 AS total_buy,
                COALESCE(SUM(l_sell), 0)                 AS total_sell,
                COALESCE(SUM(l_net),  0)                 AS total_net,
                AVG(NULLIF(score, 0))                    AS avg_score
            FROM lhb_stock_daily
            {where}
            GROUP BY industry
            """,
            params,
        )
        rows = cur.fetchall()
    return [
        ("INDUSTRY", ind, period, cnt, buy, sell, tb, ts2, tn, avg_sc)
        for ind, cnt, buy, sell, tb, ts2, tn, avg_sc in rows
    ]


# --------------------------------------------------------------------
# BROKER 维度
# --------------------------------------------------------------------


def _aggregate_broker(conn, as_of: date, period: Period) -> list[tuple]:
    start = _period_start(as_of, period)
    where = "WHERE trade_date <= %s" + (" AND trade_date >= %s" if start else "")
    params = [as_of] + ([start] if start else [])
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT
                broker_name,
                COUNT(*)                                              AS lhb_count,
                SUM(CASE WHEN direction = 'BUY'  THEN 1 ELSE 0 END)   AS buy_count,
                SUM(CASE WHEN direction = 'SELL' THEN 1 ELSE 0 END)   AS sell_count,
                COALESCE(SUM(buy_amount),  0)                         AS total_buy,
                COALESCE(SUM(sell_amount), 0)                         AS total_sell,
                COALESCE(SUM(net_amount),  0)                         AS total_net
            FROM lhb_seats
            {where}
            GROUP BY broker_name
            """,
            params,
        )
        rows = cur.fetchall()
    return [
        ("BROKER", br, period, cnt, buy, sell, tb, ts2, tn, None)
        for br, cnt, buy, sell, tb, ts2, tn in rows
    ]


# --------------------------------------------------------------------
# upsert
# --------------------------------------------------------------------


_AGG_COLS = [
    "stat_type", "stat_key", "stat_period",
    "lhb_count", "buy_count", "sell_count",
    "total_buy_amount", "total_sell_amount", "total_net_amount",
    "avg_score",
]


def _upsert_agg(conn, rows: list[tuple]) -> int:
    if not rows:
        return 0
    col_list = ",".join(_AGG_COLS)
    update_cols = [c for c in _AGG_COLS
                   if c not in ("stat_type", "stat_key", "stat_period")]
    set_clause = ",".join(f"{c}=EXCLUDED.{c}" for c in update_cols)
    sql = (
        f"INSERT INTO lhb_agg_stats ({col_list}) VALUES %s "
        f"ON CONFLICT (stat_type, stat_key, stat_period) DO UPDATE SET "
        f"{set_clause}, updated_at = NOW()"
    )
    with conn.cursor() as cur:
        execute_values(cur, sql, rows, page_size=500)
    return len(rows)


# --------------------------------------------------------------------
# 对外入口
# --------------------------------------------------------------------


def aggregate_all(conn, as_of: date | None = None,
                  periods: tuple[Period, ...] = ("7D", "30D", "90D", "ALL"),
                  include_broker: bool = True) -> dict:
    """跑全部聚合. 返回各 stat_type 写入行数."""
    if as_of is None:
        as_of = date.today()

    stock_total = broker_total = industry_total = 0
    for p in periods:
        stock_total += _upsert_agg(conn, _aggregate_stock(conn, as_of, p))
        industry_total += _upsert_agg(conn, _aggregate_industry(conn, as_of, p))
        if include_broker:
            try:
                broker_total += _upsert_agg(conn, _aggregate_broker(conn, as_of, p))
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    f"[aggregator] broker 聚合失败(lhb_seats 可能为空): {e}"
                )

    conn.commit()
    logger.info(
        f"[aggregator] 完成: stock={stock_total} broker={broker_total} "
        f"industry={industry_total} (as_of={as_of}, periods={periods})"
    )
    return {
        "stock": stock_total,
        "broker": broker_total,
        "industry": industry_total,
    }
