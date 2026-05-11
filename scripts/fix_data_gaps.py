"""
修复服务器数据库缺失的表和种子数据

执行:
  python3 scripts/fix_data_gaps.py
  python3 scripts/fix_data_gaps.py --dry-run  # 仅检查不执行
"""
import sys
from pathlib import Path
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import psycopg2
import argparse
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

PG_CONFIG = {
    "host": "117.72.184.8",
    "user": "lhb_admin",
    "password": "lhb_quant_2024",
    "dbname": "lhb_quant",
}


def get_conn():
    conn = psycopg2.connect(**PG_CONFIG)
    conn.autocommit = True
    return conn


def fix_all(dry_run=False):
    conn = get_conn()
    cur = conn.cursor()

    # ================================================================
    # 1. 创建 sim_robots 表 (strategy_comparison.py 等页面依赖)
    # ================================================================
    logger.info("=== Step 1: 创建 sim_robots 表 ===")
    sql_sim_robots = """
    CREATE TABLE IF NOT EXISTS sim_robots (
        id                SERIAL PRIMARY KEY,
        robot_id          VARCHAR(50) UNIQUE NOT NULL,
        robot_name        VARCHAR(100) NOT NULL,
        status            VARCHAR(20) DEFAULT 'draft',
        stock_source      VARCHAR(50) DEFAULT 'longhubang',
        select_strategy   VARCHAR(50) DEFAULT 'lianban_multi_factor',
        execute_strategy  VARCHAR(50) DEFAULT 'lianban_multi_factor',
        initial_capital   FLOAT DEFAULT 100000,
        position_ratio    FLOAT DEFAULT 0.1,
        max_positions     INTEGER DEFAULT 3,
        max_holding_days  INTEGER DEFAULT 7,
        stop_loss_pct     FLOAT DEFAULT -0.5,
        stop_profit_pct   FLOAT DEFAULT NULL,
        enable_batch_buy  BOOLEAN DEFAULT false,
        cost_config       JSONB DEFAULT '{}',
        strategy_params   JSONB DEFAULT '{}',
        parent_robot_id   VARCHAR(50) DEFAULT NULL,
        eliminate_reason  TEXT,
        final_score       FLOAT,
        created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        started_at        TIMESTAMP,
        ended_at          TIMESTAMP,
        updated_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """
    if not dry_run:
        cur.execute(sql_sim_robots)
        logger.info("  sim_robots 表已创建")
    else:
        logger.info("  [DRY-RUN] sim_robots 表创建")

    # ================================================================
    # 2. 创建 strategy_registry 表
    # ================================================================
    logger.info("=== Step 2: 创建 strategy_registry 表 ===")
    sql_strategy_registry = """
    CREATE TABLE IF NOT EXISTS strategy_registry (
        id              SERIAL PRIMARY KEY,
        strategy_key    VARCHAR(50) UNIQUE NOT NULL,
        strategy_name   VARCHAR(100) NOT NULL,
        category        VARCHAR(50) DEFAULT 'longhubang',
        description     TEXT,
        params_schema   JSONB DEFAULT '{}',
        is_active       BOOLEAN DEFAULT true,
        created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """
    if not dry_run:
        cur.execute(sql_strategy_registry)
        logger.info("  strategy_registry 表已创建")
    else:
        logger.info("  [DRY-RUN] strategy_registry 表创建")

    # ================================================================
    # 3. 插入默认策略到 strategies 表 (backend robots 使用)
    # ================================================================
    logger.info("=== Step 3: 插入默认策略 ===")
    sql_strategies = """
    INSERT INTO strategies (name, description, category, entry_rules, exit_rules, risk_rules, params) VALUES
    (
        '龙虎榜高分策略',
        '基于龙虎榜综合评分的选股策略，选择评分>=75的标的',
        'LHB',
        '[
            {"type": "score_above", "field": "score", "value": 75, "desc": "综合评分>=75"},
            {"type": "net_buy_positive", "field": "l_net", "value": 0, "desc": "龙虎榜净买入>0"},
            {"type": "turnover_range", "field": "turnover_rate", "min": 0.03, "max": 0.25, "desc": "换手率3%-25%"},
            {"type": "market_cap_range", "field": "float_market_cap", "min": 200000, "max": 10000000, "desc": "流通市值20亿-1000亿"}
        ]'::jsonb,
        '[
            {"type": "take_profit", "value": 0.15, "desc": "止盈15%"},
            {"type": "stop_loss", "value": -0.07, "desc": "止损7%"},
            {"type": "max_hold_days", "value": 10, "desc": "最大持仓10天"},
            {"type": "trailing_stop", "value": 0.05, "desc": "从最高点回撤5%止盈"}
        ]'::jsonb,
        '[
            {"type": "max_positions", "value": 5, "desc": "最大持仓5只"},
            {"type": "single_position_pct", "value": 0.20, "desc": "单票仓位<=20%"},
            {"type": "daily_loss_limit", "value": -0.05, "desc": "单日亏损熔断5%"},
            {"type": "sentiment_filter", "min_level": "COLD", "desc": "极寒行情不开仓"}
        ]'::jsonb,
        '{"lookback_days": 20, "score_weights": {"tech": 0.40, "fund": 0.35, "hist": 0.25}, "prefer_industries": [], "exclude_st": true, "exclude_new_stock_days": 60}'::jsonb
    ),
    (
        '机构席位跟踪策略',
        '跟踪机构专用席位净买入标的，适合中线持有',
        'LHB',
        '[
            {"type": "inst_net_positive", "field": "inst_net_amount", "value": 500, "desc": "机构净买入>=500万"},
            {"type": "inst_count", "field": "inst_buy_count", "value": 2, "desc": ">=2家机构买入"},
            {"type": "score_above", "field": "score", "value": 65, "desc": "综合评分>=65"}
        ]'::jsonb,
        '[
            {"type": "take_profit", "value": 0.20, "desc": "止盈20%"},
            {"type": "stop_loss", "value": -0.08, "desc": "止损8%"},
            {"type": "max_hold_days", "value": 20, "desc": "最大持仓20天"}
        ]'::jsonb,
        '[
            {"type": "max_positions", "value": 3, "desc": "最大持仓3只"},
            {"type": "single_position_pct", "value": 0.30, "desc": "单票仓位<=30%"},
            {"type": "daily_loss_limit", "value": -0.05, "desc": "单日亏损熔断5%"}
        ]'::jsonb,
        '{"lookback_days": 30, "score_weights": {"tech": 0.30, "fund": 0.45, "hist": 0.25}, "prefer_industries": ["半导体", "新能源", "医药生物"], "exclude_st": true, "exclude_new_stock_days": 90, "min_market_cap": 500000}'::jsonb
    ),
    (
        '游资打板策略',
        '跟踪知名游资席位涨停板标的，短线快进快出',
        'LHB',
        '[
            {"type": "is_limit_up", "value": true, "desc": "当日涨停"},
            {"type": "hot_money_seat", "value": true, "desc": "知名游资席位买入"},
            {"type": "score_above", "field": "score", "value": 70, "desc": "综合评分>=70"},
            {"type": "turnover_range", "field": "turnover_rate", "min": 0.05, "max": 0.30, "desc": "换手率5%-30%"},
            {"type": "market_cap_range", "field": "float_market_cap", "min": 100000, "max": 5000000, "desc": "流通市值10亿-500亿"}
        ]'::jsonb,
        '[
            {"type": "take_profit", "value": 0.10, "desc": "止盈10%"},
            {"type": "stop_loss", "value": -0.05, "desc": "止损5%"},
            {"type": "max_hold_days", "value": 3, "desc": "最大持仓3天"},
            {"type": "open_limit_sell", "value": true, "desc": "次日未封板则卖出"}
        ]'::jsonb,
        '[
            {"type": "max_positions", "value": 3, "desc": "最大持仓3只"},
            {"type": "single_position_pct", "value": 0.25, "desc": "单票仓位<=25%"},
            {"type": "daily_loss_limit", "value": -0.06, "desc": "单日亏损熔断6%"},
            {"type": "sentiment_filter", "min_level": "NEUTRAL", "desc": "情绪低于中性不开仓"}
        ]'::jsonb,
        '{"lookback_days": 10, "score_weights": {"tech": 0.45, "fund": 0.35, "hist": 0.20}, "prefer_industries": [], "exclude_st": true, "exclude_new_stock_days": 30, "hot_money_list": ["赵老哥", "章盟主", "炒股养家", "小鳄鱼"]}'::jsonb
    )
    ON CONFLICT DO NOTHING
    """
    if not dry_run:
        cur.execute(sql_strategies)
        cur.execute("SELECT COUNT(*) FROM strategies")
        cnt = cur.fetchone()[0]
        logger.info(f"  strategies: {cnt} 条策略")
    else:
        logger.info("  [DRY-RUN] 插入默认策略")

    # ================================================================
    # 4. 插入默认机器人到 robots 表
    # ================================================================
    logger.info("=== Step 4: 插入默认机器人到 robots 表 ===")
    if not dry_run:
        # 先查 strategy_id
        cur.execute("SELECT id FROM strategies WHERE name = '龙虎榜高分策略' LIMIT 1")
        strat_row = cur.fetchone()
        if strat_row:
            strategy_id = strat_row[0]
            cur.execute("""
                INSERT INTO robots (name, description, strategy_id, initial_capital, current_capital, cash, total_assets, status, max_positions, per_position_pct, risk_config)
                SELECT
                    '默认模拟机器人',
                    '基于龙虎榜高分策略的模拟交易机器人，初始资金100万',
                    %s,
                    1000000.00,
                    1000000.00,
                    1000000.00,
                    1000000.00,
                    'IDLE',
                    5,
                    0.20,
                    '{"stop_loss_pct": -0.07, "take_profit_pct": 0.15, "daily_loss_limit": -0.05, "trailing_stop_pct": 0.05, "max_hold_days": 10, "score_threshold": 75, "max_single_loss": -0.10, "circuit_break_daily_pct": -0.08}'::jsonb
                WHERE NOT EXISTS (SELECT 1 FROM robots WHERE name = '默认模拟机器人')
            """, (strategy_id,))
            cur.execute("SELECT COUNT(*) FROM robots")
            cnt = cur.fetchone()[0]
            logger.info(f"  robots: {cnt} 个机器人")
        else:
            logger.error("  策略未找到，无法创建机器人")
    else:
        logger.info("  [DRY-RUN] 插入默认机器人")

    # ================================================================
    # 5. 插入默认机器人到 sim_robots 表
    # ================================================================
    logger.info("=== Step 5: 插入默认机器人到 sim_robots 表 ===")
    if not dry_run:
        cur.execute("""
            INSERT INTO sim_robots (robot_id, robot_name, strategy_key, initial_capital, status, description)
            VALUES ('default', '默认策略机器人', 'default', 100000.0, 'active', '默认主板龙虎榜策略')
            ON CONFLICT (robot_id) DO NOTHING
        """)
        cur.execute("SELECT COUNT(*) FROM sim_robots")
        cnt = cur.fetchone()[0]
        logger.info(f"  sim_robots: {cnt} 个机器人")

        # 插入策略注册表
        cur.execute("""
            INSERT INTO strategy_registry (strategy_key, strategy_name, category, description)
            VALUES ('default', '默认龙虎榜策略', 'longhubang', '基于龙虎榜净买额和评分的默认选股策略')
            ON CONFLICT (strategy_key) DO NOTHING
        """)
    else:
        logger.info("  [DRY-RUN] 插入 sim_robots 种子数据")

    # ================================================================
    # 6. 检查 sim_trades 是否有 robot_id 列
    # ================================================================
    logger.info("=== Step 6: 检查 sim_trades 表结构 ===")
    cur.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name='sim_trades' AND column_name='robot_id'
    """)
    if not cur.fetchone():
        logger.info("  sim_trades 缺少 robot_id 列，正在添加")
        if not dry_run:
            cur.execute("ALTER TABLE sim_trades ADD COLUMN IF NOT EXISTS robot_id VARCHAR(32) DEFAULT 'default'")
    else:
        logger.info("  sim_trades.robot_id 列已存在")

    # ================================================================
    # 总结
    # ================================================================
    logger.info("=== 检查结果 ===")
    checks = [
        ("sim_robots", "SELECT COUNT(*) FROM sim_robots"),
        ("strategy_registry", "SELECT COUNT(*) FROM strategy_registry"),
        ("strategies", "SELECT COUNT(*) FROM strategies"),
        ("robots", "SELECT COUNT(*) FROM robots"),
    ]
    for name, sql in checks:
        try:
            cur.execute(sql)
            cnt = cur.fetchone()[0]
            logger.info(f"  {name}: {cnt} rows {'✅' if cnt > 0 else '⚠️'}")
        except Exception as e:
            logger.error(f"  {name}: ERROR {e}")

    cur.close()
    conn.close()
    logger.info("修复完成!")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="仅检查不执行")
    args = parser.parse_args()
    fix_all(dry_run=args.dry_run)
