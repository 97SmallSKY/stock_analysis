"""W11-2 Redis 热路径缓存

目标: Dashboard 首屏 / LHB 聚合 / /api/lhb 等重查询复用结果, 500ms 以下响应.

API:
    cache = RedisCache(client)      # 注入同步 redis/fakeredis 客户端
    cache_get(key) / cache_set(key, value, ttl) / cache_del(key)
    @cached(lambda request, top_n: f"dashboard:{top_n}", ttl=15)
    def my_view(...): ...

特性:
    - 默认序列化 JSON (支持 datetime/date/Decimal/numpy scalar);
      不可序列化对象 (set, bytes, custom) str() fallback.
    - Redis 失败 (ConnectionError/TimeoutError/...) 一律 warning + 透传 None,
      绝不让缓存故障击穿业务请求 (degrade to "always miss").
    - set_default_cache() 注册进程级 default, cached() 在无参时用它.
    - TTL 单位秒; None 或 <= 0 表示不过期 (SET 无 EX).
"""
from __future__ import annotations

import functools
import json
import logging
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# 进程级默认 cache 单例, 便于 @cached 无参使用
_default_cache: Optional["RedisCache"] = None


def _json_default(o: Any):
    if isinstance(o, (datetime, date)):
        return o.isoformat()
    if isinstance(o, Decimal):
        return float(o)
    to_item = getattr(o, "item", None)
    if callable(to_item):
        try:
            return to_item()
        except Exception:  # noqa: BLE001
            pass
    return str(o)


class RedisCache:
    """轻量 JSON 序列化 + TTL 封装.

    对外接口都 try/except, Redis 故障时行为等同"无缓存" (不抛).
    """

    def __init__(self, client=None, *, prefix: str = "cache:"):
        self._c = client
        self._prefix = prefix
        # 统计, 便于健康观测/测试断言
        self.hits = 0
        self.misses = 0
        self.errors = 0

    def _key(self, k: str) -> str:
        return f"{self._prefix}{k}" if self._prefix else k

    def get(self, key: str) -> Any:
        if self._c is None:
            self.misses += 1
            return None
        try:
            raw = self._c.get(self._key(key))
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[RedisCache] GET {key} failed: {e}")
            self.errors += 1
            return None
        if raw is None:
            self.misses += 1
            return None
        try:
            v = raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else raw
            self.hits += 1
            return json.loads(v)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[RedisCache] GET decode {key} failed: {e}")
            self.errors += 1
            return None

    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> bool:
        if self._c is None:
            return False
        try:
            payload = json.dumps(value, default=_json_default, ensure_ascii=False)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[RedisCache] SET serialize {key} failed: {e}")
            self.errors += 1
            return False
        try:
            if ttl and ttl > 0:
                self._c.set(self._key(key), payload, ex=int(ttl))
            else:
                self._c.set(self._key(key), payload)
            return True
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[RedisCache] SET {key} failed: {e}")
            self.errors += 1
            return False

    def delete(self, key: str) -> int:
        if self._c is None:
            return 0
        try:
            return int(self._c.delete(self._key(key)) or 0)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[RedisCache] DEL {key} failed: {e}")
            self.errors += 1
            return 0


# ------------------------------------------------------------------
# 全局便捷 API
# ------------------------------------------------------------------


def set_default_cache(cache: Optional[RedisCache]) -> None:
    global _default_cache
    _default_cache = cache


def _get_default() -> Optional[RedisCache]:
    return _default_cache


def cache_get(key: str, cache: Optional[RedisCache] = None) -> Any:
    c = cache or _get_default()
    if c is None:
        return None
    return c.get(key)


def cache_set(key: str, value: Any, ttl: Optional[int] = None,
              cache: Optional[RedisCache] = None) -> bool:
    c = cache or _get_default()
    if c is None:
        return False
    return c.set(key, value, ttl=ttl)


def cache_del(key: str, cache: Optional[RedisCache] = None) -> int:
    c = cache or _get_default()
    if c is None:
        return 0
    return c.delete(key)


def cached(
    key_fn: Callable[..., str],
    ttl: int = 15,
    *,
    cache: Optional[RedisCache] = None,
):
    """装饰器: 缓存同步函数的返回值.

    key_fn(*args, **kwargs) -> str.
    cache 为 None 时用 set_default_cache 注入的实例; 都没有则跳过缓存.
    装饰器不改变原函数签名 / 异常行为.
    """
    def deco(fn):
        @functools.wraps(fn)
        def wrapped(*args, **kwargs):
            try:
                key = key_fn(*args, **kwargs)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[cached] key_fn {fn.__name__} failed: {e}")
                return fn(*args, **kwargs)
            c = cache or _get_default()
            if c is None:
                return fn(*args, **kwargs)
            hit = c.get(key)
            if hit is not None:
                return hit
            result = fn(*args, **kwargs)
            # 只缓存非 None / 非空 dict/list. 空值让下次继续尝试 fresh.
            if result is not None and result != [] and result != {}:
                c.set(key, result, ttl=ttl)
            return result
        return wrapped
    return deco
