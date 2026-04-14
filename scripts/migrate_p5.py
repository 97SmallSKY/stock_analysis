"""
P5 数据库迁移脚本

创建内容：
1. elimination_records 淘汰记录表
2. elimination_schedule 淘汰计划表
3. dividend_records 分红记录表
"""
import sys
import logging
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from utils.storage import storage
from sqlalchemy import text
import json

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
logger = logging.getLogger(__name__)


# 默认淘汰计划配置
DEFAULT_ELIMINATION_SCHEDULE = {
    'schedule_type': 'FIXED_PERIOD',
    'period_days': 20,
    'eliminate_count': 1,
    'min_robots': 3,
    'weight_config': 'default',
}


def run():
    engine = storage._get_engine()
    
    logger.info("🚀 P5 数据库迁移开始")
    
    with engine.begin() as conn:
        # === Step 1: 创建 elimination_records 表 ===
        logger.info("\n=== Step 1: 创建 elimination_records 表 ===")
        conn.execute(text('''
            CREATE TABLE IF NOT EXISTS elimination_records (
                id                  SERIAL PRIMARY KEY,
                robot_id            VARCHAR(50) NOT NULL,
                robot_name          VARCHAR(100),
                eliminate_date      VARCHAR(10) NOT NULL,
                
                final_score         FLOAT,
                final_metrics       JSONB,
                peer_snapshot       JSONB NOT NULL,
                
                reason              TEXT,
                created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        '''))
        logger.info("  ✅ elimination_records 表已创建")
        
        # 创建索引
        conn.execute(text('''
            CREATE INDEX IF NOT EXISTS idx_elimination_records_date
            ON elimination_records(eliminate_date)
        '''))
        logger.info("  ✅ 索引已创建")
        
        # === Step 2: 创建 elimination_schedule 表 ===
        logger.info("\n=== Step 2: 创建 elimination_schedule 表 ===")
        conn.execute(text('''
            CREATE TABLE IF NOT EXISTS elimination_schedule (
                id              SERIAL PRIMARY KEY,
                schedule_type   VARCHAR(20) NOT NULL,
                period_days     INTEGER DEFAULT 20,
                eliminate_count INTEGER DEFAULT 1,
                min_robots      INTEGER DEFAULT 3,
                weight_config   VARCHAR(50) DEFAULT 'default',
                is_active       BOOLEAN DEFAULT true,
                last_run_date   VARCHAR(10),
                created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        '''))
        logger.info("  ✅ elimination_schedule 表已创建")
        
        # 插入默认配置
        conn.execute(text('''
            INSERT INTO elimination_schedule 
            (schedule_type, period_days, eliminate_count, min_robots, weight_config)
            VALUES ('FIXED_PERIOD', 20, 1, 3, 'default')
            ON CONFLICT DO NOTHING
        '''))
        logger.info("  ✅ 默认淘汰计划已插入")
        
        # === Step 3: 创建 dividend_records 表 ===
        logger.info("\n=== Step 3: 创建 dividend_records 表 ===")
        conn.execute(text('''
            CREATE TABLE IF NOT EXISTS dividend_records (
                id              SERIAL PRIMARY KEY,
                stock_code      VARCHAR(10) NOT NULL,
                stock_name      VARCHAR(20),
                ex_date         VARCHAR(10) NOT NULL,
                
                dividend_per_share  FLOAT NOT NULL,
                bonus_ratio     FLOAT DEFAULT 0,
                split_ratio     FLOAT DEFAULT 0,
                
                source          VARCHAR(50),
                created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                
                UNIQUE(stock_code, ex_date)
            )
        '''))
        logger.info("  ✅ dividend_records 表已创建")
        
        # 创建索引
        conn.execute(text('''
            CREATE INDEX IF NOT EXISTS idx_dividend_code_date
            ON dividend_records(stock_code, ex_date)
        '''))
        logger.info("  ✅ 索引已创建")
        
        # === Step 4: 添加淘汰相关字段到 sim_robots ===
        logger.info("\n=== Step 4: 添加淘汰相关字段到 sim_robots ===")
        # 检查列是否已存在
        try:
            conn.execute(text('''
                ALTER TABLE sim_robots ADD COLUMN IF NOT EXISTS eliminate_reason TEXT
            '''))
            conn.execute(text('''
                ALTER TABLE sim_robots ADD COLUMN IF NOT EXISTS final_score FLOAT
            '''))
            conn.execute(text('''
                ALTER TABLE sim_robots ADD COLUMN IF NOT EXISTS eliminated_at TIMESTAMP
            '''))
            logger.info("  ✅ sim_robots 表已添加淘汰字段")
        except Exception as e:
            logger.warning(f"  ⚠️ 添加字段可能已存在: {e}")
    
    logger.info("\n🎉 P5 迁移全部完成")


if __name__ == '__main__':
    run()
