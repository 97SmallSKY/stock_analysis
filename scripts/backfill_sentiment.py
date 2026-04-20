#!/usr/bin/env python3
"""market_sentiment 回填脚本 — 用新公式重算 sentiment_score / sentiment_level.

STO-32 落地工具(2026-04-20 P20.6 方案 A):
  旧公式 `(ratio-1)*20 + avg*600 + (lu-ld)*0.5` 导致历史 538 天分布严重双峰
  (100 占 36% / 45 NEUTRAL 长尾),区分度不足。
  新公式 `(ratio-1)*10 + avg*300 + (lu-ld)*0.1` 输出在 [0,100] 连续散落。

  本脚本:
    1. 从 market_sentiment 读所有已有日期
    2. 对每一天,用 `sentiment._calc_from_kline` 从 kline_daily 重算
    3. 在**单个事务**里 DELETE + re-INSERT(全 or 无,失败自动回滚)

用法:
    # 预演,不实际写库(打印新旧分布对比)
    python3 scripts/backfill_sentiment.py --dry-run

    # 正式回填(必须先在生产库上 pg_dump 备份)
    python3 scripts/backfill_sentiment.py --apply

    # 指定日期范围
    python3 scripts/backfill_sentiment.py --apply --from 20240101 --to 20261231

⚠️  生产库执行前必做:
    ssh jd 'pg_dump -U postgres stock_analysis -t market_sentiment \
        > /tmp/market_sentiment_backup_$(date +%Y%m%d).sql'
    (备份保留 7 天,真发现问题可 `psql -f` 恢复)
"""
from __future__ import annotations

import argparse
import logging
import statistics
import sys


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def _load_existing_dates(conn, start: str | None, end: str | None) -> list[str]:
    """从 market_sentiment 读所有 trade_date(YYYYMMDD,升序)。"""
    sql = (
        "SELECT to_char(trade_date, 'YYYYMMDD') FROM market_sentiment"
    )
    params: tuple = ()
    if start and end:
        sql += " WHERE trade_date BETWEEN %s AND %s"
        params = (
            f"{start[:4]}-{start[4:6]}-{start[6:]}",
            f"{end[:4]}-{end[4:6]}-{end[6:]}",
        )
    sql += " ORDER BY trade_date"
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return [r[0] for r in cur.fetchall()]


def _load_old_scores(conn, start: str | None, end: str | None) -> list[float]:
    sql = "SELECT sentiment_score FROM market_sentiment WHERE sentiment_score IS NOT NULL"
    params: tuple = ()
    if start and end:
        sql += " AND trade_date BETWEEN %s AND %s"
        params = (
            f"{start[:4]}-{start[4:6]}-{start[6:]}",
            f"{end[:4]}-{end[4:6]}-{end[6:]}",
        )
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return [float(r[0]) for r in cur.fetchall()]


def _format_dist(scores: list[float]) -> str:
    if not scores:
        return "(空)"
    return (
        f"n={len(scores)} min={min(scores):.2f} max={max(scores):.2f} "
        f"avg={sum(scores) / len(scores):.2f} "
        f"stddev={statistics.stdev(scores) if len(scores) > 1 else 0:.2f} "
        f"unique={len(set(round(s, 2) for s in scores))}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--from", dest="d_from", help="起始日 YYYYMMDD(默认全表)")
    parser.add_argument("--to", dest="d_to", help="结束日 YYYYMMDD(默认全表)")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="只计算新分布,不改库")
    mode.add_argument("--apply", action="store_true", help="在事务里 DELETE+INSERT 重算")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    _configure_logging(args.verbose)
    log = logging.getLogger("backfill_sentiment")

    # 延迟导入:不依赖 DB 即可展示 help
    from backend.db.session import pg_conn
    from backend.db.writer import upsert_market_sentiment
    from backend.ingest.sentiment import _calc_from_kline

    with pg_conn() as conn:
        days = _load_existing_dates(conn, args.d_from, args.d_to)
        old_scores = _load_old_scores(conn, args.d_from, args.d_to)
        if not days:
            log.warning("market_sentiment 在指定范围内无记录,退出")
            return 0
        log.info(f"范围: {days[0]} ~ {days[-1]},共 {len(days)} 天")
        log.info(f"旧分布: {_format_dist(old_scores)}")

        # --- Step 1: 先 simulate 全部天,收集新分布 ---
        new_rows: list[dict] = []
        missing_kline_days: list[str] = []
        for d in days:
            row = _calc_from_kline(conn, d)
            if row is None:
                # kline_daily 里无数据(很少见,比如港股节假日),保持原记录不动
                missing_kline_days.append(d)
                continue
            new_rows.append(row)

        new_scores = [r["sentiment_score"] for r in new_rows if r.get("sentiment_score") is not None]
        log.info(f"新分布: {_format_dist(new_scores)}")
        if missing_kline_days:
            log.warning(
                f"{len(missing_kline_days)} 天 kline_daily 无数据,将保留旧记录不动。"
                f" 样例:{missing_kline_days[:5]}"
            )

        if args.dry_run:
            log.info("[dry-run] 完成预演,未写库")
            return 0

        # --- Step 2: 事务内 DELETE + upsert(失败自动回滚)---
        # 原则:要么全成,要么全回滚,不留脏数据
        if not new_rows:
            log.error("没有可写入的行,拒绝 DELETE(保护现有数据)")
            return 2

        delete_dates = [r["trade_date"] for r in new_rows]  # ISO 格式 YYYY-MM-DD
        log.info(f"开始事务:将 DELETE {len(delete_dates)} 天 + upsert 重算结果")

        try:
            with conn.cursor() as cur:
                # DELETE 仅限于我们会重新 insert 的那批日期,避免破坏没有 kline 的天
                cur.execute(
                    "DELETE FROM market_sentiment WHERE trade_date = ANY(%s)",
                    (delete_dates,),
                )
                log.info(f"DELETE 完成,影响 {cur.rowcount} 行")

            written = upsert_market_sentiment(conn, new_rows)
            log.info(f"upsert_market_sentiment 写入 {written} 行")

            conn.commit()
            log.info("✅ 事务提交成功")
        except Exception as exc:
            conn.rollback()
            log.exception(f"❌ 回填失败,已回滚: {exc}")
            return 1

        # --- Step 3: 复核新分布 ---
        verify_scores = _load_old_scores(conn, args.d_from, args.d_to)
        log.info(f"复核新表分布: {_format_dist(verify_scores)}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
