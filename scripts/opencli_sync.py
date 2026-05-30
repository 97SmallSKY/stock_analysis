"""
本地 OpenCLI 数据同步脚本

通过本地 opencli eastmoney 命令获取龙虎榜、指数行情、北向资金等数据，
增量 upsert 到远程 PostgreSQL (lhb_quant) 数据库。

仅限本地运行，不部署到远程服务器。

用法：
  python scripts/opencli_sync.py --mode status              # 查看同步状态
  python scripts/opencli_sync.py --mode daily                # 每日增量同步（opencli，仅当天）
  python scripts/opencli_sync.py --mode daily --date 2026-05-06  # 指定日期（仅当天数据）
  python scripts/opencli_sync.py --mode backfill-api --start-date 2021-05-06  # API历史回补（5年）
  python scripts/opencli_sync.py --mode backfill-api --start-date 2021-05-06 --dry-run  # 仅输出不写入
  python scripts/opencli_sync.py --mode daily --dry-run      # 仅输出不写入

注意：opencli eastmoney longhu --date 参数实际使用 >= 过滤，不支持精确指定日期。
      历史回补使用 --mode backfill-api 直接调用东方财富 API。
"""

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd
import requests
from loguru import logger
from urllib.parse import quote_plus

# ============================================================
# 加载 .env 配置（不依赖项目 config/settings.py，避免导入冲突）
# ============================================================
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    logger.warning("python-dotenv 未安装，将依赖系统环境变量")

# 从 .env 读取 PG 配置（与项目 .env 格式一致）
PG_HOST = os.getenv("PG_HOST", "localhost")
PG_PORT = int(os.getenv("POSTGRES_PORT", os.getenv("PG_PORT", "5432")))
PG_DATABASE = os.getenv("POSTGRES_DB", os.getenv("PG_DATABASE", "lhb_quant"))
PG_USER = os.getenv("POSTGRES_USER", os.getenv("PG_USER", "lhb_admin"))
PG_PASSWORD = os.getenv("POSTGRES_PASSWORD", os.getenv("PG_PASSWORD", ""))
PG_TABLE_PREFIX = os.getenv("PG_TABLE_PREFIX", "")

PG_URL = (
    f"postgresql+psycopg2://{quote_plus(PG_USER)}:{quote_plus(PG_PASSWORD)}"
    f"@{PG_HOST}:{PG_PORT}/{PG_DATABASE}"
)


# ============================================================
# 市场后缀转换
# ============================================================
def code_to_ts_code(code: str) -> str:
    """
    将纯数字股票代码转为 ts_code（加市场后缀）

    规则：
      - 0/3 开头 → .SZ（深市 A 股）
      - 6 开头 → .SH（沪市 A 股）
      - 8/4/9 开头 → .BJ（北证）
      - 200 开头 → .SH（沪市 B 股）
      - 900 开头 → .SH（沪市 B 股）
      - 其他 → 保留原样 + 尝试推断
    """
    code = str(code).strip()
    if code.startswith(("0", "3")):
        return f"{code}.SZ"
    elif code.startswith("6"):
        return f"{code}.SH"
    elif code.startswith(("8", "4", "9")):
        # 9 开头包括北证新股（如 920xxx）和科创板指数代码
        if len(code) == 6 and code.startswith("9"):
            # 920xxx 是北证新股
            return f"{code}.BJ"
        return f"{code}.BJ"
    elif code.startswith("200"):
        return f"{code}.SH"
    elif code.startswith("900"):
        return f"{code}.SH"
    # 兜底：无法推断时保留原样
    return code


def ts_code_to_code(ts_code: str) -> str:
    """ts_code 转回纯数字代码"""
    return ts_code.split(".")[0] if "." in ts_code else ts_code


