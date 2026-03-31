"""
数据迁移工具
Excel → PostgreSQL 一键迁移

用法:
  python utils/migrate.py              # 将所有 Excel 数据迁移到 PostgreSQL
  python utils/migrate.py --table lhb_detail   # 仅迁移指定表
  python utils/migrate.py --dry-run    # 预览（不实际写入）

前提：.env 文件中 STORAGE_MODE=postgres 且 PG 配置正确
"""
import sys
import os
import argparse
import pandas as pd
from loguru import logger

# 加入项目根路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from config.settings import EXCEL_FILES, STORAGE_MODE
from utils.storage import _pg_table, PostgreSQLStorage


def migrate_excel_to_pg(table: str = None, dry_run: bool = False):
    """
    将 Excel 数据迁移到 PostgreSQL

    Args:
        table: 指定迁移的表名（为 None 则迁移全部）
        dry_run: 预览模式，不实际写入
    """
    if STORAGE_MODE != "postgres":
        logger.error("当前 STORAGE_MODE 不是 postgres，请在 .env 中设置 STORAGE_MODE=postgres")
        sys.exit(1)

    tables_to_migrate = {table: EXCEL_FILES[table]} if table else EXCEL_FILES

    pg_storage = PostgreSQLStorage()

    summary = []
    for table_name, excel_path in tables_to_migrate.items():
        pg_name = _pg_table(table_name)

        if not os.path.exists(excel_path):
            logger.warning(f"⏭  跳过 {table_name}: Excel 文件不存在 ({excel_path})")
            summary.append({"表": table_name, "PG表": pg_name, "状态": "跳过（无文件）", "行数": 0})
            continue

        try:
            df = pd.read_excel(excel_path, engine="openpyxl")
            row_count = len(df)
            logger.info(f"📖 读取 Excel: {table_name} → {row_count} 行")

            if dry_run:
                logger.info(f"  [dry-run] 将写入 PostgreSQL 表: {pg_name}")
                summary.append({"表": table_name, "PG表": pg_name, "状态": "预览（未写入）", "行数": row_count})
            else:
                ok = pg_storage.write(df, table_name, mode="replace")
                status = "✅ 成功" if ok else "❌ 失败"
                logger.info(f"  {status}: {pg_name} ({row_count} 行)")
                summary.append({"表": table_name, "PG表": pg_name, "状态": status, "行数": row_count})

        except Exception as e:
            logger.error(f"  ❌ 迁移 {table_name} 失败: {e}")
            summary.append({"表": table_name, "PG表": pg_name, "状态": f"错误: {e}", "行数": 0})

    # 打印汇总
    print("\n" + "=" * 60)
    print("迁移汇总:")
    print("=" * 60)
    summary_df = pd.DataFrame(summary)
    print(summary_df.to_string(index=False))

    if dry_run:
        print("\n[dry-run 模式] 以上为预览，未实际写入。去掉 --dry-run 参数执行真实迁移。")
    else:
        success = sum(1 for s in summary if "✅" in str(s.get("状态", "")))
        total = len(summary)
        print(f"\n完成: {success}/{total} 个表迁移成功")


def check_pg_tables():
    """检查 PostgreSQL 中 stock_ 相关表的数据量"""
    pg_storage = PostgreSQLStorage()
    stats = pg_storage.get_table_stats()
    if stats.empty:
        print("暂无 stock_ 表数据")
    else:
        print("\nPostgreSQL stock_ 表数据量:")
        print(stats.to_string(index=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Excel → PostgreSQL 数据迁移工具")
    parser.add_argument("--table", type=str, default=None,
                        help="指定迁移的表名（不填则迁移全部）")
    parser.add_argument("--dry-run", action="store_true",
                        help="预览模式，不实际写入")
    parser.add_argument("--check", action="store_true",
                        help="查看 PostgreSQL 中 stock_ 表的数据量")

    args = parser.parse_args()

    if args.check:
        check_pg_tables()
    else:
        migrate_excel_to_pg(table=args.table, dry_run=args.dry_run)
