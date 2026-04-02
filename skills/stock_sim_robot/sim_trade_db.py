"""
模拟交易数据库操作模块
======================
负责 sim_trades / sim_summary 表的创建和 CRUD 操作。

表结构：
  sim_trades  — 每笔模拟交易的完整记录（从开仓到平仓）
  sim_summary — 按周汇总的盈亏摘要（实体化，方便查询）
"""
import sys
import os
from pathlib import Path
from datetime import datetime, date

import pandas as pd
from loguru import logger

# 确保能导入 stock_analysis 包
_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(_ROOT))
os.chdir(_ROOT)

from utils.storage import storage
from config.settings import PG_URL

# ── DDL ───────────────────────────────────────────────────────────────
_CREATE_SIM_TRADES = """
CREATE TABLE IF NOT EXISTS sim_trades (
    id             SERIAL PRIMARY KEY,
    trade_date     VARCHAR(8)    NOT NULL,           -- 龙虎榜日期（T 日，YYYYMMDD）
    code           VARCHAR(6)    NOT NULL,           -- 股票代码
    name           VARCHAR(20),                      -- 股票名称
    signal_score   FLOAT,                            -- 综合评分（0-100）
    t2_win_rate    FLOAT,                            -- 历史 T+2 胜率（%）
    seat_tags      TEXT,                             -- 席位标签（JSON 数组字符串）
    buy_price      FLOAT,                            -- T+1 开盘买入价（实时行情）
    buy_source     VARCHAR(32),                      -- 买入价来源（akshare_spot/akshare_min/akshare_daily_hist）
    buy_amount     FLOAT DEFAULT 100000,             -- 买入金额（元）
    buy_shares     INT,                              -- 买入股数（100 股取整）
    list_price     FLOAT,                            -- 挂牌卖出价（LLM 建议）
    list_reason    TEXT,                             -- LLM 设置挂牌价理由
    sell_price     FLOAT,                            -- 实际卖出价（NULL = 未成交）
    sell_date      VARCHAR(8),                       -- 实际卖出日期
    sell_type      VARCHAR(16),                      -- 止盈 / 止损 / 持有到期 / 放弃
    profit         FLOAT,                            -- 盈亏金额（元）
    profit_pct     FLOAT,                            -- 盈亏百分比
    status         VARCHAR(10) DEFAULT 'open',       -- open / closed
    week_tag       VARCHAR(20),                      -- 周标签（如 2026-W13）
    llm_reason     TEXT,                             -- 选股综合摘要（飞书通知用）
    reason_tech    TEXT,                             -- 技术面分析理由
    reason_fund    TEXT,                             -- 资金面分析理由
    reason_history TEXT,                             -- 历史胜率分析理由
    reason_seat    TEXT,                             -- 席位深度分析理由
    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

_CREATE_SIM_SUMMARY = """
CREATE TABLE IF NOT EXISTS sim_summary (
    week_tag        VARCHAR(20) PRIMARY KEY,
    total_trades    INT DEFAULT 0,
    win_trades      INT DEFAULT 0,
    lose_trades     INT DEFAULT 0,
    win_rate        FLOAT,
    total_profit    FLOAT DEFAULT 0,
    avg_profit_pct  FLOAT,
    max_profit      FLOAT,
    max_loss        FLOAT,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

_CREATE_SIM_CONFIG = """
CREATE TABLE IF NOT EXISTS sim_config (
    config_key   VARCHAR(64) PRIMARY KEY,
    config_value TEXT,
    updated_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

_CREATE_SIM_STRATEGY = """
CREATE TABLE IF NOT EXISTS sim_strategy (
    strategy_key    VARCHAR(32) PRIMARY KEY,    -- 策略名称，目前只用 'default'
    stop_loss_pct   FLOAT       NOT NULL DEFAULT -5.0,  -- 止损触发线（如 -5.0 表示亏损 5%）
    sell1_pct       FLOAT       NOT NULL DEFAULT 3.0,   -- 第1档触发涨幅%（如 3.0）
    sell1_ratio     FLOAT       NOT NULL DEFAULT 0.333, -- 第1档卖出比例（如 0.333 = 1/3）
    sell2_pct       FLOAT       NOT NULL DEFAULT 5.0,   -- 第2档触发涨幅%
    sell2_ratio     FLOAT       NOT NULL DEFAULT 0.333, -- 第2档卖出比例
    sell3_pct       FLOAT       NOT NULL DEFAULT 8.0,   -- 第3档触发涨幅%（剩余全清）
    description     TEXT,                               -- 策略说明（前端展示）
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_by      VARCHAR(32) DEFAULT 'system'        -- 最后修改人
);
"""

# 默认策略配置（表为空时写入）
_DEFAULT_STRATEGY = {
    "strategy_key": "default",
    "stop_loss_pct": -5.0,
    "sell1_pct":     3.0,
    "sell1_ratio":   1/3,
    "sell2_pct":     5.0,
    "sell2_ratio":   1/3,
    "sell3_pct":     8.0,
    "description":   "三档分批卖出策略：+3%卖1/3仓，+5%再卖1/3仓，+8%清仓剩余；亏损超过5%止损全清。",
}


def _get_engine():
    """获取 SQLAlchemy engine（复用 storage 层）"""
    return storage._get_engine()


# 新字段迁移 SQL（对已存在的表追加列）
_ALTER_SIM_TRADES_NEW_COLS = [
    "ALTER TABLE sim_trades ADD COLUMN IF NOT EXISTS buy_source VARCHAR(32)",
    "ALTER TABLE sim_trades ADD COLUMN IF NOT EXISTS reason_tech TEXT",
    "ALTER TABLE sim_trades ADD COLUMN IF NOT EXISTS reason_fund TEXT",
    "ALTER TABLE sim_trades ADD COLUMN IF NOT EXISTS reason_history TEXT",
    "ALTER TABLE sim_trades ADD COLUMN IF NOT EXISTS reason_seat TEXT",
    # 分批卖出字段：三档（+3%卖1/3，+5%卖1/3，+8%全清）
    "ALTER TABLE sim_trades ADD COLUMN IF NOT EXISTS batch_mode    BOOLEAN DEFAULT TRUE",
    "ALTER TABLE sim_trades ADD COLUMN IF NOT EXISTS sell1_pct     FLOAT",
    "ALTER TABLE sim_trades ADD COLUMN IF NOT EXISTS sell1_price   FLOAT",
    "ALTER TABLE sim_trades ADD COLUMN IF NOT EXISTS sell1_shares  INT",
    "ALTER TABLE sim_trades ADD COLUMN IF NOT EXISTS sell1_date    VARCHAR(8)",
    "ALTER TABLE sim_trades ADD COLUMN IF NOT EXISTS sell1_done    BOOLEAN DEFAULT FALSE",
    "ALTER TABLE sim_trades ADD COLUMN IF NOT EXISTS sell2_pct     FLOAT",
    "ALTER TABLE sim_trades ADD COLUMN IF NOT EXISTS sell2_price   FLOAT",
    "ALTER TABLE sim_trades ADD COLUMN IF NOT EXISTS sell2_shares  INT",
    "ALTER TABLE sim_trades ADD COLUMN IF NOT EXISTS sell2_date    VARCHAR(8)",
    "ALTER TABLE sim_trades ADD COLUMN IF NOT EXISTS sell2_done    BOOLEAN DEFAULT FALSE",
    "ALTER TABLE sim_trades ADD COLUMN IF NOT EXISTS sell3_pct     FLOAT",
    "ALTER TABLE sim_trades ADD COLUMN IF NOT EXISTS sell3_price   FLOAT",
    "ALTER TABLE sim_trades ADD COLUMN IF NOT EXISTS sell3_shares  INT",
    "ALTER TABLE sim_trades ADD COLUMN IF NOT EXISTS sell3_date    VARCHAR(8)",
    "ALTER TABLE sim_trades ADD COLUMN IF NOT EXISTS sell3_done    BOOLEAN DEFAULT FALSE",
    "ALTER TABLE sim_trades ADD COLUMN IF NOT EXISTS stop_loss_pct  FLOAT DEFAULT -5.0",
    "ALTER TABLE sim_trades ADD COLUMN IF NOT EXISTS stop_loss_done BOOLEAN DEFAULT FALSE",
    # Phase B 失败计数（跨 cron 持久化）
    "ALTER TABLE sim_trades ADD COLUMN IF NOT EXISTS phase_b_fail_count INT DEFAULT 0",
    "ALTER TABLE sim_trades ADD COLUMN IF NOT EXISTS phase_b_alerted    BOOLEAN DEFAULT FALSE",
    # 需求3：仓位比例（新制=LLM决策，旧记录为NULL=固定10万制）
    "ALTER TABLE sim_trades ADD COLUMN IF NOT EXISTS invest_ratio       FLOAT",
    # 信号评分引擎新字段（v2.2 2026-04-01）
    "ALTER TABLE sim_trades ADD COLUMN IF NOT EXISTS net_buy_strength_pct FLOAT",        # 净买入强度（%市值）
    "ALTER TABLE sim_trades ADD COLUMN IF NOT EXISTS lianban_count        INT DEFAULT 0",  # 连板数
    "ALTER TABLE sim_trades ADD COLUMN IF NOT EXISTS lianban_score        FLOAT DEFAULT 0",# 连板加分
    "ALTER TABLE sim_trades ADD COLUMN IF NOT EXISTS duodao_risk          BOOLEAN DEFAULT FALSE",  # 对倒风险标记
]


def create_tables():
    """初始化建表（幂等，可重复调用），并迁移新增列"""
    try:
        from sqlalchemy import text
        engine = _get_engine()
        with engine.begin() as conn:
            conn.execute(text(_CREATE_SIM_TRADES))
            conn.execute(text(_CREATE_SIM_SUMMARY))
            conn.execute(text(_CREATE_SIM_STRATEGY))
            conn.execute(text(_CREATE_SIM_CONFIG))
            # 写入默认策略（仅在表为空时）
            existing = conn.execute(
                text("SELECT count(*) FROM sim_strategy WHERE strategy_key='default'")
            ).scalar()
            if not existing:
                conn.execute(text("""
                    INSERT INTO sim_strategy
                        (strategy_key, stop_loss_pct, sell1_pct, sell1_ratio,
                         sell2_pct, sell2_ratio, sell3_pct, description, updated_by)
                    VALUES
                        (:strategy_key, :stop_loss_pct, :sell1_pct, :sell1_ratio,
                         :sell2_pct, :sell2_ratio, :sell3_pct, :description, 'system')
                """), _DEFAULT_STRATEGY)
                logger.info("默认策略已写入 sim_strategy")
            # 新字段迁移（对已有表追加缺失列）
            for alter_sql in _ALTER_SIM_TRADES_NEW_COLS:
                try:
                    conn.execute(text(alter_sql))
                except Exception:
                    pass  # 列已存在时忽略
        logger.info("sim_trades / sim_summary / sim_strategy 表已就绪 ✅")
        # 确保 sim_trade_lessons 表也已创建
        try:
            from skills.stock_sim_robot.trade_log import ensure_table as _ensure_lessons_table
            _ensure_lessons_table()
        except Exception:
            pass  # trade_log 模块不存在时不影响主流程
        return True
    except Exception as e:
        logger.error(f"创建表失败: {e}")
        return False


def open_trade(record: dict) -> int | None:
    """
    新建一条模拟持仓记录（status=open）

    Args:
        record: 包含以下字段的字典
            trade_date, code, name, signal_score, t2_win_rate, seat_tags(str),
            list_price, list_reason, llm_reason, week_tag
            buy_amount(默认100000)

    Returns:
        新建记录的 id，失败返回 None
    """
    try:
        import numpy as np

        engine = _get_engine()
        from sqlalchemy import text

        # 设置默认值
        record.setdefault("buy_amount", 100000.0)
        record.setdefault("status", "open")
        record.setdefault("created_at", datetime.now())
        record.setdefault("updated_at", datetime.now())

        # 将 numpy 类型转换为 Python 原生类型（psycopg2 不认识 np.float64 等）
        def _to_native(v):
            if isinstance(v, (np.integer,)):
                return int(v)
            if isinstance(v, (np.floating,)):
                return None if np.isnan(v) else float(v)
            if isinstance(v, float) and (v != v):  # NaN check
                return None
            return v

        record = {k: _to_native(v) for k, v in record.items()}

        cols = ", ".join(record.keys())
        vals = ", ".join([f":{k}" for k in record.keys()])
        sql = text(f"INSERT INTO sim_trades ({cols}) VALUES ({vals}) RETURNING id")

        with engine.begin() as conn:
            result = conn.execute(sql, record)
            new_id = result.fetchone()[0]
        logger.info(f"新建模拟持仓 id={new_id}: {record.get('code')} {record.get('name')}")
        return new_id
    except Exception as e:
        logger.error(f"open_trade 失败: {e}")
        return None


def get_open_positions() -> pd.DataFrame:
    """获取所有未平仓记录（status=open）"""
    try:
        engine = _get_engine()
        from sqlalchemy import text
        sql = text("SELECT * FROM sim_trades WHERE status = 'open' ORDER BY created_at")
        df = pd.read_sql(sql, engine)
        return df
    except Exception as e:
        logger.error(f"get_open_positions 失败: {e}")
        return pd.DataFrame()


def update_buy_price(trade_id: int, buy_price: float, buy_shares: int):
    """T+1 开盘后回填买入价和股数"""
    try:
        engine = _get_engine()
        from sqlalchemy import text
        sql = text("""
            UPDATE sim_trades
            SET buy_price = :buy_price, buy_shares = :buy_shares, updated_at = CURRENT_TIMESTAMP
            WHERE id = :id
        """)
        with engine.begin() as conn:
            conn.execute(sql, {"id": trade_id, "buy_price": buy_price, "buy_shares": buy_shares})
        logger.info(f"trade_id={trade_id} 买入价更新: {buy_price}, 股数: {buy_shares}")
    except Exception as e:
        logger.error(f"update_buy_price 失败: {e}")


def close_trade(
    trade_id: int,
    sell_price: float,
    sell_date: str,
    sell_type: str,
    buy_price: float | None = None,
    buy_shares: int | None = None,
    buy_amount: float = 100000.0,
):
    """
    平仓：更新 sell_price / sell_date / sell_type / profit / profit_pct / status

    Args:
        trade_id: 记录 id
        sell_price: 卖出价
        sell_date: 卖出日期（YYYYMMDD）
        sell_type: '止盈' / '止损' / '持有到期' / '放弃买入'
        buy_price: 如果传入则用此价格计算，否则从数据库读取
        buy_shares: 同上
        buy_amount: 买入金额基数
    """
    try:
        engine = _get_engine()
        from sqlalchemy import text

        # 读取买入价（如果未传入）
        if buy_price is None or buy_shares is None:
            row = pd.read_sql(
                text("SELECT buy_price, buy_shares, buy_amount FROM sim_trades WHERE id = :id"),
                engine, params={"id": trade_id}
            )
            if row.empty:
                logger.error(f"close_trade: id={trade_id} 不存在")
                return
            buy_price  = float(row.iloc[0]["buy_price"])  if buy_price  is None else buy_price
            buy_shares = int(row.iloc[0]["buy_shares"])   if buy_shares is None else buy_shares
            buy_amount = float(row.iloc[0]["buy_amount"]) if buy_amount == 100000.0 else buy_amount

        # 计算盈亏
        if buy_price and buy_price > 0 and buy_shares and buy_shares > 0:
            actual_cost = buy_price * buy_shares
            revenue     = sell_price * buy_shares
            profit      = round(revenue - actual_cost, 2)
            profit_pct  = round((sell_price / buy_price - 1) * 100, 2)
        else:
            profit     = 0.0
            profit_pct = 0.0

        sql = text("""
            UPDATE sim_trades
            SET sell_price = :sell_price,
                sell_date  = :sell_date,
                sell_type  = :sell_type,
                profit     = :profit,
                profit_pct = :profit_pct,
                status     = 'closed',
                updated_at = CURRENT_TIMESTAMP
            WHERE id = :id
        """)
        with engine.begin() as conn:
            conn.execute(sql, {
                "id":         trade_id,
                "sell_price": sell_price,
                "sell_date":  sell_date,
                "sell_type":  sell_type,
                "profit":     profit,
                "profit_pct": profit_pct,
            })
        logger.info(
            f"trade_id={trade_id} 平仓 [{sell_type}]: 卖出价={sell_price}, "
            f"盈亏={profit:+.2f}元 ({profit_pct:+.2f}%)"
        )

        # 自动生成亏损总结（仅在亏损时触发，不阻塞主流程）
        if profit_pct < 0:
            try:
                from skills.stock_sim_robot.trade_log import auto_log_if_loss
                auto_log_if_loss(trade_id)
            except Exception as _e:
                logger.debug(f"auto_log_if_loss 非致命: {_e}")

        # 异步拉取持股期间新闻事件（不阻塞主流程）
        try:
            from modules.news_fetcher import fetch_and_classify_for_trade
            fetch_and_classify_for_trade(trade_id)
        except Exception as _e:
            logger.debug(f"fetch_and_classify_for_trade 非致命: {_e}")

    except Exception as e:
        logger.error(f"close_trade 失败: {e}")


def refresh_summary(week_tag: str | None = None):
    """
    重新计算并更新 sim_summary 表

    Args:
        week_tag: 指定周标签（如 '2026-W13'），None 则更新所有周
    """
    try:
        engine = _get_engine()
        from sqlalchemy import text

        # 查询已平仓记录
        if week_tag:
            sql = text("SELECT * FROM sim_trades WHERE status='closed' AND week_tag=:wt")
            df = pd.read_sql(sql, engine, params={"wt": week_tag})
        else:
            sql = text("SELECT * FROM sim_trades WHERE status='closed'")
            df  = pd.read_sql(sql, engine)

        if df.empty:
            logger.info("refresh_summary: 暂无已平仓记录")
            return

        for wt, grp in df.groupby("week_tag"):
            profit_vals = grp["profit_pct"].dropna()
            total_trades = int(len(grp))
            win_trades   = int((grp["profit_pct"] > 0).sum())
            lose_trades  = int((grp["profit_pct"] < 0).sum())
            win_rate     = float(round(win_trades / total_trades * 100, 1)) if total_trades > 0 else 0.0
            total_profit = float(round(grp["profit"].sum(), 2))
            avg_pct      = float(round(profit_vals.mean(), 2)) if len(profit_vals) > 0 else 0.0
            # max_profit / max_loss 存的是「单笔最大盈亏百分比」（%），不是元
            # 历史 Bug：曾错误存入 grp["profit"]（元），现已修正为 profit_pct（%）
            max_profit   = float(round(profit_vals.max(), 2)) if len(profit_vals) > 0 else 0.0
            max_loss     = float(round(profit_vals.min(), 2)) if len(profit_vals) > 0 else 0.0

            upsert = text("""
                INSERT INTO sim_summary
                    (week_tag, total_trades, win_trades, lose_trades, win_rate,
                     total_profit, avg_profit_pct, max_profit, max_loss, updated_at)
                VALUES
                    (:week_tag, :total_trades, :win_trades, :lose_trades, :win_rate,
                     :total_profit, :avg_profit_pct, :max_profit, :max_loss, CURRENT_TIMESTAMP)
                ON CONFLICT (week_tag) DO UPDATE SET
                    total_trades   = EXCLUDED.total_trades,
                    win_trades     = EXCLUDED.win_trades,
                    lose_trades    = EXCLUDED.lose_trades,
                    win_rate       = EXCLUDED.win_rate,
                    total_profit   = EXCLUDED.total_profit,
                    avg_profit_pct = EXCLUDED.avg_profit_pct,
                    max_profit     = EXCLUDED.max_profit,
                    max_loss       = EXCLUDED.max_loss,
                    updated_at     = CURRENT_TIMESTAMP
            """)
            with engine.begin() as conn:
                conn.execute(upsert, {
                    "week_tag": wt, "total_trades": total_trades,
                    "win_trades": win_trades, "lose_trades": lose_trades,
                    "win_rate": win_rate, "total_profit": total_profit,
                    "avg_profit_pct": avg_pct, "max_profit": max_profit,
                    "max_loss": max_loss,
                })
            logger.info(f"sim_summary 更新 [{wt}]: {total_trades} 笔, 胜率={win_rate}%, 总盈亏={total_profit:+.2f}元")
    except Exception as e:
        logger.error(f"refresh_summary 失败: {e}")


def get_weekly_report(week_tag: str | None = None) -> dict:
    """
    获取指定周（或本周）的汇总数据

    Args:
        week_tag: 如 '2026-W13'，None 则取本周

    Returns:
        包含 summary + trades 列表的字典
    """
    if week_tag is None:
        week_tag = _current_week_tag()

    try:
        engine = _get_engine()
        from sqlalchemy import text

        # 汇总
        sum_df = pd.read_sql(
            text("SELECT * FROM sim_summary WHERE week_tag=:wt"),
            engine, params={"wt": week_tag}
        )
        # 明细
        trade_df = pd.read_sql(
            text("SELECT * FROM sim_trades WHERE week_tag=:wt ORDER BY created_at"),
            engine, params={"wt": week_tag}
        )
        summary = sum_df.iloc[0].to_dict() if not sum_df.empty else {}
        trades  = trade_df.to_dict("records") if not trade_df.empty else []
        return {"week_tag": week_tag, "summary": summary, "trades": trades}
    except Exception as e:
        logger.error(f"get_weekly_report 失败: {e}")
        return {"week_tag": week_tag, "summary": {}, "trades": []}


def get_recent_trades(n: int = 10) -> pd.DataFrame:
    """获取最近 N 笔交易（含未平仓）"""
    try:
        engine = _get_engine()
        from sqlalchemy import text
        df = pd.read_sql(
            text("SELECT * FROM sim_trades ORDER BY created_at DESC LIMIT :n"),
            engine, params={"n": n}
        )
        return df
    except Exception as e:
        logger.error(f"get_recent_trades 失败: {e}")
        return pd.DataFrame()


def is_trade_exists_for_date(trade_date: str) -> bool:
    """检查指定 T 日是否已有交易记录（避免重复开仓）"""
    try:
        engine = _get_engine()
        from sqlalchemy import text
        row = pd.read_sql(
            text("SELECT id FROM sim_trades WHERE trade_date = :td LIMIT 1"),
            engine, params={"td": trade_date}
        )
        return not row.empty
    except Exception as e:
        logger.error(f"is_trade_exists_for_date 失败: {e}")
        return False


def _current_week_tag() -> str:
    """返回当前日期的 ISO 周标签，如 '2026-W13'"""
    today = date.today()
    iso   = today.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


# ── 总资金10万制：可用资金查询 ────────────────────────────────────────────

TOTAL_CAPITAL = 100_000.0  # 模拟账户总本金（元）


def get_available_capital(total_capital: float = TOTAL_CAPITAL) -> dict:
    """
    查询当前可用资金

    计算逻辑：
      - 已投入 = sum(open 持仓的 buy_amount)（buy_amount 在 Phase A 决策时写入，Phase B 确认后不变）
      - 可用余额 = total_capital - 已投入
      - 注意：历史旧记录 buy_amount 可能为 100000（每笔固定制），新记录为实际投入

    Returns:
        {
          "total":     100000.0,  # 总本金
          "invested":   30000.0,  # 已投入（open 持仓合计）
          "available":  70000.0,  # 可用余额
          "open_count":       1,  # 当前持仓数
          "open_positions": [    # 各持仓详情
            {"code": ..., "name": ..., "cost": ...}
          ]
        }
    """
    try:
        from sqlalchemy import text
        engine = _get_engine()
        df = pd.read_sql(
            text("SELECT id, code, name, buy_amount FROM sim_trades WHERE status='open'"),
            engine,
        )
        if df.empty:
            return {
                "total": total_capital, "invested": 0.0,
                "available": total_capital, "open_count": 0,
                "open_positions": [],
            }
        invested = float(df["buy_amount"].fillna(0).sum())
        # 防止极端历史数据导致已投入超过总本金
        invested = min(invested, total_capital)
        return {
            "total":     total_capital,
            "invested":  round(invested, 2),
            "available": round(total_capital - invested, 2),
            "open_count": len(df),
            "open_positions": [
                {
                    "code": str(r["code"]),
                    "name": str(r["name"]) if r["name"] else "",
                    "cost": float(r["buy_amount"]) if r["buy_amount"] else 0.0,
                }
                for _, r in df.iterrows()
            ],
        }
    except Exception as e:
        logger.warning(f"get_available_capital 失败，返回满额: {e}")
        return {
            "total": total_capital, "invested": 0.0,
            "available": total_capital, "open_count": 0,
            "open_positions": [],
        }


# ── 策略配置读写 ──────────────────────────────────────────────────────────

def load_strategy(strategy_key: str = "default") -> dict:
    """
    从 sim_strategy 表读取策略配置。
    如果表不存在或记录缺失，返回内置默认值（不崩溃）。

    Returns:
        dict，包含以下字段：
            stop_loss_pct  止损触发百分比（如 -5.0）
            sell1_pct      第1档触发涨幅%
            sell1_ratio    第1档卖出比例
            sell2_pct      第2档触发涨幅%
            sell2_ratio    第2档卖出比例
            sell3_pct      第3档触发涨幅%（剩余全清，ratio=1.0）
            description    策略说明
    """
    try:
        engine = _get_engine()
        from sqlalchemy import text
        row = pd.read_sql(
            text("SELECT * FROM sim_strategy WHERE strategy_key=:k"),
            engine, params={"k": strategy_key}
        )
        if not row.empty:
            r = row.iloc[0]
            return {
                "strategy_key":  strategy_key,
                "stop_loss_pct": float(r["stop_loss_pct"]),
                "sell1_pct":     float(r["sell1_pct"]),
                "sell1_ratio":   float(r["sell1_ratio"]),
                "sell2_pct":     float(r["sell2_pct"]),
                "sell2_ratio":   float(r["sell2_ratio"]),
                "sell3_pct":     float(r["sell3_pct"]),
                "description":   str(r.get("description") or ""),
                "updated_at":    str(r.get("updated_at") or ""),
                "updated_by":    str(r.get("updated_by") or ""),
            }
    except Exception as e:
        logger.warning(f"load_strategy 失败，使用默认值: {e}")
    # fallback：内置默认
    return dict(_DEFAULT_STRATEGY)


def save_strategy(strategy: dict, updated_by: str = "frontend") -> bool:
    """
    更新策略配置到 sim_strategy 表（UPSERT）

    Args:
        strategy:   包含策略字段的 dict
        updated_by: 修改来源标识（如 'frontend' / 'system'）

    Returns:
        True 表示成功
    """
    try:
        engine = _get_engine()
        from sqlalchemy import text
        sql = text("""
            INSERT INTO sim_strategy
                (strategy_key, stop_loss_pct, sell1_pct, sell1_ratio,
                 sell2_pct, sell2_ratio, sell3_pct, description,
                 updated_at, updated_by)
            VALUES
                (:strategy_key, :stop_loss_pct, :sell1_pct, :sell1_ratio,
                 :sell2_pct, :sell2_ratio, :sell3_pct, :description,
                 CURRENT_TIMESTAMP, :updated_by)
            ON CONFLICT (strategy_key) DO UPDATE SET
                stop_loss_pct = EXCLUDED.stop_loss_pct,
                sell1_pct     = EXCLUDED.sell1_pct,
                sell1_ratio   = EXCLUDED.sell1_ratio,
                sell2_pct     = EXCLUDED.sell2_pct,
                sell2_ratio   = EXCLUDED.sell2_ratio,
                sell3_pct     = EXCLUDED.sell3_pct,
                description   = EXCLUDED.description,
                updated_at    = CURRENT_TIMESTAMP,
                updated_by    = EXCLUDED.updated_by
        """)
        with engine.begin() as conn:
            conn.execute(sql, {
                "strategy_key":  strategy.get("strategy_key", "default"),
                "stop_loss_pct": float(strategy["stop_loss_pct"]),
                "sell1_pct":     float(strategy["sell1_pct"]),
                "sell1_ratio":   float(strategy["sell1_ratio"]),
                "sell2_pct":     float(strategy["sell2_pct"]),
                "sell2_ratio":   float(strategy["sell2_ratio"]),
                "sell3_pct":     float(strategy["sell3_pct"]),
                "description":   str(strategy.get("description", "")),
                "updated_by":    updated_by,
            })
        logger.info(f"策略已保存: {strategy}")
        return True
    except Exception as e:
        logger.error(f"save_strategy 失败: {e}")
        return False

# 默认三档卖出配置（用于兼容 monitor_intraday 导入 BATCH_SELL_TIERS 常量）
# 实际触发时 init_batch_sell 会从 DB 的 sim_strategy 表读取最新策略
BATCH_SELL_TIERS = [
    {"tier": 1, "pct": 3.0,  "ratio": 1/3},   # +3% 卖出 1/3
    {"tier": 2, "pct": 5.0,  "ratio": 1/3},   # +5% 再卖 1/3
    {"tier": 3, "pct": 8.0,  "ratio": 1.0},   # +8% 全清（卖出剩余全部）
]


def get_live_tiers(strategy_key: str = "default") -> list[dict]:
    """
    从 DB 读取最新策略，返回三档配置列表（格式同 BATCH_SELL_TIERS）。
    monitor_intraday 每次执行前调用此函数获取实时策略。
    """
    s = load_strategy(strategy_key)
    return [
        {"tier": 1, "pct": s["sell1_pct"], "ratio": s["sell1_ratio"]},
        {"tier": 2, "pct": s["sell2_pct"], "ratio": s["sell2_ratio"]},
        {"tier": 3, "pct": s["sell3_pct"], "ratio": 1.0},
    ]


def init_batch_sell(trade_id: int, buy_price: float, buy_shares: int,
                    strategy_key: str = "default", ahs: int = 60):
    """
    买入成交后初始化三档分批卖出计划（从 DB 读取当前策略，AHS 感知动态微调）

    分配股数规则：
      第1档 = floor(buy_shares * ratio1 / 100) * 100（100股取整）
      第2档 = floor(buy_shares * ratio2 / 100) * 100
      第3档 = buy_shares - sell1_shares - sell2_shares（剩余全部）

    AHS 感知止盈档位调整：
      AHS < 50（修复/危险期）：第1档 +2%（原+3%），第2档 +4%（原+5%）→ 更快锁利
      AHS ≥ 70（强势期）    ：第3档 +10%（原+8%） → 让利润奔跑
      其余：使用策略默认值

    Args:
        trade_id:     sim_trades 的 id
        buy_price:    买入价
        buy_shares:   买入股数
        strategy_key: 策略键（默认 'default'）
        ahs:          当前账户健康评分（0~100，默认60=中性）
    """
    try:
        # ── 实时读取策略 ──────────────────────────────────────────────
        strategy = load_strategy(strategy_key)
        tiers    = get_live_tiers(strategy_key)
        stop_pct = strategy["stop_loss_pct"]

        # ── AHS 感知微调止盈档位（不改动比例/股数，只调价格锚点）──────────────
        t1_pct = tiers[0]["pct"]  # 原第1档涨幅%
        t2_pct = tiers[1]["pct"]  # 原第2档涨幅%
        t3_pct = tiers[2]["pct"]  # 原第3档涨幅%

        if ahs < 50:
            # 修复/危险期：更快锁利，降低前两档目标
            t1_pct = min(t1_pct, 2.0)   # 第1档 ≤ +2%（原+3%）
            t2_pct = min(t2_pct, 4.0)   # 第2档 ≤ +4%（原+5%）
            logger.info(
                f"  init_batch_sell: AHS={ahs} < 50（保守期），止盈档位下调: "
                f"+{t1_pct}% / +{t2_pct}% / +{t3_pct}%"
            )
        elif ahs >= 70:
            # 强势期：让利润奔跑，第3档目标上调
            t3_pct = max(t3_pct, 10.0)  # 第3档 ≥ +10%（原+8%）
            logger.info(
                f"  init_batch_sell: AHS={ahs} >= 70（强势期），止盈档位上调: "
                f"+{t1_pct}% / +{t2_pct}% / +{t3_pct}%"
            )

        engine = _get_engine()
        from sqlalchemy import text

        # 按策略比例计算每档股数（100股取整）
        s1 = max(0, int(buy_shares * tiers[0]["ratio"] / 100) * 100)
        s2 = max(0, int(buy_shares * tiers[1]["ratio"] / 100) * 100)
        s3 = buy_shares - s1 - s2  # 剩余全部

        # 确保 s3 > 0（极少量时全部放第3档）
        if s3 <= 0:
            s1 = 0
            s2 = 0
            s3 = buy_shares

        p1 = round(buy_price * (1 + t1_pct / 100), 3)
        p2 = round(buy_price * (1 + t2_pct / 100), 3)
        p3 = round(buy_price * (1 + t3_pct / 100), 3)

        sql = text("""
            UPDATE sim_trades
            SET batch_mode    = TRUE,
                sell1_pct     = :p1pct,  sell1_price = :p1,  sell1_shares = :s1,  sell1_done = FALSE,
                sell2_pct     = :p2pct,  sell2_price = :p2,  sell2_shares = :s2,  sell2_done = FALSE,
                sell3_pct     = :p3pct,  sell3_price = :p3,  sell3_shares = :s3,  sell3_done = FALSE,
                stop_loss_pct = :slpct, stop_loss_done = FALSE,
                updated_at    = CURRENT_TIMESTAMP
            WHERE id = :id
        """)
        with engine.begin() as conn:
            conn.execute(sql, {
                "id":    trade_id,
                "p1pct": t1_pct, "p1": p1, "s1": s1,
                "p2pct": t2_pct, "p2": p2, "s2": s2,
                "p3pct": t3_pct, "p3": p3, "s3": s3,
                "slpct": stop_pct,
            })
        logger.info(
            f"trade_id={trade_id} 分批卖出计划已初始化（策略={strategy_key}，AHS={ahs}）: "
            f"档1={p1:.3f}(+{t1_pct}%,{s1}股) | "
            f"档2={p2:.3f}(+{t2_pct}%,{s2}股) | "
            f"档3={p3:.3f}(+{t3_pct}%,{s3}股) | "
            f"止损={stop_pct}%"
        )
    except Exception as e:
        logger.error(f"init_batch_sell 失败: {e}")


def mark_batch_sold(trade_id: int, tier: int, sell_date: str) -> dict | None:
    """
    标记某一档分批卖出已触发，如果3档全部完成则自动平仓

    Args:
        trade_id: 交易 id
        tier:     档次（1/2/3）
        sell_date: 触发日期（YYYYMMDD）

    Returns:
        dict 包含触发信息（price/shares/profit_pct），失败返回 None
    """
    try:
        engine = _get_engine()
        from sqlalchemy import text

        # 读取当前记录
        row = pd.read_sql(
            text("SELECT * FROM sim_trades WHERE id = :id"),
            engine, params={"id": trade_id}
        )
        if row.empty:
            logger.error(f"mark_batch_sold: id={trade_id} 不存在")
            return None

        r = row.iloc[0]
        buy_price  = float(r["buy_price"])
        buy_shares = int(r["buy_shares"])

        tier_price  = float(r[f"sell{tier}_price"])
        tier_shares = int(r[f"sell{tier}_shares"])

        # 标记本档已完成
        sql = text(f"""
            UPDATE sim_trades
            SET sell{tier}_done = TRUE,
                sell{tier}_date = :sell_date,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = :id
        """)
        with engine.begin() as conn:
            conn.execute(sql, {"id": trade_id, "sell_date": sell_date})

        profit_pct = round((tier_price / buy_price - 1) * 100, 2)
        sold_amount = round(tier_price * tier_shares, 2)

        logger.info(
            f"trade_id={trade_id} 第{tier}档触发: 价格={tier_price}, "
            f"股数={tier_shares}, 盈亏={profit_pct:+.2f}%"
        )

        # 检查是否3档全部完成 → 自动平仓
        row2 = pd.read_sql(text("SELECT * FROM sim_trades WHERE id = :id"), engine, params={"id": trade_id})
        r2 = row2.iloc[0]
        all_done = bool(r2["sell1_done"]) and bool(r2["sell2_done"]) and bool(r2["sell3_done"])

        if all_done:
            # 计算加权平均卖出价和总盈亏
            s1 = int(r2["sell1_shares"]); p1 = float(r2["sell1_price"])
            s2 = int(r2["sell2_shares"]); p2 = float(r2["sell2_price"])
            s3 = int(r2["sell3_shares"]); p3 = float(r2["sell3_price"])
            total_shares = s1 + s2 + s3
            avg_sell = round((p1*s1 + p2*s2 + p3*s3) / total_shares, 3) if total_shares > 0 else buy_price
            total_revenue = p1*s1 + p2*s2 + p3*s3
            total_cost    = buy_price * total_shares
            profit = round(total_revenue - total_cost, 2)
            profit_pct_total = round((avg_sell / buy_price - 1) * 100, 2)

            sql_close = text("""
                UPDATE sim_trades
                SET sell_price = :avg_sell,
                    sell_date  = :sell_date,
                    sell_type  = '分批卖出完成',
                    profit     = :profit,
                    profit_pct = :profit_pct,
                    status     = 'closed',
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = :id
            """)
            with engine.begin() as conn:
                conn.execute(sql_close, {
                    "id": trade_id,
                    "avg_sell": avg_sell,
                    "sell_date": sell_date,
                    "profit": profit,
                    "profit_pct": profit_pct_total,
                })
            logger.info(f"trade_id={trade_id} 3档全部完成，已自动平仓 avg_sell={avg_sell}, 盈亏={profit:+.2f}元")

        return {
            "tier":       tier,
            "price":      tier_price,
            "shares":     tier_shares,
            "sold_amount": sold_amount,
            "profit_pct": profit_pct,
            "all_done":   all_done,
        }

    except Exception as e:
        logger.error(f"mark_batch_sold 失败: {e}")
        return None


def mark_stop_loss(trade_id: int, sell_price: float, sell_date: str):
    """止损平仓（止损触发，一次性清仓）"""
    try:
        engine = _get_engine()
        from sqlalchemy import text

        row = pd.read_sql(text("SELECT * FROM sim_trades WHERE id=:id"), engine, params={"id": trade_id})
        if row.empty:
            return
        r = row.iloc[0]
        buy_price  = float(r["buy_price"])
        buy_shares = int(r["buy_shares"])

        profit = round((sell_price - buy_price) * buy_shares, 2)
        profit_pct = round((sell_price / buy_price - 1) * 100, 2)

        sql = text("""
            UPDATE sim_trades
            SET stop_loss_done = TRUE,
                sell_price  = :sell_price,
                sell_date   = :sell_date,
                sell_type   = '止损',
                profit      = :profit,
                profit_pct  = :profit_pct,
                status      = 'closed',
                updated_at  = CURRENT_TIMESTAMP
            WHERE id = :id
        """)
        with engine.begin() as conn:
            conn.execute(sql, {
                "id": trade_id,
                "sell_price": sell_price,
                "sell_date": sell_date,
                "profit": profit,
                "profit_pct": profit_pct,
            })
        logger.info(f"trade_id={trade_id} 止损平仓: 卖出价={sell_price}, 盈亏={profit:+.2f}元")

        # 自动生成亏损总结（止损一定是亏损，不阻塞主流程）
        try:
            from skills.stock_sim_robot.trade_log import auto_log_if_loss
            auto_log_if_loss(trade_id)
        except Exception as _e:
            logger.debug(f"auto_log_if_loss(止损) 非致命: {_e}")

    except Exception as e:
        logger.error(f"mark_stop_loss 失败: {e}")


def increment_phase_b_fail(trade_id: int) -> int:
    """
    Phase B 买入价获取失败时调用：累加失败计数，返回当前失败次数。

    Returns:
        更新后的 phase_b_fail_count 值
    """
    try:
        engine = _get_engine()
        from sqlalchemy import text
        with engine.begin() as conn:
            conn.execute(text("""
                UPDATE sim_trades
                SET phase_b_fail_count = COALESCE(phase_b_fail_count, 0) + 1,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = :id
            """), {"id": trade_id})
            row = conn.execute(
                text("SELECT phase_b_fail_count FROM sim_trades WHERE id = :id"),
                {"id": trade_id}
            ).fetchone()
            return row[0] if row else 0
    except Exception as e:
        logger.error(f"increment_phase_b_fail 失败: {e}")
        return 0


def mark_phase_b_alerted(trade_id: int):
    """标记该记录的 Phase B 飞书报警已发出（防止重复报警）"""
    try:
        engine = _get_engine()
        from sqlalchemy import text
        with engine.begin() as conn:
            conn.execute(text("""
                UPDATE sim_trades
                SET phase_b_alerted = TRUE,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = :id
            """), {"id": trade_id})
    except Exception as e:
        logger.error(f"mark_phase_b_alerted 失败: {e}")


def get_active_batch_positions() -> pd.DataFrame:
    """
    获取所有启用了分批卖出、且尚未全部完成的持仓（用于盘中监控轮询）。

    A 股 T+1 制度合规保护：
      - T 日（trade_date）买入的股票，最早 T+1 日才能卖出
      - 本系统语义：trade_date 是信号日，T+1 日开盘才真正买入
        所以「买入日」= T+1 = trade_date 后的第一个交易日
      - 因此：只有当 trade_date < 今天（即已经过了买入日）的持仓才能进入监控
        等价条件：今天 > T+1 日，即今天 >= T+2 日，即 _trading_days_since(trade_date) >= 2

    SQL 保护条件：trade_date < current_date（今天不是 T 日，则 T+1 日买入已成立，T+2 可卖出）
    更精确：trade_date 比今天早至少 1 个交易日，才允许进入卖出监控
    """
    try:
        engine = _get_engine()
        from sqlalchemy import text
        from datetime import date
        today_str = date.today().strftime("%Y%m%d")
        sql = text("""
            SELECT * FROM sim_trades
            WHERE status = 'open'
              AND batch_mode = TRUE
              AND buy_price IS NOT NULL
              AND buy_shares IS NOT NULL
              AND trade_date < :today
            ORDER BY created_at
        """)
        df = pd.read_sql(sql, engine, params={"today": today_str})
        if df.empty:
            return df
        # 二次过滤：确保至少经过 2 个交易日（trade_date 算 T，T+2 才能卖）
        # 即 _trading_days_since(trade_date) >= 2
        # 为避免循环调用 DB 工具，这里用简单的日期差估算
        # 精确校验留给 check_and_trigger() 内部
        return df
    except Exception as e:
        logger.error(f"get_active_batch_positions 失败: {e}")
        return pd.DataFrame()


# ══════════════════════════════════════════════════════════════════════════════
# 新增表：sim_price_log / sim_selection_log / sim_nl_strategy / sim_audit_log
# ══════════════════════════════════════════════════════════════════════════════

_CREATE_SIM_PRICE_LOG = """
CREATE TABLE IF NOT EXISTS sim_price_log (
    id          SERIAL PRIMARY KEY,
    trade_id    INT    NOT NULL,
    code        VARCHAR(6) NOT NULL,
    checked_at  TIMESTAMP NOT NULL,
    price       FLOAT  NOT NULL,           -- effective_price（涨停修正后的真实市价）
    price_raw   FLOAT,                     -- last（新浪原始 last 字段，用于对比）
    high        FLOAT,                     -- 当日最高价
    low         FLOAT,                     -- 当日最低价
    bid1        FLOAT,                     -- 买1价
    ask1        FLOAT,                     -- 卖1价
    change_pct  FLOAT,                     -- 相对买入价的涨幅%（正=涨，负=跌）
    rsi14       FLOAT,                     -- RSI(14)，按需计算（策略有指标条件时）
    macd_hist   FLOAT,                     -- MACD柱状值，按需计算
    sell1_price FLOAT,                     -- 当时档1目标价快照
    sell3_price FLOAT,                     -- 当时档3目标价快照
    stop_price  FLOAT,                     -- 当时止损价快照（buy_price * (1 + stop_loss_pct/100)）
    event       VARCHAR(32)                -- NULL=正常检查 / 止损 / 第1档 / 第2档 / 第3档
);
CREATE INDEX IF NOT EXISTS idx_price_log_trade ON sim_price_log(trade_id, checked_at);
CREATE INDEX IF NOT EXISTS idx_price_log_date  ON sim_price_log(checked_at);
"""

_CREATE_SIM_SELECTION_LOG = """
CREATE TABLE IF NOT EXISTS sim_selection_log (
    id              SERIAL PRIMARY KEY,
    trade_date      VARCHAR(8) NOT NULL,   -- T 日（龙虎榜日期，YYYYMMDD）
    trade_id        INT,                   -- 关联 sim_trades.id（入选股有值，候选无值）
    rank            INT NOT NULL,          -- 排名：1=入选，2=次优，3=第三候选，以此类推
    code            VARCHAR(6) NOT NULL,
    name            VARCHAR(20),
    signal_score    FLOAT,                 -- 综合评分（0-100）
    t2_win_rate     FLOAT,                 -- T+2历史胜率（%）
    close_price     FLOAT,                 -- T日收盘价
    reason_tech     TEXT,                  -- 技术面分析摘要
    reason_fund     TEXT,                  -- 资金面分析摘要
    reason_history  TEXT,                  -- 历史胜率分析摘要
    lianban_count   INT DEFAULT 0,          -- 连板数
    lianban_score   FLOAT DEFAULT 0,        -- 连板加分
    llm_rank_reason TEXT,                  -- LLM 对该股的排名理由（含与其他候选的对比分析）
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_selection_log_date    ON sim_selection_log(trade_date);
CREATE INDEX IF NOT EXISTS idx_selection_log_trade   ON sim_selection_log(trade_id);
"""

_CREATE_SIM_NL_STRATEGY = """
CREATE TABLE IF NOT EXISTS sim_nl_strategy (
    id              SERIAL PRIMARY KEY,
    trade_id        INT NOT NULL,          -- 绑定到具体持仓（sim_trades.id）
    nl_text         TEXT NOT NULL,         -- 自然语言策略文本（用户输入或LLM草拟）
    parsed_rules    JSONB,                 -- LLM 解析后的结构化规则 JSON（见 docs/architecture_plan.md）
    hard_stop_pct   FLOAT DEFAULT -5.0,    -- 硬兜底止损线（始终生效，不被任何规则覆盖）
    status          VARCHAR(16) DEFAULT 'draft',    -- draft / active / expired
    source          VARCHAR(16) DEFAULT 'llm_auto', -- llm_auto（LLM草拟）/ user_input（用户手动）
    confirmed_at    TIMESTAMP,             -- 人工确认时间（status改为active时记录）
    confirmed_by    VARCHAR(32),           -- 确认人（当前固定为 'user'）
    parse_error     TEXT,                  -- LLM 解析失败时的错误信息
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_nl_strategy_trade  ON sim_nl_strategy(trade_id, status);
"""

_CREATE_SIM_AUDIT_LOG = """
CREATE TABLE IF NOT EXISTS sim_audit_log (
    id              SERIAL PRIMARY KEY,
    event_date      VARCHAR(8) NOT NULL,   -- YYYYMMDD，用于按天查询和分片
    event_time      TIMESTAMP NOT NULL,    -- 精确到秒
    event_type      VARCHAR(32) NOT NULL,  -- 事件类型（见下方常量列表）
    trade_id        INT,                   -- 关联 sim_trades.id（可为 NULL）
    code            VARCHAR(6),            -- 股票代码（可为 NULL）
    name            VARCHAR(20),           -- 股票名称
    summary         TEXT NOT NULL,         -- 人类可读摘要（一句话说明发生了什么）
    detail          JSONB,                 -- 详细数据（原始数值/中间状态/决策依据等）
    result          VARCHAR(16),           -- success / failed / skipped
    operator        VARCHAR(32) DEFAULT 'robot',  -- robot / user / system
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_audit_log_date  ON sim_audit_log(event_date);
CREATE INDEX IF NOT EXISTS idx_audit_log_trade ON sim_audit_log(trade_id);
CREATE INDEX IF NOT EXISTS idx_audit_log_type  ON sim_audit_log(event_type);
CREATE INDEX IF NOT EXISTS idx_audit_log_time  ON sim_audit_log(event_time DESC);
"""

_CREATE_SIM_CANDIDATE_RESULTS = """
CREATE TABLE IF NOT EXISTS sim_candidate_results (
    id              SERIAL PRIMARY KEY,
    selection_id    INT NOT NULL,              -- FK → sim_selection_log.id
    trade_date      VARCHAR(8),               -- T 日（冗余，便于按日期查询）
    code            VARCHAR(6),
    t1_ret          FLOAT,                    -- T+1 收益%（相对 T 日收盘）
    t3_ret          FLOAT,                    -- T+3 收益%
    t5_ret          FLOAT,                    -- T+5 收益%
    t10_ret         FLOAT,                    -- T+10 收益%
    t1_close        FLOAT,                    -- T+1 收盘价
    t5_close        FLOAT,                    -- T+5 收盘价
    fetched_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(selection_id)              -- 幂等保护：同一 selection_id 只写一次
);
CREATE INDEX IF NOT EXISTS idx_cand_results_date ON sim_candidate_results(trade_date);
CREATE INDEX IF NOT EXISTS idx_cand_results_sel  ON sim_candidate_results(selection_id);
"""

_CREATE_SIM_POST_TRACKING = """
CREATE TABLE IF NOT EXISTS sim_post_tracking (
    id           SERIAL PRIMARY KEY,
    trade_id     INT NOT NULL,                -- FK → sim_trades.id
    track_day    INT NOT NULL,               -- N: 1/3/5/10/15（平仓后第N个交易日）
    track_date   VARCHAR(8),                -- 实际交易日期（跳过非交易日）
    close        FLOAT,
    high         FLOAT,
    low          FLOAT,
    volume_ratio FLOAT,                     -- 成交量/5日均量
    pct_vs_sell  FLOAT,                     -- 相对卖出价涨跌幅 %
    pct_vs_buy   FLOAT,                     -- 相对买入价涨跌幅 %
    index_pct    FLOAT,                     -- 上证指数当日涨跌幅 %
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(trade_id, track_day)
);
CREATE INDEX IF NOT EXISTS idx_post_track_trade ON sim_post_tracking(trade_id);
"""

_CREATE_SIM_NEWS_EVENTS = """
CREATE TABLE IF NOT EXISTS sim_news_events (
    id          SERIAL PRIMARY KEY,
    trade_id    INT NOT NULL,                -- FK → sim_trades.id
    code        VARCHAR(6),
    event_date  VARCHAR(8),
    title       TEXT,
    url         TEXT,
    sentiment   VARCHAR(8),                 -- positive / negative / neutral
    keywords    TEXT,                       -- JSON array，如 ["机构增持", "业绩超预期"]
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_news_trade ON sim_news_events(trade_id);
CREATE INDEX IF NOT EXISTS idx_news_date  ON sim_news_events(event_date);
"""

# 审计事件类型常量
AUDIT_PHASE_A_SELECT    = "phase_a_select"    # Phase A 选股完成
AUDIT_PHASE_A_SKIP      = "phase_a_skip"      # Phase A 无满足条件股票
AUDIT_PHASE_B_BUY       = "phase_b_buy"       # Phase B 回填买入价成功
AUDIT_PHASE_B_FAIL      = "phase_b_fail"      # Phase B 获取开盘价失败
AUDIT_MONITOR_CHECK     = "monitor_check"     # 盘中监控检查（每3分钟）
AUDIT_MONITOR_SELL      = "monitor_sell"      # 监控触发分批卖出
AUDIT_MONITOR_STOP      = "monitor_stop_loss" # 监控触发止损
AUDIT_QUOTE_FAIL        = "quote_fail"        # 行情获取失败
AUDIT_STRATEGY_CONFIRM  = "strategy_confirm"  # 用户确认 NL 策略
AUDIT_STRATEGY_DRAFT    = "strategy_draft"    # LLM 草拟策略
AUDIT_FORCE_CLOSE       = "force_close"       # 持仓超期强制平仓


def create_extended_tables():
    """
    创建扩展表：sim_price_log / sim_selection_log / sim_nl_strategy / sim_audit_log
                sim_candidate_results / sim_post_tracking / sim_news_events
    幂等操作，可重复调用。
    """
    try:
        from sqlalchemy import text
        engine = _get_engine()
        with engine.begin() as conn:
            conn.execute(text(_CREATE_SIM_PRICE_LOG))
            conn.execute(text(_CREATE_SIM_SELECTION_LOG))
            conn.execute(text(_CREATE_SIM_NL_STRATEGY))
            conn.execute(text(_CREATE_SIM_AUDIT_LOG))
            conn.execute(text(_CREATE_SIM_CANDIDATE_RESULTS))
            conn.execute(text(_CREATE_SIM_POST_TRACKING))
            conn.execute(text(_CREATE_SIM_NEWS_EVENTS))
            # Bug 1 修复：为已存在的表补加 UNIQUE 约束（幂等）
            try:
                conn.execute(text("""
                    ALTER TABLE sim_candidate_results
                    ADD CONSTRAINT IF NOT EXISTS uq_cand_sel UNIQUE (selection_id)
                """))
            except Exception:
                pass  # 约束已存在或数据库不支持 IF NOT EXISTS，忽略
        logger.info(
            "扩展表已就绪 ✅ "
            "sim_price_log / sim_selection_log / sim_nl_strategy / sim_audit_log / "
            "sim_candidate_results / sim_post_tracking / sim_news_events"
        )
        return True
    except Exception as e:
        logger.error(f"创建扩展表失败: {e}")
        return False


# ── sim_price_log CRUD ────────────────────────────────────────────────────────

def log_price_tick(
    trade_id: int,
    code: str,
    price: float,
    price_raw: float | None = None,
    high: float | None = None,
    low: float | None = None,
    bid1: float | None = None,
    ask1: float | None = None,
    change_pct: float | None = None,
    rsi14: float | None = None,
    macd_hist: float | None = None,
    sell1_price: float | None = None,
    sell3_price: float | None = None,
    stop_price: float | None = None,
    event: str | None = None,
) -> bool:
    """
    写入一条价格轨迹记录（每次 monitor 检查时调用，无论是否触发）

    Args:
        trade_id:    sim_trades.id
        code:        股票代码
        price:       effective_price（涨停修正后）
        price_raw:   last（新浪原始）
        change_pct:  相对买入价涨幅%
        event:       触发事件名称（None=正常无触发 / 止损 / 第1档 / 第2档 / 第3档）

    Returns:
        True=写入成功, False=失败（降级处理，不影响主监控流程）
    """
    try:
        from sqlalchemy import text
        engine = _get_engine()
        with engine.begin() as conn:
            conn.execute(text("""
                INSERT INTO sim_price_log
                    (trade_id, code, checked_at, price, price_raw, high, low,
                     bid1, ask1, change_pct, rsi14, macd_hist,
                     sell1_price, sell3_price, stop_price, event)
                VALUES
                    (:trade_id, :code, :checked_at, :price, :price_raw, :high, :low,
                     :bid1, :ask1, :change_pct, :rsi14, :macd_hist,
                     :sell1_price, :sell3_price, :stop_price, :event)
            """), {
                "trade_id":    int(trade_id),
                "code":        str(code),
                "checked_at":  datetime.now(),
                "price":       float(price),
                "price_raw":   float(price_raw) if price_raw is not None else None,
                "high":        float(high) if high is not None else None,
                "low":         float(low) if low is not None else None,
                "bid1":        float(bid1) if bid1 is not None else None,
                "ask1":        float(ask1) if ask1 is not None else None,
                "change_pct":  float(change_pct) if change_pct is not None else None,
                "rsi14":       float(rsi14) if rsi14 is not None else None,
                "macd_hist":   float(macd_hist) if macd_hist is not None else None,
                "sell1_price": float(sell1_price) if sell1_price is not None else None,
                "sell3_price": float(sell3_price) if sell3_price is not None else None,
                "stop_price":  float(stop_price) if stop_price is not None else None,
                "event":       str(event) if event is not None else None,
            })
        return True
    except Exception as e:
        logger.warning(f"log_price_tick 写入失败 trade_id={trade_id} code={code}: {e}")
        return False


def log_price_ticks_batch(entries: list[dict]) -> int:
    """
    批量写入 sim_price_log。

    Args:
        entries: 每条记录字段与 log_price_tick 参数一致。

    Returns:
        成功写入条数（失败时返回 0，不抛出异常）
    """
    if not entries:
        return 0

    try:
        from sqlalchemy import text
        engine = _get_engine()
        sql = text("""
            INSERT INTO sim_price_log
                (trade_id, code, checked_at, price, price_raw, high, low,
                 bid1, ask1, change_pct, rsi14, macd_hist,
                 sell1_price, sell3_price, stop_price, event)
            VALUES
                (:trade_id, :code, :checked_at, :price, :price_raw, :high, :low,
                 :bid1, :ask1, :change_pct, :rsi14, :macd_hist,
                 :sell1_price, :sell3_price, :stop_price, :event)
        """)

        params = []
        now_ts = datetime.now()
        for item in entries:
            try:
                params.append({
                    "trade_id": int(item["trade_id"]),
                    "code": str(item["code"]),
                    "checked_at": item.get("checked_at") or now_ts,
                    "price": float(item["price"]),
                    "price_raw": float(item["price_raw"]) if item.get("price_raw") is not None else None,
                    "high": float(item["high"]) if item.get("high") is not None else None,
                    "low": float(item["low"]) if item.get("low") is not None else None,
                    "bid1": float(item["bid1"]) if item.get("bid1") is not None else None,
                    "ask1": float(item["ask1"]) if item.get("ask1") is not None else None,
                    "change_pct": float(item["change_pct"]) if item.get("change_pct") is not None else None,
                    "rsi14": float(item["rsi14"]) if item.get("rsi14") is not None else None,
                    "macd_hist": float(item["macd_hist"]) if item.get("macd_hist") is not None else None,
                    "sell1_price": float(item["sell1_price"]) if item.get("sell1_price") is not None else None,
                    "sell3_price": float(item["sell3_price"]) if item.get("sell3_price") is not None else None,
                    "stop_price": float(item["stop_price"]) if item.get("stop_price") is not None else None,
                    "event": str(item["event"]) if item.get("event") is not None else None,
                })
            except Exception as _e:
                logger.debug(f"跳过非法 price_log entry: {_e}")

        if not params:
            return 0

        with engine.begin() as conn:
            conn.execute(sql, params)
        return len(params)

    except Exception as e:
        logger.warning(f"log_price_ticks_batch 写入失败，count={len(entries)}: {e}")
        return 0


def get_price_log(trade_id: int) -> pd.DataFrame:
    """获取某持仓的全部价格轨迹记录（按时间排序）"""
    try:
        from sqlalchemy import text
        engine = _get_engine()
        return pd.read_sql(
            text("SELECT * FROM sim_price_log WHERE trade_id=:id ORDER BY checked_at"),
            engine,
            params={"id": int(trade_id)},
        )
    except Exception as e:
        logger.error(f"get_price_log 失败 trade_id={trade_id}: {e}")
        return pd.DataFrame()


# ── sim_selection_log CRUD ────────────────────────────────────────────────────

def save_selection_log(entries: list[dict]) -> bool:
    """
    保存选股过程记录（入选股 + Top3候选）

    Args:
        entries: list of dict，每个 dict 包含：
            trade_date, trade_id(可选), rank, code, name, signal_score,
            t2_win_rate, close_price, reason_tech, reason_fund,
            reason_history, lianban_count, lianban_score, llm_rank_reason

    Returns:
        True=全部成功, False=有失败
    """
    if not entries:
        return True
    try:
        from sqlalchemy import text
        engine = _get_engine()
        with engine.begin() as conn:
            for e in entries:
                conn.execute(text("""
                    INSERT INTO sim_selection_log
                        (trade_date, trade_id, rank, code, name, signal_score,
                         t2_win_rate, close_price, reason_tech, reason_fund,
                         reason_history, lianban_count, lianban_score, llm_rank_reason)
                    VALUES
                        (:trade_date, :trade_id, :rank, :code, :name, :signal_score,
                         :t2_win_rate, :close_price, :reason_tech, :reason_fund,
                         :reason_history, :lianban_count, :lianban_score, :llm_rank_reason)
                """), {
                    "trade_date":      str(e.get("trade_date", "")),
                    "trade_id":        int(e["trade_id"]) if e.get("trade_id") else None,
                    "rank":            int(e.get("rank", 1)),
                    "code":            str(e.get("code", "")),
                    "name":            str(e.get("name", "")) if e.get("name") else None,
                    "signal_score":    float(e["signal_score"]) if e.get("signal_score") is not None else None,
                    "t2_win_rate":     float(e["t2_win_rate"]) if e.get("t2_win_rate") is not None else None,
                    "close_price":     float(e["close_price"]) if e.get("close_price") is not None else None,
                    "reason_tech":     str(e["reason_tech"]) if e.get("reason_tech") else None,
                    "reason_fund":     str(e["reason_fund"]) if e.get("reason_fund") else None,
                    "reason_history":  str(e["reason_history"]) if e.get("reason_history") else None,
                    "lianban_count":   int(e.get("lianban_count") or 0),
                    "lianban_score":   float(e.get("lianban_score") or 0),
                    "llm_rank_reason": str(e["llm_rank_reason"]) if e.get("llm_rank_reason") else None,
                })
        logger.info(f"save_selection_log: 写入 {len(entries)} 条选股记录 ✅")
        return True
    except Exception as e:
        logger.error(f"save_selection_log 失败: {e}")
        return False


def get_selection_log(trade_date: str | None = None, trade_id: int | None = None) -> pd.DataFrame:
    """查询选股过程记录"""
    try:
        from sqlalchemy import text
        engine = _get_engine()
        if trade_id is not None:
            sql = text("SELECT * FROM sim_selection_log WHERE trade_id=:id ORDER BY rank")
            params = {"id": int(trade_id)}
        elif trade_date is not None:
            sql = text("SELECT * FROM sim_selection_log WHERE trade_date=:d ORDER BY rank")
            params = {"d": str(trade_date)}
        else:
            sql = text("SELECT * FROM sim_selection_log ORDER BY created_at DESC LIMIT 50")
            params = {}
        return pd.read_sql(sql, engine, params=params)
    except Exception as e:
        logger.error(f"get_selection_log 失败: {e}")
        return pd.DataFrame()


# ── sim_candidate_results CRUD ────────────────────────────────────────────────

def save_candidate_results(entries: list[dict]) -> bool:
    """
    保存候选股 T+N 实际收益（幂等：已存在则跳过）

    每个 entry 需包含:
        selection_id, trade_date, code,
        t1_ret, t3_ret, t5_ret, t10_ret, t1_close, t5_close
    """
    if not entries:
        return True
    try:
        from sqlalchemy import text
        engine = _get_engine()
        with engine.begin() as conn:
            for e in entries:
                conn.execute(text("""
                    INSERT INTO sim_candidate_results
                        (selection_id, trade_date, code,
                         t1_ret, t3_ret, t5_ret, t10_ret, t1_close, t5_close)
                    VALUES
                        (:selection_id, :trade_date, :code,
                         :t1_ret, :t3_ret, :t5_ret, :t10_ret, :t1_close, :t5_close)
                    ON CONFLICT DO NOTHING
                """), {
                    "selection_id": e.get("selection_id"),
                    "trade_date":   e.get("trade_date"),
                    "code":         e.get("code"),
                    "t1_ret":       e.get("t1_ret"),
                    "t3_ret":       e.get("t3_ret"),
                    "t5_ret":       e.get("t5_ret"),
                    "t10_ret":      e.get("t10_ret"),
                    "t1_close":     e.get("t1_close"),
                    "t5_close":     e.get("t5_close"),
                })
        return True
    except Exception as e:
        logger.error(f"save_candidate_results 失败: {e}")
        return False


def get_candidate_results(trade_date: str) -> pd.DataFrame:
    """
    查询某天所有候选股的 T+N 收益（JOIN sim_selection_log 以获取评分和排名）
    """
    try:
        from sqlalchemy import text
        engine = _get_engine()
        sql = text("""
            SELECT
                sl.id AS selection_id, sl.trade_date, sl.code, sl.name,
                sl.rank, sl.signal_score, sl.lianban_count,
                sl.reason_tech, sl.reason_fund, sl.llm_rank_reason,
                sl.trade_id,
                cr.t1_ret, cr.t3_ret, cr.t5_ret, cr.t10_ret,
                cr.t1_close, cr.t5_close
            FROM sim_selection_log sl
            LEFT JOIN sim_candidate_results cr ON cr.selection_id = sl.id
            WHERE sl.trade_date = :d
            ORDER BY sl.rank ASC
        """)
        return pd.read_sql(sql, engine, params={"d": str(trade_date)})
    except Exception as e:
        logger.error(f"get_candidate_results 失败: {e}")
        return pd.DataFrame()


def get_pending_candidate_dates() -> list[str]:
    """
    返回 sim_selection_log 中还有候选股未填充 T+5 收益的日期列表
    用于 daily_update step10 批量补全
    """
    try:
        from sqlalchemy import text
        engine = _get_engine()
        sql = text("""
            SELECT DISTINCT sl.trade_date
            FROM sim_selection_log sl
            LEFT JOIN sim_candidate_results cr ON cr.selection_id = sl.id
            WHERE cr.id IS NULL
              AND sl.trade_date <= TO_CHAR(CURRENT_DATE - INTERVAL '5 days', 'YYYYMMDD')
            ORDER BY sl.trade_date
        """)
        rows = pd.read_sql(sql, engine)
        return rows["trade_date"].tolist() if not rows.empty else []
    except Exception as e:
        logger.error(f"get_pending_candidate_dates 失败: {e}")
        return []


# ── sim_post_tracking CRUD ────────────────────────────────────────────────────

def save_post_tracking(entries: list[dict]) -> bool:
    """
    保存平仓后跟踪数据（幂等：UNIQUE(trade_id, track_day)）

    每个 entry 需包含:
        trade_id, track_day, track_date, close, high, low,
        volume_ratio, pct_vs_sell, pct_vs_buy, index_pct
    """
    if not entries:
        return True
    try:
        from sqlalchemy import text
        engine = _get_engine()
        with engine.begin() as conn:
            for e in entries:
                conn.execute(text("""
                    INSERT INTO sim_post_tracking
                        (trade_id, track_day, track_date, close, high, low,
                         volume_ratio, pct_vs_sell, pct_vs_buy, index_pct)
                    VALUES
                        (:trade_id, :track_day, :track_date, :close, :high, :low,
                         :volume_ratio, :pct_vs_sell, :pct_vs_buy, :index_pct)
                    ON CONFLICT (trade_id, track_day) DO NOTHING
                """), {
                    "trade_id":     e.get("trade_id"),
                    "track_day":    e.get("track_day"),
                    "track_date":   e.get("track_date"),
                    "close":        e.get("close"),
                    "high":         e.get("high"),
                    "low":          e.get("low"),
                    "volume_ratio": e.get("volume_ratio"),
                    "pct_vs_sell":  e.get("pct_vs_sell"),
                    "pct_vs_buy":   e.get("pct_vs_buy"),
                    "index_pct":    e.get("index_pct"),
                })
        return True
    except Exception as e:
        logger.error(f"save_post_tracking 失败: {e}")
        return False


def get_post_tracking(trade_id: int) -> pd.DataFrame:
    """查询某笔交易的平仓后跟踪数据"""
    try:
        from sqlalchemy import text
        engine = _get_engine()
        sql = text(
            "SELECT * FROM sim_post_tracking WHERE trade_id=:id ORDER BY track_day"
        )
        return pd.read_sql(sql, engine, params={"id": int(trade_id)})
    except Exception as e:
        logger.error(f"get_post_tracking 失败: {e}")
        return pd.DataFrame()


def get_pending_post_tracking_trades() -> pd.DataFrame:
    """
    返回需要继续跟踪的已平仓交易（sim_post_tracking 记录数 < 5）
    用于 daily_update step9 批量补全
    """
    try:
        from sqlalchemy import text
        engine = _get_engine()
        sql = text("""
            SELECT t.id AS trade_id, t.code, t.name,
                   t.buy_price, t.sell_price, t.sell_date,
                   COUNT(pt.id) AS tracked_days
            FROM sim_trades t
            LEFT JOIN sim_post_tracking pt ON pt.trade_id = t.id
            WHERE t.status = 'closed'
              AND t.sell_date IS NOT NULL
              AND t.sell_date <= TO_CHAR(CURRENT_DATE - INTERVAL '1 day', 'YYYYMMDD')
            GROUP BY t.id, t.code, t.name, t.buy_price, t.sell_price, t.sell_date
            HAVING COUNT(pt.id) < 5
            ORDER BY t.sell_date DESC
            LIMIT 100
        """)
        return pd.read_sql(sql, engine)
    except Exception as e:
        logger.error(f"get_pending_post_tracking_trades 失败: {e}")
        return pd.DataFrame()


# ── sim_news_events CRUD ──────────────────────────────────────────────────────

def save_news_events(entries: list[dict]) -> bool:
    """
    保存持股期间新闻事件

    每个 entry 需包含:
        trade_id, code, event_date, title, url, sentiment, keywords
    """
    if not entries:
        return True
    try:
        from sqlalchemy import text
        engine = _get_engine()
        with engine.begin() as conn:
            for e in entries:
                conn.execute(text("""
                    INSERT INTO sim_news_events
                        (trade_id, code, event_date, title, url, sentiment, keywords)
                    VALUES
                        (:trade_id, :code, :event_date, :title, :url, :sentiment, :keywords)
                """), {
                    "trade_id":   e.get("trade_id"),
                    "code":       e.get("code"),
                    "event_date": e.get("event_date"),
                    "title":      e.get("title"),
                    "url":        e.get("url"),
                    "sentiment":  e.get("sentiment"),
                    "keywords":   e.get("keywords"),
                })
        return True
    except Exception as e:
        logger.error(f"save_news_events 失败: {e}")
        return False


def get_news_events(trade_id: int) -> pd.DataFrame:
    """查询某笔交易持股期间的新闻事件"""
    try:
        from sqlalchemy import text
        engine = _get_engine()
        sql = text(
            "SELECT * FROM sim_news_events WHERE trade_id=:id ORDER BY event_date"
        )
        return pd.read_sql(sql, engine, params={"id": int(trade_id)})
    except Exception as e:
        logger.error(f"get_news_events 失败: {e}")
        return pd.DataFrame()


# ── sim_nl_strategy CRUD ──────────────────────────────────────────────────────

def save_nl_strategy(
    trade_id: int,
    nl_text: str,
    parsed_rules: dict | None = None,
    hard_stop_pct: float = -5.0,
    source: str = "llm_auto",
    parse_error: str | None = None,
) -> int | None:
    """
    保存一条自然语言策略（默认 status='draft'）

    Returns:
        新记录的 id，失败返回 None
    """
    import json
    try:
        from sqlalchemy import text
        engine = _get_engine()
        with engine.begin() as conn:
            result = conn.execute(text("""
                INSERT INTO sim_nl_strategy
                    (trade_id, nl_text, parsed_rules, hard_stop_pct, status, source, parse_error)
                VALUES
                    (:trade_id, :nl_text, :parsed_rules, :hard_stop_pct, 'draft', :source, :parse_error)
                RETURNING id
            """), {
                "trade_id":      int(trade_id),
                "nl_text":       str(nl_text),
                "parsed_rules":  json.dumps(parsed_rules, ensure_ascii=False) if parsed_rules else None,
                "hard_stop_pct": float(hard_stop_pct),
                "source":        str(source),
                "parse_error":   str(parse_error) if parse_error else None,
            })
            new_id = result.scalar()
        logger.info(f"save_nl_strategy: trade_id={trade_id} → strategy_id={new_id} ✅")
        return new_id
    except Exception as e:
        logger.error(f"save_nl_strategy 失败 trade_id={trade_id}: {e}")
        return None


def confirm_nl_strategy(strategy_id: int, confirmed_by: str = "user") -> bool:
    """
    人工确认 NL 策略：将 status 改为 'active'，同时将同一 trade_id 的其他 active 策略置为 expired

    Args:
        strategy_id: sim_nl_strategy.id
        confirmed_by: 确认人（默认 'user'）

    Returns:
        True=成功
    """
    try:
        from sqlalchemy import text
        engine = _get_engine()
        with engine.begin() as conn:
            # 先查出 trade_id
            row = conn.execute(
                text("SELECT trade_id FROM sim_nl_strategy WHERE id=:id"),
                {"id": int(strategy_id)},
            ).fetchone()
            if not row:
                logger.warning(f"confirm_nl_strategy: strategy_id={strategy_id} 不存在")
                return False
            trade_id = row[0]
            # 将该 trade 下的已有 active 策略置为 expired
            conn.execute(text("""
                UPDATE sim_nl_strategy
                SET status='expired', updated_at=CURRENT_TIMESTAMP
                WHERE trade_id=:trade_id AND status='active'
            """), {"trade_id": int(trade_id)})
            # 激活目标策略
            conn.execute(text("""
                UPDATE sim_nl_strategy
                SET status='active', confirmed_at=CURRENT_TIMESTAMP,
                    confirmed_by=:confirmed_by, updated_at=CURRENT_TIMESTAMP
                WHERE id=:id
            """), {"id": int(strategy_id), "confirmed_by": str(confirmed_by)})
        logger.info(f"confirm_nl_strategy: strategy_id={strategy_id} 已激活 ✅")
        return True
    except Exception as e:
        logger.error(f"confirm_nl_strategy 失败 strategy_id={strategy_id}: {e}")
        return False


def get_active_nl_strategy(trade_id: int) -> dict | None:
    """
    获取某持仓当前 active 的 NL 策略

    Returns:
        dict（含 parsed_rules 已反序列化）或 None
    """
    import json
    try:
        from sqlalchemy import text
        engine = _get_engine()
        row = pd.read_sql(
            text("SELECT * FROM sim_nl_strategy WHERE trade_id=:id AND status='active' ORDER BY confirmed_at DESC LIMIT 1"),
            engine,
            params={"id": int(trade_id)},
        )
        if row.empty:
            return None
        rec = row.iloc[0].to_dict()
        # 反序列化 parsed_rules
        if rec.get("parsed_rules") and isinstance(rec["parsed_rules"], str):
            try:
                rec["parsed_rules"] = json.loads(rec["parsed_rules"])
            except Exception:
                pass
        return rec
    except Exception as e:
        logger.error(f"get_active_nl_strategy 失败 trade_id={trade_id}: {e}")
        return None


def get_nl_strategies_for_trade(trade_id: int) -> pd.DataFrame:
    """获取某持仓的所有 NL 策略（按时间倒序）"""
    try:
        from sqlalchemy import text
        engine = _get_engine()
        return pd.read_sql(
            text("SELECT * FROM sim_nl_strategy WHERE trade_id=:id ORDER BY created_at DESC"),
            engine,
            params={"id": int(trade_id)},
        )
    except Exception as e:
        logger.error(f"get_nl_strategies_for_trade 失败: {e}")
        return pd.DataFrame()


# ── sim_audit_log CRUD ────────────────────────────────────────────────────────

def write_audit_log(
    event_type: str,
    summary: str,
    trade_id: int | None = None,
    code: str | None = None,
    name: str | None = None,
    detail: dict | None = None,
    result: str = "success",
    operator: str = "robot",
    event_time: datetime | None = None,
) -> bool:
    """
    写入一条审计日志记录

    Args:
        event_type: 事件类型（使用 AUDIT_* 常量）
        summary:    人类可读摘要（一句话）
        trade_id:   关联持仓 ID（可为 None）
        code:       股票代码（可为 None）
        name:       股票名称（可为 None）
        detail:     详细数据 dict（会序列化为 JSONB）
        result:     success / failed / skipped
        operator:   robot / user / system
        event_time: 事件时间（默认为当前时间）

    Returns:
        True=写入成功，False=失败（降级，不影响主流程）

    使用示例：
        write_audit_log(
            event_type=AUDIT_MONITOR_SELL,
            summary="分批卖出第1档触发：002361 神剑股份 @15.00 涨幅+10.0%",
            trade_id=42,
            code="002361",
            name="神剑股份",
            detail={
                "tier": 1, "price": 15.0, "target_price": 14.1,
                "change_pct": 10.0, "profit_pct": 10.0, "shares": 300,
            },
        )
    """
    import json
    now = event_time or datetime.now()
    event_date = now.strftime("%Y%m%d")

    try:
        from sqlalchemy import text
        engine = _get_engine()
        with engine.begin() as conn:
            conn.execute(text("""
                INSERT INTO sim_audit_log
                    (event_date, event_time, event_type, trade_id, code, name,
                     summary, detail, result, operator)
                VALUES
                    (:event_date, :event_time, :event_type, :trade_id, :code, :name,
                     :summary, :detail, :result, :operator)
            """), {
                "event_date": event_date,
                "event_time": now,
                "event_type": str(event_type),
                "trade_id":   int(trade_id) if trade_id is not None else None,
                "code":       str(code) if code else None,
                "name":       str(name) if name else None,
                "summary":    str(summary),
                "detail":     json.dumps(detail, ensure_ascii=False, default=str) if detail else None,
                "result":     str(result),
                "operator":   str(operator),
            })
        return True
    except Exception as e:
        # 审计日志写入失败不应影响主监控流程，只记录 WARNING
        logger.warning(f"write_audit_log 失败 [{event_type}] {summary[:50]}: {e}")
        return False


def get_audit_logs(
    trade_id: int | None = None,
    event_date: str | None = None,
    event_type: str | None = None,
    limit: int = 100,
) -> pd.DataFrame:
    """
    查询审计日志（按时间倒序）

    Args:
        trade_id:   按持仓过滤
        event_date: 按日期过滤（YYYYMMDD）
        event_type: 按事件类型过滤
        limit:      最多返回行数

    Returns:
        DataFrame，按 event_time 倒序排列
    """
    try:
        from sqlalchemy import text
        engine = _get_engine()
        conditions = []
        params: dict = {"limit": limit}
        if trade_id is not None:
            conditions.append("trade_id = :trade_id")
            params["trade_id"] = int(trade_id)
        if event_date is not None:
            conditions.append("event_date = :event_date")
            params["event_date"] = str(event_date)
        if event_type is not None:
            conditions.append("event_type = :event_type")
            params["event_type"] = str(event_type)
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        sql = text(f"SELECT * FROM sim_audit_log {where} ORDER BY event_time DESC LIMIT :limit")
        return pd.read_sql(sql, engine, params=params)
    except Exception as e:
        logger.error(f"get_audit_logs 失败: {e}")
        return pd.DataFrame()


# ── 更新 create_tables 以包含扩展表 ──────────────────────────────────────────

_ORIGINAL_CREATE_TABLES = create_tables  # 保存原函数引用


def create_tables():
    """
    初始化所有表（核心表 + 扩展表），幂等操作，可重复调用。
    包含：sim_trades / sim_summary / sim_strategy + 新字段迁移
          + sim_price_log / sim_selection_log / sim_nl_strategy / sim_audit_log
    """
    ok1 = _ORIGINAL_CREATE_TABLES()
    ok2 = create_extended_tables()
    ok3 = create_trade_events_table()
    return ok1 and ok2 and ok3


# ══════════════════════════════════════════════════════════════════════════════
# sim_trade_events：交易流水表（每次买入/加仓/分批卖出/止损/平仓各一条独立记录）
# ══════════════════════════════════════════════════════════════════════════════

_CREATE_SIM_TRADE_EVENTS = """
CREATE TABLE IF NOT EXISTS sim_trade_events (
    id          SERIAL PRIMARY KEY,
    trade_id    INT    NOT NULL,           -- 关联 sim_trades.id
    code        VARCHAR(6)  NOT NULL,      -- 股票代码
    name        VARCHAR(20),               -- 股票名称
    event_type  VARCHAR(20) NOT NULL,      -- BUY / SELL_T1 / SELL_T2 / SELL_T3 / STOP_LOSS / EXPIRED / FORCE_CLOSE
    event_date  VARCHAR(8),                -- YYYYMMDD
    event_time  TIMESTAMP,                 -- 精确时间（监控触发时有值）
    price       FLOAT  NOT NULL,           -- 成交价/参考价
    shares      INT    NOT NULL,           -- 股数（买入为正，卖出为正股数）
    amount      FLOAT,                     -- 金额（price × shares）
    tier        INT,                       -- 分批档次（1/2/3，买入时为0）
    profit      FLOAT,                     -- 本次操作盈亏（元，卖出时有值）
    profit_pct  FLOAT,                     -- 本次操作盈亏%（相对买入价）
    source      VARCHAR(32),               -- 行情来源（sina_realtime 等）
    note        TEXT,                      -- 备注（如"买入价回填"/"止损触发"）
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_trade_events_trade ON sim_trade_events(trade_id, event_type);
CREATE INDEX IF NOT EXISTS idx_trade_events_date  ON sim_trade_events(event_date);
"""


def create_trade_events_table() -> bool:
    """创建 sim_trade_events 表（幂等）"""
    try:
        from sqlalchemy import text
        engine = _get_engine()
        with engine.begin() as conn:
            conn.execute(text(_CREATE_SIM_TRADE_EVENTS))
        logger.info("sim_trade_events 表已就绪 ✅")
        return True
    except Exception as e:
        logger.error(f"创建 sim_trade_events 表失败: {e}")
        return False


def log_trade_event(
    trade_id: int,
    code: str,
    name: str,
    event_type: str,
    price: float,
    shares: int,
    event_date: str | None = None,
    event_time=None,
    tier: int | None = None,
    profit: float | None = None,
    profit_pct: float | None = None,
    source: str | None = None,
    note: str | None = None,
) -> bool:
    """
    写入一条交易流水记录

    event_type 枚举：
      BUY         - 买入（Phase B 回填买入价时写入）
      SELL_T1     - 第1档分批卖出（盘中监控触发）
      SELL_T2     - 第2档分批卖出
      SELL_T3     - 第3档分批卖出（通常即全部卖出）
      STOP_LOSS   - 止损清仓
      EXPIRED     - 持有到期平仓（T+2收盘）
      FORCE_CLOSE - 超时强制平仓（超过 FORCE_CLOSE_DAYS）

    Returns:
        True=写入成功，False=失败（降级，不影响主流程）
    """
    from datetime import date as _date, datetime as _datetime
    now = _datetime.now()
    try:
        from sqlalchemy import text
        engine = _get_engine()
        with engine.begin() as conn:
            conn.execute(text("""
                INSERT INTO sim_trade_events
                    (trade_id, code, name, event_type, event_date, event_time,
                     price, shares, amount, tier, profit, profit_pct, source, note)
                VALUES
                    (:trade_id, :code, :name, :event_type, :event_date, :event_time,
                     :price, :shares, :amount, :tier, :profit, :profit_pct, :source, :note)
            """), {
                "trade_id":   int(trade_id),
                "code":       str(code),
                "name":       str(name) if name else None,
                "event_type": str(event_type),
                "event_date": str(event_date) if event_date else _date.today().strftime("%Y%m%d"),
                "event_time": event_time or now,
                "price":      float(price),
                "shares":     int(shares),
                "amount":     round(float(price) * int(shares), 2),
                "tier":       int(tier) if tier is not None else None,
                "profit":     round(float(profit), 2) if profit is not None else None,
                "profit_pct": round(float(profit_pct), 4) if profit_pct is not None else None,
                "source":     str(source) if source else None,
                "note":       str(note) if note else None,
            })
        return True
    except Exception as e:
        logger.warning(f"log_trade_event 失败 trade_id={trade_id} [{event_type}]: {e}")
        return False


def get_trade_events(trade_id: int | None = None, event_date: str | None = None,
                     limit: int = 200) -> pd.DataFrame:
    """
    查询交易流水记录（按时间倒序）

    Args:
        trade_id:   按持仓过滤（None=全部）
        event_date: 按日期过滤（YYYYMMDD，None=全部）
        limit:      最多返回条数

    Returns:
        DataFrame，按 event_time / created_at 倒序
    """
    try:
        from sqlalchemy import text
        engine = _get_engine()
        conditions = []
        params: dict = {"limit": limit}
        if trade_id is not None:
            conditions.append("trade_id = :trade_id")
            params["trade_id"] = int(trade_id)
        if event_date is not None:
            conditions.append("event_date = :event_date")
            params["event_date"] = str(event_date)
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        sql = text(
            f"SELECT * FROM sim_trade_events {where} "
            f"ORDER BY COALESCE(event_time, created_at) DESC LIMIT :limit"
        )
        return pd.read_sql(sql, engine, params=params)
    except Exception as e:
        logger.error(f"get_trade_events 失败: {e}")
        return pd.DataFrame()


# ══════════════════════════════════════════════════════════════════════════════
# 反馈调节框架：绩效统计 / 账户健康评分 / 熔断检查
# ══════════════════════════════════════════════════════════════════════════════

def get_performance_stats(lookback_trades: int = 10) -> dict:
    """
    返回近期实战绩效统计，作为仓位决策和熔断检查的数据基础。

    Returns:
        {
          "total_closed":       int,    # 历史总结算笔数
          "recent_trades":      int,    # 近 N 笔数量（可能 < lookback）
          "recent_win_rate":    float,  # 近 N 笔胜率（%）
          "recent_avg_pnl":     float,  # 近 N 笔平均盈亏（%）
          "recent_total_pnl":   float,  # 近 N 笔累计盈亏（%，基于 TOTAL_CAPITAL）
          "today_pnl":          float,  # 今日已结算盈亏（%，相对 TOTAL_CAPITAL）
          "week_pnl":           float,  # 本周累计盈亏（元→%，来自 sim_summary）
          "month_pnl":          float,  # 本月累计盈亏（%）
          "max_drawdown_pct":   float,  # 近 N 笔中峰谷最大回撤（%）
          "consecutive_losses": int,    # 当前连续亏损笔数
          "consecutive_wins":   int,    # 当前连续盈利笔数
          "last_trade_pnl":     float,  # 最近一笔盈亏（%）
        }
    """
    _empty = {
        "total_closed": 0, "recent_trades": 0,
        "recent_win_rate": 50.0, "recent_avg_pnl": 0.0,
        "recent_total_pnl": 0.0, "today_pnl": 0.0,
        "week_pnl": 0.0, "month_pnl": 0.0,
        "max_drawdown_pct": 0.0, "consecutive_losses": 0,
        "consecutive_wins": 0,
        "last_trade_pnl": 0.0,
    }
    try:
        from sqlalchemy import text as _text
        engine = _get_engine()
        today_str = date.today().strftime("%Y%m%d")

        # ── 历史总结算笔数 ──────────────────────────────────────────
        total_row = pd.read_sql(
            _text("SELECT count(*) AS cnt FROM sim_trades WHERE status='closed'"), engine
        )
        total_closed = int(total_row.iloc[0]["cnt"]) if not total_row.empty else 0

        if total_closed == 0:
            return _empty

        # ── 近 N 笔已结算交易 ────────────────────────────────────────
        recent_df = pd.read_sql(
            _text("SELECT profit_pct, profit, sell_date, updated_at "
                  "FROM sim_trades WHERE status='closed' "
                  "ORDER BY updated_at DESC LIMIT :n"),
            engine, params={"n": lookback_trades}
        )
        recent_trades = len(recent_df)
        if recent_trades == 0:
            return {**_empty, "total_closed": total_closed}

        pnl_vals = recent_df["profit_pct"].fillna(0.0).astype(float).tolist()

        recent_win_rate  = round(sum(1 for p in pnl_vals if p > 0) / recent_trades * 100, 1)
        recent_avg_pnl   = round(sum(pnl_vals) / recent_trades, 2)
        recent_total_pnl = round(sum(pnl_vals), 2)  # 各笔 pnl 之和（非复利）

        last_trade_pnl   = round(pnl_vals[0], 2) if pnl_vals else 0.0

        # ── 连续亏损笔数 ─────────────────────────────────────────────
        consecutive_losses = 0
        for p in pnl_vals:
            if p < 0:
                consecutive_losses += 1
            else:
                break

        # ── 连续盈利笔数 ─────────────────────────────────────────────
        consecutive_wins = 0
        for p in pnl_vals:
            if p > 0:
                consecutive_wins += 1
            else:
                break

        # ── 近 N 笔最大回撤（峰谷法，基于累计盈亏曲线）─────────────
        cumulative = 0.0
        peak = 0.0
        max_dd = 0.0
        for p in reversed(pnl_vals):  # 从最早到最近
            cumulative += p
            if cumulative > peak:
                peak = cumulative
            dd = peak - cumulative
            if dd > max_dd:
                max_dd = dd
        max_drawdown_pct = round(-max_dd, 2)  # 负数表示回撤

        # ── 今日盈亏（元→%） ─────────────────────────────────────────
        today_df = pd.read_sql(
            _text("SELECT sum(profit) AS total_profit "
                  "FROM sim_trades WHERE status='closed' AND sell_date=:d"),
            engine, params={"d": today_str}
        )
        today_profit = float(today_df.iloc[0]["total_profit"] or 0) if not today_df.empty else 0.0
        today_pnl = round(today_profit / TOTAL_CAPITAL * 100, 2)

        # ── 本周盈亏（来自 sim_summary，已有汇总）───────────────────
        cur_week = _current_week_tag()
        week_df  = pd.read_sql(
            _text("SELECT total_profit FROM sim_summary WHERE week_tag=:wt"),
            engine, params={"wt": cur_week}
        )
        week_profit = float(week_df.iloc[0]["total_profit"] or 0) if not week_df.empty else 0.0
        week_pnl    = round(week_profit / TOTAL_CAPITAL * 100, 2)

        # ── 本月盈亏 ─────────────────────────────────────────────────
        month_prefix = today_str[:6]  # YYYYMM
        month_df = pd.read_sql(
            _text("SELECT sum(profit) AS mp FROM sim_trades "
                  "WHERE status='closed' AND sell_date LIKE :prefix"),
            engine, params={"prefix": f"{month_prefix}%"}
        )
        month_profit = float(month_df.iloc[0]["mp"] or 0) if not month_df.empty else 0.0
        month_pnl    = round(month_profit / TOTAL_CAPITAL * 100, 2)

        return {
            "total_closed":       total_closed,
            "recent_trades":      recent_trades,
            "recent_win_rate":    recent_win_rate,
            "recent_avg_pnl":     recent_avg_pnl,
            "recent_total_pnl":   recent_total_pnl,
            "today_pnl":          today_pnl,
            "week_pnl":           week_pnl,
            "month_pnl":          month_pnl,
            "max_drawdown_pct":   max_drawdown_pct,
            "consecutive_losses": consecutive_losses,
            "consecutive_wins":   consecutive_wins,
            "last_trade_pnl":     last_trade_pnl,
        }
    except Exception as e:
        logger.error(f"get_performance_stats 失败: {e}")
        return _empty


def calc_account_health_score(stats: dict | None = None) -> int:
    """
    基于近期绩效统计，计算账户健康评分 AHS（0~100）。

    数据不足时（<5 笔）返回中性值 60，既不激进也不保守。

    评分组成（基础 50 分）：
      近10笔胜率（±25分）：≥70%→+25；60~70→+15；50~60→+5；40~50→-5；30~40→-15；<30→-25
      近10笔平均盈亏（±15分）：>2%→+15；>1%→+10；>0%→+5；>-1%→-5；>-2%→-10；≤-2%→-15
      连续亏损惩罚（最多-20分）：1笔→0；2笔→-5；3笔→-10；4+笔→-20
      今日盈亏修正（±10分）：>+1%→+10；0~1%→+5；-1~0%→-5；<-1%→-10

    Returns:
        int，范围 0~100
    """
    if stats is None:
        stats = get_performance_stats()

    # 数据不足时返回中性值
    if stats.get("total_closed", 0) < 5:
        return 60

    score = 50  # 基础分

    # ── 近10笔胜率贡献 ──────────────────────────────────────────────
    wr = stats.get("recent_win_rate", 50.0)
    if wr >= 70:   score += 25
    elif wr >= 60: score += 15
    elif wr >= 50: score += 5
    elif wr >= 40: score -= 5
    elif wr >= 30: score -= 15
    else:          score -= 25

    # ── 近10笔平均盈亏贡献 ───────────────────────────────────────────
    avg = stats.get("recent_avg_pnl", 0.0)
    if avg > 2.0:   score += 15
    elif avg > 1.0: score += 10
    elif avg > 0.0: score += 5
    elif avg > -1.0: score -= 5
    elif avg > -2.0: score -= 10
    else:            score -= 15

    # ── 连续亏损惩罚 ────────────────────────────────────────────────
    cl = stats.get("consecutive_losses", 0)
    if cl >= 4:   score -= 20
    elif cl == 3: score -= 10
    elif cl == 2: score -= 5
    # cl <= 1 → 0 分惩罚

    # ── 今日盈亏紧急修正 ────────────────────────────────────────────
    today = stats.get("today_pnl", 0.0)
    if today > 1.0:    score += 10
    elif today > 0.0:  score += 5
    elif today > -1.0: score -= 5
    else:              score -= 10

    return max(0, min(100, score))


# ── 运行模式常量 ────────────────────────────────────────────────────────────
_TRADING_MODES = ("normal", "cautious", "alert", "crisis")

# 四档模式对应的仓位系数/入场门槛/每日建仓上限
_MODE_CONFIG: dict[str, dict] = {
    "normal":   {"position_coef": 1.0, "min_score": 50, "max_daily_open": 5, "allow_add": True,  "color": "green",  "label": "🟢 正常"},
    "cautious": {"position_coef": 0.7, "min_score": 60, "max_daily_open": 3, "allow_add": True,  "color": "yellow", "label": "🟡 谨慎"},
    "alert":    {"position_coef": 0.4, "min_score": 70, "max_daily_open": 1, "allow_add": False, "color": "orange", "label": "🟠 警戒"},
    "crisis":   {"position_coef": 0.2, "min_score": 80, "max_daily_open": 1, "allow_add": False, "color": "red",    "label": "🔴 危机"},
}

_CONFIG_KEY_MODE = "trading_mode"


def get_trading_mode() -> str:
    """
    从 sim_config 表读取当前运行模式。
    读取失败或无记录时返回 'normal'。
    """
    try:
        from sqlalchemy import text as _text
        engine = _get_engine()
        row = pd.read_sql(
            _text("SELECT config_value FROM sim_config WHERE config_key=:k"),
            engine, params={"k": _CONFIG_KEY_MODE}
        )
        if not row.empty:
            val = str(row.iloc[0]["config_value"]).strip()
            if val in _TRADING_MODES:
                return val
    except Exception as e:
        logger.debug(f"get_trading_mode 读取失败（使用 normal 默认值）: {e}")
    return "normal"


def set_trading_mode(mode: str, reason: str = "") -> bool:
    """
    持久化写入运行模式到 sim_config 表（UPSERT）。

    Args:
        mode:   "normal" / "cautious" / "alert" / "crisis"
        reason: 变更原因（仅写日志，不入库）

    Returns:
        True 写入成功，False 失败
    """
    if mode not in _TRADING_MODES:
        logger.error(f"set_trading_mode: 无效模式 '{mode}'，允许值: {_TRADING_MODES}")
        return False
    try:
        from sqlalchemy import text as _text
        engine = _get_engine()
        with engine.begin() as conn:
            conn.execute(_text("""
                INSERT INTO sim_config (config_key, config_value, updated_at)
                VALUES (:k, :v, NOW())
                ON CONFLICT (config_key)
                DO UPDATE SET config_value=EXCLUDED.config_value, updated_at=NOW()
            """), {"k": _CONFIG_KEY_MODE, "v": mode})
        label = _MODE_CONFIG[mode]["label"]
        logger.info(f"✅ 运行模式已切换 → {label}（{reason}）")
        return True
    except Exception as e:
        logger.error(f"set_trading_mode 写入失败: {e}")
        return False


def _ahs_to_mode(ahs: int) -> str:
    """
    基于 AHS 单独决定模式（当无法从 stats 做升降级判断时使用）。
      80~100 → normal
      65~79  → normal（轻微保守，但不切换模式）
      50~64  → cautious
      35~49  → alert
      0~34   → crisis
    """
    if ahs >= 50:   return "normal"
    elif ahs >= 35: return "cautious"
    elif ahs >= 20: return "alert"
    else:           return "crisis"


def calc_position_size(
    score: int,
    ahs: int,
    mode: str,
    total_capital: float = 100_000,
) -> float:
    """
    按 4档模式 + 信号评分计算本次建仓金额（元）。

    公式：
      base          = total_capital × 10%（单笔基准）
      score_mult    = 1.5 若 score >= 85；1.2 若 score >= 75；1.0 否则
      mode_coef     = {normal:1.0, cautious:0.7, alert:0.4, crisis:0.2}
      result        = base × score_mult × mode_coef
      上限约束       = min(result, total_capital × 12%)

    危机模式 + 85分+信号 → 特殊：允许 coef × 1.5（最多不超过 alert 系数）

    Args:
        score:         信号评分 0~100
        ahs:           账户健康评分 0~100
        mode:          运行模式 "normal"/"cautious"/"alert"/"crisis"
        total_capital: 总资金（默认10万）

    Returns:
        建仓金额（元），已按上限截断
    """
    base = total_capital * 0.10
    score_mult = 1.5 if score >= 85 else (1.2 if score >= 75 else 1.0)
    cfg   = _MODE_CONFIG.get(mode, _MODE_CONFIG["normal"])
    coef  = cfg["position_coef"]

    # 危机/警戒模式下 85分+信号享受 ×1.5 弹性（不超过 alert 上限 0.4）
    if mode == "crisis" and score >= 85:
        coef = min(coef * 1.5, _MODE_CONFIG["alert"]["position_coef"])

    result = base * score_mult * coef
    capped = min(result, total_capital * 0.12)
    logger.info(
        f"calc_position_size: score={score} ahs={ahs} mode={mode} "
        f"→ base={base:.0f}×{score_mult}×{coef}={result:.0f} (上限={capped:.0f}元)"
    )
    return round(capped, 2)


def check_circuit_breaker(stats: dict | None = None) -> dict:
    """
    分级降速熔断检查（v2：4档运行模式，替代原二元停车逻辑）。

    人工覆盖：设置环境变量 SKIP_CIRCUIT_BREAKER=1 可临时绕过所有熔断。

    四档模式（从宽到严）：
      normal  → 正常：仓位100%，门槛50分，每日最多5只
      cautious→ 谨慎：仓位70%，门槛60分，每日最多3只
      alert   → 警戒：仓位40%，门槛70分，每日最多1只
      crisis  → 危机：仓位20%，门槛80分，每日最多1只（但80+信号仍可参与）

    升级触发条件（变严）：
      normal  → cautious: 连亏3笔 OR 单日亏>-2% OR 周亏>-3%
      cautious→ alert:    连亏5笔 OR 周亏>-5% OR AHS<45
      alert   → crisis:   连亏7笔 OR 月亏>-8% OR 近10笔最大回撤>-12%

    自动恢复条件（变宽）：
      连赢2笔  → 自动升一级（变宽松）

    向后兼容：
      allow_open = True  （4档模式下始终允许开仓，由 min_score 过滤替代）
      旧代码判断 if not cb["allow_open"] 不会触发（保留向后兼容）

    Returns:
        {
          "allow_open":    bool,   # 向后兼容，4档模式下始终 True
          "mode":          str,    # "normal"/"cautious"/"alert"/"crisis"
          "mode_label":    str,    # "🟢 正常" 等带颜色的显示文本
          "position_coef": float,  # 仓位系数 1.0/0.7/0.4/0.2
          "min_score":     int,    # 最低入场评分门槛
          "max_daily_open":int,    # 每日最多开仓只数
          "allow_add":     bool,   # 是否允许加仓
          "reason":        str,    # 说明文本（模式变化原因）
          "level":         str,    # "ok"/"warning"/"halt"（向后兼容）
          "ahs":           int,    # 同步计算的 AHS 值
          "stats":         dict,   # 绩效统计快照
        }
    """
    import os
    # 人工覆盖开关
    if os.getenv("SKIP_CIRCUIT_BREAKER") == "1":
        logger.warning("⚠️  SKIP_CIRCUIT_BREAKER=1，熔断检查已跳过（人工覆盖）")
        cfg = _MODE_CONFIG["normal"]
        return {
            "allow_open": True, "mode": "normal", "mode_label": cfg["label"],
            "position_coef": cfg["position_coef"], "min_score": cfg["min_score"],
            "max_daily_open": cfg["max_daily_open"], "allow_add": cfg["allow_add"],
            "reason": "", "level": "ok", "ahs": 60, "stats": {},
        }

    if stats is None:
        stats = get_performance_stats()

    ahs = calc_account_health_score(stats)

    today_pnl  = stats.get("today_pnl",  0.0)
    week_pnl   = stats.get("week_pnl",   0.0)
    month_pnl  = stats.get("month_pnl",  0.0)
    cl         = stats.get("consecutive_losses", 0)
    cw         = stats.get("consecutive_wins", 0)
    max_dd     = stats.get("max_drawdown_pct", 0.0)
    total_closed = stats.get("total_closed", 0)

    # ── 读取当前持久化模式 ──────────────────────────────────────────────
    current_mode = get_trading_mode()

    # ── 自动恢复：连赢2笔 → 升一级（变宽松）─────────────────────────────
    if cw >= 2 and current_mode != "normal":
        mode_list = list(_TRADING_MODES)
        cur_idx   = mode_list.index(current_mode)
        new_mode  = mode_list[max(0, cur_idx - 1)]
        if new_mode != current_mode:
            set_trading_mode(new_mode, f"连赢{cw}笔自动恢复")
            current_mode = new_mode
            logger.info(f"🔼 模式自动恢复：→ {current_mode}（连赢{cw}笔）")

    # ── 升级判断（变严），只向严格方向移动 ────────────────────────────────
    target_mode = current_mode

    # normal → cautious
    if current_mode == "normal":
        if cl >= 3 or today_pnl < -2.0 or week_pnl < -3.0:
            target_mode = "cautious"

    # → alert（从 normal/cautious 都可触发）
    if current_mode in ("normal", "cautious") and target_mode in ("normal", "cautious"):
        if cl >= 5 or week_pnl < -5.0 or ahs < 45:
            target_mode = "alert"

    # → crisis（从任何模式都可触发）
    if target_mode != "crisis":
        if cl >= 7 or month_pnl < -8.0 or (total_closed >= 5 and max_dd < -12.0):
            target_mode = "crisis"

    # 写入模式变化
    if target_mode != current_mode:
        reason_parts = []
        if cl >= 7:              reason_parts.append(f"连亏{cl}笔")
        elif cl >= 5:            reason_parts.append(f"连亏{cl}笔")
        elif cl >= 3:            reason_parts.append(f"连亏{cl}笔")
        if today_pnl < -2.0:    reason_parts.append(f"单日亏{today_pnl:.1f}%")
        if week_pnl  < -3.0:    reason_parts.append(f"周亏{week_pnl:.1f}%")
        if month_pnl < -8.0:    reason_parts.append(f"月亏{month_pnl:.1f}%")
        if ahs < 45:             reason_parts.append(f"AHS={ahs}")
        if max_dd < -12.0:       reason_parts.append(f"最大回撤{max_dd:.1f}%")
        trigger_reason = "、".join(reason_parts) or "自动降档"
        set_trading_mode(target_mode, trigger_reason)
        current_mode = target_mode

    cfg = _MODE_CONFIG[current_mode]

    # ── 构建说明文本 ────────────────────────────────────────────────────
    if current_mode == "normal":
        reason = ""
        level  = "ok"
    elif current_mode == "cautious":
        reason = (
            f"谨慎模式（仓位70%，门槛60分）— "
            f"连亏{cl}笔 / 今日{today_pnl:+.1f}% / 周{week_pnl:+.1f}%"
        )
        level = "warning"
    elif current_mode == "alert":
        reason = (
            f"警戒模式（仓位40%，门槛70分）— "
            f"连亏{cl}笔 / AHS={ahs} / 周{week_pnl:+.1f}%"
        )
        level = "warning"
    else:  # crisis
        reason = (
            f"危机模式（仓位20%，门槛80分）— "
            f"连亏{cl}笔 / 月{month_pnl:+.1f}% / 最大回撤{max_dd:.1f}%；"
            f"80分+信号仍可参与"
        )
        level = "halt"

    if current_mode == "normal":
        logger.info(
            f"✅ 分级检查通过 mode=normal AHS={ahs} "
            f"今日={today_pnl:+.1f}% 本周={week_pnl:+.1f}% 本月={month_pnl:+.1f}% "
            f"连亏={cl}笔 连赢={cw}笔"
        )
    else:
        logger.warning(
            f"⚡ 运行模式 [{current_mode}] {cfg['label']} | "
            f"AHS={ahs} 仓位={cfg['position_coef']*100:.0f}% 门槛={cfg['min_score']}分 | {reason}"
        )

    return {
        "allow_open":    True,
        "mode":          current_mode,
        "mode_label":    cfg["label"],
        "position_coef": cfg["position_coef"],
        "min_score":     cfg["min_score"],
        "max_daily_open":cfg["max_daily_open"],
        "allow_add":     cfg["allow_add"],
        "reason":        reason,
        "level":         level,
        "ahs":           ahs,
        "stats":         stats,
    }


if __name__ == "__main__":
    # 测试：初始化建表
    logging_ok = create_tables()
    create_trade_events_table()
    print(f"建表结果: {logging_ok}")
