"""STO-58: CORS allowlist 测试。

覆盖 3 场景 + 1 bonus:
    1. 空 allowlist(同源部署,无 CORS 响应头)
    2. 授权源(Origin in allowlist) -> 响应头含 Access-Control-Allow-Origin
    3. 未授权源(Origin 不在 allowlist) -> 响应头不含 Access-Control-Allow-Origin
    4. (bonus) allow_methods 收敛,PATCH 不在列表

注意:CORSMiddleware 对未授权源不返 400,而是不加 CORS 响应头,
浏览器根据缺失的响应头拒绝请求。所以测试断言「不含 header」,不是 4xx。
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient


def _reset_settings() -> None:
    import backend.config as cfg
    cfg._settings = None


def _make_app() -> FastAPI:
    from backend.config import get_settings
    s = get_settings()
    app = FastAPI()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=s.cors.origins,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "X-API-Key", "Authorization"],
    )

    @app.get("/ping")
    def _ping() -> dict:
        return {"ok": True}

    return app


def test_empty_allowlist_no_cors_header(monkeypatch):
    monkeypatch.setenv("ALLOWED_ORIGINS", "")
    _reset_settings()
    client = TestClient(_make_app())

    r = client.options(
        "/ping",
        headers={
            "Origin": "https://evil.com",
            "Access-Control-Request-Method": "GET",
        },
    )
    lower_keys = {k.lower() for k in r.headers}
    assert "access-control-allow-origin" not in lower_keys


def test_authorized_origin_gets_cors_header(monkeypatch):
    monkeypatch.setenv(
        "ALLOWED_ORIGINS", "http://localhost:5173,http://jd.117.72.184.8"
    )
    _reset_settings()
    client = TestClient(_make_app())

    r = client.options(
        "/ping",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert r.headers.get("access-control-allow-origin") == "http://localhost:5173"


def test_unauthorized_origin_no_cors_header(monkeypatch):
    monkeypatch.setenv("ALLOWED_ORIGINS", "http://localhost:5173")
    _reset_settings()
    client = TestClient(_make_app())

    r = client.options(
        "/ping",
        headers={
            "Origin": "https://evil.com",
            "Access-Control-Request-Method": "GET",
        },
    )
    lower_keys = {k.lower() for k in r.headers}
    assert "access-control-allow-origin" not in lower_keys


def test_allowed_methods_excludes_patch(monkeypatch):
    """AC (d):allow_methods 收敛,PATCH 不在列表。"""
    monkeypatch.setenv("ALLOWED_ORIGINS", "http://localhost:5173")
    _reset_settings()
    client = TestClient(_make_app())

    r = client.options(
        "/ping",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "PATCH",
        },
    )
    allow_methods = r.headers.get("access-control-allow-methods", "")
    assert "PATCH" not in allow_methods
    assert "GET" in allow_methods
