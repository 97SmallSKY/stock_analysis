"""
策略加载器 - 根据 strategy_id 动态加载策略类
"""
import sys
import importlib
import logging
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))

from utils.storage import storage
from sqlalchemy import text
from .strategy_base import BaseStrategy

logger = logging.getLogger(__name__)

_strategy_cache: dict[str, BaseStrategy] = {}


def load_strategy(strategy_id: str, params: dict = None) -> BaseStrategy:
    """
    根据 strategy_id 加载策略实例
    
    流程：
    1. 查 strategy_registry 表获取 module_path
    2. 动态 import 模块
    3. 调用 create_strategy(params) 工厂函数
    """
    cache_key = f"{strategy_id}:{hash(str(params))}"
    if cache_key in _strategy_cache:
        return _strategy_cache[cache_key]
    
    engine = storage._get_engine()
    with engine.connect() as conn:
        row = conn.execute(text('''
            SELECT module_path, default_params
            FROM strategy_registry
            WHERE strategy_id = :sid AND is_active = true
        '''), {'sid': strategy_id}).fetchone()
    
    if not row:
        raise ValueError(f"策略 {strategy_id} 未注册或已禁用")
    
    row = dict(row._mapping)
    module_path = row['module_path']
    default_params = row.get('default_params') or {}
    
    # 合并参数
    merged_params = {**default_params, **(params or {})}
    
    try:
        module = importlib.import_module(module_path)
        strategy = module.create_strategy(merged_params)
        
        if not isinstance(strategy, BaseStrategy):
            raise TypeError(f"{module_path}.create_strategy() 返回类型不是 BaseStrategy")
        
        _strategy_cache[cache_key] = strategy
        logger.info(f"✅ 加载策略: {strategy.get_name()} ({strategy_id})")
        return strategy
        
    except Exception as e:
        logger.error(f"❌ 加载策略 {strategy_id} 失败: {e}")
        raise


def list_strategies() -> list[dict]:
    """列出所有已注册策略"""
    engine = storage._get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text('''
            SELECT strategy_id, strategy_name, strategy_type,
                   description, support_batch, is_active
            FROM strategy_registry
            ORDER BY id
        ''')).fetchall()
    
    return [dict(r._mapping) for r in rows]


def clear_cache():
    """清除策略缓存"""
    global _strategy_cache
    _strategy_cache = {}


# === 单元测试 ===
if __name__ == '__main__':
    import os
    os.chdir(ROOT)
    
    print("=== 测试策略加载器 ===")
    
    # 列出所有策略
    strategies = list_strategies()
    print(f"已注册策略数: {len(strategies)}")
    for s in strategies:
        print(f"  - {s['strategy_id']}: {s['strategy_name']} ({s['strategy_type']})")
