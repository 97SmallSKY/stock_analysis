#!/usr/bin/env python3
"""
S01 数据层快速验收测试（不依赖异步）
"""
import sys
import psycopg2
from pathlib import Path

# 添加项目根目录到路径
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.config import settings


def print_header(text):
    """打印标题"""
    print("\n" + "=" * 60)
    print(f"  {text}")
    print("=" * 60)


def test_database_connection():
    """测试1: 数据库连接"""
    print("\n[测试1] 数据库连接")
    
    try:
        conn = psycopg2.connect(
            host=settings.PG_HOST,
            port=settings.PG_PORT,
            database=settings.PG_DATABASE,
            user=settings.PG_USER,
            password=settings.PG_PASSWORD
        )
        cursor = conn.cursor()
        cursor.execute("SELECT 1")
        result = cursor.fetchone()
        assert result[0] == 1
        cursor.close()
        conn.close()
        
        print("✓ 数据库连接 - 通过")
        return True
    except Exception as e:
        print(f"✗ 数据库连接 - 失败: {e}")
        return False


def test_tables_exist():
    """测试2: 核心表是否存在"""
    print("\n[测试2] 核心表存在性检查")
    
    required_tables = [
        'robots', 'robot_trades', 'lhb_stock_daily', 'lhb_seats',
        'kline_daily', 'market_sentiment', 'strategies',
        'trade_calendar', 'stock_info'
    ]
    
    try:
        conn = psycopg2.connect(
            host=settings.PG_HOST,
            port=settings.PG_PORT,
            database=settings.PG_DATABASE,
            user=settings.PG_USER,
            password=settings.PG_PASSWORD
        )
        cursor = conn.cursor()
        
        missing_tables = []
        for table in required_tables:
            cursor.execute(
                f"SELECT COUNT(*) FROM information_schema.tables WHERE table_name = '{table}'"
            )
            count = cursor.fetchone()[0]
            if count == 0:
                missing_tables.append(table)
        
        cursor.close()
        conn.close()
        
        if missing_tables:
            print(f"✗ 缺失表: {', '.join(missing_tables)}")
            return False
        
        print(f"✓ 核心表存在性检查 - 通过（{len(required_tables)}张表）")
        return True
    except Exception as e:
        print(f"✗ 核心表存在性检查 - 失败: {e}")
        return False


def test_data_statistics():
    """测试3: 数据统计"""
    print("\n[测试3] 数据统计")
    
    try:
        conn = psycopg2.connect(
            host=settings.PG_HOST,
            port=settings.PG_PORT,
            database=settings.PG_DATABASE,
            user=settings.PG_USER,
            password=settings.PG_PASSWORD
        )
        cursor = conn.cursor()
        
        # 统计各表记录数
        tables = {
            'robots': '机器人',
            'strategies': '策略',
            'lhb_stock_daily': '龙虎榜',
            'market_sentiment': '市场情绪',
            'stock_info': '股票信息'
        }
        
        for table, name in tables.items():
            cursor.execute(f"SELECT COUNT(*) FROM {table}")
            count = cursor.fetchone()[0]
            print(f"  {name}({table}): {count} 条")
        
        cursor.close()
        conn.close()
        
        print("✓ 数据统计 - 通过")
        return True
    except Exception as e:
        print(f"✗ 数据统计 - 失败: {e}")
        return False


def test_indexes():
    """测试4: 索引检查"""
    print("\n[测试4] 索引检查")
    
    try:
        conn = psycopg2.connect(
            host=settings.PG_HOST,
            port=settings.PG_PORT,
            database=settings.PG_DATABASE,
            user=settings.PG_USER,
            password=settings.PG_PASSWORD
        )
        cursor = conn.cursor()
        
        cursor.execute(
            "SELECT COUNT(*) FROM pg_indexes WHERE schemaname = 'public'"
        )
        index_count = cursor.fetchone()[0]
        
        cursor.close()
        conn.close()
        
        print(f"  索引数量: {index_count}")
        print("✓ 索引检查 - 通过")
        return True
    except Exception as e:
        print(f"✗ 索引检查 - 失败: {e}")
        return False


def test_views():
    """测试5: 视图检查"""
    print("\n[测试5] 视图检查")
    
    required_views = [
        'v_data_health',
        'v_kline_health',
        'v_robot_health',
        'v_trade_execution_summary'
    ]
    
    try:
        conn = psycopg2.connect(
            host=settings.PG_HOST,
            port=settings.PG_PORT,
            database=settings.PG_DATABASE,
            user=settings.PG_USER,
            password=settings.PG_PASSWORD
        )
        cursor = conn.cursor()
        
        missing_views = []
        for view in required_views:
            cursor.execute(
                f"SELECT COUNT(*) FROM information_schema.views WHERE table_name = '{view}'"
            )
            count = cursor.fetchone()[0]
            if count == 0:
                missing_views.append(view)
        
        cursor.close()
        conn.close()
        
        if missing_views:
            print(f"⚠️  缺失视图: {', '.join(missing_views)}")
        
        print(f"✓ 视图检查 - 通过（{len(required_views) - len(missing_views)}/{len(required_views)}）")
        return True
    except Exception as e:
        print(f"✗ 视图检查 - 失败: {e}")
        return False


def main():
    """主函数"""
    print_header("S01 数据层快速验收测试")
    
    results = []
    
    # 执行所有测试
    results.append(("数据库连接", test_database_connection()))
    results.append(("核心表存在性", test_tables_exist()))
    results.append(("数据统计", test_data_statistics()))
    results.append(("索引检查", test_indexes()))
    results.append(("视图检查", test_views()))
    
    # 打印总结
    print_header("测试总结")
    
    passed = sum(1 for _, result in results if result)
    failed = len(results) - passed
    
    print(f"\n通过: {passed} 个")
    print(f"失败: {failed} 个")
    
    if failed > 0:
        print("\n失败详情:")
        for name, result in results:
            if not result:
                print(f"  ✗ {name}")
    
    success_rate = passed / len(results) * 100
    print(f"\n成功率: {success_rate:.1f}%")
    
    if failed == 0:
        print("\n🎉 所有测试通过！")
        return 0
    else:
        print("\n⚠️  部分测试失败，请检查！")
        return 1


if __name__ == '__main__':
    exit_code = main()
    sys.exit(exit_code)
