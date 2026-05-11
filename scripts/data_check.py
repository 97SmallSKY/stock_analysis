#!/usr/bin/env python3
"""数据完整性扫描 + 幂等补齐脚本"""
import sys, os, argparse
from datetime import datetime, date, timedelta
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

import pandas as pd
from loguru import logger
from sqlalchemy import text
from utils.storage import storage

logger.add(ROOT / "logs/data_check.log", rotation="7 days", retention="30 days",
           encoding="utf-8", level="INFO")

# ── 需要扫描的核心表 ──
# 注意：date_col 必须是表中实际存在的列名
CORE_TABLES = {
    # 真实分析表（上游数据源）— 按时序检查
    "lhb_stock_daily":    {"date_col": "trade_date", "desc": "龙虎榜个股日度数据"},
    "lhb_seats":          {"date_col": "trade_date", "desc": "龙虎榜席位明细"},
    "kline_daily":        {"date_col": "trade_date", "desc": "日K线"},
    "market_sentiment":   {"date_col": "trade_date", "desc": "市场情绪"},
    "trade_calendar":     {"date_col": "cal_date",   "desc": "交易日历"},
    # 逻辑表（ETL 桥接产出）— 按时序检查
    "lhb_top10":          {"date_col": "trade_date", "desc": "龙虎榜Top10 (逻辑)"},
    "lhb_detail":         {"date_col": "trade_date", "desc": "龙虎榜明细 (逻辑)"},
    "lhb_top10_prices":   {"date_col": "trade_date", "desc": "龙虎榜T+N价格 (逻辑)"},
    "lhb_returns":        {"date_col": "trade_date", "desc": "龙虎榜收益表 (逻辑)"},
    # 聚合表 — 只检查总行数，不按日期检查
    "lhb_jigou":          {"date_col": None,          "desc": "机构席位汇总 (聚合)"},
    "lhb_ggtj":           {"date_col": None,          "desc": "个股上榜统计 (聚合)"},
}


def get_latest_date(table: str, date_col: str) -> str | None:
    """查询表中最新的日期"""
    engine = storage._get_engine()
    with engine.connect() as conn:
        try:
            r = conn.execute(
                text(f'SELECT MAX("{date_col}") FROM "{table}"')
            ).scalar()
            return str(r)[:10] if r else None
        except Exception as e:
            return f"ERROR: {e}"


def get_date_range(table: str, date_col: str) -> tuple:
    """查询表的日期范围"""
    engine = storage._get_engine()
    with engine.connect() as conn:
        try:
            r = conn.execute(
                text(f'SELECT MIN("{date_col}"), MAX("{date_col}"), COUNT(*) FROM "{table}"')
            ).fetchone()
            return (str(r[0])[:10] if r[0] else None,
                    str(r[1])[:10] if r[1] else None,
                    int(r[2]) if r[2] else 0)
        except Exception as e:
            return (None, None, f"ERROR: {e}")


def get_missing_dates(table: str, date_col: str, start: str, end: str) -> list:
    """查询指定范围内缺失的交易日"""
    engine = storage._get_engine()
    with engine.connect() as conn:
        try:
            existing = conn.execute(
                text(f'SELECT DISTINCT "{date_col}" FROM "{table}" WHERE "{date_col}" BETWEEN :sd AND :ed ORDER BY 1'),
                {"sd": start, "ed": end}
            ).fetchall()
            existing_set = {str(r[0])[:10] for r in existing}

            # 获取交易日历
            try:
                cals = conn.execute(
                    text("SELECT DISTINCT cal_date FROM trade_calendar WHERE cal_date BETWEEN :sd AND :ed AND is_open = 1 ORDER BY 1"),
                    {"sd": start, "ed": end}
                ).fetchall()
                trade_dates = {str(r[0])[:10] for r in cals}
            except Exception:
                # 无交易日历就用自然日
                trade_dates = set()
                d = datetime.strptime(start, "%Y-%m-%d")
                end_dt = datetime.strptime(end, "%Y-%m-%d")
                while d <= end_dt:
                    trade_dates.add(d.strftime("%Y-%m-%d"))
                    d += timedelta(days=1)

            missing = sorted(trade_dates - existing_set)
            return missing
        except Exception as e:
            return [f"ERROR: {e}"]


