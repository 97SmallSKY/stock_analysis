"""
数据运维工具
PostgreSQL 表检查和状态监控

用法:
  python utils/migrate.py --check              # 查看所有表的数据量
  python utils/migrate.py --stats             # 查看表统计信息

前提：.env 文件中 PG 配置正确
"""
import sys
import os
import argparse
from loguru import logger

# 加入项目根路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from utils.storage import PostgreSQLStorage


def check_pg_tables():
    """检查 PostgreSQL 中所有表的数据量"""
    pg_storage = PostgreSQLStorage()
    stats = pg_storage.get_table_stats()
    if stats.empty:
        print("暂无表数据或连接失败")
    else:
        print("\nPostgreSQL 表数据量:")
        print(stats.to_string(index=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="PostgreSQL 数据运维工具")
    parser.add_argument("--check", action="store_true", help="查看所有表的数据量")
    parser.add_argument("--stats", action="store_true", help="查看表统计信息（等同于 --check）")

    args = parser.parse_args()

    check_pg_tables()
