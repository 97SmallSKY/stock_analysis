"""
P4 数据库迁移脚本

创建内容：
1. market_snapshots 行情快照表
2. monitor_events 监控事件日志表
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
    
    logger.info("🚀 P4 数据库迁移开始")
    
    with engine.begin() as conn:
        # === Step 1: 创建 market_snapshots 表 ===
        logger.info("\n=== Step 1: 创建 market_snapshots 表 ===")
        conn.execute(text('''
            CREATE TABLE IF NOT EXISTS market_snapshots (
                id              SERIAL PRIMARY KEY,
                stock_code      VARCHAR(10) NOT NULL,
                stock_name      VARCHAR(20),
                trade_date      VARCHAR(10) NOT NULL,
                snapshot_time   TIMESTAMP NOT NULL,
                
                price           FLOAT,
                open            FLOAT,
                high            FLOAT,
                low             FLOAT,
                pre_close       FLOAT,
                volume          BIGINT,
                amount          FLOAT,
                change_pct      FLOAT,
                
                interval_type   VARCHAR(10) DEFAULT '5min',
                created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        '''))
        logger.info("  ✅ market_snapshots 表已创建")
        
        # 创建索引
        conn.execute(text('''
            CREATE INDEX IF NOT EXISTS idx_snapshot_code_date
            ON market_snapshots(stock_code, trade_date, snapshot_time)
        '''))
        logger.info("  ✅ 索引已创建")
        
        # === Step 2: 创建 monitor_events 表 ===
        logger.info("\n=== Step 2: 创建 monitor_events 表 ===")
        conn.execute(text('''
            CREATE TABLE IF NOT EXISTS monitor_events (
                id              SERIAL PRIMARY KEY,
                robot_id        VARCHAR(50) NOT NULL,
                stock_code      VARCHAR(10) NOT NULL,
                stock_name      VARCHAR(20),
                trade_date      VARCHAR(10),
                event_time      TIMESTAMP NOT NULL,
                
                event_type      VARCHAR(20) NOT NULL,
                
                trigger_price   FLOAT,
                threshold       FLOAT,
                current_pct     FLOAT,
                
                action_taken    BOOLEAN DEFAULT false,
                trade_id       INTEGER,
                
                memo            TEXT,
                created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        '''))
        logger.info("  ✅ monitor_events 表已创建")
        
        # 创建索引
        conn.execute(text('''
            CREATE INDEX IF NOT EXISTS idx_monitor_events_robot
            ON monitor_events(robot_id, trade_date)
        '''))
        logger.info("  ✅ 索引已创建")
    
    logger.info("\n🎉 P4 迁移全部完成")


if __name__ == '__main__':
    run()
