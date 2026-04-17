"""
多数据源适配层

提供统一的数据获取接口，支持多个数据源的优先级路由、熔断降级、缓存管理。
"""

from .priority_config import DataSource, DataCategory, PriorityConfig
from .circuit_breaker import CircuitBreaker, CBState, CBConfig
from .cache_manager import CacheManager, CacheEntry
from .adapters.base import BaseDataAdapter, FetchResult
from .manager import DataSourceManager

__all__ = [
    # 配置
    "DataSource",
    "DataCategory",
    "PriorityConfig",
    # 熔断器
    "CircuitBreaker",
    "CBState",
    "CBConfig",
    # 缓存
    "CacheManager",
    "CacheEntry",
    # 适配器
    "BaseDataAdapter",
    "FetchResult",
    # 管理器
    "DataSourceManager",
]