def scan_all(target_date: str = None):
    """扫描所有核心表的数据完整性"""
    if target_date is None:
        target_date = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")

    # 三个月窗口
    start_3m = (datetime.strptime(target_date, "%Y-%m-%d") - timedelta(days=90)).strftime("%Y-%m-%d")

    print(f"\n{'='*70}")
    print(f"  数据完整性扫描 — {target_date} (范围: {start_3m} ~ {target_date})")
    print(f"{'='*70}\n")

    results = []
    for table, meta in CORE_TABLES.items():
        dc = meta["date_col"]
        desc = meta["desc"]
        if dc is None:
            # 聚合表 — 只查总行数
            engine = storage._get_engine()
            with engine.connect() as conn:
                try:
                    cnt = conn.execute(text(f'SELECT COUNT(*) FROM "{table}"')).scalar()
                except Exception:
                    cnt = "ERROR"
            status = f"✅ {cnt} 行" if isinstance(cnt, int) and cnt > 0 else "❌ 空表"
            results.append({"table": table, "desc": desc, "status": status, "count": cnt})
            print(f"  {table:25s}  {status:30s}  聚合表，不检查日期")
            continue

        date_min, date_max, count = get_date_range(table, dc)
        latest = get_latest_date(table, dc)

        # 判断状态
        if date_max is None or "ERROR" in str(date_max):
            status = "❌ 空表/错误"
        elif date_max >= target_date:
            status = "✅ 已覆盖"
        else:
            days_behind = (datetime.strptime(target_date, "%Y-%m-%d") -
                          datetime.strptime(date_max, "%Y-%m-%d")).days
            status = f"⚠️  落后 {days_behind} 天 (最新={date_max})"

        results.append({
            "table": table, "desc": desc, "status": status,
            "date_min": date_min, "date_max": date_max, "count": count,
            "latest": latest,
        })

        print(f"  {table:25s}  {status:30s}  {str(date_min or '?'):12s}~{str(date_max or '?'):12s}  {str(count):>8s} 行")

    # ── 重点检查昨天数据 ──
    print(f"\n{'─'*70}")
    print(f"  {target_date} 当日数据详情")
    print(f"{'─'*70}")
    engine = storage._get_engine()
    for table, meta in CORE_TABLES.items():
        dc = meta["date_col"]
        if dc is None:
            # 聚合表 — 只显示总行数
            with engine.connect() as conn:
                try:
                    cnt = conn.execute(text(f'SELECT COUNT(*) FROM "{table}"')).scalar()
                    print(f"  📊 {table:25s}  {cnt} 行 (聚合表)")
                except Exception as e:
                    print(f"  ❌ {table:25s}  ERROR: {e}")
            continue
        with engine.connect() as conn:
            try:
                cnt = conn.execute(
                    text(f'SELECT COUNT(*) FROM "{table}" WHERE "{dc}" = :d'),
                    {"d": target_date}
                ).scalar()
                flag = "✅" if cnt > 0 else "❌"
                print(f"  {flag} {table:25s}  {cnt} 行")
            except Exception as e:
                print(f"  ❌ {table:25s}  ERROR: {e}")

    return results


def backfill_yesterday():
    """幂等补齐昨天数据 — 调用现有 data_fetcher + opencli_sync"""
    yesterday = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")
    logger.info(f"开始幂等补齐: {yesterday}")

    # Step 1: opencli 同步（幂等 — upsert 写入）
    logger.info("Step 1: opencli 同步...")
    try:
        from scripts.opencli_sync import run_daily_sync
        run_daily_sync(yesterday)
    except ImportError:
        logger.warning("opencli_sync 不可用，跳过")

    # Step 2: AKShare 市场情绪（幂等 — fetch_market_sentiment 内部 upsert）
    logger.info("Step 2: 市场情绪...")
    from modules.data_fetcher import fetch_market_sentiment
    fetch_market_sentiment()

    # Step 3: AKShare 龙虎榜明细（幂等）
    logger.info("Step 3: 龙虎榜明细...")
    from modules.data_fetcher import fetch_lhb_detail
    fetch_lhb_detail(yesterday, yesterday, save=True)

    # Step 4: AKShare 机构席位
    logger.info("Step 4: 机构席位...")
    from modules.data_fetcher import fetch_lhb_jigou
    fetch_lhb_jigou()

    # Step 5: AKShare 个股上榜统计
    logger.info("Step 5: 个股上榜统计...")
    from modules.data_fetcher import fetch_lhb_ggtj
    fetch_lhb_ggtj()

    # Step 6: ETL 桥接（幂等 — DELETE + INSERT 或 MERGE）
    logger.info("Step 6: ETL 桥接...")
    etl_script = ROOT / "scripts/etl_real_to_logical.py"
    if etl_script.exists():
        import subprocess
        subprocess.run([sys.executable, str(etl_script)], check=False)

    logger.info(f"补齐完成: {yesterday}")


def main():
    parser = argparse.ArgumentParser(description="数据完整性扫描 & 幂等补齐")
    parser.add_argument("--scan", action="store_true", default=True,
                        help="扫描数据完整性 (默认)")
    parser.add_argument("--date", type=str, default=None,
                        help="指定扫描日期 (YYYY-MM-DD)")
    parser.add_argument("--backfill", action="store_true",
                        help="补齐缺失数据")
    parser.add_argument("--check-gaps", action="store_true",
                        help="检查历史数据缺口")
    args = parser.parse_args()

    target = args.date or (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")

    if args.scan:
        scan_all(target)

    if args.backfill:
        backfill_yesterday()

    if args.check_gaps:
        print(f"\n{'='*70}")
        print(f"  数据缺口检查 (近90天)")
        print(f"{'='*70}")
        start = (datetime.strptime(target, "%Y-%m-%d") - timedelta(days=90)).strftime("%Y-%m-%d")
        for table, meta in CORE_TABLES.items():
            dc = meta["date_col"]
            missing = get_missing_dates(table, dc, start, target)
            if missing and "ERROR" not in str(missing[0]):
                print(f"\n  ⚠️  {table}: 缺失 {len(missing)} 天")
                if len(missing) <= 10:
                    for d in missing:
                        print(f"     - {d}")
                else:
                    print(f"     {missing[0]} ~ {missing[-1]}")


if __name__ == "__main__":
    main()
