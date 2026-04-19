"""P16.5 lhb_tn_price 回填.

输入: lhb_stock_daily (每只股票的上榜日) + kline_daily (日线行情).
输出: lhb_tn_price (基准日 + N_day 的 close / pnl_pct / high / low / limit 标记).

策略: 用 kline_daily 按 ts_code ORDER BY trade_date 的 row_number 做 offset N,
得到 T+N 日的收盘价; 区间 high/low 用 MAX/MIN 聚合 trade_date BETWEEN T AND T+N.
纯 SQL INSERT ... ON CONFLICT DO UPDATE, 不走 Python 循环,速度 O(rows).

用法 (CLI):
  python -m backend.cli lhb-tn-price --from 20260101 --to 20260417

涨跌停判断:
  * T+N close 相对 T+(N-1) close 的变动;这里简化为「close / pre_close - 1 >= 0.099」作为涨停判据
    (主板 10% 偏差的近似;ST / 创业板 / 科创板先不区分,上层可按 ts_code 前缀自定义).
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Iterable, Optional

from backend.db.session import pg_conn

logger = logging.getLogger(__name__)

DEFAULT_N_DAYS = (1, 2, 3, 5, 10)

# 主板涨跌停幅度近似 10%, 实际 ST/创业板/科创板不同, 留给上层按 ts_code 前缀扩展
LIMIT_PCT = 0.099


def _pg_date(d) -> str:
    if isinstance(d, date):
        return d.isoformat()
    # 支持 'YYYYMMDD' / 'YYYY-MM-DD'
    s = str(d).replace("-", "")
    if len(s) != 8:
        raise ValueError(f"bad date: {d}")
    return f"{s[:4]}-{s[4:6]}-{s[6:]}"


def run_range(
    date_from,
    date_to,
    *,
    n_days: Iterable[int] = DEFAULT_N_DAYS,
    only_pending: bool = True,
) -> dict:
    """回填 [date_from, date_to] 区间内上榜股票的 T+N 价格.

    - only_pending=True: 跳过已 DONE 的 stock_daily_id+n_day 组合 (断点续跑).
    - 返回 stats dict.
    """
    df = _pg_date(date_from)
    dt = _pg_date(date_to)
    n_days = tuple(n_days)
    if not n_days:
        raise ValueError("n_days 为空")

    stats = {"n_days": list(n_days), "inserted": 0, "updated": 0, "skipped": 0}

    with pg_conn() as conn, conn.cursor() as cur:
        for n in n_days:
            # 单个 N 做一次 upsert.
            # 找基准日 T 的 row_num + close; 然后 T+n 行的 close;
            # 区间 high/low 通过 kline_daily 子查询 MAX/MIN WHERE trade_date BETWEEN T AND T+n
            sql = """
            WITH base AS (
              SELECT lsd.id AS stock_daily_id, lsd.ts_code, lsd.trade_date AS t0,
                     lsd.close_price AS base_close
              FROM lhb_stock_daily lsd
              WHERE lsd.trade_date BETWEEN %(df)s::date AND %(dt)s::date
            ),
            ranked AS (
              SELECT k.ts_code, k.trade_date, k.close,
                     ROW_NUMBER() OVER (PARTITION BY k.ts_code ORDER BY k.trade_date) AS rn
              FROM kline_daily k
              WHERE k.trade_date BETWEEN %(df)s::date AND (%(dt)s::date + INTERVAL '30 days')
            ),
            t_rn AS (
              SELECT b.stock_daily_id, b.ts_code, b.t0, b.base_close, r.rn AS t_rn
              FROM base b
              JOIN ranked r ON r.ts_code = b.ts_code AND r.trade_date = b.t0
            ),
            tn AS (
              SELECT tr.stock_daily_id, tr.ts_code, tr.t0, tr.base_close,
                     r2.trade_date AS target_date, r2.close AS tn_close
              FROM t_rn tr
              JOIN ranked r2 ON r2.ts_code = tr.ts_code AND r2.rn = tr.t_rn + %(n)s
            ),
            agg AS (
              SELECT tn.stock_daily_id, tn.ts_code, tn.t0, tn.target_date,
                     tn.tn_close,
                     MAX(k2.high) AS high_price,
                     MIN(k2.low)  AS low_price,
                     (tn.tn_close - tn.base_close) / NULLIF(tn.base_close, 0) AS pnl,
                     (MAX(k2.high) - tn.base_close) / NULLIF(tn.base_close, 0) AS high_pct_calc,
                     (MIN(k2.low)  - tn.base_close) / NULLIF(tn.base_close, 0) AS low_pct_calc
              FROM tn
              JOIN kline_daily k2 ON k2.ts_code = tn.ts_code
                AND k2.trade_date BETWEEN tn.t0 AND tn.target_date
              GROUP BY tn.stock_daily_id, tn.ts_code, tn.t0, tn.target_date, tn.tn_close, tn.base_close
            )
            INSERT INTO lhb_tn_price (
              stock_daily_id, ts_code, trade_date, n_day,
              target_date, close_price, pnl_pct,
              high_price, low_price, high_pct, low_pct,
              is_limit_up, is_limit_down, calc_status, updated_at
            )
            SELECT
              a.stock_daily_id, a.ts_code, a.t0, %(n)s,
              a.target_date, a.tn_close, a.pnl,
              a.high_price, a.low_price, a.high_pct_calc, a.low_pct_calc,
              a.pnl >= %(lim)s, a.pnl <= -%(lim)s, 'DONE', NOW()
            FROM agg a
            ON CONFLICT (stock_daily_id, n_day) DO UPDATE SET
              target_date = EXCLUDED.target_date,
              close_price = EXCLUDED.close_price,
              pnl_pct     = EXCLUDED.pnl_pct,
              high_price  = EXCLUDED.high_price,
              low_price   = EXCLUDED.low_price,
              high_pct    = EXCLUDED.high_pct,
              low_pct     = EXCLUDED.low_pct,
              is_limit_up = EXCLUDED.is_limit_up,
              is_limit_down = EXCLUDED.is_limit_down,
              calc_status = 'DONE',
              updated_at  = NOW()
            WHERE (%(only_pending)s = FALSE OR lhb_tn_price.calc_status <> 'DONE')
            """
            cur.execute(sql, {
                "df": df, "dt": dt, "n": n,
                "lim": LIMIT_PCT,
                "only_pending": only_pending,
            })
            rows = cur.rowcount
            stats[f"n{n}_upserted"] = rows
            logger.info(f"[tn_price] n={n} upserted={rows}")
        conn.commit()

    return stats


def run_all_empty() -> dict:
    """全量重跑 (小心!). 回填 lhb_stock_daily 的最小/最大日期区间."""
    with pg_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT MIN(trade_date), MAX(trade_date) FROM lhb_stock_daily")
        mn, mx = cur.fetchone()
    if not mn or not mx:
        return {"skipped": "lhb_stock_daily empty"}
    return run_range(mn, mx, only_pending=True)
