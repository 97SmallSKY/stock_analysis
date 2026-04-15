#!/usr/bin/env python3
"""
T+N 价格回填脚本
================
功能：为 lhb_top10 表中的每只股票填充 T+1/T+2/T+5/T+10 价格数据
用于历史数据回填（2024-01-01 到 2026-04-15）

使用方法：
  python scripts/backfill_prices.py                    # 回填所有历史数据
  python scripts/backfill_prices.py --limit 50        # 限制处理数量（测试用）
  python scripts/backfill_prices.py --days 1,2         # 只回填 T+1, T+2
"""
import sys
import os
import time
import argparse
from pathlib import Path
import pandas as pd
import akshare as ak
from loguru import logger

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from utils.storage import storage
from utils.retry import safe_akshare_call

logger.add(ROOT / "logs/backfill_prices.log", rotation="7 days", retention="30 days",
           encoding="utf-8", level="INFO")

_DAYS = [1, 2, 5, 10]


def can_fill(trade_date_str, n, today_str):
    """检查是否可以填充 T+N 数据"""
    td = pd.Timestamp(trade_date_str)
    today = pd.Timestamp(today_str)
    days_passed = (today - td).days
    min_days = int(n * 1.4) if n > 2 else n
    return days_passed >= min_days


def fetch_kline(code, trade_date_str):
    """获取K线数据"""
    c = str(code).zfill(6)
    if c.startswith("6") or c[:2] in ("11", "51"):
        prefix = "sh"
    elif c[0] in ("4", "8") or c[:2] in ("82", "83", "87", "88", "43"):
        prefix = "bj"
    else:
        prefix = "sz"
    
    end_dt = (pd.Timestamp(trade_date_str) + pd.Timedelta(days=40)).strftime("%Y%m%d")

    # 新浪
    for attempt in range(2):
        try:
            raw = ak.stock_zh_a_daily(
                symbol=f"{prefix}{c}", adjust="qfq",
                start_date=trade_date_str, end_date=end_dt,
            )
            if raw is not None and len(raw) >= 2:
                raw = raw.rename(columns={
                    "date": "日期", "open": "开盘",
                    "high": "最高", "low": "最低", "close": "收盘",
                })
                if all(col in raw.columns for col in ["日期", "开盘", "最高", "最低", "收盘"]):
                    return raw[["日期", "开盘", "最高", "最低", "收盘"]].sort_values("日期").reset_index(drop=True)
        except Exception as e:
            logger.debug(f"  [新浪 {attempt+1}] {c} 失败: {e}")
            time.sleep(1)

    # 东方财富
    for attempt in range(2):
        try:
            raw2 = ak.stock_zh_a_hist(
                symbol=c, period="daily", adjust="qfq",
                start_date=trade_date_str, end_date=end_dt,
            )
            if raw2 is not None and len(raw2) >= 2:
                if all(col in raw2.columns for col in ["日期", "开盘", "最高", "最低", "收盘"]):
                    return raw2[["日期", "开盘", "最高", "最低", "收盘"]].sort_values("日期").reset_index(drop=True)
        except Exception as e:
            logger.debug(f"  [东财 {attempt+1}] {c} 失败: {e}")
            time.sleep(1)

    return None


def fill_prices_for_stock(code, trade_date_str, today_str, target_days):
    """为单只股票填充 T+N 价格"""
    hist = fetch_kline(code, trade_date_str)
    if hist is None or len(hist) < 2:
        return None

    updates = {}
    
    # T+1 买入价（开盘价）
    if len(hist) >= 2:
        buy_price = round(float(hist.iloc[1]["开盘"]), 3)
        updates["T+1买入价"] = buy_price
        updates["T+1最高价"] = round(float(hist.iloc[1]["最高"]), 3)
        updates["T+1最低价"] = round(float(hist.iloc[1]["最低"]), 3)
        updates["T+1收盘价"] = round(float(hist.iloc[1]["收盘"]), 3)
        updates["T+1收盘收益%"] = round((hist.iloc[1]["收盘"] / hist.iloc[0]["收盘"] - 1) * 100, 2) if hist.iloc[0]["收盘"] > 0 else 0
        updates["T+1最高收益%"] = round((hist.iloc[1]["最高"] / buy_price - 1) * 100, 2)
    else:
        return None

    # T+N 价格
    for n in target_days:
        if n < len(hist):
            r = hist.iloc[n]
            hp = float(r["最高"])
            lp = float(r["最低"])
            cp = float(r["收盘"])
            updates[f"T+{n}最高价"] = round(hp, 3)
            updates[f"T+{n}最低价"] = round(lp, 3)
            updates[f"T+{n}收盘价"] = round(cp, 3)
            if buy_price > 0:
                updates[f"T+{n}收盘收益%"] = round((cp / buy_price - 1) * 100, 2)
                updates[f"T+{n}最高收益%"] = round((hp / buy_price - 1) * 100, 2)

    return updates


