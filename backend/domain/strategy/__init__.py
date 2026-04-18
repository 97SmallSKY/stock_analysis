"""策略领域层.

模块:
  base      BaseStrategy 抽象基类 + 数据契约 (Candidate / SellSignal)
  registry  @register_strategy 装饰器 + 工厂查询
  builtins  若干内置参考策略 (LhbHotMoney / LhbInstitution)
"""
from .base import (
    BaseStrategy,
    Candidate,
    SellSignal,
    MarketContext,
    Position,
)
from .registry import (
    register_strategy,
    get_strategy,
    list_strategies,
    clear_registry,
)

__all__ = [
    "BaseStrategy", "Candidate", "SellSignal", "MarketContext", "Position",
    "register_strategy", "get_strategy", "list_strategies", "clear_registry",
]
