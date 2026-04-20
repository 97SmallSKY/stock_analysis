"""STO-6: API 鉴权 + 限流测试。

覆盖 4 场景 + 开关行为:
    1. AUTH_ENABLED=false (默认)    -> 写端点无 key 也能通过 (向后兼容)
    2. AUTH_ENABLED=true, 无 key      -> 401
    3. AUTH_ENABLED=true, 错 key      -> 403
    4. AUTH_ENABLED=true, 正 key      -> 200
    5. 超限 -> 429 (写端点 RATE_LIMIT_WRITE=2/minute)

注意
----
- ``get_settings`` 有 lru/单例缓存, 测试里用 ``reload_settings`` fixture 清掉。
- slowapi 用内存 backend, Limiter 是模块级单例, 不同测试共享计数器。
  为避免污染, 每个超限测试独立改 ``RATE_LIMIT_WRITE`` 值 + 重建 app 够用;
  但更稳的做法是 reset limiter storage (``limiter.reset()``), 见 test_rate_limit_429。
"""
from __future__ import annotations

import importlib

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.robots import router as robots_router


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _reset_settings():
    """清掉 config 单例, 让下一次 get_settings() 重读环境变量。"""
    import backend.config as cfg
    cfg._settings = None


def _make_app() -> FastAPI:
    """最小 app:挂 robots router + 限流中间件。每个测试独立 app,避免状态泄漏。"""
    # 先 reload rate_limit 模块(limiter 是模块级单例,必须重建)
    import backend.core.rate_limit as rl
    importlib.reload(rl)
    # robots router import 了 rate_limit.default_limit / write_limit,
    # reload rate_limit 后这些符号绑定到了新 limiter;必须也 reload 路由模块。
    import backend.api.robots as robots_mod
    importlib.reload(robots_mod)

    app = FastAPI()
    app.state.robot_registry = {}
    rl.install_rate_limit(app)
    app.include_router(robots_mod.router, prefix="/api/robots")
    return app


@pytest.fixture
def env_auth_off(monkeypatch):
    """场景 1:鉴权关闭(默认行为)。"""
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("API_KEYS", "")
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")  # 隔离限流
    _reset_settings()
    yield
    _reset_settings()


@pytest.fixture
def env_auth_on(monkeypatch):
    """场景 2~4:鉴权开启, 配置两个 key。"""
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("API_KEYS", "key-alpha-secret,key-beta-secret")
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    _reset_settings()
    yield
    _reset_settings()


# ---------------------------------------------------------------------------
# 场景 1: AUTH_ENABLED=false 向后兼容
# ---------------------------------------------------------------------------

def test_auth_disabled_allows_write_without_key(env_auth_off):
    """AUTH_ENABLED=false 时,写端点无 key 也能通过 (jd 当前部署保持兼容)。"""
    client = TestClient(_make_app())
    r = client.post("/api/robots/create", json={"cash": 100_000})
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True


def test_auth_enabled_but_no_keys_configured_still_allows(monkeypatch):
    """AUTH_ENABLED=true 但 API_KEYS 为空 -> effective_enabled=False,放行。

    这是防误部署锁死的安全阀。
    """
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("API_KEYS", "")
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    _reset_settings()
    try:
        client = TestClient(_make_app())
        r = client.post("/api/robots/create", json={"cash": 100_000})
        assert r.status_code == 200
    finally:
        _reset_settings()


# ---------------------------------------------------------------------------
# 场景 2: 鉴权开启 + 无 Key -> 401
# ---------------------------------------------------------------------------

def test_auth_enabled_missing_key_returns_401(env_auth_on):
    client = TestClient(_make_app())
    r = client.post("/api/robots/create", json={"cash": 100_000})
    assert r.status_code == 401
    assert "Missing API key" in r.json()["detail"]
    assert r.headers.get("WWW-Authenticate") == "ApiKey"


# ---------------------------------------------------------------------------
# 场景 3: 鉴权开启 + 错 Key -> 403
# ---------------------------------------------------------------------------

def test_auth_enabled_wrong_key_returns_403(env_auth_on):
    client = TestClient(_make_app())
    r = client.post(
        "/api/robots/create",
        json={"cash": 100_000},
        headers={"X-API-Key": "wrong-key"},
    )
    assert r.status_code == 403
    assert "Invalid API key" in r.json()["detail"]


# ---------------------------------------------------------------------------
# 场景 4: 鉴权开启 + 正 Key -> 200
# ---------------------------------------------------------------------------

def test_auth_enabled_correct_key_returns_200(env_auth_on):
    client = TestClient(_make_app())
    r = client.post(
        "/api/robots/create",
        json={"cash": 100_000},
        headers={"X-API-Key": "key-alpha-secret"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True


def test_auth_enabled_second_key_also_works(env_auth_on):
    """多 key 配置下,任一有效都放行(便于 key 轮换)。"""
    client = TestClient(_make_app())
    r = client.post(
        "/api/robots/create",
        json={"cash": 100_000},
        headers={"X-API-Key": "key-beta-secret"},
    )
    assert r.status_code == 200, r.text


# ---------------------------------------------------------------------------
# 只读端点即使鉴权开启也不要求 key
# ---------------------------------------------------------------------------

def test_read_endpoints_dont_require_key(env_auth_on):
    """GET /api/robots/strategies 是只读,鉴权开启状态下也应无需 key 通过。"""
    client = TestClient(_make_app())
    r = client.get("/api/robots/strategies")
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# 场景 5: 限流 -> 429
# ---------------------------------------------------------------------------

def test_rate_limit_429(monkeypatch):
    """写端点第 3 次请求应返回 429(RATE_LIMIT_WRITE=2/minute)。"""
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "true")
    monkeypatch.setenv("RATE_LIMIT_WRITE", "2/minute")
    monkeypatch.setenv("RATE_LIMIT_DEFAULT", "100/minute")
    _reset_settings()
    try:
        app = _make_app()
        client = TestClient(app)
        # 前两次 200
        for i in range(2):
            r = client.post("/api/robots/create", json={"cash": 1000})
            assert r.status_code == 200, f"req {i}: {r.text}"
        # 第三次 429
        r3 = client.post("/api/robots/create", json={"cash": 1000})
        assert r3.status_code == 429, r3.text
        body = r3.json()
        assert "Rate limit exceeded" in body["detail"]
    finally:
        _reset_settings()


def test_rate_limit_global_disable_exempts_all(monkeypatch):
    """RATE_LIMIT_ENABLED=false 时,即使装了装饰器也全部放行(全局开关)。

    用一个极严 limit=1/minute 的端点,连续 5 次都应 200,证明 exempt_when 生效。
    """
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    monkeypatch.setenv("RATE_LIMIT_WRITE", "1/minute")
    monkeypatch.setenv("AUTH_ENABLED", "false")
    _reset_settings()
    try:
        app = _make_app()
        client = TestClient(app)
        for i in range(5):
            r = client.post("/api/robots/create", json={"cash": 1000})
            assert r.status_code == 200, f"req {i}: {r.text}"
    finally:
        _reset_settings()
