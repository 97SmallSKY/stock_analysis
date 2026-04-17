#!/usr/bin/env python3
"""
龙虎榜量化交易系统 v2 - 数据库完全重置脚本
文件: scripts/reset_database.py
说明: 连接到远程生产数据库，删除并重新初始化
"""
import os
import sys
from pathlib import Path
import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
from dotenv import load_dotenv

# 添加项目根目录到路径
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# 加载环境变量
load_dotenv(PROJECT_ROOT / '.env')

# 颜色代码
class Colors:
    RED = '\033[0;31m'
    GREEN = '\033[0;32m'
    YELLOW = '\033[1;33m'
    BLUE = '\033[0;34m'
    NC = '\033[0m'  # No Color

def print_colored(text, color):
    print(f"{color}{text}{Colors.NC}")

def print_step(step, total, message):
    print_colored(f"\n[步骤 {step}/{total}] {message}", Colors.BLUE)

def confirm_reset():
    """确认是否执行重置"""
    db_host = os.getenv('PG_HOST')
    db_port = os.getenv('PG_PORT')
    db_name = os.getenv('PG_DATABASE')
    
    print_colored("=" * 60, Colors.BLUE)
    print_colored("  龙虎榜量化交易系统 v2 - 数据库完全重置", Colors.BLUE)
    print_colored("=" * 60, Colors.BLUE)
    print()
    print_colored(f"⚠️  警告: 此操作将删除数据库 '{db_name}' 及其所有数据！", Colors.YELLOW)
    print_colored(f"⚠️  目标服务器: {db_host}:{db_port}", Colors.YELLOW)
    print()
    
    confirm = input("是否继续? (输入 'YES' 确认删除): ")
    return confirm == 'YES'

def get_db_config():
    """获取数据库配置"""
    return {
        'host': os.getenv('PG_HOST'),
        'port': os.getenv('PG_PORT'),
        'database': os.getenv('PG_DATABASE'),
        'user': os.getenv('PG_USER'),
        'password': os.getenv('PG_PASSWORD')
    }

def connect_to_postgres(db_name='postgres'):
    """连接到 PostgreSQL"""
    config = get_db_config()
    config['database'] = db_name
    
    try:
        conn = psycopg2.connect(**config)
        conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        return conn
    except Exception as e:
        print_colored(f"✗ 数据库连接失败: {e}", Colors.RED)
        sys.exit(1)

def execute_sql_file(conn, filepath):
    """执行 SQL 文件"""
    cursor = conn.cursor()
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            sql = f.read()
            cursor.execute(sql)
        cursor.close()
        return True
    except Exception as e:
        print_colored(f"✗ 执行失败: {e}", Colors.RED)
        cursor.close()
        return False

def main():
    """主函数"""
    # 确认操作
    if not confirm_reset():
        print_colored("操作已取消", Colors.RED)
        sys.exit(0)
    
    config = get_db_config()
    db_name = config['database']
    
    # 步骤 1: 检查连接
    print_step(1, 6, "检查数据库连接...")
    conn = connect_to_postgres('postgres')
    print_colored("✓ 数据库连接成功", Colors.GREEN)
    
    # 步骤 2: 终止活动连接
    print_step(2, 6, "终止所有活动连接...")
    cursor = conn.cursor()
    cursor.execute(f"""
        SELECT pg_terminate_backend(pid) 
        FROM pg_stat_activity 
        WHERE datname = '{db_name}' AND pid <> pg_backend_pid();
    """)
    cursor.close()
    print_colored("✓ 活动连接已终止", Colors.GREEN)
    
    # 步骤 3: 删除现有数据库
    print_step(3, 6, f"删除现有数据库 '{db_name}'...")
    cursor = conn.cursor()
    cursor.execute(f"DROP DATABASE IF EXISTS {db_name};")
    cursor.close()
    print_colored(f"✓ 数据库 '{db_name}' 已删除", Colors.GREEN)
    
    # 步骤 4: 创建新数据库
    print_step(4, 6, f"创建新数据库 '{db_name}'...")
    cursor = conn.cursor()
    cursor.execute(f"""
        CREATE DATABASE {db_name} 
        WITH OWNER = {config['user']}
             ENCODING = 'UTF8'
             LC_COLLATE = 'zh_CN.UTF-8'
             LC_CTYPE = 'zh_CN.UTF-8'
             TEMPLATE = template0;
    """)
    cursor.close()
    print_colored(f"✓ 数据库 '{db_name}' 已创建", Colors.GREEN)
    
    # 关闭到 postgres 的连接
    conn.close()
    
    # 步骤 5: 启用 TimescaleDB 扩展
    print_step(5, 6, "启用 TimescaleDB 扩展...")
    conn = connect_to_postgres(db_name)
    cursor = conn.cursor()
    try:
        cursor.execute("CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;")
        print_colored("✓ TimescaleDB 扩展已启用", Colors.GREEN)
    except Exception as e:
        print_colored(f"⚠ TimescaleDB 扩展启用失败（可能未安装）: {e}", Colors.YELLOW)
        print_colored("  系统将继续执行，但 TimescaleDB 功能不可用", Colors.YELLOW)
    cursor.close()
    
    # 步骤 6: 执行迁移脚本
    print_step(6, 6, "执行迁移脚本...")
    
    migration_files = [
        ('001_init_schema.sql', '表结构创建'),
        ('002_init_indexes.sql', '索引创建'),
        ('003_init_triggers.sql', '触发器创建'),
        ('004_init_seed_data.sql', '基础数据导入'),
    ]
    
    migrations_dir = PROJECT_ROOT / 'backend' / 'db' / 'migrations'
    
    for filename, description in migration_files:
        filepath = migrations_dir / filename
        print(f"  → 执行 {filename}")
        
        if not filepath.exists():
            print_colored(f"  ✗ 文件不存在: {filepath}", Colors.RED)
            continue
        
        if execute_sql_file(conn, filepath):
            print_colored(f"  ✓ {description}完成", Colors.GREEN)
        else:
            print_colored(f"  ✗ {description}失败", Colors.RED)
            conn.close()
            sys.exit(1)
    
    # 查询统计信息
    print()
    print_colored("=" * 60, Colors.BLUE)
    print_colored("  ✓ 数据库重置完成！", Colors.GREEN)
    print_colored("=" * 60, Colors.BLUE)
    print()
    print_colored("统计信息:", Colors.GREEN)
    
    cursor = conn.cursor()
    
    # 表数量
    cursor.execute("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = 'public';")
    table_count = cursor.fetchone()[0]
    print(f"  - 表数量: {table_count}")
    
    # 索引数量
    cursor.execute("SELECT COUNT(*) FROM pg_indexes WHERE schemaname = 'public';")
    index_count = cursor.fetchone()[0]
    print(f"  - 索引数量: {index_count}")
    
    # 触发器数量
    cursor.execute("SELECT COUNT(*) FROM pg_trigger WHERE NOT tgisinternal;")
    trigger_count = cursor.fetchone()[0]
    print(f"  - 触发器数量: {trigger_count}")
    
    # 视图数量
    cursor.execute("SELECT COUNT(*) FROM information_schema.views WHERE table_schema = 'public';")
    view_count = cursor.fetchone()[0]
    print(f"  - 视图数量: {view_count}")
    
    cursor.close()
    conn.close()
    
    print()
    print_colored("下一步操作:", Colors.YELLOW)
    print("  1. 运行数据采集器导入股票列表和交易日历")
    print("  2. 执行首次龙虎榜数据采集")
    print("  3. 启动定时调度器")
    print()

if __name__ == '__main__':
    main()
