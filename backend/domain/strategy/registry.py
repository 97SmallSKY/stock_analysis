"""策略注册表.

用法:
    from backend.domain.strategy import register_strategy, BaseStrategy

    @register_strategy("lhb_hot_money")
    class LhbHotMoneyStrategy(BaseStrategy):
        name = "lhb_hot_money"
        ...

    cls = get_strategy("lhb_hot_money")
    inst = cls(params={"threshold": 70})

设计:
- 注册表是进程内全局 dict, 运行时按 name 查找
- 装饰器可接受显式 name, 否则用 cls.name
- 重复注册抛 ValueError, 除非 override=True (便于测试/热更)
- clear_registry() 专供测试
"""
from __future__ import annotations

from typing import Callable, Optional, Type

from backend.domain.strategy.base import BaseStrategy

_REGISTRY: dict[str, Type[BaseStrategy]] = {}


def register_strategy(name: Optional[str] = None, *,
                      override: bool = False) -> Callable:
    """装饰器: 注册 BaseStrategy 子类到全局注册表."""
    def _decorator(cls: Type[BaseStrategy]) -> Type[BaseStrategy]:
        if not isinstance(cls, type) or not issubclass(cls, BaseStrategy):
            raise TypeError(
                f"register_strategy 只能装饰 BaseStrategy 子类, 收到 {cls!r}"
            )
        key = name or getattr(cls, "name", None) or cls.__name__
        if not key:
            raise ValueError("注册名不能为空")
        if key in _REGISTRY and not override:
            raise ValueError(f"策略名 {key!r} 已注册 (如需覆盖请 override=True)")
        # 保证 cls.name 和注册键一致
        if getattr(cls, "name", None) != key:
            cls.name = key
        _REGISTRY[key] = cls
        return cls
    return _decorator


def get_strategy(name: str) -> Type[BaseStrategy]:
    if name not in _REGISTRY:
        raise KeyError(
            f"未知策略 {name!r}, 已注册: {sorted(_REGISTRY.keys())}"
        )
    return _REGISTRY[name]


def list_strategies() -> list[str]:
    return sorted(_REGISTRY.keys())


def clear_registry() -> None:
    """仅供测试: 清空注册表."""
    _REGISTRY.clear()
