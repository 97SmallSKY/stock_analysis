# tests/test_db_tables.py
"""
数据库表结构验证测试
直接连接生产PG，验证init.sql执行结果
"""

import pytest


# ================================================================
# 1. 表是否存在
# ================================================================

EXPECTED_TABLES = [
    "stock_info",
    "trade_calendar",
    "app_config",
    "strategies",
    "strategy_params",
    "datasource_config",
    "datasource_circuit_log",
    "collector_error_log",
    "audit_logs",
    "kline_daily",
    "lhb_stock_daily",
    "lhb_seats",
    "lhb_tn_price",
    "lhb_agg_stats",
    "lhb_institution_stat",
    "market_snapshots",
    "market_sentiment",
    "robots",
    "robot_trades",
    "trade_executions",
    "cash_ledger",
    "daily_nav",
    "sim_trades",
    "sim_trade_snapshots",
    "trade_events",
    "selection_logs",
    "backtest_tasks",
    "backtest_results",
]


class TestTablesExist:
    """验证所有表是否创建成功"""

    def test_table_count(self, db_cursor):
        db_cursor.execute("""
            SELECT COUNT(*) as cnt FROM information_schema.tables 
            WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
        """)
        result = db_cursor.fetchone()
        assert result["cnt"] >= 28, f"期望至少28张表，实际{result['cnt']}张"

    @pytest.mark.parametrize("table_name", EXPECTED_TABLES)
    def test_table_exists(self, db_cursor, table_name):
        db_cursor.execute("""
            SELECT EXISTS (
                SELECT 1 FROM information_schema.tables 
                WHERE table_schema = 'public' AND table_name = %s
            ) as exists
        """, (table_name,))
        result = db_cursor.fetchone()
        assert result["exists"], f"表 {table_name} 不存在"


# ================================================================
# 2. 枚举类型
# ================================================================

EXPECTED_ENUMS = [
    "robot_status",
    "trade_direction",
    "trade_phase",
    "exec_status",
    "ledger_type",
    "sim_trade_status",
    "backtest_status",
    "seat_direction",
    "circuit_state",
    "audit_action",
    "data_category",
]


class TestEnumTypes:
    """验证枚举类型"""

    @pytest.mark.parametrize("enum_name", EXPECTED_ENUMS)
    def test_enum_exists(self, db_cursor, enum_name):
        db_cursor.execute("""
            SELECT EXISTS (
                SELECT 1 FROM pg_type WHERE typname = %s AND typtype = 'e'
            ) as exists
        """, (enum_name,))
        result = db_cursor.fetchone()
        assert result["exists"], f"枚举类型 {enum_name} 不存在"

    def test_robot_status_values(self, db_cursor):
        db_cursor.execute("""
            SELECT enumlabel FROM pg_enum 
            JOIN pg_type ON pg_enum.enumtypid = pg_type.oid 
            WHERE typname = 'robot_status' ORDER BY enumsortorder
        """)
        values = [row["enumlabel"] for row in db_cursor.fetchall()]
        assert values == ["IDLE", "RUNNING", "PAUSED", "STOPPED", "ERROR"]

    def test_trade_direction_values(self, db_cursor):
        db_cursor.execute("""
            SELECT enumlabel FROM pg_enum 
            JOIN pg_type ON pg_enum.enumtypid = pg_type.oid 
            WHERE typname = 'trade_direction' ORDER BY enumsortorder
        """)
        values = [row["enumlabel"] for row in db_cursor.fetchall()]
        assert values == ["BUY", "SELL"]


# ================================================================
# 3. 扩展
# ================================================================

EXPECTED_EXTENSIONS = [
    "pg_stat_statements",
    "pg_trgm",
    "uuid-ossp",
    "pgcrypto",
]


class TestExtensions:
    """验证扩展安装"""

    @pytest.mark.parametrize("ext_name", EXPECTED_EXTENSIONS)
    def test_extension_exists(self, db_cursor, ext_name):
        db_cursor.execute("""
            SELECT EXISTS (
                SELECT 1 FROM pg_extension WHERE extname = %s
            ) as exists
        """, (ext_name,))
        result = db_cursor.fetchone()
        assert result["exists"], f"扩展 {ext_name} 未安装"


# ================================================================
# 4. 角色
# ================================================================

class TestRoles:
    """验证角色创建"""

    @pytest.mark.parametrize("role_name", ["lhb_admin", "lhb_readonly", "lhb_app"])
    def test_role_exists(self, db_cursor, role_name):
        db_cursor.execute("""
            SELECT EXISTS (
                SELECT 1 FROM pg_roles WHERE rolname = %s
            ) as exists
        """, (role_name,))
        result = db_cursor.fetchone()
        assert result["exists"], f"角色 {role_name} 不存在"


# ================================================================
# 5. 核心表字段验证
# ================================================================

