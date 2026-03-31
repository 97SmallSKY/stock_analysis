"""
PostgreSQL 连接测试
验证 stock_analysis 独立数据库连接是否正常

用法:
    python utils/_test_pg.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import create_engine, text, inspect as sa_inspect

user = os.getenv("PG_USER",     "stock_user")
pwd  = os.getenv("PG_PASSWORD", "")
host = os.getenv("PG_HOST",     "localhost")
port = os.getenv("PG_PORT",     "5432")
db   = os.getenv("PG_DATABASE", "stock_analysis")
pfx  = os.getenv("PG_TABLE_PREFIX", "")

from urllib.parse import quote_plus as qp
url = f"postgresql://{qp(user)}:{qp(pwd)}@{host}:{port}/{db}"

print("=" * 55)
print("PostgreSQL 连接测试")
print("=" * 55)
print(f"  Host     : {host}:{port}")
print(f"  Database : {db}")
print(f"  User     : {user}")
print(f"  Prefix   : {pfx!r}（空 = 不加前缀）")
print()

try:
    engine = create_engine(url, pool_pre_ping=True)
    with engine.connect() as conn:
        row = conn.execute(text("SELECT version(), current_database(), current_user")).fetchone()
        print(f"✅ 连接成功!")
        print(f"   版本    : {row[0][:55]}")
        print(f"   数据库  : {row[1]}")
        print(f"   用户    : {row[2]}")

        # 检查现有表
        insp = sa_inspect(engine)
        tables = sorted(insp.get_table_names(schema="public"))

        print()
        if tables:
            print(f"现有表（共 {len(tables)} 张）:")
            # 获取各表行数
            for t in tables:
                try:
                    cnt = conn.execute(text(f'SELECT COUNT(*) FROM "{t}"')).scalar()
                    print(f"  {t:35s}  {cnt:>8,} 行")
                except Exception:
                    print(f"  {t:35s}  （无法查询）")
        else:
            print("暂无数据表（首次运行 app.py 写入数据后自动创建）")

except Exception as e:
    print(f"❌ 连接失败: {e}")
    print()
    print("排查步骤:")
    print("  1. docker ps | grep videofetcher-db  # 确认容器运行")
    print("  2. docker start videofetcher-db       # 若未运行则启动")
    print("  3. 确认 .env 文件中 PG_PASSWORD 正确")
