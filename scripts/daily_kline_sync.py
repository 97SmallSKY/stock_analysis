"""
每日 K 线增量同步(远端 cron 跑,不依赖 opencli)

从东方财富 push2 接口一次性拉全市场 A 股当日快照,
upsert 进 kline_daily(含 close_qfq / close_hfq / adj_factor)。

收盘后 19:00 跑。非交易日自动退出。

用法:
  python scripts/daily_kline_sync.py                 # 跑今天
  python scripts/daily_kline_sync.py --date 2026-05-08
  python scripts/daily_kline_sync.py --dry-run
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import psycopg2
import requests
from loguru import logger
from psycopg2.extras import execute_values

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass

PG_CONN_KW = dict(
    host=os.getenv("PG_HOST", "localhost"),
    port=int(os.getenv("POSTGRES_PORT", os.getenv("PG_PORT", "5432"))),
    dbname=os.getenv("POSTGRES_DB", os.getenv("PG_DATABASE", "lhb_quant")),
    user=os.getenv("POSTGRES_USER", os.getenv("PG_USER", "lhb_admin")),
    password=os.getenv("POSTGRES_PASSWORD", os.getenv("PG_PASSWORD", "")),
)

CLIST_URL = "https://push2.eastmoney.com/api/qt/clist/get"
UA = "Mozilla/5.0 (X11; Linux x86_64)"

# 沪深京 A 股: m:1+t:2,3(沪 A) m:0+t:6,80(深 A) m:0+t:81(北证)
MARKETS = {
    "SH": "m:1+t:2,m:1+t:23",
    "SZ": "m:0+t:6,m:0+t:80",
    "BJ": "m:0+t:81",
}

# 字段编码参考 eastmoney clist
# f2=最新价 f3=涨跌幅% f4=涨跌额 f5=成交量(手) f6=成交额(元) f7=振幅% f8=换手率%
# f9=市盈率 f10=量比 f12=code f14=name f15=最高 f16=最低 f17=开盘 f18=昨收
# f20=总市值 f21=流通市值
FIELDS = "f2,f3,f4,f5,f6,f7,f8,f10,f12,f14,f15,f16,f17,f18,f20,f21"

UPSERT_SQL = """
INSERT INTO kline_daily (
    ts_code, trade_date, open, high, low, close, pre_close,
    pct_change, vol, amount, turnover_rate, volume_ratio,
    total_mv, circ_mv, is_limit_up, is_limit_down,
    created_at
) VALUES %s
ON CONFLICT (ts_code, trade_date) DO UPDATE SET
    open = EXCLUDED.open,
    high = EXCLUDED.high,
    low  = EXCLUDED.low,
    close = EXCLUDED.close,
    pre_close = EXCLUDED.pre_close,
    pct_change = EXCLUDED.pct_change,
    vol = EXCLUDED.vol,
    amount = EXCLUDED.amount,
    turnover_rate = EXCLUDED.turnover_rate,
    volume_ratio = EXCLUDED.volume_ratio,
    total_mv = EXCLUDED.total_mv,
    circ_mv = EXCLUDED.circ_mv,
    is_limit_up = EXCLUDED.is_limit_up,
    is_limit_down = EXCLUDED.is_limit_down
"""

# close_qfq / close_hfq / adj_factor 单独一句 UPDATE,除权日才会变。
# 非除权日 close_qfq = close,且 adj_factor 从前一交易日继承。
ADJ_UPDATE_SQL = """
UPDATE kline_daily k
   SET adj_factor = prev.adj_factor,
       close_qfq  = k.close,
       close_hfq  = k.close * prev.adj_factor
  FROM (
      SELECT DISTINCT ON (ts_code) ts_code, adj_factor
        FROM kline_daily
       WHERE adj_factor IS NOT NULL AND trade_date < %s
       ORDER BY ts_code, trade_date DESC
  ) prev
 WHERE k.ts_code = prev.ts_code
   AND k.trade_date = %s
   AND k.adj_factor IS NULL
