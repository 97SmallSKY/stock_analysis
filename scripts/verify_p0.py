"""P0 修复验证：三个bug + 交易成本"""
import sys
import logging
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from utils.storage import storage
from sqlalchemy import text, inspect

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
logger = logging.getLogger(__name__)


def verify():
    engine = storage._get_engine()
    inspector = inspect(engine)
    errors = []
    
    logger.info("=" * 60)
    logger.info("P0 阶段验证开始")
    logger.info("=" * 60)
    
    # 验证1: lianban_score 字段
    logger.info("\n【验证1】sim_selection_log.lianban_score 字段")
    try:
        cols = [c['name'] for c in inspector.get_columns('sim_selection_log')]
        if 'lianban_score' in cols:
            logger.info("  ✅ 通过 - lianban_score 字段已存在")
        else:
            errors.append("sim_selection_log 缺少 lianban_score")
            logger.error("  ❌ 失败 - lianban_score 字段不存在")
    except Exception as e:
        errors.append(f"验证 sim_selection_log 异常: {e}")
        logger.error(f"  ❌ 异常: {e}")
    
    # 验证2: robot_id 字段
    logger.info("\n【验证2】robot_id 字段（所有模拟交易表）")
    robot_id_tables = ['sim_trades', 'sim_trade_events', 'sim_selection_log']
    for table in robot_id_tables:
        try:
            cols = [c['name'] for c in inspector.get_columns(table)]
            if 'robot_id' in cols:
                logger.info(f"  ✅ {table}.robot_id 存在")
            else:
                errors.append(f"{table} 缺少 robot_id")
                logger.error(f"  ❌ {table}.robot_id 不存在")
        except Exception as e:
            errors.append(f"验证 {table} 异常: {e}")
            logger.error(f"  ❌ {table} 异常: {e}")
    
    # 验证3: 交易成本字段
    logger.info("\n【验证3】交易成本字段（sim_trades）")
    cost_cols = [
        'buy_commission', 'buy_transfer_fee', 'buy_slippage_price',
        'sell_commission', 'sell_transfer_fee', 'sell_stamp_tax', 
        'sell_slippage_price', 'total_fees', 'net_profit', 'net_profit_pct'
    ]
    try:
        cols = [c['name'] for c in inspector.get_columns('sim_trades')]
        missing = []
        for col in cost_cols:
            if col in cols:
                logger.info(f"  ✅ sim_trades.{col} 存在")
            else:
                missing.append(col)
                logger.error(f"  ❌ sim_trades.{col} 不存在")
        if missing:
            errors.append(f"sim_trades 缺少成本字段: {missing}")
    except Exception as e:
        errors.append(f"验证 sim_trades 成本字段异常: {e}")
        logger.error(f"  ❌ 异常: {e}")
    
    # 验证4: sim_trade_events 成本字段
    logger.info("\n【验证4】交易成本字段（sim_trade_events）")
    event_cost_cols = ['commission', 'transfer_fee', 'stamp_tax', 'slippage_price', 'total_cost']
    try:
        cols = [c['name'] for c in inspector.get_columns('sim_trade_events')]
        missing = []
        for col in event_cost_cols:
            if col in cols:
                logger.info(f"  ✅ sim_trade_events.{col} 存在")
            else:
                missing.append(col)
                logger.error(f"  ❌ sim_trade_events.{col} 不存在")
        if missing:
            errors.append(f"sim_trade_events 缺少成本字段: {missing}")
    except Exception as e:
        errors.append(f"验证 sim_trade_events 成本字段异常: {e}")
        logger.error(f"  ❌ 异常: {e}")
    
    # 验证5: 无仓位溢出
    logger.info("\n【验证5】仓位溢出检查")
    try:
        with engine.connect() as conn:
            overflow = conn.execute(text('''
                SELECT id, code, name, buy_amount, buy_shares, buy_price
                FROM sim_trades
                WHERE buy_amount > 0 AND buy_price > 0 AND buy_shares > 0
                  AND buy_shares * buy_price > buy_amount * 1.5
            ''')).fetchall()
            
            if overflow:
                errors.append(f"仓位溢出: {len(overflow)} 条记录")
                logger.error(f"  ❌ 发现 {len(overflow)} 条溢出记录")
                for row in overflow[:3]:  # 只显示前3条
                    r = dict(row._mapping)
                    logger.error(f"     id={r['id']} {r['code']} shares={r['buy_shares']} amount={r['buy_amount']}")
            else:
                logger.info("  ✅ 通过 - 无溢出记录")
    except Exception as e:
        errors.append(f"仓位溢出检查异常: {e}")
        logger.error(f"  ❌ 异常: {e}")
    
    # 验证6: 交易成本计算模块
    logger.info("\n【验证6】交易成本计算模块")
    try:
        from skills.stock_sim_robot.trade_cost import calc_buy_cost, calc_sell_cost
        
        # 测试沪市买入
        buy = calc_buy_cost(10.0, 1000, '600001')
        assert buy['commission'] == 5.0, f"佣金应为最低5元，实际{buy['commission']}"
        assert buy['transfer_fee'] == 0.1, f"沪市过户费应为0.1元，实际{buy['transfer_fee']}"
        logger.info("  ✅ 沪市买入测试通过")
        
        # 测试深市卖出
        sell = calc_sell_cost(10.0, 1000, '000001')
        assert sell['stamp_tax'] == 5.0, f"印花税应为5元，实际{sell['stamp_tax']}"
        assert sell['transfer_fee'] == 0.0, f"深市过户费应为0，实际{sell['transfer_fee']}"
        logger.info("  ✅ 深市卖出测试通过")
        
    except AssertionError as e:
        errors.append(f"交易成本计算错误: {e}")
        logger.error(f"  ❌ 测试失败: {e}")
    except Exception as e:
        errors.append(f"交易成本模块导入异常: {e}")
        logger.error(f"  ❌ 导入失败: {e}")
    
    # 验证7: 仓位计算模块
    logger.info("\n【验证7】仓位计算模块")
    try:
        from skills.stock_sim_robot.position_calc import calculate_buy_shares
        
        result = calculate_buy_shares(10000, 10.0, '600001', 100000)
        assert result['valid'], f"应该有效，原因: {result['reason']}"
        assert result['shares'] > 0, f"股数应大于0，实际{result['shares']}"
        assert result['cost_detail']['total_paid'] <= 10000, \
            f"总支出应不超过预算，实际{result['cost_detail']['total_paid']}"
        logger.info(f"  ✅ 仓位计算测试通过 - 股数={result['shares']}, 支出={result['cost_detail']['total_paid']:.0f}")
        
    except AssertionError as e:
        errors.append(f"仓位计算错误: {e}")
        logger.error(f"  ❌ 测试失败: {e}")
    except Exception as e:
        errors.append(f"仓位计算模块导入异常: {e}")
        logger.error(f"  ❌ 导入失败: {e}")
    
    # 汇总
    logger.info("\n" + "=" * 60)
    if errors:
        logger.error(f"🔴 P0 验证失败 - 发现 {len(errors)} 个问题:")
        for i, e in enumerate(errors, 1):
            logger.error(f"  {i}. {e}")
        logger.info("=" * 60)
        return False
    else:
        logger.info("🟢 P0 全部验证通过!")
        logger.info("=" * 60)
        return True


if __name__ == '__main__':
    sys.exit(0 if verify() else 1)
