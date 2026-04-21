"""STO-56 · SPA fallback 路径遍历加固回归测试。

漏洞复现(加固前)::

    GET /../package.json          → 200 + frontend/package.json 内容
    GET /../../backend/config.py  → 200 + 后端配置源码
    GET /../../.env               → 200 + 敏感环境变量

根因:`_spa_fallback` 里 `dist / path` 允许 `..` 逃出 dist,且 `candidate.is_file()`
直接对逃出路径放行,把 dist 外的任意文件作为 FileResponse 返回。

加固:`.resolve().is_relative_to(dist_resolved)` 校验候选路径必须落在 dist 根下;
否则兜底返回 index.html(既不泄漏,也不破坏 SPA history 模式的合法前端路由)。

本测试通过 raw ASGI scope 注入未归一路径,绕过 httpx TestClient 的 URL 归一化
(httpx 会把 `..` resolve 掉,无法复现真实攻击)。
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest


# --------------------------------------------------------------------
# 辅助:建一个临时 dist,其 sibling 放敏感文件(模拟真实部署 layout)
# --------------------------------------------------------------------


@pytest.fixture
def fake_spa_app(tmp_path, monkeypatch):
    """构造包含可泄漏敏感文件的临时 FastAPI app。

    目录结构(模拟真实 frontend/ 布局):
      tmp_path/
        frontend/
          dist/
            index.html
            assets/main.js
          SECRET.txt            ← dist sibling,加固前 `/../SECRET.txt` 可读
        OUTER_SECRET.txt        ← dist.parent.parent sibling,加固前 `/../../OUTER_SECRET.txt` 可读
    """
    frontend = tmp_path / "frontend"
    dist = frontend / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><body>SPA_INDEX</body>")
    (dist / "assets" / "main.js").write_text("// ok main asset")
    (frontend / "SECRET.txt").write_text("SIBLING_LEAK_MARKER")
    (tmp_path / "OUTER_SECRET.txt").write_text("OUTER_LEAK_MARKER")

    # monkey patch dist 解析函数,让 _mount_frontend 指向临时 dist
    import backend.api.main as M
    monkeypatch.setattr(M, "_frontend_dist_dir", lambda: dist)

    app = M.create_app()
    return app, dist, frontend


# --------------------------------------------------------------------
# Raw ASGI 请求工具(不经 httpx,保留 raw path 里的 ..)
# --------------------------------------------------------------------


async def _raw_get(app: Any, path: str) -> tuple[int, bytes]:
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [(b"host", b"testserver")],
        "server": ("127.0.0.1", 8000),
        "client": ("127.0.0.1", 12345),
    }
    body_chunks: list[bytes] = []
    status_holder = [500]

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(msg):
        if msg["type"] == "http.response.start":
            status_holder[0] = msg["status"]
        elif msg["type"] == "http.response.body":
            body_chunks.append(msg.get("body", b""))

    await app(scope, receive, send)
    return status_holder[0], b"".join(body_chunks)


def raw_get(app: Any, path: str) -> tuple[int, bytes]:
    return asyncio.get_event_loop().run_until_complete(_raw_get(app, path)) \
        if False else asyncio.run(_raw_get(app, path))


# --------------------------------------------------------------------
# 攻击路径:断言不泄漏(body 不含 sibling 文件标记字符串)
# --------------------------------------------------------------------


class TestPathTraversalBlocked:
    def test_sibling_file_not_leaked(self, fake_spa_app):
        """`/../SECRET.txt` 不能读到 dist sibling(SIBLING_LEAK_MARKER)。"""
        app, _dist, _frontend = fake_spa_app
        status, body = raw_get(app, "/../SECRET.txt")
        assert status == 200  # SPA fallback 返回 index.html 也是 200
        assert b"SIBLING_LEAK_MARKER" not in body
        assert b"SPA_INDEX" in body  # 应兜底返回 index.html

    def test_deep_parent_file_not_leaked(self, fake_spa_app):
        """`/../../OUTER_SECRET.txt` 不能读到项目外文件。"""
        app, _dist, _frontend = fake_spa_app
        status, body = raw_get(app, "/../../OUTER_SECRET.txt")
        assert status == 200
        assert b"OUTER_LEAK_MARKER" not in body
        assert b"SPA_INDEX" in body

    def test_static_prefix_attack_normalized_to_index(self, fake_spa_app):
        """`/static/../SECRET.txt` 被 Starlette 归一化后走 SPA fallback,同样不泄漏。"""
        app, _dist, _frontend = fake_spa_app
        status, body = raw_get(app, "/static/../SECRET.txt")
        assert status == 200
        assert b"SIBLING_LEAK_MARKER" not in body

    def test_absolute_path_attempt_not_leaked(self, fake_spa_app):
        """尝试直接绝对路径(如 `//etc/passwd`)也不应越权读文件。"""
        app, _dist, _frontend = fake_spa_app
        # 注意:Starlette 会对 scope.path 做基本解析,这里用 /etc/passwd 冒名顶替
        status, body = raw_get(app, "/etc/passwd")
        assert status == 200
        # 线上 /etc/passwd 常见开头 root:x:0:0;我们不去读它,只要 body 不含即可
        assert b"root:x:" not in body
        assert b"SPA_INDEX" in body


# --------------------------------------------------------------------
# 正向行为:合法请求仍能工作
# --------------------------------------------------------------------


class TestSpaFallbackHappyPath:
    def test_root_returns_index(self, fake_spa_app):
        app, _dist, _frontend = fake_spa_app
        status, body = raw_get(app, "/")
        assert status == 200
        assert b"SPA_INDEX" in body

    def test_asset_direct_link(self, fake_spa_app):
        """/assets/main.js 走 StaticFiles,不经 SPA fallback。"""
        app, _dist, _frontend = fake_spa_app
        status, body = raw_get(app, "/assets/main.js")
        assert status == 200
        assert b"ok main asset" in body

    def test_unknown_vue_route_returns_index(self, fake_spa_app):
        """合法前端 history 路由(如 /robots)应兜底返 index.html。"""
        app, _dist, _frontend = fake_spa_app
        status, body = raw_get(app, "/robots")
        assert status == 200
        assert b"SPA_INDEX" in body

    def test_inside_dist_file_served_directly(self, fake_spa_app, tmp_path):
        """dist 根下确实存在的文件(如 /favicon.ico)仍可直接 serve。"""
        app, dist, _frontend = fake_spa_app
        (dist / "favicon.ico").write_bytes(b"ICO_CONTENT")
        status, body = raw_get(app, "/favicon.ico")
        assert status == 200
        assert body == b"ICO_CONTENT"
