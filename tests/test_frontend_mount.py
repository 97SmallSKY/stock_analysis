"""tests/test_frontend_mount.py — P5.1b

验证 backend.api.main._mount_frontend 行为:
  - dist 目录不存在时只 warn,不抛
  - dist 存在时 / 返回 index.html,SPA fallback 把非 API 路径也指到 index.html
  - /assets/* 走 StaticFiles(返回真实文件内容)
  - 不吃掉 /healthz 等 API 路由
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.main import _mount_frontend


def _make_app(monkeypatch, dist: Path | None) -> FastAPI:
    """造一个最小 FastAPI,强制 _frontend_dist_dir 指向指定目录。"""
    app = FastAPI()

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    @app.get("/api/ping")
    def api_ping():
        return {"pong": True}

    if dist is not None:
        monkeypatch.setattr(
            "backend.api.main._frontend_dist_dir", lambda: dist,
        )
    _mount_frontend(app)
    return app


# ----------------------------------------------------------------------
# dist 不存在:静默跳过
# ----------------------------------------------------------------------


def test_mount_noop_when_dist_missing(monkeypatch, tmp_path):
    fake_dist = tmp_path / "nonexistent"
    app = _make_app(monkeypatch, fake_dist)
    client = TestClient(app)
    # API 仍然正常
    assert client.get("/healthz").status_code == 200
    # SPA 挂载应未生效, / 没被注册 → 404
    assert client.get("/").status_code == 404


# ----------------------------------------------------------------------
# dist 存在:挂载成功,行为正确
# ----------------------------------------------------------------------


def _build_fake_dist(root: Path) -> Path:
    """模拟 vite 产物。"""
    dist = root / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text(
        "<!doctype html><html><body>SPA-ROOT</body></html>", encoding="utf-8",
    )
    (dist / "assets" / "main.abc123.js").write_text(
        "console.log('hello-vite');", encoding="utf-8",
    )
    (dist / "favicon.ico").write_bytes(b"\x00\x00ICO")
    return dist


def test_root_returns_index_html(monkeypatch, tmp_path):
    dist = _build_fake_dist(tmp_path)
    app = _make_app(monkeypatch, dist)
    r = TestClient(app).get("/")
    assert r.status_code == 200
    assert "SPA-ROOT" in r.text


def test_assets_static_served(monkeypatch, tmp_path):
    dist = _build_fake_dist(tmp_path)
    app = _make_app(monkeypatch, dist)
    r = TestClient(app).get("/assets/main.abc123.js")
    assert r.status_code == 200
    assert "hello-vite" in r.text


def test_direct_file_served(monkeypatch, tmp_path):
    dist = _build_fake_dist(tmp_path)
    app = _make_app(monkeypatch, dist)
    r = TestClient(app).get("/favicon.ico")
    assert r.status_code == 200
    assert r.content.startswith(b"\x00\x00ICO")


def test_spa_fallback_returns_index_html(monkeypatch, tmp_path):
    dist = _build_fake_dist(tmp_path)
    app = _make_app(monkeypatch, dist)
    # Vue Router 的一个虚拟路径,应该 fallback 到 index.html
    r = TestClient(app).get("/lhb/daily/20240101")
    assert r.status_code == 200
    assert "SPA-ROOT" in r.text


def test_api_route_not_shadowed(monkeypatch, tmp_path):
    dist = _build_fake_dist(tmp_path)
    app = _make_app(monkeypatch, dist)
    client = TestClient(app)
    # 先挂了 healthz/api,再挂 SPA fallback;FastAPI 应优先命中先注册的具体路径
    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.get("/api/ping").json() == {"pong": True}
