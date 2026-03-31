"""
数据存储抽象层
- 本地开发（STORAGE_MODE=excel）：Excel 文件
- 本地/云上（STORAGE_MODE=postgres）：PostgreSQL
  - 复用 videofetcher 数据库，表名统一加 PG_TABLE_PREFIX（默认 stock_）前缀
  - 切换环境只需修改 .env，无需改代码

环境变量快速参考：
  STORAGE_MODE=postgres
  PG_HOST=localhost          # 云上改为服务器 IP
  PG_PORT=5432
  PG_DATABASE=videofetcher
  PG_USER=videofetcher_user
  PG_PASSWORD=VF2026!@#Pass
  PG_TABLE_PREFIX=stock_
"""
import os
import pandas as pd
from loguru import logger
from typing import Optional

# 确保 data 目录存在
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
os.makedirs(DATA_DIR, exist_ok=True)

try:
    from config.settings import STORAGE_MODE, EXCEL_FILES, PG_URL, PG_TABLE_PREFIX
except ImportError:
    STORAGE_MODE = "excel"
    PG_TABLE_PREFIX = "stock_"
    EXCEL_FILES = {
        "lhb_detail": os.path.join(DATA_DIR, "lhb_detail.xlsx"),
        "lhb_jigou": os.path.join(DATA_DIR, "lhb_jigou.xlsx"),
        "stock_kline": os.path.join(DATA_DIR, "stock_kline.xlsx"),
        "backtest_results": os.path.join(DATA_DIR, "backtest_results.xlsx"),
        "selected_stocks": os.path.join(DATA_DIR, "selected_stocks.xlsx"),
        "market_sentiment": os.path.join(DATA_DIR, "market_sentiment.xlsx"),
    }
    PG_URL = ""


def _pg_table(table: str) -> str:
    """
    将逻辑表名转换为 PostgreSQL 实际表名（加前缀）

    例：'lhb_detail'  →  'stock_lhb_detail'
        'stock_kline' →  'stock_stock_kline'（已含前缀时不重复加）
    """
    if PG_TABLE_PREFIX and not table.startswith(PG_TABLE_PREFIX):
        return f"{PG_TABLE_PREFIX}{table}"
    return table


# ============================================================
# Excel 存储
# ============================================================

class ExcelStorage:
    """Excel 文件存储（STORAGE_MODE=excel 时使用）"""

    def read(self, table: str, sheet_name: str = "Sheet1") -> pd.DataFrame:
        """读取 Excel 数据"""
        file_path = EXCEL_FILES.get(table)
        if not file_path or not os.path.exists(file_path):
            logger.debug(f"Excel 文件不存在: {file_path}")
            return pd.DataFrame()
        try:
            df = pd.read_excel(file_path, sheet_name=sheet_name, engine="openpyxl")
            return df
        except Exception as e:
            logger.error(f"读取 Excel 失败 [{table}]: {e}")
            return pd.DataFrame()

    def write(
        self,
        df: pd.DataFrame,
        table: str,
        sheet_name: str = "Sheet1",
        mode: str = "replace",
    ) -> bool:
        """
        写入 Excel 数据
        mode: 'replace' 覆盖 | 'append' 追加（基于日期去重）
        """
        file_path = EXCEL_FILES.get(table)
        if not file_path:
            logger.error(f"未知的表名: {table}")
            return False

        try:
            if mode == "append" and os.path.exists(file_path):
                existing = pd.read_excel(file_path, sheet_name=sheet_name, engine="openpyxl")
                date_col = _detect_date_column(df)
                if date_col and date_col in existing.columns:
                    new_dates = df[date_col].astype(str).unique()
                    existing = existing[~existing[date_col].astype(str).isin(new_dates)]
                df = pd.concat([existing, df], ignore_index=True)

            df.to_excel(file_path, sheet_name=sheet_name, index=False, engine="openpyxl")
            logger.info(f"数据写入 Excel 成功: {file_path} ({len(df)} 行)")
            return True
        except Exception as e:
            logger.error(f"写入 Excel 失败 [{table}]: {e}")
            return False

    def query_by_date(self, table: str, trade_date: str) -> pd.DataFrame:
        """按 trade_date 查询（Excel 模式）"""
        df = self.read(table)
        if df.empty or "trade_date" not in df.columns:
            return pd.DataFrame()
        return df[df["trade_date"].astype(str) == str(trade_date)].copy()

    def query(self, table: str, filters: dict = None, sheet_name: str = "Sheet1") -> pd.DataFrame:
        """带过滤条件查询"""
        df = self.read(table, sheet_name)
        if df.empty or not filters:
            return df
        for col, val in filters.items():
            if col in df.columns:
                if isinstance(val, (list, tuple)):
                    df = df[df[col].isin(val)]
                else:
                    df = df[df[col] == val]
        return df


