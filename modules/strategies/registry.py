from .base import BaseStrategy

_REGISTRY: dict[str, type[BaseStrategy]] = {}


def register_strategy(name: str):
    def decorator(cls):
        _REGISTRY[name] = cls
        cls.name = name
        return cls
    return decorator


def get_strategy(name: str) -> type[BaseStrategy]:
    return _REGISTRY[name]


def list_strategies() -> list[str]:
    return list(_REGISTRY.keys())


def list_strategy_meta() -> list[dict]:
    return [
        {"name": name, "description": cls.description, "param_schema": cls.param_schema}
        for name, cls in _REGISTRY.items()
    ]
