#!/usr/bin/env python
"""
数据回填脚本

用于回填指定日期范围的历史数据
"""
import argparse
import asyncio
from datetime import date, timedelta
from typing import List
import chinese_calendar as cc
from loguru import logger

from backend import AsyncSessionLocal
from backend import LhbCollector, MarketCollector


async def backfill_data(
    start_date: date,
    end_date: date,
    collectors: List[str] = None,
    dry_run: bool = False
):
    """
    回填历史数据
    
    Args:
        start_date: 开始日期
        end_date: 结束日期
        collectors: 采集器列表 ['lhb', 'market']
        dry_run: 演练模式
    """
    if collectors is None:
        collectors = ['lhb', 'market']
    
    current_date = start_date
    total_days = (end_date - start_date).days + 1
    processed_days = 0
    
    logger.info(f"开始回填数据: {start_date} 到 {end_date}")
    logger.info(f"采集器: {collectors}")
    logger.info(f"模式: {'演练' if dry_run else '正式'}")
    
    while current_date <= end_date:
        processed_days += 1
        
        # 跳过非交易日
        if not cc.is_workday(current_date):
            logger.info(f"跳过非交易日: {current_date}")
            current_date += timedelta(days=1)
            continue
        
        logger.info(f"[{processed_days}/{total_days}] 处理日期: {current_date}")
        
        async with AsyncSessionLocal() as db:
            # 采集龙虎榜数据
            if 'lhb' in collectors:
                try:
                    lhb_collector = LhbCollector(db)
                    result = await lhb_collector.run(
                        trade_date=current_date,
                        dry_run=dry_run
                    )
                    
                    if result['success']:
                        logger.info(f"  ✓ 龙虎榜: {result.get('records_count', 0)} 条记录")
                    else:
                        logger.warning(f"  ⚠ 龙虎榜采集失败: {result.get('error', 'Unknown')}")
                except Exception as e:
                    logger.error(f"  ✗ 龙虎榜采集异常: {e}")
            
            # 采集市场情绪数据
            if 'market' in collectors:
                try:
                    market_collector = MarketCollector(db)
                    result = await market_collector.run(
                        trade_date=current_date,
                        dry_run=dry_run
                    )
                    
                    if result['success']:
                        logger.info(f"  ✓ 市场情绪: {result.get('records_count', 0)} 条记录")
                    else:
                        logger.warning(f"  ⚠ 市场情绪采集失败: {result.get('error', 'Unknown')}")
                except Exception as e:
                    logger.error(f"  ✗ 市场情绪采集异常: {e}")
        
        current_date += timedelta(days=1)
    
    logger.info(f"回填完成! 处理了 {processed_days} 天数据")


def main():
    parser = argparse.ArgumentParser(description='回填历史数据')
    parser.add_argument('--start', type=str, help='开始日期 (YYYY-MM-DD)')
    parser.add_argument('--end', type=str, help='结束日期 (YYYY-MM-DD)')
    parser.add_argument('--days', type=int, help='回填最近 N 天')
    parser.add_argument('--collectors', nargs='+', default=['lhb', 'market'],
                       choices=['lhb', 'market'], help='采集器列表')
    parser.add_argument('--dry-run', action='store_true', help='演练模式')
    
    args = parser.parse_args()
    
    # 解析日期
    if args.days:
        end_date = date.today()
        start_date = end_date - timedelta(days=args.days)
    elif args.start and args.end:
        start_date = date.fromisoformat(args.start)
        end_date = date.fromisoformat(args.end)
    else:
        parser.error("请指定 --days 或 --start 和 --end")
        return
    
    # 运行回填
    asyncio.run(backfill_data(
        start_date=start_date,
        end_date=end_date,
        collectors=args.collectors,
        dry_run=args.dry_run
    ))


if __name__ == '__main__':
    main()