# ============================================================
# PostgreSQL 存储
# ============================================================

class PostgreSQLStorage:
    """
    PostgreSQL 数据库存储（STORAGE_MODE=postgres 时使用）

    表名映射（PG_TABLE_PREFIX=stock_ 时）：
        lhb_detail        → stock_lhb_detail
        lhb_jigou         → stock_lhb_jigou
        stock_kline       → stock_stock_kline  ← 注意：stock_kline 会变成 stock_stock_kline
        backtest_results  → stock_backtest_results
        selected_stocks   → stock_selected_stocks
        market_sentiment  → stock_market_sentiment

    所有写入/读取操作自动加前缀，调用方无感知。
    """

    def __init__(self):
        self._engine = None
        logger.info(
            f"PostgreSQL 存储初始化 | host={os.getenv('PG_HOST','localhost')} "
            f"db={os.getenv('PG_DATABASE','videofetcher')} "
            f"prefix={PG_TABLE_PREFIX!r}"
        )

    def _get_engine(self):
        if self._engine is None:
            try:
                from sqlalchemy import create_engine
                self._engine = create_engine(
                    PG_URL,
                    pool_pre_ping=True,       # 连接前检测是否存活
                    pool_size=5,
                    max_overflow=10,
                    pool_timeout=30,
                )
                # 验证连接
                with self._engine.connect() as conn:
                    logger.info("PostgreSQL 连接成功 ✅")
            except Exception as e:
                logger.error(f"PostgreSQL 连接失败: {e}")
                logger.info("提示：确认 PostgreSQL 容器已启动 → docker start lobe-postgres")
                raise
        return self._engine

    def read(self, table: str, sheet_name: str = "Sheet1") -> pd.DataFrame:
        """从 PostgreSQL 读取数据（表名自动加前缀）"""
        pg_table = _pg_table(table)
        try:
            engine = self._get_engine()
            # 先检查表是否存在，避免抛错
            from sqlalchemy import inspect as sa_inspect
            insp = sa_inspect(engine)
            if not insp.has_table(pg_table):
                logger.debug(f"PostgreSQL 表不存在: {pg_table}")
                return pd.DataFrame()
            df = pd.read_sql_table(pg_table, engine)
            logger.debug(f"从 PostgreSQL 读取 {pg_table}: {len(df)} 行")
            return df
        except Exception as e:
            logger.error(f"读取 PostgreSQL 失败 [{pg_table}]: {e}")
            return pd.DataFrame()

    def write(
        self,
        df: pd.DataFrame,
        table: str,
        sheet_name: str = "Sheet1",
        mode: str = "replace",
    ) -> bool:
        """
        写入 PostgreSQL（表名自动加前缀）
        mode: 'replace' 覆盖整张表 | 'append' 追加（按日期去重后再 append）
        """
        pg_table = _pg_table(table)
        try:
            engine = self._get_engine()

            if mode == "append":
                # 安全追加：先确保表存在，再用 if_exists="append" 逐行写入
                # 【注意】不再使用"读取全表→按日期过滤→覆盖写入"模式，
                # 该模式会因日期去重导致同日其他数据丢失（例如 lhb_top10_prices 同日多股）
                from sqlalchemy import inspect as sa_inspect
                insp = sa_inspect(engine)
                if not insp.has_table(pg_table):
                    # 表不存在，直接 append（pandas 自动建表）
                    df.to_sql(pg_table, engine, if_exists="append", index=False, method="multi", chunksize=1000)
                else:
                    # 表已存在：直接追加，依赖数据库唯一约束去重（ON CONFLICT DO NOTHING）
                    # 若表上无唯一约束，会产生重复行，但不会丢失现有数据
                    df.to_sql(pg_table, engine, if_exists="append", index=False, method="multi", chunksize=1000)
            else:
                df.to_sql(pg_table, engine, if_exists="replace", index=False, method="multi", chunksize=1000)

            logger.info(f"数据写入 PostgreSQL 成功: {pg_table} ({len(df)} 行)")
            return True
        except Exception as e:
            logger.error(f"写入 PostgreSQL 失败 [{pg_table}]: {e}")
            return False

    def query(self, table: str, filters: dict = None, sheet_name: str = "Sheet1") -> pd.DataFrame:
        """带过滤条件查询（表名自动加前缀）"""
        pg_table = _pg_table(table)
        try:
            engine = self._get_engine()
            if not filters:
                return pd.read_sql_table(pg_table, engine)
            # 参数化查询，防止 SQL 注入
            from sqlalchemy import text
            conditions = " AND ".join([f"{k} = :{k}" for k in filters])
            sql = text(f"SELECT * FROM {pg_table} WHERE {conditions}")
            return pd.read_sql(sql, engine, params=filters)
        except Exception as e:
            logger.error(f"查询 PostgreSQL 失败 [{pg_table}]: {e}")
            return pd.DataFrame()

    def query_by_date(self, table: str, trade_date: str) -> pd.DataFrame:
        """按 trade_date 查询指定日期的数据（自动加表前缀）"""
        pg_table = _pg_table(table)
        try:
            engine = self._get_engine()
            from sqlalchemy import inspect as sa_inspect, text
            insp = sa_inspect(engine)
            if not insp.has_table(pg_table):
                return pd.DataFrame()
            sql = text(f"SELECT * FROM {pg_table} WHERE trade_date = :td")
            df = pd.read_sql(sql, engine, params={"td": trade_date})
            logger.debug(f"query_by_date {pg_table} [{trade_date}]: {len(df)} 行")
            return df
        except Exception as e:
            logger.error(f"query_by_date 失败 [{pg_table}]: {e}")
            return pd.DataFrame()

    def get_table_stats(self) -> pd.DataFrame:
        """查看所有 stock_ 表的数据量（运维用）"""
        try:
            engine = self._get_engine()
            sql = f"""
                SELECT tablename, n_live_tup as row_count
                FROM pg_stat_user_tables
                WHERE tablename LIKE '{PG_TABLE_PREFIX}%'
                ORDER BY tablename
            """
            return pd.read_sql(sql, engine)
        except Exception as e:
            logger.error(f"获取表统计失败: {e}")
            return pd.DataFrame()


# ============================================================
# 存储工厂
# ============================================================

def get_storage():
    """
    根据 STORAGE_MODE 环境变量返回对应存储实例

    切换方式（修改 .env 文件即可，无需改代码）：
      STORAGE_MODE=excel    → 使用本地 Excel 文件
      STORAGE_MODE=postgres → 使用 PostgreSQL（本地 Docker 或云上）
    """
    if STORAGE_MODE == "postgres":
        return PostgreSQLStorage()
    else:
        logger.info("使用 Excel 存储模式（STORAGE_MODE=excel）")
        return ExcelStorage()


# 全局存储实例（单例）
storage = get_storage()


# ============================================================
# 辅助函数
# ============================================================

def _detect_date_column(df: pd.DataFrame) -> Optional[str]:
    """自动探测日期字段"""
    date_col_candidates = ["trade_date", "上榜日期", "date", "日期", "交易日期", "select_date", "fetch_date"]
    for col in date_col_candidates:
        if col in df.columns:
            return col
    return None
