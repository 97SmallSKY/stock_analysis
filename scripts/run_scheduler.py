#!/usr/bin/env python3
"""
数据采集调度器启动脚本
"""
import sys
import asyncio
from pathlib import Path

# 添加项目根目录到路径
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backend import DataScheduler

async def main():
    scheduler = DataScheduler()
    scheduler.start()  # 同步调用
    
    # 保持运行
    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        scheduler.stop()

if __name__ == '__main__':
    print("=" * 60)
    print("  龙虎榜量化交易系统 v2 - 数据采集调度器")
    print("=" * 60)
    print()
    
    asyncio.run(main())
