"""
P0 数据库迁移脚本

修复内容：
1. sim_selection_log 添加 lianban_score 字段
2. 所有模拟交易表添加 robot_id 字段
3. sim_trades / sim_trade_events 添加交易成本字段
4. 修复仓位溢出的脏数据
"""
import sys
import logging
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from utils.storage import storage
from sqlalchemy import text, inspect

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
logger = logging.getLogger(__name__)

TOTAL_CAPITAL = 100000


def add_column_if_missing(conn, inspector, table: str, column: str, col_type: str):
    """安全添加字段"""
    if table not in inspector.get_table_names():
        logger.warning(f"  表 {table} 不存在，跳过")
        return False
    
    existing = [c['name'] for c in inspector.get_columns(table)]
    if column in existing:
        logger.info(f"  ✓ {table}.{column} 已存在")
        return False
    
    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"))
    logger.info(f"  ✅ {table}.{column} 已添加")
    return True


def step1_fix_selection_log(conn, inspector):
    """Bug2: 补 lianban_score 字段"""
    logger.info("\n=== Step 1: 修复 sim_selection_log ===")
    add_column_if_missing(conn, inspector, 'sim_selection_log', 'lianban_score', 'FLOAT DEFAULT 0')


def step2_add_robot_id(conn, inspector):
    """Bug3: 所有表添加 robot_id"""
    logger.info("\n=== Step 2: 添加 robot_id ===")
    
    tables = ['sim_trades', 'sim_trade_events', 'sim_selection_log']
    
    for table in tables:
        added = add_column_if_missing(
            conn, inspector, table,
            'robot_id', "VARCHAR(50) DEFAULT 'default'"
        )
        if added:
            conn.execute(text(
                f"UPDATE {table} SET robot_id = 'default' WHERE robot_id IS NULL"
            ))
    
    # 添加索引
    try:
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_sim_trades_robot_status 
            ON sim_trades(robot_id, status)
        """))
        logger.info("  ✅ 索引已创建")
    except Exception as e:
        logger.warning(f"  索引创建失败（可能已存在）: {e}")


def step3_add_cost_fields(conn, inspector):
    """新增交易成本字段"""
    logger.info("\n=== Step 3: 添加交易成本字段 ===")
    
    # sim_trades 表：买卖两侧的成本
    cost_columns = {
        'buy_commission': 'FLOAT DEFAULT 0',
        'buy_transfer_fee': 'FLOAT DEFAULT 0',
        'buy_slippage_price': 'FLOAT',
        'sell_commission': 'FLOAT DEFAULT 0',
        'sell_transfer_fee': 'FLOAT DEFAULT 0',
        'sell_stamp_tax': 'FLOAT DEFAULT 0',
        'sell_slippage_price': 'FLOAT',
        'total_fees': 'FLOAT DEFAULT 0',
        'net_profit': 'FLOAT',
        'net_profit_pct': 'FLOAT',
    }
    
    for col, col_type in cost_columns.items():
        add_column_if_missing(conn, inspector, 'sim_trades', col, col_type)
    
    # sim_trade_events 表：每笔事件的成本
    event_cost_columns = {
        'commission': 'FLOAT DEFAULT 0',
        'transfer_fee': 'FLOAT DEFAULT 0',
        'stamp_tax': 'FLOAT DEFAULT 0',
        'slippage_price': 'FLOAT',
        'total_cost': 'FLOAT DEFAULT 0',
    }
    
    for col, col_type in event_cost_columns.items():
        add_column_if_missing(conn, inspector, 'sim_trade_events', col, col_type)


def step4_fix_dirty_data(conn):
    """Bug1: 修复仓位溢出的脏数据"""
    logger.info("\n=== Step 4: 修复脏数据 ===")
    
    rows = conn.execute(text('''
        SELECT id, code, name, buy_amount, buy_price, buy_shares
        FROM sim_trades
        WHERE buy_amount > 0 
          AND buy_price > 0
          AND buy_shares > 0
          AND buy_shares * buy_price > buy_amount * 1.5
    ''')).fetchall()
    
    if not rows:
        logger.info("  ✓ 无异常数据")
        return
    
    logger.info(f"  发现 {len(rows)} 条异常记录")
    
    for row in rows:
        r = dict(row._mapping)
        old_shares = r['buy_shares']
        old_amount = old_shares * r['buy_price']
        
        # 重新计算正确股数
        new_shares = int(r['buy_amount'] / r['buy_price'] / 100) * 100
        if new_shares < 100 and r['buy_amount'] >= r['buy_price'] * 100:
            new_shares = 100
        new_amount = new_shares * r['buy_price']
        
        logger.info(
            f"  修复 id={r['id']} {r['code']} {r['name']}: "
            f"shares {old_shares}→{new_shares}, "
            f"amount {old_amount:.0f}→{new_amount:.0f}"
        )
        
        # 只更新 sim_trades 表（sim_trade_events 没有 direction 字段，跳过）
        conn.execute(text('''
            UPDATE sim_trades SET buy_shares = :shares WHERE id = :id
        '''), {'shares': new_shares, 'id': r['id']})
    
    logger.info(f"  ✅ 已修复 {len(rows)} 条记录")


def run():
    engine = storage._get_engine()
    inspector = inspect(engine)
    
    logger.info("🚀 P0 数据库迁移开始")
    
    with engine.begin() as conn:
        step1_fix_selection_log(conn, inspector)
        step2_add_robot_id(conn, inspector)
        step3_add_cost_fields(conn, inspector)
        step4_fix_dirty_data(conn)
    
    logger.info("\n🎉 P0 迁移全部完成")


if __name__ == '__main__':
    run()