"""


def is_trading_day(conn, date_str: str) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT is_open FROM trade_calendar WHERE cal_date = %s LIMIT 1",
            (date_str,),
        )
        r = cur.fetchone()
    return bool(r and r[0])


def to_ts_code(code: str, market_key: str) -> str:
    if market_key == "SH":
        return f"{code}.SH"
    if market_key == "SZ":
        return f"{code}.SZ"
    return f"{code}.BJ"


def fetch_market(market_key: str, fs: str) -> list[dict]:
    params = {
        "pn": 1, "pz": 6000, "po": 1, "np": 1,
        "fltt": 2, "invt": 2, "fid": "f3",
        "fs": fs, "fields": FIELDS,
    }
    resp = requests.get(CLIST_URL, params=params, headers={"User-Agent": UA}, timeout=20)
    resp.raise_for_status()
    data = resp.json().get("data") or {}
    diff = data.get("diff") or []
    out = []
    for it in diff:
        code = str(it.get("f12") or "").zfill(6)
        if not code:
            continue
        close = _num(it.get("f2"))
        pre_close = _num(it.get("f18"))
        change = _num(it.get("f4"))
        pct = _num(it.get("f3"))
        high = _num(it.get("f15"))
        low = _num(it.get("f16"))
        if close is None or pre_close is None:
            continue
        out.append({
            "ts_code": to_ts_code(code, market_key),
            "open": _num(it.get("f17")),
            "high": high,
            "low": low,
            "close": close,
            "pre_close": pre_close,
            # push2 返回百分比形式(f3=-0.71),kline_daily 存 decimal
            "pct_change": (pct / 100.0) if pct is not None else None,
            "vol": _num(it.get("f5")),
            # f6 是元,kline_daily.amount 存千元(参照既有 schema)
            "amount": (_num(it.get("f6")) / 1000.0) if it.get("f6") is not None else None,
            "turnover_rate": (_num(it.get("f8")) / 100.0) if it.get("f8") is not None else None,
            "volume_ratio": _num(it.get("f10")),
            # f20/f21 单位是元,schema 里 total_mv/circ_mv 常用万元,这里按万元存
            "total_mv": (_num(it.get("f20")) / 10000.0) if it.get("f20") is not None else None,
            "circ_mv": (_num(it.get("f21")) / 10000.0) if it.get("f21") is not None else None,
            "is_limit_up": (change is not None and pre_close and abs(change / pre_close - _limit_rate(code)) < 0.0002),
            "is_limit_down": (change is not None and pre_close and abs(-change / pre_close - _limit_rate(code)) < 0.0002),
        })
    return out


def _num(v):
    if v is None or v == "-" or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _limit_rate(code: str) -> float:
    # 主板 10%,科创 / 创业 20%,北证 30%,ST 5%(这里忽略 ST)
    if code.startswith(("688", "300")):
        return 0.20
    if code.startswith(("4", "8", "9")):
        return 0.30
    return 0.10


def upsert_rows(conn, rows: list[dict], trade_date: str) -> int:
    if not rows:
        return 0
    now = datetime.now()
    tuples = [
        (
            r["ts_code"], trade_date,
            r["open"], r["high"], r["low"], r["close"], r["pre_close"],
            r["pct_change"], r["vol"], r["amount"], r["turnover_rate"], r["volume_ratio"],
            r["total_mv"], r["circ_mv"],
            r["is_limit_up"], r["is_limit_down"],
            now,
        )
        for r in rows
    ]
    with conn.cursor() as cur:
        execute_values(cur, UPSERT_SQL, tuples, page_size=1000)
    conn.commit()
    return len(tuples)


def propagate_adj_factor(conn, trade_date: str) -> int:
    """新写入行继承前一交易日的 adj_factor。除权日会被后续离线修正脚本覆盖。"""
    with conn.cursor() as cur:
        cur.execute(ADJ_UPDATE_SQL, (trade_date, trade_date))
        updated = cur.rowcount
    conn.commit()
    return updated


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    conn = psycopg2.connect(**PG_CONN_KW)
    if not is_trading_day(conn, args.date):
        logger.info(f"{args.date} 非交易日,退出")
        return 0

    total_rows = []
    for mk, fs in MARKETS.items():
        try:
            rows = fetch_market(mk, fs)
        except Exception as e:
            logger.exception(f"拉取 {mk} 失败: {e}")
            rows = []
        logger.info(f"{mk} 拉取 {len(rows)} 行")
        total_rows.extend(rows)
        time.sleep(0.5)

    if args.dry_run:
        logger.info(f"[dry] 共 {len(total_rows)} 行,首行={total_rows[0] if total_rows else None}")
        return 0

    n = upsert_rows(conn, total_rows, args.date)
    logger.info(f"upsert {n} 行")
    adj_n = propagate_adj_factor(conn, args.date)
    logger.info(f"继承 adj_factor {adj_n} 行")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
