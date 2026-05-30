"""P1 阶段验证：机器人管理 + 现金流水 + 策略注册"""
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
    logger.info("P1 阶段验证开始")
    logger.info("=" * 60)
    
    # 验证1: sim_robots 表存在
    logger.info("\n【验证1】sim_robots 表")
    if 'sim_robots' in inspector.get_table_names():
        cols = [c['name'] for c in inspector.get_columns('sim_robots')]
        required = ['robot_id', 'robot_name', 'status', 'initial_capital', 'position_ratio']
        missing = [c for c in required if c not in cols]
        if missing:
            errors.append(f"sim_robots 缺少字段: {missing}")
            logger.error(f"  ❌ 缺少字段: {missing}")
        else:
            logger.info("  ✅ sim_robots 表结构正确")
    else:
        errors.append("sim_robots 表不存在")
        logger.error("  ❌ sim_robots 表不存在")
    
    # 验证2: cash_ledger 表存在
    logger.info("\n【验证2】cash_ledger 表")
    if 'cash_ledger' in inspector.get_table_names():
        cols = [c['name'] for c in inspector.get_columns('cash_ledger')]
        required = ['robot_id', 'tx_type', 'amount', 'balance_after']
        missing = [c for c in required if c not in cols]
        if missing:
            errors.append(f"cash_ledger 缺少字段: {missing}")
            logger.error(f"  ❌ 缺少字段: {missing}")
        else:
            logger.info("  ✅ cash_ledger 表结构正确")
    else:
        errors.append("cash_ledger 表不存在")
        logger.error("  ❌ cash_ledger 表不存在")
    
    # 验证3: strategy_registry 表存在
    logger.info("\n【验证3】strategy_registry 表")
    if 'strategy_registry' in inspector.get_table_names():
        cols = [c['name'] for c in inspector.get_columns('strategy_registry')]
        required = ['strategy_id', 'strategy_name', 'module_path']
        missing = [c for c in required if c not in cols]
        if missing:
            errors.append(f"strategy_registry 缺少字段: {missing}")
            logger.error(f"  ❌ 缺少字段: {missing}")
        else:
            logger.info("  ✅ strategy_registry 表结构正确")
    else:
        errors.append("strategy_registry 表不存在")
        logger.error("  ❌ strategy_registry 表不存在")
    
    # 验证4: 默认策略已注册
    logger.info("\n【验证4】默认策略注册")
    try:
        with engine.connect() as conn:
            result = conn.execute(text('''
                SELECT strategy_id, strategy_name FROM strategy_registry WHERE is_active = true
            ''')).fetchall()
            
            if result:
                for r in result:
                    logger.info(f"  ✅ 策略: {r[0]} - {r[1]}")
            else:
                errors.append("无已注册策略")
                logger.error("  ❌ 无已注册策略")
    except Exception as e:
        errors.append(f"策略查询异常: {e}")
        logger.error(f"  ❌ 异常: {e}")
    
    # 验证5: 默认机器人存在
    logger.info("\n【验证5】默认机器人")
    try:
        with engine.connect() as conn:
            result = conn.execute(text('''
                SELECT robot_id, robot_name, status FROM sim_robots WHERE robot_id = 'default'
            ''')).fetchone()
            
            if result:
                logger.info(f"  ✅ 默认机器人: {result[1]} ({result[2]})")
            else:
                errors.append("默认机器人不存在")
                logger.error("  ❌ 默认机器人不存在")
    except Exception as e:
        errors.append(f"机器人查询异常: {e}")
        logger.error(f"  ❌ 异常: {e}")
    
    # 验证6: CashManager 模块
    logger.info("\n【验证6】CashManager 模块")
    try:
        from skills.stock_sim_robot.cash_manager import CashManager
        cm = CashManager('default')
        balance = cm.get_balance()
        logger.info(f"  ✅ CashManager 可用，默认机器人余额: ¥{balance:.2f}")
    except Exception as e:
        errors.append(f"CashManager 导入失败: {e}")
        logger.error(f"  ❌ 导入失败: {e}")
    
    # 验证7: RobotManager 模块
    logger.info("\n【验证7】RobotManager 模块")
    try:
        from skills.stock_sim_robot.robot_manager import RobotManager
        rm = RobotManager()
        robots = rm.list_robots()
        logger.info(f"  ✅ RobotManager 可用，机器人数量: {len(robots)}")
    except Exception as e:
        errors.append(f"RobotManager 导入失败: {e}")
        logger.error(f"  ❌ 导入失败: {e}")
    
    # 验证8: StrategyLoader 模块
    logger.info("\n【验证8】StrategyLoader 模块")
    try:
        from skills.stock_sim_robot.strategy_loader import list_strategies
        strategies = list_strategies()
        logger.info(f"  ✅ StrategyLoader 可用，策略数量: {len(strategies)}")
    except Exception as e:
        errors.append(f"StrategyLoader 导入失败: {e}")
        logger.error(f"  ❌ 导入失败: {e}")
    
    # 验证9: BaseStrategy 模块
    logger.info("\n【验证9】BaseStrategy 模块")
    try:
        from skills.stock_sim_robot.strategy_base import BaseStrategy, SelectionSignal, TradeSignal
        logger.info("  ✅ BaseStrategy 模块导入成功")
    except Exception as e:
        errors.append(f"BaseStrategy 导入失败: {e}")
        logger.error(f"  ❌ 导入失败: {e}")
    
    # 汇总
    logger.info("\n" + "=" * 60)
    if errors:
        logger.error(f"🔴 P1 验证失败 - 发现 {len(errors)} 个问题:")
        for i, e in enumerate(errors, 1):
            logger.error(f"  {i}. {e}")
        logger.info("=" * 60)
        return False
    else:
        logger.info("🟢 P1 全部验证通过!")
        logger.info("=" * 60)
        return True


if __name__ == '__main__':
    sys.exit(0 if verify() else 1)
