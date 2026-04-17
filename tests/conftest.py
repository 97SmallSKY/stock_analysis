# tests/conftest.py

import os
import pytest
import psycopg2
from psycopg2.extras import RealDictCursor
from pathlib import Path


def load_env():
    """从.env文件加载环境变量"""
    env_path = Path(__file__).parent.parent / ".env"
    if env_path.exists():
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    os.environ.setdefault(key.strip(), value.strip())


# 启动时加载.env
load_env()

# 从.env读取数据库配置
DB_CONFIG = {
    "host": os.getenv("PG_HOST", "localhost"),
    "port": int(os.getenv("PG_PORT", "5432")),
    "dbname": os.getenv("POSTGRES_DB", "lhb_quant"),
    "user": os.getenv("POSTGRES_USER", "lhb_admin"),
    "password": os.getenv("POSTGRES_PASSWORD", "lhb_quant_2024"),
}


@pytest.fixture(scope="session")
def db_conn():
    """数据库连接(整个测试会话共享)"""
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        conn.autocommit = True
        yield conn
        conn.close()
    except psycopg2.OperationalError as e:
        pytest.skip(f"数据库连接失败: {e}")


@pytest.fixture(scope="function")
def db_cursor(db_conn):
    """只读cursor(不修改数据)"""
    cursor = db_conn.cursor(cursor_factory=RealDictCursor)
    yield cursor
    cursor.close()


@pytest.fixture(scope="function")
def tx_cursor():
    """事务cursor(测试后自动回滚，不污染数据)"""
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        conn.autocommit = False
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        yield cursor
        conn.rollback()
        cursor.close()
        conn.close()
    except psycopg2.OperationalError as e:
        pytest.skip(f"数据库连接失败: {e}")


@pytest.fixture(scope="session")
def data_source_config():
    """数据源配置"""
    return {
        "tushare": {
            "token": os.getenv("TUSHARE_TOKEN", ""),
            "rate_limit": 200,
        },
        "akshare": {"rate_limit": 30},
        "sina": {"rate_limit": 20},
        "crawler": {"rate_limit": 10},
    }