def main():
    parser = argparse.ArgumentParser(description="T+N 价格回填")
    parser.add_argument("--limit", type=int, default=0,
                        help="限制处理数量 (0=不限制)")
    parser.add_argument("--days", type=str, default="1,2,5,10",
                        help="回填哪些 T+N (逗号分隔, 默认 1,2,5,10)")
    parser.add_argument("--start", type=str, default="20240101",
                        help="开始日期 (YYYYMMDD)")
    parser.add_argument("--end", type=str, default="20260410",
                        help="结束日期 (YYYYMMDD)")
    args = parser.parse_args()

    target_days = [int(d.strip()) for d in args.days.split(",")]
    today_str = pd.Timestamp.today().strftime("%Y%m%d")

    logger.info(f"{'='*60}")
    logger.info(f"T+N 价格回填开始")
    logger.info(f"日期范围: {args.start} ~ {args.end}")
    logger.info(f"目标 T+N: {target_days}")
    logger.info(f"{'='*60}")

    # 读取 lhb_top10 数据
    top10 = storage.read("lhb_top10")
    if top10.empty:
        logger.error("lhb_top10 表为空!")
        return

    # 过滤日期范围
    top10 = top10[
        (top10["trade_date"].astype(str) >= args.start) &
        (top10["trade_date"].astype(str) <= args.end)
    ]

    logger.info(f"待处理: {len(top10)} 条记录")

    # 去重（同一日期同一代码只处理一次）
    top10 = top10.drop_duplicates(subset=["trade_date", "代码"])

    total = len(top10)
    success = 0
    failed = 0
    skipped = 0

    from sqlalchemy import text
    engine = storage._get_engine()

    for i, (_, row) in enumerate(top10.iterrows()):
        if args.limit > 0 and i >= args.limit:
            logger.info(f"已达到处理上限 {args.limit}，停止")
            break

        trade_date_str = str(row["trade_date"])
        code = str(row["代码"]).zfill(6)
        progress = f"[{i+1}/{total}]"

        # 检查是否需要填充
        needs_fill = False
        for n in target_days:
            if can_fill(trade_date_str, n, today_str):
                needs_fill = True
                break
        
        if not needs_fill:
            skipped += 1
            continue

        try:
            updates = fill_prices_for_stock(code, trade_date_str, today_str, target_days)
            if updates is None:
                logger.debug(f"{progress} {trade_date_str} {code}: 无法获取K线")
                failed += 1
                continue

            # 写入数据库
            updates["代码"] = code
            updates["trade_date"] = trade_date_str

            cols_sql = ", ".join([f'"{c}"' for c in updates])
            def _pn(c): return c.replace("+", "p").replace("%", "pct").replace(" ", "_")
            vals_sql = ", ".join([f":{_pn(c)}" for c in updates])
            params = {_pn(c): v for c, v in updates.items()}

            with engine.begin() as conn:
                conn.execute(
                    text(f"INSERT INTO lhb_top10_prices ({cols_sql}) VALUES ({vals_sql}) ON CONFLICT DO NOTHING"),
                    params,
                )

            logger.info(f"{progress} {trade_date_str} {code}: ✓")
            success += 1

        except Exception as e:
            logger.error(f"{progress} {trade_date_str} {code}: ✗ {e}")
            failed += 1

        time.sleep(0.5)  # API 限流

        # 每100条输出进度
        if (i + 1) % 100 == 0:
            logger.info(f"进度: {i+1}/{total} | 成功: {success} | 失败: {failed} | 跳过: {skipped}")

    logger.info(f"{'='*60}")
    logger.info(f"T+N 价格回填完成!")
    logger.info(f"成功: {success} | 失败: {failed} | 跳过: {skipped}")
    logger.info(f"{'='*60}")


if __name__ == "__main__":
    main()
