"""backend.cache — W11-2 Redis 热路径缓存."""
from .redis_cache import (
    RedisCache,
    cache_get, cache_set, cache_del, set_default_cache,
    cached,
)

__all__ = [
    "RedisCache",
    "cache_get", "cache_set", "cache_del", "set_default_cache",
    "cached",
]
