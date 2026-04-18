"""tests/integration/conftest.py — E2E 集成测试 fixtures.

目标: 用真实 FastAPI app (backend.api.main.app) + TestClient 跑全链路,
但把 pg_conn / Redis 换成内存 fake, 避免依赖外部服务.

注入点:
  - backend.db.session.pg_conn           -> 产生返回预置 rows 的 cursor
  - backend.api.dashboard.pg_conn         (模块级 import)
  - backend.api.lhb.pg_conn               (模块级 import)
  - backend.api.reports 不需要, 它走 app.state.report_conn_factory
  - fakeredis 作为 app.state.redis (给 /ws/snapshot)

每个测试独立 cursor, 不污染.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date
from typing import Iterable

import pytest
from fastapi.testclient import TestClient


# --------------------------------------------------------------------
# fake cursor / conn
# --------------------------------------------------------------------


class FakeCursor:
    """非常轻量的 cursor, 由 script (list[(cols, rows)]) 驱动.

    每次 execute 向下一个脚本项, fetchone/fetchall 取对应 rows.
    script 不够用时 fall back 空结果, 避免用例写脆.
    """

    def __init__(self, script: Iterable[tuple[list[str], list[tuple]]]):
        self._script = list(script)
        self._idx = -1
        self._current = ([], [])
        self.description = None
        self.rowcount = 0

    def __enter__(self): return self
    def __exit__(self, *a): return False

    def execute(self, sql, params=()):
        self._idx += 1
        if self._idx < len(self._script):
            cols, rows = self._script[self._idx]
        else:
            cols, rows = ([], [])
        self._current = (cols, rows)
        self.description = [
            type("C", (), {"name": c})() for c in cols
        ] if cols else None
        self.rowcount = len(rows)

    def fetchone(self):
        rows = self._current[1]
        return rows[0] if rows else None

    def fetchall(self):
        return list(self._current[1])


class FakeConn:
    """每次 cursor() 返回同一个 FakeCursor (按顺序服务 execute).

    支持 dict-cursor_factory: 忽略参数直接返回普通 cursor (测试里 execute
    的 sql 只需要 col name 对齐).
    """

    def __init__(self, script):
        self._cur = FakeCursor(script)
        self.rolled_back = 0
        self.committed = 0

    def cursor(self, cursor_factory=None):
        return self._cur

    def rollback(self): self.rolled_back += 1
    def commit(self): self.committed += 1
    def __enter__(self): return self
    def __exit__(self, *a): return False


@pytest.fixture
def db_script():
    """子 fixture: 返回一个 list, 测试 append (cols, rows) 准备 cursor 结果."""
    return []


@pytest.fixture
def pg_conn_factory(db_script):
    @contextmanager
    def factory():
        yield FakeConn(db_script)
    return factory


@pytest.fixture
def client(monkeypatch, pg_conn_factory):
    """返回 TestClient, 全局 patch pg_conn + 挂 fakeredis."""
    # 让所有模块 import 的 pg_conn 都是我们的 factory.
    # 注意: 这里要覆盖 backend.db.session.pg_conn 以及各 API 模块里
    # `from backend.db.session import pg_conn` 已经本地缓存的引用.
    import backend.db.session as db_session
    import backend.api.dashboard as dash_mod
    import backend.api.lhb as lhb_mod
    monkeypatch.setattr(db_session, "pg_conn", pg_conn_factory)
    monkeypatch.setattr(dash_mod, "pg_conn", pg_conn_factory)
    monkeypatch.setattr(lhb_mod, "pg_conn", pg_conn_factory)

    # app 已经在模块加载时创建, 这里复用; 不 teardown 以免影响其它用例
    from backend.api.main import app

    # /api/reports 自己查 app.state.report_conn_factory
    app.state.report_conn_factory = pg_conn_factory
    # /ws/snapshot 从 app.state.redis 取
    import fakeredis
    app.state.redis = fakeredis.FakeStrictRedis(decode_responses=True)

    return TestClient(app)
