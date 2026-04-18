"""tests/test_runtime_wire.py — W12-7

验证 backend.core.runtime.wire_runtime:
  - 默认 env 全关: robot_registry 就绪, 其它组件均为 False, 不抛
  - REDIS 开启 + 真 ping 失败 -> status['redis'] False, 不阻塞
  - REDIS 开启 + fake_redis 能 ping -> status['redis'] True,
      app.state.cache / redis / snapshot_publisher 都就绪
  - INTRADAY 开启但无 manager -> False, 不阻塞
  - INTRADAY 开启 + mock manager + fakeredis -> True
  - WEEKLY 开启 -> True, shutdown_runtime 能关掉
  - shutdown_runtime 对未 start 的 app 幂等
"""
from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI

import backend.core.runtime as R


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    for k in ("RUNTIME_ENABLE_REDIS", "RUNTIME_ENABLE_INTRADAY",
              "RUNTIME_ENABLE_WEEKLY"):
        monkeypatch.delenv(k, raising=False)
    yield


@pytest.fixture(autouse=True)
def _reset_default_cache():
    from backend.cache import set_default_cache
    set_default_cache(None)
    yield
    set_default_cache(None)


def test_all_off_by_default():
    app = FastAPI()
    s = R.wire_runtime(app)
    assert s == {"redis": False, "intraday": False, "weekly": False, "robots": True}
    assert app.state.robot_registry == {}
    assert app.state.runtime_status == s


def test_redis_enabled_but_ping_fails(monkeypatch):
    monkeypatch.setenv("RUNTIME_ENABLE_REDIS", "1")
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:65530/0")  # 无法连接
    # 把 redis.Redis.from_url 换成会 ping 失败的客户端
    import redis
    bad = MagicMock()
    bad.ping.side_effect = RuntimeError("refused")
    monkeypatch.setattr(redis.Redis, "from_url", staticmethod(lambda *a, **k: bad))

    app = FastAPI()
    s = R.wire_runtime(app)
    assert s["redis"] is False
    assert not hasattr(app.state, "cache")


def test_redis_enabled_with_fake_ok(monkeypatch):
    monkeypatch.setenv("RUNTIME_ENABLE_REDIS", "1")
    import fakeredis
    fake = fakeredis.FakeStrictRedis(decode_responses=True)
    import redis
    monkeypatch.setattr(redis.Redis, "from_url",
                        staticmethod(lambda *a, **k: fake))

    app = FastAPI()
    s = R.wire_runtime(app)
    assert s["redis"] is True
    assert app.state.redis is fake
    assert app.state.cache is not None
    assert app.state.snapshot_publisher is not None
    # set_default_cache 生效 (cache_get 可用)
    from backend.cache import cache_set, cache_get
    cache_set("probe", {"v": 1}, ttl=60)
    assert cache_get("probe") == {"v": 1}


def test_intraday_without_manager_is_skipped(monkeypatch):
    monkeypatch.setenv("RUNTIME_ENABLE_INTRADAY", "1")
    app = FastAPI()
    # 不设 app.state.manager
    s = R.wire_runtime(app)
    assert s["intraday"] is False


def test_intraday_with_manager_ok(monkeypatch):
    monkeypatch.setenv("RUNTIME_ENABLE_INTRADAY", "1")
    app = FastAPI()
    # 给个最小 manager (adapter 为空亦可, scheduler.start 不依赖真 adapter)
    mgr = MagicMock()
    mgr._router._adapters = {}
    app.state.manager = mgr
    s = R.wire_runtime(app)
    assert s["intraday"] is True
    assert app.state.intraday_scheduler is not None
    R.shutdown_runtime(app)  # 清理 scheduler


def test_weekly_enabled(monkeypatch):
    monkeypatch.setenv("RUNTIME_ENABLE_WEEKLY", "1")
    app = FastAPI()
    s = R.wire_runtime(app)
    assert s["weekly"] is True
    assert app.state.weekly_scheduler is not None
    R.shutdown_runtime(app)


def test_shutdown_idempotent_noscheduler():
    app = FastAPI()
    # 没跑 wire_runtime 也能调
    R.shutdown_runtime(app)
    R.shutdown_runtime(app)


def test_redis_missing_module_skips(monkeypatch):
    monkeypatch.setenv("RUNTIME_ENABLE_REDIS", "1")
    # 临时抽掉 redis 模块
    saved = sys.modules.pop("redis", None)
    monkeypatch.setitem(sys.modules, "redis", None)  # import redis 会拿到 None 触发 ImportError
    try:
        app = FastAPI()
        s = R.wire_runtime(app)
        # ImportError 会被 except 吃掉 -> False
        assert s["redis"] is False
    finally:
        if saved is not None:
            sys.modules["redis"] = saved
        else:
            sys.modules.pop("redis", None)
