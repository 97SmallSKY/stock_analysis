"""
本地一次性回补 5 年全 A 股日 K 线 + 复权数据到 kline_daily

为每只股票调用 opencli astock kline 三次(none / fwd / bwd),
以 --adj none 为基准行,adj_factor = close_hfq / close,close_qfq 从 fwd 档拿。
批量 upsert,断点续传,限速退避。

用法:
  python scripts/backfill_kline_5y.py --start 2021-05-08 --end 2026-05-08
  python scripts/backfill_kline_5y.py --resume                   # 继续上次
  python scripts/backfill_kline_5y.py --dry-run --limit 5        # 只跑 5 只不写库
  python scripts/backfill_kline_5y.py --codes 600519.SH,000001.SZ

前置:
  1. 已 apply scripts/migrate_20260508c_kline_adj_factor.sql
  2. 本机装了 opencli,远程 PG 在 .env 可达
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import psycopg2
from psycopg2.extras import execute_values
from loguru import logger

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

PROGRESS_FILE = Path(__file__).resolve().parent.parent / "data" / "backfill_kline_progress.json"

UPSERT_SQL = """
INSERT INTO kline_daily (
    ts_code, trade_date, open, high, low, close, pre_close,
    pct_change, vol, amount, turnover_rate,
    is_limit_up, is_limit_down,
    adj_factor, close_qfq, close_hfq,
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
    is_limit_up = EXCLUDED.is_limit_up,
    is_limit_down = EXCLUDED.is_limit_down,
    adj_factor = EXCLUDED.adj_factor,
    close_qfq = EXCLUDED.close_qfq,
    close_hfq = EXCLUDED.close_hfq
"""


def run_opencli_kline(code_raw: str, adj: str, start: str, end: str,
                     retries: int = 3) -> list[dict]:
    """调 opencli astock kline,返回 JSON 列表,失败 None。code_raw 纯数字。"""
    cmd = [
        "opencli", "astock", "kline", code_raw,
        "--period", "day", "--adj", adj,
        "--from", start, "--to", end,
        "--limit", "5000",
        "-f", "json",
    ]
    delay = 0.3
    for attempt in range(retries):
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            if res.returncode != 0:
                logger.warning(f"opencli rc={res.returncode} code={code_raw} adj={adj} stderr={res.stderr[:200]}")
                time.sleep(delay * (attempt + 1))
                continue
            return json.loads(res.stdout)
        except subprocess.TimeoutExpired:
            logger.warning(f"opencli timeout code={code_raw} adj={adj} attempt={attempt+1}")
            time.sleep(delay * 3 * (attempt + 1))
        except json.JSONDecodeError as e:
            logger.warning(f"opencli JSON parse fail code={code_raw} adj={adj}: {e}")
            time.sleep(delay * (attempt + 1))
    return []


def ts_code_to_raw(ts_code: str) -> str:
    """600519.SH → 600519"""
    return ts_code.split(".")[0]


def fetch_one_stock(ts_code: str, start: str, end: str) -> list[tuple]:
    """
    对一只股票跑 3 档,合并为 upsert 行列表。
    adj_factor = close_hfq / close_none,close_qfq 直接取 fwd 档 close。
    任一档拿不到则跳过该股(返回空)。
    """
    code_raw = ts_code_to_raw(ts_code)
    none_rows = run_opencli_kline(code_raw, "none", start, end)
    if not none_rows:
        return []
    time.sleep(0.2)
    fwd_rows = run_opencli_kline(code_raw, "fwd", start, end)
    time.sleep(0.2)
    bwd_rows = run_opencli_kline(code_raw, "bwd", start, end)
    time.sleep(0.2)

    fwd_by_date = {r["trade_date"]: r["close"] for r in fwd_rows}
    bwd_by_date = {r["trade_date"]: r["close"] for r in bwd_rows}

    now = datetime.now()
    out = []
    for r in none_rows:
        d = r["trade_date"]
        close_none = r["close"]
        close_hfq = bwd_by_date.get(d)
        close_qfq = fwd_by_date.get(d)
        adj_factor = (close_hfq / close_none) if (close_hfq and close_none) else None
        out.append((
            r["ts_code"], d,
            r.get("open"), r.get("high"), r.get("low"), close_none,
            r.get("pre_close"), r.get("pct_change"),
            r.get("vol"), r.get("amount"), r.get("turnover_rate"),
            r.get("is_limit_up"), r.get("is_limit_down"),
            adj_factor, close_qfq, close_hfq,
            now,
        ))
    return out


def load_stock_codes(conn, codes_filter: Optional[list[str]], limit: Optional[int]) -> list[str]:
    with conn.cursor() as cur:
        if codes_filter:
            cur.execute(
                "SELECT ts_code FROM stock_info WHERE ts_code = ANY(%s) ORDER BY ts_code",
                (codes_filter,),
            )
        else:
            # stock_info.exchange 字段 5507/6297 为空,不可靠,改用 ts_code 后缀判断
            cur.execute(
                "SELECT ts_code FROM stock_info "
                " WHERE COALESCE(is_active, true) = true "
                "   AND (ts_code LIKE '%%.SH' OR ts_code LIKE '%%.SZ' OR ts_code LIKE '%%.BJ') "
                " ORDER BY ts_code"
            )
        codes = [r[0] for r in cur.fetchall()]
    if limit:
        codes = codes[:limit]
    return codes


def load_progress() -> set[str]:
    if PROGRESS_FILE.exists():
        return set(json.loads(PROGRESS_FILE.read_text()))
    return set()


def save_progress(done: set[str]) -> None:
    PROGRESS_FILE.parent.mkdir(parents=True, exist_ok=True)
    PROGRESS_FILE.write_text(json.dumps(sorted(done)))


def batch_upsert(conn, rows: list[tuple]) -> None:
    if not rows:
        return
    with conn.cursor() as cur:
        execute_values(cur, UPSERT_SQL, rows, page_size=1000)
    conn.commit()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2021-05-08")
    ap.add_argument("--end", default=datetime.now().strftime("%Y-%m-%d"))
    ap.add_argument("--resume", action="store_true", help="跳过 progress 文件里已完成的 ts_code")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, help="只跑前 N 只")
    ap.add_argument("--codes", help="逗号分隔指定 ts_code,优先于 --limit")
    ap.add_argument("--batch-size", type=int, default=5000, help="累积多少行 upsert 一次")
    args = ap.parse_args()

    codes_filter = [c.strip() for c in args.codes.split(",")] if args.codes else None

    conn = psycopg2.connect(**PG_CONN_KW)
    codes = load_stock_codes(conn, codes_filter, args.limit)
    logger.info(f"候选 ts_code 数量: {len(codes)}")

    done: set[str] = load_progress() if args.resume else set()
    if args.resume:
        logger.info(f"resume: 已完成 {len(done)},跳过")
        codes = [c for c in codes if c not in done]

    pending_rows: list[tuple] = []
    total_rows_written = 0
    t0 = time.time()

    for i, ts in enumerate(codes, 1):
        try:
            rows = fetch_one_stock(ts, args.start, args.end)
        except Exception as e:
            logger.exception(f"fetch {ts} 失败: {e}")
            rows = []

        if rows:
            pending_rows.extend(rows)
            done.add(ts)
        else:
            logger.warning(f"{ts} 无数据或失败,跳过")

        if args.dry_run:
            if rows:
                logger.info(f"[dry] {ts} {len(rows)} 行 首行={rows[0]}")
        elif len(pending_rows) >= args.batch_size:
            batch_upsert(conn, pending_rows)
            total_rows_written += len(pending_rows)
            pending_rows = []
            save_progress(done)

        if i % 20 == 0:
            elapsed = time.time() - t0
            rate = i / elapsed if elapsed else 0
            eta_min = (len(codes) - i) / rate / 60 if rate else 0
            logger.info(f"进度 {i}/{len(codes)} rate={rate:.2f} ts/s eta={eta_min:.1f}min 已写={total_rows_written}")

    if pending_rows and not args.dry_run:
        batch_upsert(conn, pending_rows)
        total_rows_written += len(pending_rows)
        save_progress(done)

    conn.close()
    logger.info(f"完成 总股票={len(done)} 总行={total_rows_written} 耗时={(time.time()-t0)/60:.1f}min")


if __name__ == "__main__":
    main()
