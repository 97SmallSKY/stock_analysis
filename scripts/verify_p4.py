"""P4 阶段验证：实时监控 + 盘中止损"""
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
    logger.info("P4 阶段验证开始")
    logger.info("=" * 60)
    
    # 验证1: market_snapshots 表存在
    logger.info("\n【验证1】market_snapshots 表")
    if 'market_snapshots' in inspector.get_table_names():
        cols = [c['name'] for c in inspector.get_columns('market_snapshots')]
        required = ['stock_code', 'trade_date', 'snapshot_time', 'price']
        missing = [c for c in required if c not in cols]
        if missing:
            errors.append(f"market_snapshots 缺少字段: {missing}")
            logger.error(f"  ❌ 缺少字段: {missing}")
        else:
            logger.info("  ✅ market_snapshots 表结构正确")
    else:
        errors.append("market_snapshots 表不存在")
        logger.error("  ❌ market_snapshots 表不存在")
    
    # 验证2: monitor_events 表存在
    logger.info("\n【验证2】monitor_events 表")
    if 'monitor_events' in inspector.get_table_names():
        cols = [c['name'] for c in inspector.get_columns('monitor_events')]
        required = ['robot_id', 'stock_code', 'event_time', 'event_type']
        missing = [c for c in required if c not in cols]
        if missing:
            errors.append(f"monitor_events 缺少字段: {missing}")
            logger.error(f"  ❌ 缺少字段: {missing}")
        else:
            logger.info("  ✅ monitor_events 表结构正确")
    else:
        errors.append("monitor_events 表不存在")
        logger.error("  ❌ monitor_events 表不存在")
    
    # 验证3: realtime_monitor 模块
    logger.info("\n【验证3】realtime_monitor 模块")
    try:
        from engine.live.realtime_monitor import RealtimeMonitor, start_monitor, POLL_INTERVAL, SNAPSHOT_INTERVAL
        logger.info("  ✅ realtime_monitor 模块导入成功")
        
        # 检查配置
        if POLL_INTERVAL == 10 and SNAPSHOT_INTERVAL == 300:
            logger.info(f"  ✅ 配置验证通过 (轮询={POLL_INTERVAL}s, 快照={SNAPSHOT_INTERVAL}s)")
        else:
            errors.append(f"配置值异常: POLL_INTERVAL={POLL_INTERVAL}, SNAPSHOT_INTERVAL={SNAPSHOT_INTERVAL}")
            logger.error(f"  ❌ 配置值异常")
    except Exception as e:
        errors.append(f"realtime_monitor 模块导入失败: {e}")
        logger.error(f"  ❌ 导入失败: {e}")
    
    # 验证4: Supervisor 配置文件
    logger.info("\n【验证4】Supervisor 配置文件")
    conf_file = ROOT / 'scripts' / 'sim_monitor.conf'
    if conf_file.exists():
        logger.info(f"  ✅ 配置文件存在: {conf_file}")
        
        # 检查关键配置
        content = conf_file.read_text()
        if 'sim_monitor' in content and 'realtime_monitor' in content:
            logger.info("  ✅ 配置文件内容正确")
        else:
            errors.append("Supervisor 配置文件内容不完整")
            logger.error("  ❌ 配置文件内容不完整")
    else:
        errors.append("Supervisor 配置文件不存在")
        logger.error("  ❌ 配置文件不存在")
    
    # 验证5: 依赖模块
    logger.info("\n【验证5】依赖模块检查")
    dependencies = [
        ('robot.manager', 'RobotManager'),
        ('robot.cash_manager', 'CashManager'),
        ('strategy.loader', 'load_strategy'),
        ('robot.trade_cost', 'calc_slippage_price'),
        ('data.fetcher', 'get_data_manager'),
    ]
    
    all_deps_ok = True
    for module_name, attr in dependencies:
        try:
            module = __import__(module_name, fromlist=[attr])
            getattr(module, attr)
            logger.debug(f"  ✓ {module_name}.{attr}")
        except Exception as e:
            errors.append(f"依赖 {module_name}.{attr} 导入失败: {e}")
            logger.error(f"  ❌ {module_name}.{attr}: {e}")
            all_deps_ok = False
    
    if all_deps_ok:
        logger.info("  ✅ 所有依赖模块可用")
    
    # 验证6: 数据库关联检查
    logger.info("\n【验证6】数据库关联检查")
    required_tables = ['sim_trades', 'cash_ledger', 'sim_robots']
    missing_tables = []
    for t in required_tables:
        if t not in inspector.get_table_names():
            missing_tables.append(t)
    
    if missing_tables:
        errors.append(f"缺少必要表: {missing_tables}")
        logger.error(f"  ❌ 缺少表: {missing_tables}")
    else:
        logger.info("  ✅ 所有依赖表都存在")
    
    # 汇总
    logger.info("\n" + "=" * 60)
    if errors:
        logger.error(f"🔴 P4 验证失败 - 发现 {len(errors)} 个问题:")
        for i, e in enumerate(errors, 1):
            logger.error(f"  {i}. {e}")
        logger.info("=" * 60)
        return False
    else:
        logger.info("🟢 P4 全部验证通过!")
        logger.info("=" * 60)
        return True


if __name__ == '__main__':
    sys.exit(0 if verify() else 1)
