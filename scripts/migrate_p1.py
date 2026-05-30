"""
P1 数据库迁移脚本

创建内容：
1. sim_robots 机器人管理表
2. cash_ledger 现金流水表
3. strategy_registry 策略注册表
4. 预置默认策略
5. 创建默认机器人
"""
import sys
import logging
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from utils.storage import storage
from sqlalchemy import text

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
logger = logging.getLogger(__name__)


def run():
    engine = storage._get_engine()
    
    logger.info("🚀 P1 数据库迁移开始")
    
    with engine.begin() as conn:
        # === Step 1: 创建 sim_robots 表 ===
        logger.info("\n=== Step 1: 创建 sim_robots 表 ===")
        conn.execute(text('''
            CREATE TABLE IF NOT EXISTS sim_robots (
                id                SERIAL PRIMARY KEY,
                robot_id          VARCHAR(50) UNIQUE NOT NULL,
                robot_name        VARCHAR(100) NOT NULL,
                
                -- 状态: draft/running/paused/ended/eliminated
                status            VARCHAR(20) DEFAULT 'draft',
                
                -- 来源配置
                stock_source      VARCHAR(50) DEFAULT 'longhubang',
                select_strategy   VARCHAR(50) DEFAULT 'lianban_multi_factor',
                execute_strategy  VARCHAR(50) DEFAULT 'lianban_multi_factor',
                
                -- 资金配置
                initial_capital   FLOAT DEFAULT 100000,
                position_ratio     FLOAT DEFAULT 0.1,
                max_positions     INTEGER DEFAULT 3,
                
                -- 风控配置
                max_holding_days  INTEGER DEFAULT 7,
                stop_loss_pct     FLOAT DEFAULT -0.5,
                stop_profit_pct   FLOAT DEFAULT NULL,
                enable_batch_buy  BOOLEAN DEFAULT false,
                
                -- 交易成本配置（可自定义覆盖默认值）
                cost_config       JSONB DEFAULT '{}',
                
                -- 策略参数
                strategy_params   JSONB DEFAULT '{}',
                
                -- 继承关系
                parent_robot_id   VARCHAR(50) DEFAULT NULL,
                
                -- 淘汰相关
                eliminate_reason  TEXT,
                final_score       FLOAT,
                
                -- 时间
                created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                started_at        TIMESTAMP,
                ended_at          TIMESTAMP,
                updated_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        '''))
        logger.info("  ✅ sim_robots 表已创建")
        
        # === Step 2: 创建 cash_ledger 表 ===
        logger.info("\n=== Step 2: 创建 cash_ledger 表 ===")
        conn.execute(text('''
            CREATE TABLE IF NOT EXISTS cash_ledger (
                id              SERIAL PRIMARY KEY,
                robot_id        VARCHAR(50) NOT NULL,
                trade_date      VARCHAR(10),
                
                -- 流水类型: INIT/BUY/SELL/DIVIDEND/FEE/TRANSFER_IN/TRANSFER_OUT
                tx_type         VARCHAR(20) NOT NULL,
                
                -- 金额（正数=入账，负数=出账）
                amount          FLOAT NOT NULL,
                
                -- 关联信息
                trade_id        INTEGER,
                stock_code      VARCHAR(10),
                stock_name      VARCHAR(20),
                
                -- 余额快照（流水后的余额）
                balance_after   FLOAT NOT NULL,
                
                -- 说明
                memo            TEXT,
                
                created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        '''))
        logger.info("  ✅ cash_ledger 表已创建")
        
        # 创建索引
        conn.execute(text('''
            CREATE INDEX IF NOT EXISTS idx_cash_ledger_robot 
            ON cash_ledger(robot_id, created_at)
        '''))
        logger.info("  ✅ 索引已创建")
        
        # === Step 3: 创建 strategy_registry 表 ===
        logger.info("\n=== Step 3: 创建 strategy_registry 表 ===")
        conn.execute(text('''
            CREATE TABLE IF NOT EXISTS strategy_registry (
                id              SERIAL PRIMARY KEY,
                strategy_id     VARCHAR(50) UNIQUE NOT NULL,
                strategy_name   VARCHAR(100) NOT NULL,
                
                -- 策略类型: select/execute/both
                strategy_type   VARCHAR(20) DEFAULT 'both',
                
                -- Python 模块路径
                module_path     VARCHAR(200) NOT NULL,
                
                -- 策略说明
                description     TEXT,
                
                -- 默认参数
                default_params  JSONB DEFAULT '{}',
                
                -- 是否支持分批建仓
                support_batch   BOOLEAN DEFAULT false,
                
                is_active       BOOLEAN DEFAULT true,
                created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        '''))
        logger.info("  ✅ strategy_registry 表已创建")
        
        # === Step 4: 预置默认策略 ===
        logger.info("\n=== Step 4: 预置默认策略 ===")
        conn.execute(text('''
            INSERT INTO strategy_registry 
            (strategy_id, strategy_name, strategy_type, module_path, description, support_batch)
            VALUES 
            ('lianban_multi_factor', '龙虎榜多因子策略', 'both',
             'skills.stock_sim_robot.modules.strategy_lianban',
             '基于龙虎榜数据，多因子评分选股，T+1买入，支持止损/止盈/到期平仓',
             false)
            ON CONFLICT (strategy_id) DO NOTHING
        '''))
        logger.info("  ✅ 龙虎榜策略已注册")
        
        # === Step 5: 创建默认机器人 ===
        logger.info("\n=== Step 5: 创建默认机器人 ===")
        conn.execute(text('''
            INSERT INTO sim_robots 
            (robot_id, robot_name, status, initial_capital, position_ratio, max_positions,
             max_holding_days, stop_loss_pct)
            VALUES ('default', '默认机器人（历史数据）', 'running', 100000, 1.0, 10, 7, -0.5)
            ON CONFLICT (robot_id) DO NOTHING
        '''))
        logger.info("  ✅ 默认机器人已创建")
        
        # === Step 6: 补录现金流水 ===
        logger.info("\n=== Step 6: 补录现金流水 ===")
        
        # 检查是否已有流水
        result = conn.execute(text('''
            SELECT COUNT(*) as cnt FROM cash_ledger WHERE robot_id = 'default'
        ''')).fetchone()
        
        if result and result[0] == 0:
            # 初始资金
            conn.execute(text('''
                INSERT INTO cash_ledger 
                (robot_id, tx_type, amount, balance_after, trade_date, memo)
                VALUES ('default', 'INIT', 100000, 100000, '20240101', '初始资金')
            '''))
            logger.info("  ✅ 初始资金流水已补录")
        else:
            logger.info("  ✓ 已有流水记录，跳过")
    
    logger.info("\n🎉 P1 迁移全部完成")


if __name__ == '__main__':
    run()
