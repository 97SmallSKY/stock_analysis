"""
P2 数据库迁移脚本

创建内容：
1. sim_daily_summary 每日净值快照表
2. benchmark_daily 基准指数行情表
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
    
    logger.info("🚀 P2 数据库迁移开始")
    
    with engine.begin() as conn:
        # === Step 1: 创建 sim_daily_summary 表 ===
        logger.info("\n=== Step 1: 创建 sim_daily_summary 表 ===")
        conn.execute(text('''
            CREATE TABLE IF NOT EXISTS sim_daily_summary (
                id              SERIAL PRIMARY KEY,
                robot_id        VARCHAR(50) NOT NULL,
                trade_date      VARCHAR(10) NOT NULL,
                
                -- 资产快照
                cash            FLOAT NOT NULL DEFAULT 0,
                holding_value   FLOAT NOT NULL DEFAULT 0,
                total_value     FLOAT NOT NULL DEFAULT 0,
                nav             FLOAT NOT NULL DEFAULT 1.0,
                
                -- 当日变动
                daily_return    FLOAT DEFAULT 0,
                daily_return_pct FLOAT DEFAULT 0,
                
                -- 累计指标
                cumulative_return_pct FLOAT DEFAULT 0,
                max_drawdown_pct      FLOAT DEFAULT 0,
                
                -- 持仓明细快照（JSON）
                holdings_snapshot JSONB DEFAULT '[]',
                
                -- 交易成本累计
                total_fees_cumulative FLOAT DEFAULT 0,
                
                created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                
                UNIQUE(robot_id, trade_date)
            )
        '''))
        logger.info("  ✅ sim_daily_summary 表已创建")
        
        # 创建索引
        conn.execute(text('''
            CREATE INDEX IF NOT EXISTS idx_daily_summary_robot_date 
            ON sim_daily_summary(robot_id, trade_date DESC)
        '''))
        logger.info("  ✅ 索引已创建")
        
        # === Step 2: 创建 benchmark_daily 表 ===
        logger.info("\n=== Step 2: 创建 benchmark_daily 表 ===")
        conn.execute(text('''
            CREATE TABLE IF NOT EXISTS benchmark_daily (
                id              SERIAL PRIMARY KEY,
                index_code      VARCHAR(20) NOT NULL,
                index_name      VARCHAR(50),
                trade_date      VARCHAR(10) NOT NULL,
                close_price     FLOAT NOT NULL,
                change_pct      FLOAT DEFAULT 0,
                nav             FLOAT DEFAULT 1.0,
                
                UNIQUE(index_code, trade_date)
            )
        '''))
        logger.info("  ✅ benchmark_daily 表已创建")
        
        # 创建索引
        conn.execute(text('''
            CREATE INDEX IF NOT EXISTS idx_benchmark_date 
            ON benchmark_daily(trade_date DESC)
        '''))
        logger.info("  ✅ 索引已创建")
        
        # === Step 3: 预置基准指数代码 ===
        logger.info("\n=== Step 3: 预置基准指数配置 ===")
        # 这里的实际数据由 daily_settlement 拉取，这里只做标记
        logger.info("  ✓ 基准指数：沪深300(000300)、中证500(000905)、上证指数(000001)、创业板指(399006)")
    
    logger.info("\n🎉 P2 迁移全部完成")


if __name__ == '__main__':
    run()
