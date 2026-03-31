#!/usr/bin/env python3
"""
lhb_features 历史数据回填脚本
==============================
用法：
  # 后台运行（推荐，约10~15分钟）
  nohup /root/miniconda3/bin/python scripts/backfill_features.py \
    >> logs/backfill_features.log 2>&1 &

  # 前台运行（调试用）
  /root/miniconda3/bin/python scripts/backfill_features.py

逻辑：
  1. 读取 lhb_top10 的全部交易日列表
  2. 读取 lhb_features 已有的日期（幂等跳过）
  3. 对缺失的日期，按时间序逐日调用 run_feature_update
  4. 进度写入日志，可随时 tail -f logs/backfill_features.log 查看
"""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
import os
os.chdir(ROOT)

from loguru import logger
from utils.storage import storage
from modules.feature_engine import run_feature_update

logger.add(
    ROOT / "logs/backfill_features.log",
    rotation="50 MB",
    retention="30 days",
    encoding="utf-8",
    level="INFO",
)

def main():
    logger.info("=" * 60)
    logger.info("lhb_features 历史回填开始")
    logger.info("=" * 60)

    # ── 读取需要回填的日期 ────────────────────────────────────
    top10 = storage.read("lhb_top10")
    if top10.empty:
        logger.error("lhb_top10 为空，无法回填")
        return

    all_dates = sorted(top10["trade_date"].astype(str).unique())
    logger.info(f"lhb_top10 共 {len(all_dates)} 个交易日: {all_dates[0]} ~ {all_dates[-1]}")

    # 已有数据的日期
    try:
        feats = storage.read("lhb_features")
        done_dates = set(feats["trade_date"].astype(str).unique()) if not feats.empty else set()
    except Exception:
        done_dates = set()

    pending = [d for d in all_dates if d not in done_dates]
    logger.info(f"已完成: {len(done_dates)} 日，待回填: {len(pending)} 日")

    if not pending:
        logger.info("所有日期已有数据，无需回填")
        return

    # ── 逐日回填 ─────────────────────────────────────────────
    total = len(pending)
    success_days = 0
    skip_days = 0
    error_days = 0

    for i, trade_date in enumerate(pending):
        try:
            result = run_feature_update(trade_date, force=False, sleep_between=0.5)
            if result["total"] == 0:
                logger.info(f"[{i+1}/{total}] {trade_date} - 无数据（跳过）")
                skip_days += 1
            else:
                logger.info(
                    f"[{i+1}/{total}] {trade_date} - "
                    f"成功={result['success']} 失败={result['error']} 总={result['total']}"
                )
                success_days += 1
        except Exception as e:
            logger.error(f"[{i+1}/{total}] {trade_date} - 异常: {e}")
            error_days += 1

        # 每10个日期暂停一下，保护 API
        if (i + 1) % 10 == 0:
            logger.info(f"  --- 已处理 {i+1}/{total} 日，休息3秒 ---")
            time.sleep(3)

    logger.info("=" * 60)
    logger.info(
        f"回填完成: 成功={success_days} 跳过={skip_days} 失败={error_days} 总={total}"
    )
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