# ============================================================
# OpenCLI 命令调用
# ============================================================
def run_opencli(site: str, command: str, args: dict = None, format: str = "json") -> list:
    """
    调用 opencli 命令并返回 JSON 结果列表

    Args:
        site: 站点名（如 eastmoney）
        command: 命令名（如 longhu）
        args: 命令参数 dict（如 {"date": "2026-05-06", "limit": 100}）
        format: 输出格式，默认 json

    Returns:
        解析后的 JSON 列表
    """
    cmd_parts = ["opencli", site, command, "-f", format]
    if args:
        for key, value in args.items():
            if value is not None:
                cmd_parts.extend([f"--{key}", str(value)])

    logger.debug(f"执行命令: {' '.join(cmd_parts)}")

    try:
        result = subprocess.run(
            cmd_parts,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            logger.error(f"opencli 命令失败 (exit={result.returncode}): {result.stderr[:200]}")
            return []

        # 解析 JSON 输出
        output = result.stdout.strip()
        if not output:
            logger.warning(f"opencli {site} {command} 返回空结果")
            return []

        data = json.loads(output)
        if isinstance(data, list):
            return data
        elif isinstance(data, dict):
            return [data]
        else:
            logger.warning(f"opencli 返回非标准 JSON: {type(data)}")
            return []

    except subprocess.TimeoutExpired:
        logger.error(f"opencli 命令超时: {' '.join(cmd_parts)}")
        return []
    except json.JSONDecodeError as e:
        logger.error(f"JSON 解析失败: {e}")
        return []
    except Exception as e:
        logger.error(f"opencli 命令异常: {e}")
        return []


# ============================================================
# OpenCLISyncer 核心类
# ============================================================
class OpenCLISyncer:
    """
    本地 OpenCLI → 远程 PG 数据同步器

    通过 opencli eastmoney 命令获取数据，
    通过 SQLAlchemy 写入远程 lhb_quant 数据库。
    """

    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run
        self._engine = None
        logger.info(
            f"OpenCLISyncer 初始化 | PG={PG_HOST}:{PG_PORT}/{PG_DATABASE} "
            f"user={PG_USER} dry_run={dry_run}"
        )

    def _get_engine(self):
        """创建 SQLAlchemy engine"""
        if self._engine is None:
            from sqlalchemy import create_engine
            self._engine = create_engine(
                PG_URL,
                pool_pre_ping=True,
                pool_size=3,
                max_overflow=5,
                pool_timeout=30,
            )
            # 验证连接
            with self._engine.connect() as conn:
                logger.info(f"PostgreSQL 连接成功 ✅ ({PG_HOST}:{PG_PORT}/{PG_DATABASE})")
        return self._engine

    # ----------------------------------------------------------
    # 龙虎榜数据
    # ----------------------------------------------------------
    def fetch_longhu(self, date: str = None) -> pd.DataFrame:
        """
        获取龙虎榜明细数据

        Args:
            date: 交易日期 YYYY-MM-DD，默认为昨天

        Returns:
            DataFrame with opencli raw columns
        """
        args = {"limit": 100}
        if date:
            args["date"] = date

        data = run_opencli("eastmoney", "longhu", args)
        if not data:
            logger.warning(f"龙虎榜数据获取失败 (date={date})")
            return pd.DataFrame()

        df = pd.DataFrame(data)
        logger.info(f"龙虎榜数据获取成功: {len(df)} 行 (date={date or '昨天'})")
        return df

    def map_to_lhb_stock_daily(self, raw_df: pd.DataFrame) -> pd.DataFrame:
        """
        将 opencli longhu 原始数据映射到 lhb_stock_daily 表结构

        字段映射：
          tradeDate → trade_date (date 类型)
          code → ts_code (加市场后缀)
          name → name
          closePrice → close_price
          changeRate → pct_change
          netAmt → l_net
          buyAmt → l_buy
          sellAmt → l_sell
          turnover → amount
          dealRatio → turnover_rate
          market → reason_type (简化)
          reason → reason
        """
        if raw_df.empty:
            return pd.DataFrame()

        df = raw_df.copy()

        # 基础映射
        mapped = pd.DataFrame()
        mapped["ts_code"] = df["code"].apply(code_to_ts_code)
        mapped["trade_date"] = pd.to_datetime(df["tradeDate"]).dt.date
        mapped["name"] = df["name"]
        mapped["close_price"] = df["closePrice"]
        mapped["pct_change"] = df["changeRate"]
        mapped["l_net"] = df["netAmt"]
        mapped["l_buy"] = df["buyAmt"]
        mapped["l_sell"] = df["sellAmt"]
        mapped["amount"] = df["turnover"]
        mapped["turnover_rate"] = df["dealRatio"]

        # reason_type 从 market 字段简化
        # "深交所主板" → "SZ_MAIN", "沪市A股" → "SH_MAIN" 等
        market_map = {
            "深交所主板": "SZ_MAIN",
            "深交所创业板": "SZ_CYB",
            "深交所风险警示板": "SZ_WARNING",
            "沪市A股": "SH_MAIN",
            "上交所主板": "SH_MAIN",
            "沪市科创板": "SH_KCB",
            "上交所科创板": "SH_KCB",
            "上交所风险警示板": "SH_WARNING",
            "北证A股": "BJ_A",
        }
        mapped["reason_type"] = df["market"].map(market_map).fillna("OTHER")
        mapped["reason"] = df["reason"]

        # 补充空字段（PG 表有这些列但 opencli 不提供）
        mapped["float_market_cap"] = None
        mapped["market_cap"] = None
        mapped["industry"] = None
        mapped["score"] = None
        mapped["score_tech"] = None
        mapped["score_fund"] = None
        mapped["score_hist"] = None
        mapped["score_detail"] = None

        # boardAmt 映射到 amount（成交额），但 opencli 的 turnover 就是成交额
        # l_net 应是净买额 netAmt，而非 boardAmt

        logger.info(f"龙虎榜字段映射完成: {len(mapped)} 行")
        return mapped

    def _ensure_stock_info(self, mapped_df: pd.DataFrame) -> int:
        """
        确保 stock_info 表中有所有即将写入的 ts_code

        lhb_stock_daily 有对 stock_info 的外键约束，
        缺失的 ts_code 会导致写入失败。
        此方法先将不在 stock_info 中的 ts_code 补充进去。

        Returns:
            新增的 stock_info 行数
        """
        if mapped_df.empty or self.dry_run:
            return 0

        engine = self._get_engine()
        from sqlalchemy import text

        # 获取已有的 ts_code
        ts_codes_to_insert = mapped_df["ts_code"].unique().tolist()
        with engine.connect() as conn:
            existing = conn.execute(
                text("SELECT ts_code FROM stock_info WHERE ts_code = ANY(:codes)"),
                {"codes": ts_codes_to_insert}
            ).fetchall()
        existing_codes = {r[0] for r in existing}

        missing_codes = [c for c in ts_codes_to_insert if c not in existing_codes]
        if not missing_codes:
            logger.debug("stock_info 外键约束满足，无需补充")
            return 0

        logger.info(f"stock_info 缺失 {len(missing_codes)} 个 ts_code，正在补充")

        rows_inserted = 0
        for ts_code in missing_codes:
            # 从 mapped_df 中找该 ts_code 的信息
            match = mapped_df[mapped_df["ts_code"] == ts_code].iloc[0]
            symbol = ts_code_to_code(ts_code)

            # 推断市场
            if ts_code.endswith(".SZ"):
                market = "SZ"
                exchange = "SZSE"
            elif ts_code.endswith(".SH"):
                market = "SH"
                exchange = "SSE"
            elif ts_code.endswith(".BJ"):
                market = "BJ"
                exchange = "BSE"
            else:
                market = "UNKNOWN"
                exchange = "UNKNOWN"

            sql = text("""
                INSERT INTO stock_info (ts_code, symbol, name, market, exchange, is_active)
                VALUES (:ts_code, :symbol, :name, :market, :exchange, true)
                ON CONFLICT (ts_code) DO NOTHING
            """)
            try:
                with engine.begin() as conn:
                    conn.execute(sql, {
                        "ts_code": ts_code,
                        "symbol": symbol,
                        "name": match["name"] if pd.notna(match["name"]) else symbol,
                        "market": market,
                        "exchange": exchange,
                    })
                rows_inserted += 1
            except Exception as e:
                logger.error(f"stock_info 补充失败 [{ts_code}]: {e}")

        logger.info(f"stock_info 补充完成: {rows_inserted} 行")
        return rows_inserted

    def sync_lhb_stock_daily(self, mapped_df: pd.DataFrame) -> int:
        """
        增量 upsert 龙虎榜数据到 PG lhb_stock_daily 表

        使用 ON CONFLICT (ts_code, trade_date) DO UPDATE 确保无重复
        写入前自动补充 stock_info 外键依赖

        Returns:
            写入/更新的行数
        """
        if mapped_df.empty:
            logger.warning("无龙虎榜数据可写入")
            return 0

        if self.dry_run:
            logger.info(f"[DRY-RUN] 龙虎榜数据 {len(mapped_df)} 行，未写入 PG")
            print(mapped_df.to_string(max_rows=10))
            return len(mapped_df)

        # 先补充 stock_info 外键依赖
        self._ensure_stock_info(mapped_df)

        engine = self._get_engine()
        from sqlalchemy import text

        rows_written = 0
        for _, row in mapped_df.iterrows():
            # upsert SQL
            sql = text("""
                INSERT INTO lhb_stock_daily
                    (ts_code, trade_date, name, close_price, pct_change,
                     l_net, l_buy, l_sell, amount, turnover_rate,
                     reason_type, reason, float_market_cap, market_cap,
                     industry, score, score_tech, score_fund, score_hist,
                     score_detail)
                VALUES
                    (:ts_code, :trade_date, :name, :close_price, :pct_change,
                     :l_net, :l_buy, :l_sell, :amount, :turnover_rate,
                     :reason_type, :reason, :float_market_cap, :market_cap,
                     :industry, :score, :score_tech, :score_fund, :score_hist,
                     :score_detail)
                ON CONFLICT (ts_code, trade_date) DO UPDATE SET
                    name = EXCLUDED.name,
                    close_price = EXCLUDED.close_price,
                    pct_change = EXCLUDED.pct_change,
                    l_net = EXCLUDED.l_net,
                    l_buy = EXCLUDED.l_buy,
                    l_sell = EXCLUDED.l_sell,
                    amount = EXCLUDED.amount,
                    turnover_rate = EXCLUDED.turnover_rate,
                    reason_type = EXCLUDED.reason_type,
                    reason = EXCLUDED.reason,
                    updated_at = NOW()
            """)

            params = {
                "ts_code": row["ts_code"],
                "trade_date": row["trade_date"],
                "name": row["name"],
                "close_price": float(row["close_price"]) if pd.notna(row["close_price"]) else None,
                "pct_change": float(row["pct_change"]) if pd.notna(row["pct_change"]) else None,
                "l_net": float(row["l_net"]) if pd.notna(row["l_net"]) else None,
                "l_buy": float(row["l_buy"]) if pd.notna(row["l_buy"]) else None,
                "l_sell": float(row["l_sell"]) if pd.notna(row["l_sell"]) else None,
                "amount": float(row["amount"]) if pd.notna(row["amount"]) else None,
                "turnover_rate": float(row["turnover_rate"]) if pd.notna(row["turnover_rate"]) else None,
                "reason_type": row["reason_type"],
                "reason": row["reason"],
                "float_market_cap": None,
                "market_cap": None,
                "industry": None,
                "score": None,
                "score_tech": None,
                "score_fund": None,
                "score_hist": None,
                "score_detail": None,
            }

            try:
                with engine.begin() as conn:
                    conn.execute(sql, params)
                rows_written += 1
            except Exception as e:
                logger.error(f"写入失败 [{row['ts_code']} {row['trade_date']}]: {e}")

        logger.info(f"龙虎榜数据写入完成: {rows_written} 行 (upsert)")
        return rows_written

    # ----------------------------------------------------------
    # 指数行情 & 北向资金
    # ----------------------------------------------------------
    def fetch_index_board(self) -> pd.DataFrame:
        """
        获取主要市场指数行情

        Returns:
            DataFrame with columns: code, name, price, changePercent, etc.
        """
        data = run_opencli("eastmoney", "index-board", {"group": "main"})
        if not data:
            return pd.DataFrame()
        return pd.DataFrame(data)

    def fetch_northbound(self) -> Optional[float]:
        """
        获取北向资金当日净流入（亿元）

        Returns:
            北向资金净流入额（亿元），或 None
        """
        data = run_opencli("eastmoney", "northbound")
        if not data:
            return None

        # 取最后一个有效值（当日最终累计净流入）
        total_net = None
        for item in reversed(data):
            if "totalNetYi" in item and item["totalNetYi"] is not None:
                total_net = float(item["totalNetYi"])
                break

        if total_net is not None:
            logger.info(f"北向资金净流入: {total_net:.2f} 亿元")
        return total_net

    def sync_market_sentiment_supplement(
        self,
        date: str = None,
        index_board_df: pd.DataFrame = None,
        northbound_net: float = None,
    ) -> bool:
        """
        补充市场情绪表中的北向资金和指数收盘价数据

        更新已有记录的 lhb_total_net、sh_close、sz_close、cy_close、northbound_net 字段，
        不创建新的 market_sentiment 行（由服务器端采集脚本创建）

        Args:
            date: 交易日期 YYYY-MM-DD
            index_board_df: 指数行情 DataFrame（code/price 列）
            northbound_net: 北向资金净流入（亿元）
        """
        if self.dry_run:
            logger.info(f"[DRY-RUN] 市场情绪补充: date={date}, northbound={northbound_net}")
            if index_board_df is not None and not index_board_df.empty:
                print(index_board_df.to_string())
            return True

        engine = self._get_engine()
        from sqlalchemy import text

        if not date:
            return True

        # 从 index_board_df 提取指数收盘价
        sh_close = sz_close = cy_close = None
        if index_board_df is not None and not index_board_df.empty and "code" in index_board_df.columns:
            idx = index_board_df.set_index("code")["price"]
            sh_close = float(idx["000001"]) if "000001" in idx else None
            sz_close = float(idx["399001"]) if "399001" in idx else None
            cy_close = float(idx["399006"]) if "399006" in idx else None

        northbound_wan = round(northbound_net * 10000, 2) if northbound_net is not None else None

        sql = text("""
            UPDATE market_sentiment
            SET
                lhb_total_net = COALESCE(:lhb_total_net, lhb_total_net),
                sh_close = COALESCE(:sh_close, sh_close),
                sz_close = COALESCE(:sz_close, sz_close),
                cy_close = COALESCE(:cy_close, cy_close),
                northbound_net = COALESCE(:northbound_net, northbound_net),
                updated_at = NOW()
            WHERE trade_date = :trade_date
        """)
        params = {
            "trade_date": date,
            "lhb_total_net": northbound_wan,
            "sh_close": sh_close,
            "sz_close": sz_close,
            "cy_close": cy_close,
            "northbound_net": northbound_wan,
        }
        try:
            with engine.begin() as conn:
                result = conn.execute(sql, params)
            logger.info(
                f"市场情绪补充完成: date={date} "
                f"SH={sh_close} SZ={sz_close} CY={cy_close} "
                f"northbound={northbound_net}亿 affected={result.rowcount}"
            )
            return result.rowcount > 0
        except Exception as e:
            logger.error(f"市场情绪更新失败: {e}")
            return False

        return True

    # ----------------------------------------------------------
    # 运行模式
    # ----------------------------------------------------------
    def run_daily(self, date: str = None) -> dict:
        """
        每日增量同步

        流程：
        1. 获取龙虎榜数据 → 映射 → upsert 到 lhb_stock_daily
        2. 获取指数行情 + 北向资金 → 补充 market_sentiment

        Args:
            date: 交易日期 YYYY-MM-DD，默认为今天（盘中/盘后）或最近交易日
        """
        logger.info(f"=== 开始每日同步 (date={date or 'auto'}) ===")

        stats = {
            "lhb_rows": 0,
            "sentiment_updated": False,
            "northbound_net": None,
            "errors": [],
        }

        # Step 1: 龙虎榜
        raw_lhb = self.fetch_longhu(date=date)
        if raw_lhb.empty:
            stats["errors"].append("龙虎榜数据获取失败")
        else:
            mapped_lhb = self.map_to_lhb_stock_daily(raw_lhb)
            stats["lhb_rows"] = self.sync_lhb_stock_daily(mapped_lhb)

        # Step 2: 指数行情 & 北向资金 → 市场情绪补充
        index_df = self.fetch_index_board()
        northbound = self.fetch_northbound()

        # 日期：优先用指定日期，否则从龙虎榜数据推断
        sentiment_date = date
        if not sentiment_date and not raw_lhb.empty:
            sentiment_date = raw_lhb["tradeDate"].iloc[0]

        stats["sentiment_updated"] = self.sync_market_sentiment_supplement(
            date=sentiment_date,
            index_board_df=index_df,
            northbound_net=northbound,
        )
        stats["northbound_net"] = northbound

        logger.info(f"=== 每日同步完成: 龙虎榜={stats['lhb_rows']}行, 情绪补充={stats['sentiment_updated']} ===")
        return stats

    def run_backfill(self, days: int = 30, end_date: str = None) -> dict:
        """
        历史数据回补

        从 end_date 开始往回补 days 天的数据

        Args:
            days: 回补天数
            end_date: 结束日期 YYYY-MM-DD，默认今天
        """
        if end_date:
            end = datetime.strptime(end_date, "%Y-%m-%d")
        else:
            end = datetime.now()

        logger.info(f"=== 开始历史回补: {days} 天, end={end.strftime('%Y-%m-%d')} ===")

        total_stats = {
            "total_lhb_rows": 0,
            "days_processed": 0,
            "days_failed": 0,
        }

        for i in range(days):
            target_date = end - timedelta(days=i)
            date_str = target_date.strftime("%Y-%m-%d")

            # 跳过周末（简单判断）
            if target_date.weekday() >= 5:  # Saturday=5, Sunday=6
                logger.debug(f"跳过周末: {date_str}")
                continue

            logger.info(f"回补日期: {date_str}")
            daily_stats = self.run_daily(date=date_str)

            if daily_stats["lhb_rows"] > 0:
                total_stats["days_processed"] += 1
                total_stats["total_lhb_rows"] += daily_stats["lhb_rows"]
            elif daily_stats["errors"]:
                total_stats["days_failed"] += 1
            else:
                # 该日期无龙虎榜数据（可能是非交易日或当天无人上榜）
                total_stats["days_processed"] += 1

        logger.info(
            f"=== 回补完成: 处理{total_stats['days_processed']}天 "
            f"共{total_stats['total_lhb_rows']}行, "
            f"失败{total_stats['days_failed']}天 ==="
        )
        return total_stats

    def run_status(self) -> dict:
        """
        查看同步状态

        显示远程 PG 中核心表的数据量和最新日期
        """
        engine = self._get_engine()
        from sqlalchemy import text

        status = {}

        # lhb_stock_daily
        try:
            with engine.connect() as conn:
                result = conn.execute(text("""
                    SELECT
                        COUNT(*) as total_rows,
                        MIN(trade_date) as min_date,
                        MAX(trade_date) as max_date
                    FROM lhb_stock_daily
                """))
                row = result.fetchone()
                status["lhb_stock_daily"] = {
                    "total_rows": row[0],
                    "min_date": str(row[1]),
                    "max_date": str(row[2]),
                }
        except Exception as e:
            status["lhb_stock_daily"] = {"error": str(e)}

        # market_sentiment
        try:
            with engine.connect() as conn:
                result = conn.execute(text("""
                    SELECT
                        COUNT(*) as total_rows,
                        MIN(trade_date) as min_date,
                        MAX(trade_date) as max_date
                    FROM market_sentiment
                """))
                row = result.fetchone()
                status["market_sentiment"] = {
                    "total_rows": row[0],
                    "min_date": str(row[1]),
                    "max_date": str(row[2]),
                }
        except Exception as e:
            status["market_sentiment"] = {"error": str(e)}

        # lhb_seats
        try:
            with engine.connect() as conn:
                result = conn.execute(text("""
                    SELECT
                        COUNT(*) as total_rows,
                        MIN(trade_date) as min_date,
                        MAX(trade_date) as max_date
                    FROM lhb_seats
                """))
                row = result.fetchone()
                status["lhb_seats"] = {
                    "total_rows": row[0],
                    "min_date": str(row[1]),
                    "max_date": str(row[2]),
                }
        except Exception as e:
            status["lhb_seats"] = {"error": str(e)}

        # 输出
        logger.info("=== 远程 PG 数据库同步状态 ===")
        for table, info in status.items():
            if "error" in info:
                logger.info(f"  {table}: ❌ {info['error']}")
            else:
                logger.info(
                    f"  {table}: ✅ {info['total_rows']} 行 "
                    f"({info['min_date']} ~ {info['max_date']})"
                )

        # 检查 opencli 可用性
        logger.info("=== OpenCLI 命令可用性 ===")
        for cmd_name, site_cmd, test_args in [
            ("龙虎榜", ("eastmoney", "longhu"), {"limit": 1}),
            ("指数行情", ("eastmoney", "index-board"), {"group": "main"}),
            ("北向资金", ("eastmoney", "northbound"), None),
        ]:
            test_data = run_opencli(site_cmd[0], site_cmd[1], test_args)
            if test_data:
                logger.info(f"  {cmd_name}: ✅ 可用 ({len(test_data)} 条)")
            else:
                logger.warning(f"  {cmd_name}: ❌ 不可用")

        return status

    # ----------------------------------------------------------
    # 东方财富 API 直接调用（历史回补用）
    # ----------------------------------------------------------
    def fetch_lhb_from_api(self, date: str) -> pd.DataFrame:
        """
        直接调用东方财富 API 获取指定日期的龙虎榜数据

        与 opencli longhu 命令不同，此方法：
        - 使用 TRADE_DATE = 'YYYY-MM-DD' 精确过滤
        - pageSize 设为 5000，可获取一天的全部数据
        - 支持历史日期（opencli --date 参数不起作用）

        Args:
            date: 交易日期 YYYY-MM-DD

        Returns:
            DataFrame with standardized columns (同 opencli 格式)
        """
        url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
        params = {
            "sortColumns": "TRADE_DATE,SECURITY_CODE",
            "sortTypes": "-1,1",
            "pageSize": "5000",
            "pageNumber": "1",
            "reportName": "RPT_DAILYBILLBOARD_DETAILS",
            "columns": "ALL",
            "source": "WEB",
            "client": "WEB",
            "filter": f"(TRADE_DATE='{date}')",
        }
        headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}

        try:
            resp = requests.get(url, params=params, headers=headers, timeout=30)
            if resp.status_code != 200:
                logger.error(f"API 请求失败: HTTP {resp.status_code}")
                return pd.DataFrame()

            data = resp.json()
            rows = data.get("result", {}).get("data", [])
            if not rows:
                logger.debug(f"日期 {date} 无龙虎榜数据")
                return pd.DataFrame()

            # 转换为与 opencli longhu 相同的格式
            formatted = []
            for it in rows:
                formatted.append({
                    "tradeDate": str(it.get("TRADE_DATE", ""))[:10],
                    "code": it.get("SECURITY_CODE", ""),
                    "name": it.get("SECURITY_NAME_ABBR", ""),
                    "closePrice": it.get("CLOSE_PRICE"),
                    "changeRate": it.get("CHANGE_RATE"),
                    "netAmt": it.get("BILLBOARD_NET_AMT"),
                    "buyAmt": it.get("BILLBOARD_BUY_AMT"),
                    "sellAmt": it.get("BILLBOARD_SELL_AMT"),
                    "boardAmt": it.get("BILLBOARD_DEAL_AMT"),
                    "turnover": it.get("ACCUM_AMOUNT"),
                    "dealRatio": it.get("DEAL_AMOUNT_RATIO"),
                    "market": it.get("TRADE_MARKET", ""),
                    "reason": it.get("EXPLANATION", ""),
                })

            df = pd.DataFrame(formatted)
            logger.info(f"API 龙虎榜数据获取成功: {len(df)} 行 (date={date})")
            return df

        except requests.Timeout:
            logger.error(f"API 请求超时 (date={date})")
            return pd.DataFrame()
        except Exception as e:
            logger.error(f"API 请求异常 (date={date}): {e}")
            return pd.DataFrame()

    def get_trade_calendar(self, start_date: str, end_date: str) -> list:
        """
        从 PG trade_calendar 获取交易日列表

        Args:
            start_date: 开始日期 YYYY-MM-DD
            end_date: 结束日期 YYYY-MM-DD

        Returns:
            交易日期列表 ['2021-05-06', '2021-05-07', ...]
        """
        engine = self._get_engine()
        from sqlalchemy import text

        try:
            with engine.connect() as conn:
                result = conn.execute(text("""
                    SELECT cal_date FROM trade_calendar
                    WHERE cal_date >= :start AND cal_date <= :end
                      AND is_open = true
                    ORDER BY cal_date
                """), {"start": start_date, "end": end_date})
                dates = [str(row[0]) for row in result.fetchall()]
            logger.info(f"交易日历: {len(dates)} 个交易日 ({start_date} ~ {end_date})")
            return dates
        except Exception as e:
            logger.error(f"交易日历查询失败: {e}")
            # 回退：简单生成日期列表（含周末，API 调用时会返回空数据）
            dates = []
            start = datetime.strptime(start_date, "%Y-%m-%d")
            end = datetime.strptime(end_date, "%Y-%m-%d")
            current = start
            while current <= end:
                if current.weekday() < 5:  # 排除周末
                    dates.append(current.strftime("%Y-%m-%d"))
                current += timedelta(days=1)
            logger.info(f"回退日期列表: {len(dates)} 天（含非交易日）")
            return dates

    def run_backfill_api(
        self,
        start_date: str = None,
        end_date: str = None,
        delay: float = 0.5,
    ) -> dict:
        """
        通过东方财富 API 直接回补历史龙虎榜数据

        opencli longhu 命令的 --date 参数不起作用（API 使用 >= 过滤），
        因此历史回补必须直接调用东方财富 API。

        Args:
            start_date: 开始日期 YYYY-MM-DD（默认5年前）
            end_date: 结束日期 YYYY-MM-DD（默认 PG 中最早数据前一天）
            delay: 每次请求之间的延迟秒数（避免被封）
        """
        # 确定 PG 中已有的最早日期
        engine = self._get_engine()
        from sqlalchemy import text

        with engine.connect() as conn:
            result = conn.execute(text(
                "SELECT MIN(trade_date) FROM lhb_stock_daily"
            ))
            pg_min_date = result.fetchone()[0]

        if pg_min_date:
            # 回补到 PG 最早数据前一天
            end_default = str(datetime.strptime(str(pg_min_date), "%Y-%m-%d") - timedelta(days=1))[:10]
        else:
            end_default = datetime.now().strftime("%Y-%m-%d")

        # 默认开始日期为5年前
        start_default = str(datetime.now() - timedelta(days=365 * 5))[:10]

        start = start_date or start_default
        end = end_date or end_default

        logger.info(f"=== 开始 API 历史回补: {start} ~ {end} ===")

        # 获取交易日历
        trade_dates = self.get_trade_calendar(start, end)

        total_stats = {
            "total_rows": 0,
            "days_with_data": 0,
            "days_no_data": 0,
            "days_failed": 0,
            "stock_info_added": 0,
        }

        for i, date_str in enumerate(trade_dates):
            logger.info(f"[{i+1}/{len(trade_dates)}] 回补日期: {date_str}")

            raw_df = self.fetch_lhb_from_api(date_str)

            if raw_df.empty:
                # 可能是非交易日或当天无人上榜
                total_stats["days_no_data"] += 1
                continue

            mapped_df = self.map_to_lhb_stock_daily(raw_df)

            if not self.dry_run:
                # 补充 stock_info 外键依赖
                added = self._ensure_stock_info(mapped_df)
                total_stats["stock_info_added"] += added

                # 批量 upsert
                rows = self.sync_lhb_stock_daily(mapped_df)
                total_stats["total_rows"] += rows
            else:
                total_stats["total_rows"] += len(mapped_df)
                logger.info(f"[DRY-RUN] {date_str}: {len(mapped_df)} 行")

            total_stats["days_with_data"] += 1

            # 请求间延迟，避免被封
            if delay > 0 and not self.dry_run:
                time.sleep(delay)

        logger.info(
            f"=== API 回补完成: {total_stats['days_with_data']}天有数据 "
            f"共{total_stats['total_rows']}行, "
            f"{total_stats['days_no_data']}天无数据, "
            f"{total_stats['days_failed']}天失败, "
            f"stock_info补充{total_stats['stock_info_added']}行 ==="
        )
        return total_stats


