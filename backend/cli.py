"""命令行入口。

用法:
    python -m backend.cli ingest stock-info
    python -m backend.cli ingest calendar --from 20200101 --to 20261231
    python -m backend.cli ingest kline    --from 20200101 --to 20260417 [--codes 000001.SZ,600519.SH | all]
    python -m backend.cli ingest lhb      --from 20240101 --to 20260417
    python -m backend.cli ingest sentiment --from 20240101 --to 20260417
    python -m backend.cli score          --from 20240101 --to 20241231 [--only-empty] [--limit 100]
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime


def _today() -> str:
    return datetime.now().strftime("%Y%m%d")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="backend.cli")
    sub = parser.add_subparsers(dest="cmd", required=True)

    ingest = sub.add_parser("ingest", help="数据灌库")
    ingest_sub = ingest.add_subparsers(dest="dataset", required=True)

    ingest_sub.add_parser("stock-info")

    cal = ingest_sub.add_parser("calendar")
    cal.add_argument("--from", dest="start", default="20200101")
    cal.add_argument("--to", dest="end", default=_today())
    cal.add_argument("--exchange", default="SSE")

    kl = ingest_sub.add_parser("kline")
    kl.add_argument("--from", dest="start", default="20200101")
    kl.add_argument("--to", dest="end", default=_today())
    kl.add_argument("--codes", default="all",
                    help="逗号分隔的 ts_code，或 'all' 代表全市场")
    kl.add_argument("--workers", type=int, default=4,
                    help="并发采集线程数 (默认 4). 1=串行, 推荐 2~6")

    lhb = ingest_sub.add_parser("lhb")
    lhb.add_argument("--from", dest="start", default="20240101")
    lhb.add_argument("--to", dest="end", default=_today())

    # 仅回填 lhb_seats(主表已齐时用)
    lhbs = ingest_sub.add_parser("lhb-seats")
    lhbs.add_argument("--from", dest="start", default="20240101")
    lhbs.add_argument("--to", dest="end", default=_today())
    lhbs.add_argument("--no-skip", action="store_true",
                      help="不跳过 lhb_seats 已有的日期(强制重采)")

    st = ingest_sub.add_parser("sentiment")
    st.add_argument("--from", dest="start", default="20240101")
    st.add_argument("--to", dest="end", default=_today())

    sc = sub.add_parser("score", help="龙虎榜评分批量计算")
    sc.add_argument("--from", dest="start", default="20240101")
    sc.add_argument("--to", dest="end", default=_today())
    sc.add_argument("--only-empty", action="store_true",
                    help="只处理 score=0 的行(断点续跑)")
    sc.add_argument("--limit", type=int, default=None,
                    help="最多处理多少行(调试用)")

    args = parser.parse_args(argv)

    from backend.core.startup import bootstrap
    bootstrap()
    logging.getLogger().setLevel(logging.INFO)

    if args.cmd == "ingest":
        if args.dataset == "stock-info":
            from backend.ingest import stock_info
            stock_info.run()
        elif args.dataset == "calendar":
            from backend.ingest import calendar as cal_mod
            cal_mod.run(args.start, args.end, args.exchange)
        elif args.dataset == "kline":
            from backend.ingest import kline
            codes = None if args.codes in ("all", "") else [c.strip() for c in args.codes.split(",") if c.strip()]
            kline.run(args.start, args.end, codes, workers=args.workers)
        elif args.dataset == "lhb":
            from backend.ingest import lhb
            lhb.run(args.start, args.end)
        elif args.dataset == "lhb-seats":
            from backend.ingest import lhb as lhb_mod
            lhb_mod.run_seats(args.start, args.end,
                              skip_existing=not args.no_skip)
        elif args.dataset == "sentiment":
            from backend.ingest import sentiment
            sentiment.run(args.start, args.end)
    elif args.cmd == "score":
        from backend.scoring import runner as score_runner
        score_runner.run(
            args.start, args.end,
            only_empty=args.only_empty,
            limit=args.limit,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
