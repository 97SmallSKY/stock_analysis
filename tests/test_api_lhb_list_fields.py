"""STO-74: /api/lhb 列表返回必须含 close / pct_change,且可按 pct_change 排序。"""
from __future__ import annotations

import datetime as dt
from contextlib import contextmanager

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


class _FakeCursor:
    def __init__(self, queue):
        self._queue = list(queue)
        self._current_rows: list = []
        self.description = []
        self.last_sql: str = ""

    def execute(self, sql, *a, **k):
        self.last_sql = sql
        if not self._queue:
            self._current_rows = []
            self.description = []
            return
        rows, cols = self._queue.pop(0)
        self._current_rows = list(rows)
        self.description = [type("C", (), {"name": n}) for n in cols]

    def fetchone(self):
        return self._current_rows[0] if self._current_rows else None

    def fetchall(self):
        return self._current_rows

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeConn:
    def __init__(self, queue):
        self.cur = _FakeCursor(queue)

    def cursor(self):
        return self.cur

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def commit(self):
        pass


def _make_pg(queue):
    holder = {}

    @contextmanager
    def fake():
        conn = _FakeConn(queue)
        holder["conn"] = conn
        yield conn

    return fake, holder


@pytest.fixture
def client(monkeypatch):
    from backend.api import lhb as lhb_mod
    lhb_mod._lhb_cache.clear()
    app = FastAPI()
    app.include_router(lhb_mod.router, prefix="/api/lhb")
    return TestClient(app), lhb_mod


def test_list_returns_close_and_pct_change(client, monkeypatch):
    cli, _ = client
    rows = [
        ("000001.SZ", "平安银行", dt.date(2026, 4, 24), "日振幅值达到7%",
         12.34, 0.0521, 100.0, 50.0, 50.0, 800.0, 9.1, {}),
    ]
    cols = ["ts_code", "name", "trade_date", "reason",
            "close", "pct_change",
            "buy_amount", "sell_amount", "net_amount", "turnover",
            "score", "score_detail"]
    fake_pg, _ = _make_pg([([(1,)], ["count"]), (rows, cols)])
    monkeypatch.setattr("backend.api.lhb.pg_conn", fake_pg)

    r = cli.get("/api/lhb?date_from=20260424&limit=5")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["total"] == 1
    row = data["rows"][0]
    # STO-74 核心断言:API 必须透出 close 和 pct_change
    assert "close" in row and row["close"] == 12.34
    assert "pct_change" in row and row["pct_change"] == pytest.approx(0.0521)


def test_list_accepts_order_by_pct_change(client, monkeypatch):
    cli, lhb_mod = client
    lhb_mod._lhb_cache.clear()
    fake_pg, holder = _make_pg([([(0,)], ["count"]), ([], [])])
    monkeypatch.setattr("backend.api.lhb.pg_conn", fake_pg)

    r = cli.get("/api/lhb?order_by=pct_change&limit=5")
    assert r.status_code == 200, r.text
    # SQL 真正按 pct_change 列排序(防映射表漏补)
    assert "ORDER BY l.pct_change" in holder["conn"].cur.last_sql


def test_list_rejects_unknown_order_by(client, monkeypatch):
    cli, _ = client
    r = cli.get("/api/lhb?order_by=close&limit=5")
    # close 不在白名单 → 422
    assert r.status_code == 422