# ============================================================
# CLI 入口
# ============================================================
def main():
    parser = argparse.ArgumentParser(
        description="本地数据同步到远程 PG（支持 opencli 和东方财富 API）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例：
  python scripts/opencli_sync.py --mode status              # 查看同步状态
  python scripts/opencli_sync.py --mode daily                # 每日增量同步（opencli，当天数据）
  python scripts/opencli_sync.py --mode daily --date 2026-05-06  # 指定日期（仅当天）
  python scripts/opencli_sync.py --mode backfill-api --start-date 2021-05-06  # API回补5年龙虎榜
  python scripts/opencli_sync.py --mode backfill-api --start-date 2021-05-06 --dry-run  # 仅输出
  python scripts/opencli_sync.py --mode daily --dry-run      # 仅输出不写入

注意：opencli eastmoney longhu 的 --date 参数实际使用 >= 过滤，无法获取指定日期数据。
      历史回补请使用 --mode backfill-api 直接调用东方财富 API。
        """,
    )

    parser.add_argument(
        "--mode",
        choices=["daily", "backfill-api", "status"],
        default="daily",
        help="运行模式: daily(每日增量-opencli当天), backfill-api(API历史回补), status(查看状态)",
    )
    parser.add_argument(
        "--date",
        type=str,
        default=None,
        help="指定交易日期 YYYY-MM-DD（仅 daily 模式）",
    )
    parser.add_argument(
        "--start-date",
        type=str,
        default=None,
        help="回补开始日期 YYYY-MM-DD（仅 backfill-api 模式，默认5年前）",
    )
    parser.add_argument(
        "--end-date",
        type=str,
        default=None,
        help="回补结束日期 YYYY-MM-DD（仅 backfill-api 模式，默认PG最早日期前一天）",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.5,
        help="API请求间隔秒数（仅 backfill-api 模式，默认0.5秒）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="仅输出数据，不写入 PG",
    )

    args = parser.parse_args()

    # 配置日志
    logger.remove()
    logger.add(sys.stderr, level="INFO", format="{time:HH:mm:ss} | {level:<7} | {message}")

    syncer = OpenCLISyncer(dry_run=args.dry_run)

    if args.mode == "status":
        syncer.run_status()
    elif args.mode == "daily":
        syncer.run_daily(date=args.date)
    elif args.mode == "backfill-api":
        syncer.run_backfill_api(
            start_date=args.start_date,
            end_date=args.end_date,
            delay=args.delay,
        )


if __name__ == "__main__":
    main()