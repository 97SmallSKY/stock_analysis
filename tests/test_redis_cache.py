"""tests/test_redis_cache.py — W11-2

覆盖 backend.cache.redis_cache:
  RedisCache
    - set/get roundtrip (fakeredis); JSON 序列化 datetime/Decimal
    - TTL: ex 传参;  ttl<=0 不设 ex
    - miss 返回 None 并 misses+=1
    - 无 client 时 get/set/delete 直接 no-op 返回 None/False/0
    - GET 异常走 warning, 返回 None
    - SET 序列化失败 (set 对象) 降级返回 False
    - delete 幂等
  cache_get/set/del (通过 set_default_cache)
  @cached 装饰器
    - 首次调用 miss -> 执行原函数写入缓存
    - 二次调用命中不执行原函数
    - None / [] / {} 不缓存, 下次仍执行
    - cache=None 时降级直通
    - key_fn 抛错时直通业务
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from backend.cache.redis_cache import (
    RedisCache, cached, cache_get, cache_set, cache_del, set_default_cache,
)


@pytest.fixture
def fake_redis():
    import fakeredis
    return fakeredis.FakeStrictRedis(decode_responses=True)


@pytest.fixture(autouse=True)
def _reset_default():
    set_default_cache(None)
    yield
    set_default_cache(None)


# --------------------------------------------------------------------
# RedisCache
# --------------------------------------------------------------------


class TestRedisCache:
    def test_roundtrip_json(self, fake_redis):
        c = RedisCache(fake_redis)
        ok = c.set("k1", {"ts": datetime(2026, 4, 18, 10, 0), "x": Decimal("1.23")}, ttl=60)
        assert ok is True
        v = c.get("k1")
        assert v["x"] == 1.23
        assert v["ts"].startswith("2026-04-18T10:00")
        assert c.hits == 1 and c.misses == 0

    def test_miss_returns_none_and_counts(self, fake_redis):
        c = RedisCache(fake_redis)
        assert c.get("nope") is None
        assert c.misses == 1 and c.hits == 0

    def test_ttl_applied(self, fake_redis):
        c = RedisCache(fake_redis, prefix="p:")
        c.set("k", 1, ttl=30)
        # fakeredis 支持 TTL 读取
        assert 0 < fake_redis.ttl("p:k") <= 30

    def test_no_ttl_persistent(self, fake_redis):
        c = RedisCache(fake_redis, prefix="")
        c.set("k", 1)          # ttl=None
        assert fake_redis.ttl("k") == -1   # 永不过期

    def test_none_client_noop(self):
        c = RedisCache(client=None)
        assert c.get("x") is None
        assert c.set("x", 1) is False
        assert c.delete("x") == 0

    def test_get_error_degrades(self):
        bad = MagicMock()
        bad.get.side_effect = RuntimeError("down")
        c = RedisCache(bad)
        assert c.get("k") is None
        assert c.errors == 1

    def test_set_serialize_failure(self, fake_redis):
        c = RedisCache(fake_redis)
        # set 对象 -> json 不能, _json_default str(o) fallback 可序列化, 所以此处改造
        class Nope:
            def __repr__(self):
                raise RuntimeError("repr")
        # _json_default 最后 str(o) 会触发 __repr__ -> RuntimeError, 包装失败
        assert c.set("k", Nope()) is False
        assert c.errors == 1

    def test_delete(self, fake_redis):
        c = RedisCache(fake_redis)
        c.set("k", 1)
        assert c.delete("k") == 1
        assert c.delete("k") == 0


# --------------------------------------------------------------------
# cache_get/set/del 全局便捷
# --------------------------------------------------------------------


class TestGlobalShortcut:
    def test_via_default(self, fake_redis):
        c = RedisCache(fake_redis)
        set_default_cache(c)
        assert cache_set("x", 42, ttl=60)
        assert cache_get("x") == 42
        assert cache_del("x") == 1

    def test_without_default(self):
        assert cache_get("x") is None
        assert cache_set("x", 1) is False
        assert cache_del("x") == 0


# --------------------------------------------------------------------
# @cached 装饰器
# --------------------------------------------------------------------


class TestCachedDecorator:
    def test_hit_after_first_call(self, fake_redis):
        c = RedisCache(fake_redis)
        calls = []

        @cached(lambda n: f"heavy:{n}", ttl=60, cache=c)
        def heavy(n):
            calls.append(n)
            return {"n": n, "v": n * 2}

        r1 = heavy(5)
        r2 = heavy(5)
        assert r1 == r2
        assert calls == [5]                  # 第二次没跑

    def test_different_keys_independent(self, fake_redis):
        c = RedisCache(fake_redis)
        calls = []

        @cached(lambda n: f"k:{n}", cache=c)
        def fn(n): calls.append(n); return n

        fn(1); fn(2); fn(1); fn(2)
        assert calls == [1, 2]

    def test_none_and_empty_not_cached(self, fake_redis):
        c = RedisCache(fake_redis)
        calls = []

        @cached(lambda: "none_key", cache=c)
        def none_fn(): calls.append(1); return None

        @cached(lambda: "list_key", cache=c)
        def list_fn(): calls.append(1); return []

        none_fn(); none_fn()
        list_fn(); list_fn()
        assert calls == [1, 1, 1, 1]

    def test_cache_none_no_default_direct_call(self):
        # 既没 cache 也没 default -> 直通
        calls = []

        @cached(lambda: "k")
        def fn(): calls.append(1); return 42

        assert fn() == 42
        assert fn() == 42
        assert calls == [1, 1]

    def test_key_fn_error_degrades(self, fake_redis):
        c = RedisCache(fake_redis)

        @cached(lambda: (_ for _ in ()).throw(RuntimeError("boom")), cache=c)
        def fn(): return 7

        assert fn() == 7     # key_fn 挂了仍能跑出业务

    def test_uses_default_cache(self, fake_redis):
        c = RedisCache(fake_redis)
        set_default_cache(c)
        calls = []

        @cached(lambda: "dk")
        def fn(): calls.append(1); return {"x": 1}

        fn(); fn()
        assert calls == [1]
