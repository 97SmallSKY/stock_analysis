#!/usr/bin/env python3
"""幂等补齐指定日期的数据"""
import sys, os, argparse
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from loguru import logger

logger.add(ROOT / "logs/backfill.log", rotation="7 days", retention="30 days",
           encoding="utf-8", level="INFO")


def backfill(target_date: str):
    """幂等补齐一个日期的所有数据"""
    logger.info(f"{'='*50}")
    logger.info(f"幂等补齐: {target_date}")
    logger.info(f"{'='*50}")

    # ── Step 1: 市场情绪 ──
    logger.info("Step 1: 市场情绪...")
    try:
        from modules.data_fetcher import fetch_market_sentiment
        fetch_market_sentiment()
        logger.info("  ✅ 市场情绪")
    except Exception as e:
        logger.warning(f"  ⚠️  市场情绪失败: {e}")

    # ── Step 2: 龙虎榜明细（含席位）─
    logger.info("Step 2: 龙虎榜明细...")
    try:
        from modules.data_fetcher import fetch_lhb_detail
        fetch_lhb_detail(target_date, target_date, save=True)
        logger.info("  ✅ 龙虎榜明细")
    except Exception as e:
        logger.warning(f"  ⚠️  龙虎榜明细失败: {e}")

    # ── Step 3: 机构席位 ─
    logger.info("Step 3: 机构席位...")
    try:
        from modules.data_fetcher import fetch_lhb_jigou
        fetch_lhb_jigou()
        logger.info("  ✅ 机构席位")
    except Exception as e:
        logger.warning(f"  ⚠️  机构席位失败: {e}")

    # ── Step 4: 个股上榜统计 ──
    logger.info("Step 4: 个股上榜统计...")
    try:
        from modules.data_fetcher import fetch_lhb_ggtj
        fetch_lhb_ggtj()
        logger.info("  ✅ 个股上榜统计")
    except Exception as e:
        logger.warning(f"  ⚠️  个股上榜统计失败: {e}")

    # ── Step 5: Top10 (fetch_lhb_netbuy_by_date) ──
    logger.info("Step 5: 龙虎榜Top10...")
    try:
        from modules.data_fetcher import fetch_lhb_netbuy_by_date
        fetch_lhb_netbuy_by_date(target_date)
        logger.info("  ✅ 龙虎榜Top10")
    except Exception as e:
        logger.warning(f"  ⚠️  龙虎榜Top10失败: {e}")

    # ── Step 6: T+N 价格填充 ──
    logger.info("Step 6: T+N 价格填充...")
    try:
        target_8 = target_date.replace("-", "")
        from scripts.daily_update import step3_fill_null_prices, step8_fill_volume_ratio
        step3_fill_null_prices()
        step8_fill_volume_ratio()
        logger.info("  ✅ T+N 价格填充")
    except Exception as e:
        logger.warning(f"  ⚠️  T+N 价格填充失败: {e}")

    # ── Step 7: ETL 桥接 ──
    logger.info("Step 7: ETL 桥接...")
    try:
        import subprocess
        etl_path = ROOT / "scripts/etl_real_to_logical.py"
        if etl_path.exists():
            result = subprocess.run([sys.executable, str(etl_path)],
                                    capture_output=True, text=True, timeout=120)
            if result.returncode == 0:
                logger.info("  ✅ ETL 桥接完成")
            else:
                logger.warning(f"  ⚠️  ETL 返回非0: {result.stderr[:200]}")
        else:
            logger.warning("  ⚠️  ETL 脚本不存在")
    except Exception as e:
        logger.warning(f"  ⚠️  ETL 失败: {e}")

    # ── Step 8: opencli 补充 ──
    logger.info("Step 8: opencli 补充同步...")
    try:
        import subprocess
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts/opencli_sync.py"), "--mode", "daily",
             "--date", target_date],
            capture_output=True, text=True, timeout=60
        )
        if result.returncode == 0:
            logger.info("  ✅ opencli 同步完成")
        else:
            logger.warning(f"  ⚠️  opencli 返回非0")
    except Exception as e:
        logger.warning(f"  ⚠️  opencli 失败: {e}")

    logger.info(f"补齐完成: {target_date}")


def main():
    parser = argparse.ArgumentParser(description="幂等补齐指定日期数据")
    parser.add_argument("--date", type=str, default=None,
                        help="日期 (YYYY-MM-DD)，默认昨天")
    args = parser.parse_args()

    if args.date:
        target = args.date
    else:
        target = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")

    backfill(target)


if __name__ == "__main__":
    main()
