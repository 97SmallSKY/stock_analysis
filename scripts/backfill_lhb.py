#!/usr/bin/env python3
"""龙虎榜全量/区间回填脚本.

用法:
    python scripts/backfill_lhb.py --from 20200101 --to 20260417
    python scripts/backfill_lhb.py --from 20240101 --to 20260417 --no-seats
    python scripts/backfill_lhb.py --from 20240101 --to 20260417 --no-skip-existing

特性:
- 自动从 trade_calendar 读交易日列表 (没有时回退到自然日)
- 默认跳过 lhb_stock_daily 已覆盖的交易日 (断点续跑)
- 完成后统一跑 aggregator.aggregate_all 一次
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timedelta


def _daterange(start: str, end: str) -> list[str]:
    s = datetime.strptime(start, "%Y%m%d")
    e = datetime.strptime(end, "%Y%m%d")
    out = []
    cur = s
    while cur <= e:
        out.append(cur.strftime("%Y%m%d"))
        cur += timedelta(days=1)
    return out


def _load_trading_days(start: str, end: str) -> list[str]:
    """从 trade_calendar 读 (exchange=SSE, is_open=TRUE) 的交易日."""
    try:
        from backend.db.session import pg_conn

        with pg_conn() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT to_char(cal_date,'YYYYMMDD') FROM trade_calendar "
                "WHERE exchange='SSE' AND is_open=TRUE "
                "AND cal_date BETWEEN %s AND %s ORDER BY cal_date",
                (
                    f"{start[:4]}-{start[4:6]}-{start[6:]}",
                    f"{end[:4]}-{end[4:6]}-{end[6:]}",
                ),
            )
            return [r[0] for r in cur.fetchall()]
    except Exception as e:  # noqa: BLE001
        logging.warning(f"[backfill_lhb] 读交易日历失败, 回退到自然日: {e}")
        return []


def _progress(idx: int, total: int, ret: dict) -> None:
    if idx % 10 == 0 or idx == total:
        logging.info(
            f"[backfill_lhb] 进度 {idx}/{total} last={ret.get('trade_date')} "
            f"main={ret.get('main_rows')} seats={ret.get('seat_rows')}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="backfill_lhb")
    parser.add_argument("--from", dest="start", required=True,
                        help="起始日期 YYYYMMDD")
    parser.add_argument("--to", dest="end",
                        default=datetime.now().strftime("%Y%m%d"),
                        help="结束日期 YYYYMMDD (默认今天)")
    parser.add_argument("--no-seats", dest="fetch_seats",
                        action="store_false", default=True,
                        help="不抓席位 (仅主表)")
    parser.add_argument("--no-skip-existing", dest="skip_existing",
                        action="store_false", default=True,
                        help="不跳过已覆盖的交易日")
    parser.add_argument("--no-aggregate", dest="aggregate",
                        action="store_false", default=True,
                        help="完成后不跑 aggregate_all")
    args = parser.parse_args(argv)

    # 初始化代理清理 + 日志
    from backend.core.startup import bootstrap
    bootstrap()
    logging.getLogger().setLevel(logging.INFO)

    from backend.data_gateway.manager import DataSourceManager
    from backend.db.session import pg_conn
    from backend.domain.lhb.daily_job import run_range

    manager = DataSourceManager()

    trade_days = _load_trading_days(args.start, args.end)
    if not trade_days:
        trade_days = _daterange(args.start, args.end)
    logging.info(f"[backfill_lhb] 待处理 {len(trade_days)} 天 "
                 f"[{args.start} -> {args.end}]")

    summary = run_range(
        pg_conn,
        manager,
        trade_days,
        fetch_seats=args.fetch_seats,
        aggregate_at_end=args.aggregate,
        skip_existing=args.skip_existing,
        progress_cb=_progress,
    )
    logging.info(f"[backfill_lhb] 结束: {summary}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
