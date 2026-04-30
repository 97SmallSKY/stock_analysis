"""/api/lhb/* Pydantic response_model 契约测试 (STO-121 Phase 1b).

只测 response_model 绑定生效，不测业务逻辑（业务逻辑在 test_api_lhb.py）。
"""
from __future__ import annotations

import datetime as dt
from contextlib import contextmanager

from fastapi import FastAPI
from fastapi.testclient import TestClient


class _FakeCursor:
    def __init__(self, result_queue):
        self._queue = list(result_queue)
        self._current_rows: list = []
        self.description = []

    def execute(self, *a, **k):
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
    def __init__(self, result_queue):
        self._cursor = _FakeCursor(result_queue)

    def cursor(self):
        return self._cursor

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def commit(self):
        pass


def _make_pg_conn(canned_list):
    call_count = {"n": 0}

    @contextmanager
    def fake():
        idx = call_count["n"]
        call_count["n"] += 1
        conn = _FakeConn(canned_list[idx])
        yield conn

    return fake


def _mount() -> TestClient:
    from backend.api.lhb import router as lhb_router
    app = FastAPI()
    app.include_router(lhb_router, prefix="/api/lhb")
    return TestClient(app)


def test_list_lhb_response_model(monkeypatch):
    """STO-121: response_model=LhbListResp 绑定生效，响应包含必填字段."""
    rows = [
        ("000001.SZ", "平安银行", dt.date(2026, 1, 5), "机构买入",
         10.8, 0.023, 5000.0, 3000.0, 2000.0, 100000.0, 75.5, None),
    ]
    cols = ["ts_code", "name", "trade_date", "reason",
            "close", "pct_change", "buy_amount", "sell_amount",
            "net_amount", "turnover", "score", "score_detail"]
    monkeypatch.setattr(
        "backend.api.lhb.pg_conn",
        _make_pg_conn([
            [([(1,)], ["count"])],  # COUNT(*) 查询
            [(rows, cols)],          # 主查询
        ]),
    )
    c = _mount()
    r = c.get("/api/lhb?limit=10")
    assert r.status_code == 200, r.text
    body = r.json()
    # response_model=LhbListResp 确保这些字段一定存在
    assert "total" in body and "limit" in body and "offset" in body and "rows" in body
    assert isinstance(body["rows"], list)
    if body["rows"]:
        row = body["rows"][0]
        assert "ts_code" in row
        assert row["ts_code"] == "000001.SZ"


def test_list_seats_response_model(monkeypatch):
    """STO-121: response_model=LhbSeatsResp 绑定生效."""
    rows = [
        ("000001.SZ", "平安银行", dt.date(2026, 1, 5), "国泰君安", "BUY",
         5000.0, 0.0, 5000.0, 1),
    ]
    cols = ["ts_code", "name", "trade_date", "broker_name", "direction",
            "buy_amount", "sell_amount", "net_amount", "rank_no"]
    monkeypatch.setattr(
        "backend.api.lhb.pg_conn",
        _make_pg_conn([[(rows, cols)]]),
    )
    c = _mount()
    r = c.get("/api/lhb/seats?trade_date=20260105")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "rows" in body and "n" in body
    assert isinstance(body["rows"], list)
    assert body["n"] == 1
