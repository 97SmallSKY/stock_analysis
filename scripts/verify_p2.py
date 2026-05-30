"""P2 阶段验证：每日净值 + 净值曲线"""
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
    logger.info("P2 阶段验证开始")
    logger.info("=" * 60)
    
    # 验证1: sim_daily_summary 表存在
    logger.info("\n【验证1】sim_daily_summary 表")
    if 'sim_daily_summary' in inspector.get_table_names():
        cols = [c['name'] for c in inspector.get_columns('sim_daily_summary')]
        required = ['robot_id', 'trade_date', 'cash', 'holding_value', 'total_value', 'nav']
        missing = [c for c in required if c not in cols]
        if missing:
            errors.append(f"sim_daily_summary 缺少字段: {missing}")
            logger.error(f"  ❌ 缺少字段: {missing}")
        else:
            logger.info("  ✅ sim_daily_summary 表结构正确")
    else:
        errors.append("sim_daily_summary 表不存在")
        logger.error("  ❌ sim_daily_summary 表不存在")
    
    # 验证2: benchmark_daily 表存在
    logger.info("\n【验证2】benchmark_daily 表")
    if 'benchmark_daily' in inspector.get_table_names():
        cols = [c['name'] for c in inspector.get_columns('benchmark_daily')]
        required = ['index_code', 'trade_date', 'close_price']
        missing = [c for c in required if c not in cols]
        if missing:
            errors.append(f"benchmark_daily 缺少字段: {missing}")
            logger.error(f"  ❌ 缺少字段: {missing}")
        else:
            logger.info("  ✅ benchmark_daily 表结构正确")
    else:
        errors.append("benchmark_daily 表不存在")
        logger.error("  ❌ benchmark_daily 表不存在")
    
    # 验证3: DailySettlement 模块
    logger.info("\n【验证3】DailySettlement 模块")
    try:
        from engine.live.daily_settlement import DailySettlement
        ds = DailySettlement()
        logger.info("  ✅ DailySettlement 模块导入成功")
    except Exception as e:
        errors.append(f"DailySettlement 导入失败: {e}")
        logger.error(f"  ❌ 导入失败: {e}")
    
    # 验证4: 备份脚本存在
    logger.info("\n【验证4】备份脚本")
    backup_script = ROOT / 'scripts' / 'backup_db.sh'
    if backup_script.exists():
        logger.info(f"  ✅ 备份脚本存在: {backup_script}")
    else:
        errors.append("备份脚本不存在")
        logger.error("  ❌ 备份脚本不存在")
    
    # 验证5: 数据获取模块（多数据源）
    logger.info("\n【验证5】数据获取模块")
    try:
        from data.fetcher import get_data_manager
        dm = get_data_manager()
        logger.info("  ✅ 多数据源管理器可用")
    except Exception as e:
        errors.append(f"数据获取模块导入失败: {e}")
        logger.error(f"  ❌ 导入失败: {e}")
    
    # 验证6: RobotManager 和 CashManager
    logger.info("\n【验证6】机器人管理模块")
    try:
        from robot.manager import RobotManager
        from robot.cash_manager import CashManager
        rm = RobotManager()
        logger.info("  ✅ RobotManager 可用")
        cm = CashManager('default')
        balance = cm.get_balance()
        logger.info(f"  ✅ CashManager 可用，默认机器人余额: ¥{balance:.2f}")
    except Exception as e:
        errors.append(f"机器人管理模块导入失败: {e}")
        logger.error(f"  ❌ 导入失败: {e}")
    
    # 汇总
    logger.info("\n" + "=" * 60)
    if errors:
        logger.error(f"🔴 P2 验证失败 - 发现 {len(errors)} 个问题:")
        for i, e in enumerate(errors, 1):
            logger.error(f"  {i}. {e}")
        logger.info("=" * 60)
        return False
    else:
        logger.info("🟢 P2 全部验证通过!")
        logger.info("=" * 60)
        return True


if __name__ == '__main__':
    sys.exit(0 if verify() else 1)
