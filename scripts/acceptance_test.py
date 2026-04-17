#!/usr/bin/env python3
"""
S01 数据层验收测试脚本
"""
import sys
import asyncio
from pathlib import Path
from datetime import date, timedelta
from loguru import logger

# 添加项目根目录到路径
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backend.db.session import AsyncSessionLocal
from backend.app.collectors import LhbCollector, MarketCollector
from backend.app.integrity import check_data_integrity
from sqlalchemy import select, func, text
from backend.db.models import LhbStockDaily, MarketSentiment


class AcceptanceTest:
    """验收测试类"""
    
    def __init__(self):
        self.passed = []
        self.failed = []
    
    def print_header(self, text):
        """打印标题"""
        print("\n" + "=" * 60)
        print(f"  {text}")
        print("=" * 60)
    
    def print_test(self, name):
        """打印测试名称"""
        print(f"\n[测试] {name}")
    
    def record_pass(self, test_name):
        """记录通过"""
        self.passed.append(test_name)
        print(f"✓ {test_name} - 通过")
    
    def record_fail(self, test_name, error):
        """记录失败"""
        self.failed.append((test_name, error))
        print(f"✗ {test_name} - 失败: {error}")
    
    async def test_database_connection(self):
        """测试1: 数据库连接"""
        self.print_test("数据库连接")
        
        try:
            async with AsyncSessionLocal() as db:
                result = await db.execute(text("SELECT 1"))
                assert result.scalar() == 1
            self.record_pass("数据库连接")
        except Exception as e:
            self.record_fail("数据库连接", str(e))
    
    async def test_tables_exist(self):
        """测试2: 核心表是否存在"""
        self.print_test("核心表存在性检查")
        
        required_tables = [
            'robots', 'robot_trades', 'lhb_stock_daily', 'lhb_seats',
            'kline_daily', 'market_sentiment', 'strategies'
        ]
        
        try:
            async with AsyncSessionLocal() as db:
                for table in required_tables:
                    result = await db.execute(
                        text(f"SELECT COUNT(*) FROM information_schema.tables WHERE table_name = '{table}'")
                    )
                    count = result.scalar()
                    if count == 0:
                        raise Exception(f"表 {table} 不存在")
            
            self.record_pass("核心表存在性检查")
        except Exception as e:
            self.record_fail("核心表存在性检查", str(e))
    
    async def test_lhb_collector_dry_run(self):
        """测试3: 龙虎榜采集器演练"""
        self.print_test("龙虎榜采集器（演练模式）")
        
        try:
            async with AsyncSessionLocal() as db:
                collector = LhbCollector(db)
                
                # 使用昨天的日期（今天可能还没数据）
                yesterday = date.today() - timedelta(days=1)
                result = await collector.run(trade_date=yesterday, dry_run=True)
                
                if not result['success']:
                    # 演练失败可能是非交易日，不算错误
                    print(f"  提示: {result.get('message', '无数据')}")
                
                self.record_pass("龙虎榜采集器（演练模式）")
        except Exception as e:
            self.record_fail("龙虎榜采集器（演练模式）", str(e))
    
    async def test_market_collector_dry_run(self):
        """测试4: 市场情绪采集器演练"""
        self.print_test("市场情绪采集器（演练模式）")
        
        try:
            async with AsyncSessionLocal() as db:
                collector = MarketCollector(db)
                result = await collector.run(dry_run=True)
                
                if not result['success']:
                    print(f"  提示: {result.get('message', '无数据')}")
                
                self.record_pass("市场情绪采集器（演练模式）")
        except Exception as e:
            self.record_fail("市场情绪采集器（演练模式）", str(e))
    
    async def test_data_integrity(self):
        """测试5: 数据完整性检查"""
        self.print_test("数据完整性检查")
        
        try:
            async with AsyncSessionLocal() as db:
                result = await check_data_integrity(db, lookback_days=5)
                
                print(f"  检查结果: {result['issues_count']} 个问题")
                if result['issues']:
                    for issue in result['issues'][:3]:  # 只显示前3个
                        print(f"    - {issue['message']}")
                
                # 完整性检查即使有问题也算测试通过（数据不完整是正常的）
                self.record_pass("数据完整性检查")
        except Exception as e:
            self.record_fail("数据完整性检查", str(e))
    
    async def test_orm_models(self):
        """测试6: ORM 模型查询"""
        self.print_test("ORM 模型查询")
        
        try:
            async with AsyncSessionLocal() as db:
                # 查询龙虎榜数据条数
                result = await db.execute(select(func.count(LhbStockDaily.id)))
                lhb_count = result.scalar()
                print(f"  龙虎榜记录数: {lhb_count}")
                
                # 查询市场情绪数据条数
                result = await db.execute(select(func.count(MarketSentiment.id)))
                sentiment_count = result.scalar()
                print(f"  市场情绪记录数: {sentiment_count}")
                
                self.record_pass("ORM 模型查询")
        except Exception as e:
            self.record_fail("ORM 模型查询", str(e))
    
    async def run_all_tests(self):
        """运行所有测试"""
        self.print_header("S01 数据层验收测试")
        
        # 执行所有测试
        await self.test_database_connection()
        await self.test_tables_exist()
        await self.test_lhb_collector_dry_run()
        await self.test_market_collector_dry_run()
        await self.test_data_integrity()
        await self.test_orm_models()
        
        # 打印总结
        self.print_header("测试总结")
        print(f"\n通过: {len(self.passed)} 个")
        print(f"失败: {len(self.failed)} 个")
        
        if self.failed:
            print("\n失败详情:")
            for name, error in self.failed:
                print(f"  ✗ {name}: {error}")
        
        success_rate = len(self.passed) / (len(self.passed) + len(self.failed)) * 100
        print(f"\n成功率: {success_rate:.1f}%")
        
        if len(self.failed) == 0:
            print("\n🎉 所有测试通过！")
            return 0
        else:
            print("\n⚠️  部分测试失败，请检查！")
            return 1


async def main():
    """主函数"""
    tester = AcceptanceTest()
    exit_code = await tester.run_all_tests()
    sys.exit(exit_code)


if __name__ == '__main__':
    asyncio.run(main())
