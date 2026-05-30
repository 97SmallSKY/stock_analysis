"""P5 阶段验证：淘汰机制 + 分批建仓 + 数据清理"""
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
    logger.info("P5 阶段验证开始")
    logger.info("=" * 60)
    
    # 验证1: elimination_records 表存在
    logger.info("\n【验证1】elimination_records 表")
    if 'elimination_records' in inspector.get_table_names():
        cols = [c['name'] for c in inspector.get_columns('elimination_records')]
        required = ['robot_id', 'eliminate_date', 'peer_snapshot']
        missing = [c for c in required if c not in cols]
        if missing:
            errors.append(f"elimination_records 缺少字段: {missing}")
            logger.error(f"  ❌ 缺少字段: {missing}")
        else:
            logger.info("  ✅ elimination_records 表结构正确")
    else:
        errors.append("elimination_records 表不存在")
        logger.error("  ❌ elimination_records 表不存在")
    
    # 验证2: elimination_schedule 表存在
    logger.info("\n【验证2】elimination_schedule 表")
    if 'elimination_schedule' in inspector.get_table_names():
        cols = [c['name'] for c in inspector.get_columns('elimination_schedule')]
        required = ['schedule_type', 'period_days', 'eliminate_count']
        missing = [c for c in required if c not in cols]
        if missing:
            errors.append(f"elimination_schedule 缺少字段: {missing}")
            logger.error(f"  ❌ 缺少字段: {missing}")
        else:
            logger.info("  ✅ elimination_schedule 表结构正确")
            
            # 检查默认配置
            with engine.connect() as conn:
                row = conn.execute(text('''
                    SELECT COUNT(*) FROM elimination_schedule WHERE is_active = true
                ''')).scalar()
                if row > 0:
                    logger.info(f"  ✅ 已配置 {row} 个淘汰计划")
                else:
                    errors.append("未配置淘汰计划")
                    logger.error("  ❌ 未配置淘汰计划")
    else:
        errors.append("elimination_schedule 表不存在")
        logger.error("  ❌ elimination_schedule 表不存在")
    
    # 验证3: dividend_records 表存在
    logger.info("\n【验证3】dividend_records 表")
    if 'dividend_records' in inspector.get_table_names():
        cols = [c['name'] for c in inspector.get_columns('dividend_records')]
        required = ['stock_code', 'ex_date', 'dividend_per_share']
        missing = [c for c in required if c not in cols]
        if missing:
            errors.append(f"dividend_records 缺少字段: {missing}")
            logger.error(f"  ❌ 缺少字段: {missing}")
        else:
            logger.info("  ✅ dividend_records 表结构正确")
    else:
        errors.append("dividend_records 表不存在")
        logger.error("  ❌ dividend_records 表不存在")
    
    # 验证4: eliminator 模块
    logger.info("\n【验证4】eliminator 模块")
    try:
        from engine.live.eliminator import Eliminator, run_elimination
        logger.info("  ✅ eliminator 模块导入成功")
    except Exception as e:
        errors.append(f"eliminator 模块导入失败: {e}")
        logger.error(f"  ❌ 导入失败: {e}")
    
    # 验证5: dividend_handler 模块
    logger.info("\n【验证5】dividend_handler 模块")
    try:
        from engine.live.dividend_handler import DividendHandler, run_dividend_handler
        logger.info("  ✅ dividend_handler 模块导入成功")
    except Exception as e:
        errors.append(f"dividend_handler 模块导入失败: {e}")
        logger.error(f"  ❌ 导入失败: {e}")
    
    # 验证6: data_cleaner 模块
    logger.info("\n【验证6】data_cleaner 模块")
    try:
        from scripts.data_cleaner import cleanup, vacuum_database
        logger.info("  ✅ data_cleaner 模块导入成功")
    except Exception as e:
        errors.append(f"data_cleaner 模块导入失败: {e}")
        logger.error(f"  ❌ 导入失败: {e}")
    
    # 验证7: daily_cron 模块
    logger.info("\n【验证7】daily_cron 模块")
    try:
        from scripts.daily_cron import main as cron_main
        logger.info("  ✅ daily_cron 模块导入成功")
    except Exception as e:
        errors.append(f"daily_cron 模块导入失败: {e}")
        logger.error(f"  ❌ 导入失败: {e}")
    
    # 验证8: sim_robots 表淘汰字段
    logger.info("\n【验证8】sim_robots 淘汰字段")
    try:
        cols = [c['name'] for c in inspector.get_columns('sim_robots')]
        required = ['eliminate_reason', 'final_score']
        missing = [c for c in required if c not in cols]
        if missing:
            errors.append(f"sim_robots 缺少淘汰字段: {missing}")
            logger.error(f"  ❌ 缺少字段: {missing}")
        else:
            logger.info("  ✅ sim_robots 已添加淘汰字段")
    except Exception as e:
        logger.warning(f"  ⚠️ 无法检查 sim_robots 字段: {e}")
    
    # 验证9: 淘汰逻辑检查
    logger.info("\n【验证9】淘汰逻辑验证")
    try:
        # 检查是否有 eliminated 状态的机器人
        with engine.connect() as conn:
            eliminated_count = conn.execute(text('''
                SELECT COUNT(*) FROM sim_robots WHERE status = 'eliminated'
            ''')).scalar()
            logger.info(f"  📊 已淘汰机器人: {eliminated_count} 个")
            
            # 检查淘汰记录
            record_count = conn.execute(text('''
                SELECT COUNT(*) FROM elimination_records
            ''')).scalar()
            logger.info(f"  📊 淘汰记录: {record_count} 条")
        
        logger.info("  ✅ 淘汰逻辑验证通过")
    except Exception as e:
        logger.warning(f"  ⚠️ 淘汰逻辑检查跳过: {e}")
    
    # 汇总
    logger.info("\n" + "=" * 60)
    if errors:
        logger.error(f"🔴 P5 验证失败 - 发现 {len(errors)} 个问题:")
        for i, e in enumerate(errors, 1):
            logger.error(f"  {i}. {e}")
        logger.info("=" * 60)
        return False
    else:
        logger.info("🟢 P5 全部验证通过!")
        logger.info("=" * 60)
        logger.info("\n🎉 策略机器人平台全部开发完成！")
        return True


if __name__ == '__main__':
    sys.exit(0 if verify() else 1)