class TestTableColumns:
    """验证关键表的字段结构"""

    def test_stock_info_columns(self, db_cursor):
        db_cursor.execute("""
            SELECT column_name FROM information_schema.columns 
            WHERE table_name = 'stock_info' ORDER BY ordinal_position
        """)
        columns = [row["column_name"] for row in db_cursor.fetchall()]
        required = ["ts_code", "symbol", "name", "industry", "market", "is_active"]
        for col in required:
            assert col in columns, f"stock_info 缺少字段 {col}"

    def test_lhb_stock_daily_columns(self, db_cursor):
        db_cursor.execute("""
            SELECT column_name FROM information_schema.columns 
            WHERE table_name = 'lhb_stock_daily' ORDER BY ordinal_position
        """)
        columns = [row["column_name"] for row in db_cursor.fetchall()]
        required = [
            "ts_code", "trade_date", "close_price", "pct_change",
            "l_buy", "l_sell", "l_net", "reason", "score",
            "score_tech", "score_fund", "score_hist",
        ]
        for col in required:
            assert col in columns, f"lhb_stock_daily 缺少字段 {col}"

    def test_robots_columns(self, db_cursor):
        db_cursor.execute("""
            SELECT column_name FROM information_schema.columns 
            WHERE table_name = 'robots' ORDER BY ordinal_position
        """)
        columns = [row["column_name"] for row in db_cursor.fetchall()]
        required = [
            "id", "name", "strategy_id", "initial_capital", "cash",
            "total_pnl", "total_pnl_pct", "total_trades",
            "win_count", "lose_count", "status", "risk_config", "ahs_score",
        ]
        for col in required:
            assert col in columns, f"robots 缺少字段 {col}"

    def test_robot_trades_columns(self, db_cursor):
        db_cursor.execute("""
            SELECT column_name FROM information_schema.columns 
            WHERE table_name = 'robot_trades' ORDER BY ordinal_position
        """)
        columns = [row["column_name"] for row in db_cursor.fetchall()]
        required = [
            "robot_id", "ts_code", "direction", "price", "shares",
            "commission", "slippage", "pnl_amount", "pnl_pct",
            "hold_days", "phase", "signal_score",
        ]
        for col in required:
            assert col in columns, f"robot_trades 缺少字段 {col}"


# ================================================================
# 6. 唯一约束
# ================================================================

class TestUniqueConstraints:
    """验证唯一约束"""

    EXPECTED_CONSTRAINTS = [
        ("lhb_stock_daily", "uk_lhb_stock_daily"),
        ("lhb_seats", "uk_lhb_seats"),
        ("kline_daily", "uk_kline_daily"),
        ("daily_nav", "uk_daily_nav"),
        ("market_sentiment", "uk_market_sentiment"),
        ("lhb_tn_price", "uk_lhb_tn_price"),
    ]

    @pytest.mark.parametrize("table_name,constraint_name", EXPECTED_CONSTRAINTS)
    def test_unique_constraint_exists(self, db_cursor, table_name, constraint_name):
        db_cursor.execute("""
            SELECT EXISTS (
                SELECT 1 FROM information_schema.table_constraints 
                WHERE table_name = %s 
                AND constraint_name = %s 
                AND constraint_type = 'UNIQUE'
            ) as exists
        """, (table_name, constraint_name))
        result = db_cursor.fetchone()
        assert result["exists"], f"表 {table_name} 缺少唯一约束 {constraint_name}"


# ================================================================
# 7. 索引
# ================================================================

class TestIndexes:
    """验证关键索引"""

    def test_kline_daily_indexes(self, db_cursor):
        db_cursor.execute("""
            SELECT indexname FROM pg_indexes 
            WHERE tablename = 'kline_daily' ORDER BY indexname
        """)
        indexes = [row["indexname"] for row in db_cursor.fetchall()]
        assert len(indexes) >= 3, f"kline_daily 索引不足，当前: {indexes}"

    def test_lhb_stock_daily_indexes(self, db_cursor):
        db_cursor.execute("""
            SELECT indexname FROM pg_indexes 
            WHERE tablename = 'lhb_stock_daily' ORDER BY indexname
        """)
        indexes = [row["indexname"] for row in db_cursor.fetchall()]
        assert len(indexes) >= 3, f"lhb_stock_daily 索引不足，当前: {indexes}"

    def test_robot_trades_indexes(self, db_cursor):
        db_cursor.execute("""
            SELECT indexname FROM pg_indexes 
            WHERE tablename = 'robot_trades' ORDER BY indexname
        """)
        indexes = [row["indexname"] for row in db_cursor.fetchall()]
        assert len(indexes) >= 3, f"robot_trades 索引不足，当前: {indexes}"


# ================================================================
# 8. PostgreSQL配置验证
# ================================================================

class TestPGConfig:
    """验证PostgreSQL配置"""

    def test_shared_buffers(self, db_cursor):
        db_cursor.execute("SHOW shared_buffers")
        result = db_cursor.fetchone()
        assert result["shared_buffers"] == "256MB", f"shared_buffers={result['shared_buffers']}"

    def test_work_mem(self, db_cursor):
        db_cursor.execute("SHOW work_mem")
        result = db_cursor.fetchone()
        assert result["work_mem"] == "8MB", f"work_mem={result['work_mem']}"

    def test_jit_off(self, db_cursor):
        db_cursor.execute("SHOW jit")
        result = db_cursor.fetchone()
        assert result["jit"] == "off", f"jit={result['jit']}"

    def test_timezone(self, db_cursor):
        db_cursor.execute("SHOW timezone")
        result = db_cursor.fetchone()
        assert result["TimeZone"] == "Asia/Shanghai"

    def test_max_connections(self, db_cursor):
        db_cursor.execute("SHOW max_connections")
        result = db_cursor.fetchone()
        assert result["max_connections"] == "50"

    def test_pg_stat_statements(self, db_cursor):
        db_cursor.execute("SHOW shared_preload_libraries")
        result = db_cursor.fetchone()
        assert "pg_stat_statements" in result["shared_preload_libraries"]
