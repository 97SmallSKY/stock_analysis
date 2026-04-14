"""P3 阶段验证：对比看板 + 评分体系"""
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
    logger.info("P3 阶段验证开始")
    logger.info("=" * 60)
    
    # 验证1: score_weights_config 表存在
    logger.info("\n【验证1】score_weights_config 表")
    if 'score_weights_config' in inspector.get_table_names():
        cols = [c['name'] for c in inspector.get_columns('score_weights_config')]
        required = ['config_name', 'weights', 'benchmarks', 'is_active']
        missing = [c for c in required if c not in cols]
        if missing:
            errors.append(f"score_weights_config 缺少字段: {missing}")
            logger.error(f"  ❌ 缺少字段: {missing}")
        else:
            logger.info("  ✅ score_weights_config 表结构正确")
            
            # 检查默认配置是否存在
            with engine.connect() as conn:
                row = conn.execute(text('''
                    SELECT config_name FROM score_weights_config WHERE config_name = 'default'
                ''')).fetchone()
                if row:
                    logger.info("  ✅ 默认权重配置已存在")
                else:
                    errors.append("默认权重配置不存在")
                    logger.error("  ❌ 默认权重配置不存在")
    else:
        errors.append("score_weights_config 表不存在")
        logger.error("  ❌ score_weights_config 表不存在")
    
    # 验证2: scorer 模块
    logger.info("\n【验证2】scorer 模块")
    try:
        from strategy.scorer import (
            calc_robot_metrics,
            calc_robot_score,
            rank_robots,
            load_weights_config,
            DEFAULT_WEIGHTS,
            DEFAULT_BENCHMARKS,
        )
        logger.info("  ✅ scorer 模块导入成功")
        
        # 检查权重总和
        total_weight = sum(DEFAULT_WEIGHTS.values())
        if abs(total_weight - 1.0) < 0.01:
            logger.info(f"  ✅ 默认权重总和验证通过 ({total_weight:.2f})")
        else:
            errors.append(f"默认权重总和不为1: {total_weight:.2f}")
            logger.error(f"  ❌ 默认权重总和异常: {total_weight:.2f}")
    except Exception as e:
        errors.append(f"scorer 模块导入失败: {e}")
        logger.error(f"  ❌ 导入失败: {e}")
    
    # 验证3: charts 模块
    logger.info("\n【验证3】charts 模块")
    try:
        from analysis.charts import (
            chart_nav_comparison,
            chart_drawdown_comparison,
            chart_sharpe_comparison,
            chart_metrics_table,
            chart_trade_scatter,
            chart_score_radar,
        )
        logger.info("  ✅ charts 模块导入成功")
        
        # 测试创建图表（空数据）
        fig = chart_sharpe_comparison(['test'])
        logger.info(f"  ✅ 图表创建测试成功")
    except Exception as e:
        errors.append(f"charts 模块导入失败: {e}")
        logger.error(f"  ❌ 导入失败: {e}")
    
    # 验证4: 前端页面
    logger.info("\n【验证4】策略对比页面")
    page_file = ROOT / 'ui' / 'pages' / 'strategy_comparison.py'
    if page_file.exists():
        logger.info(f"  ✅ 页面文件存在: {page_file}")
        
        # 检查 render 函数
        try:
            from ui.pages.strategy_comparison import render
            logger.info("  ✅ render 函数可导入")
        except Exception as e:
            errors.append(f"页面 render 函数导入失败: {e}")
            logger.error(f"  ❌ render 导入失败: {e}")
    else:
        errors.append("策略对比页面不存在")
        logger.error("  ❌ 页面文件不存在")
    
    # 验证5: 数据库关联检查
    logger.info("\n【验证5】数据库关联检查")
    required_tables = ['sim_daily_summary', 'sim_trades', 'sim_robots', 'benchmark_daily']
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
        logger.error(f"🔴 P3 验证失败 - 发现 {len(errors)} 个问题:")
        for i, e in enumerate(errors, 1):
            logger.error(f"  {i}. {e}")
        logger.info("=" * 60)
        return False
    else:
        logger.info("🟢 P3 全部验证通过!")
        logger.info("=" * 60)
        return True


if __name__ == '__main__':
    sys.exit(0 if verify() else 1)
