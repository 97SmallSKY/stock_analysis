"""
P3 数据库迁移脚本

创建内容：
1. score_weights_config 评分权重配置表
"""
import sys
import logging
import json
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from utils.storage import storage
from sqlalchemy import text

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
logger = logging.getLogger(__name__)


# 默认评分权重配置
DEFAULT_WEIGHTS = {
    'return_rate': 0.25,
    'max_drawdown': 0.20,
    'sharpe_ratio': 0.20,
    'win_rate': 0.10,
    'profit_loss_ratio': 0.10,
    'cost_ratio': 0.10,
    'sample_size': 0.05,
}

DEFAULT_BENCHMARKS = {
    'return_rate': {'perfect': 50, 'zero': 0},
    'max_drawdown': {'perfect': 0, 'zero': -30},
    'sharpe_ratio': {'perfect': 3.0, 'zero': 0},
    'win_rate': {'perfect': 80, 'zero': 30},
    'profit_loss_ratio': {'perfect': 3.0, 'zero': 0.5},
    'cost_ratio': {'perfect': 0, 'zero': 5},
    'sample_size': {'perfect': 30, 'zero': 3},
}


def run():
    engine = storage._get_engine()
    
    logger.info("🚀 P3 数据库迁移开始")
    
    with engine.begin() as conn:
        # === Step 1: 创建 score_weights_config 表 ===
        logger.info("\n=== Step 1: 创建 score_weights_config 表 ===")
        conn.execute(text('''
            CREATE TABLE IF NOT EXISTS score_weights_config (
                id              SERIAL PRIMARY KEY,
                config_name     VARCHAR(50) UNIQUE NOT NULL,
                weights         JSONB NOT NULL,
                benchmarks      JSONB,
                description     TEXT,
                is_active       BOOLEAN DEFAULT true,
                created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        '''))
        logger.info("  ✅ score_weights_config 表已创建")
        
        # === Step 2: 插入默认配置 ===
        logger.info("\n=== Step 2: 插入默认评分权重配置 ===")
        from sqlalchemy import cast
        from sqlalchemy.dialects.postgresql import JSONB as PG_JSONB
        conn.execute(text('''
            INSERT INTO score_weights_config (config_name, weights, benchmarks, description)
            VALUES ('default', :weights, :benchmarks, '默认评分权重配置')
            ON CONFLICT (config_name) DO UPDATE SET
                weights = EXCLUDED.weights,
                benchmarks = EXCLUDED.benchmarks,
                updated_at = CURRENT_TIMESTAMP
        '''), {
            'weights': json.dumps(DEFAULT_WEIGHTS),
            'benchmarks': json.dumps(DEFAULT_BENCHMARKS),
        })
        logger.info("  ✅ 默认配置已插入")
        
        # === Step 3: 创建索引 ===
        logger.info("\n=== Step 3: 创建索引 ===")
        conn.execute(text('''
            CREATE INDEX IF NOT EXISTS idx_weights_config_name 
            ON score_weights_config(config_name)
        '''))
        logger.info("  ✅ 索引已创建")
    
    logger.info("\n🎉 P3 迁移全部完成")


if __name__ == '__main__':
    run()
