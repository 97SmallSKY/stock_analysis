#!/usr/bin/env python3
"""
数据采集调度器启动脚本
"""
import sys
from pathlib import Path

# 添加项目根目录到路径
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.scheduler import DataScheduler

if __name__ == '__main__':
    print("=" * 60)
    print("  龙虎榜量化交易系统 v2 - 数据采集调度器")
    print("=" * 60)
    print()
    
    scheduler = DataScheduler()
    scheduler.run_forever()
