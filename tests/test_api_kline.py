"""P7.3 /api/kline/{daily,weekly,minute} 接口单测.

与 test_api_lhb_dashboard.py 同构: monkeypatch pg_conn 返回 canned rows.
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
    from backend.api.kline import router as kline_router
    app = FastAPI()
    app.include_router(kline_router, prefix="/api/kline")
    return TestClient(app)


# ---------------------------------------------------------------------
# /api/kline/daily
# ---------------------------------------------------------------------
def test_kline_daily_happy(monkeypatch):
    rows = [
        (dt.date(2026, 1, 5), 10.0, 11.0, 9.5, 10.8, 123456.0),
        (dt.date(2026, 1, 6), 10.8, 11.2, 10.5, 11.0, 100000.0),
    ]
    cols = ["trade_date", "open", "high", "low", "close", "vol"]
    monkeypatch.setattr(
        "backend.api.kline.pg_conn", _make_pg_conn([[(rows, cols)]])
    )
    c = _mount()
    r = c.get("/api/kline/daily?ts_code=000001.SZ&from=20260101&to=20260131")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ts_code"] == "000001.SZ"
    assert body["freq"] == "daily"
    assert body["empty_reason"] is None
    assert len(body["rows"]) == 2
    assert body["rows"][0]["date"] == "2026-01-05"
    assert body["rows"][0]["close"] == 10.8


def test_kline_daily_empty(monkeypatch):
    """DB 返回空 → empty_reason='out_of_range'."""
    cols = ["trade_date", "open", "high", "low", "close", "vol"]
    monkeypatch.setattr(
        "backend.api.kline.pg_conn", _make_pg_conn([[([], cols)]])
    )
    c = _mount()
    r = c.get("/api/kline/daily?ts_code=000001.SZ")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["rows"] == []
    assert body["empty_reason"] == "out_of_range"


def test_kline_daily_bad_code():
    c = _mount()
    r = c.get("/api/kline/daily?ts_code=BADCODE")
    assert r.status_code == 400


def test_kline_daily_bad_date():
    c = _mount()
    r = c.get("/api/kline/daily?ts_code=000001.SZ&from=abc")
    assert r.status_code == 400


def test_kline_daily_code_normalized(monkeypatch):
    """小写 ts_code 应被归一成大写."""
    cols = ["trade_date", "open", "high", "low", "close", "vol"]
    monkeypatch.setattr(
        "backend.api.kline.pg_conn", _make_pg_conn([[([], cols)]])
    )
    c = _mount()
    r = c.get("/api/kline/daily?ts_code=000001.sz")
    assert r.status_code == 200
    assert r.json()["ts_code"] == "000001.SZ"


# ---------------------------------------------------------------------
# /api/kline/weekly
# ---------------------------------------------------------------------
def test_kline_weekly_happy(monkeypatch):
    rows = [
        (dt.date(2026, 1, 5), 10.0, 11.5, 9.5, 11.0, 500000.0),
        (dt.date(2026, 1, 12), 11.0, 12.0, 10.8, 11.8, 420000.0),
    ]
    cols = ["date", "open", "high", "low", "close", "vol"]
    monkeypatch.setattr(
        "backend.api.kline.pg_conn", _make_pg_conn([[(rows, cols)]])
    )
    c = _mount()
    r = c.get("/api/kline/weekly?ts_code=000001.SZ&from=20260101&to=20260201")
    assert r.status_code == 200
    body = r.json()
    assert body["freq"] == "weekly"
    assert len(body["rows"]) == 2
    assert body["rows"][0]["close"] == 11.0


# ---------------------------------------------------------------------
# /api/kline/minute — 永远返回空 + empty_reason
# ---------------------------------------------------------------------
def test_kline_minute_no_source():
    """分钟 K 当前无数据源, 统一返回 empty_reason='no_source_table'."""
    c = _mount()
    r = c.get("/api/kline/minute?ts_code=000001.SZ&trade_date=20260418")
    assert r.status_code == 200
    body = r.json()
    assert body["freq"] == "minute"
    assert body["rows"] == []
    assert body["empty_reason"] == "no_source_table"
    assert body["trade_date"] == "2026-04-18"


def test_kline_minute_bad_date():
    c = _mount()
    r = c.get("/api/kline/minute?ts_code=000001.SZ&trade_date=bad")
    assert r.status_code == 400


def test_kline_daily_db_failure(monkeypatch):
    """pg_conn 抛异常 → 500."""
    from contextlib import contextmanager

    @contextmanager
    def boom():
        raise RuntimeError("db dead")
        yield  # pragma: no cover

    monkeypatch.setattr("backend.api.kline.pg_conn", boom)
    c = _mount()
    r = c.get("/api/kline/daily?ts_code=000001.SZ")
    assert r.status_code == 500


def test_kline_weekly_empty(monkeypatch):
    cols = ["date", "open", "high", "low", "close", "vol"]
    monkeypatch.setattr(
        "backend.api.kline.pg_conn", _make_pg_conn([[([], cols)]])
    )
    c = _mount()
    r = c.get("/api/kline/weekly?ts_code=000001.SZ")
    assert r.status_code == 200
    body = r.json()
    assert body["rows"] == []
    assert body["empty_reason"] == "out_of_range"


def test_kline_weekly_db_failure(monkeypatch):
    from contextlib import contextmanager

    @contextmanager
    def boom():
        raise RuntimeError("db dead")
        yield  # pragma: no cover

    monkeypatch.setattr("backend.api.kline.pg_conn", boom)
    c = _mount()
    r = c.get("/api/kline/weekly?ts_code=000001.SZ")
    assert r.status_code == 500


def test_kline_bad_ts_code_empty():
    """ts_code 缺少 '.' → 400."""
    c = _mount()
    r = c.get("/api/kline/daily?ts_code=000001")
    assert r.status_code == 400


def test_kline_weekly_bad_date():
    c = _mount()
    r = c.get("/api/kline/weekly?ts_code=000001.SZ&from=xxx")
    assert r.status_code == 400


def test_kline_daily_only_to(monkeypatch):
    """只传 to, 不传 from: 会走 where >= from? 不, 只走 <= to."""
    cols = ["trade_date", "open", "high", "low", "close", "vol"]
    monkeypatch.setattr(
        "backend.api.kline.pg_conn", _make_pg_conn([[([], cols)]])
    )
    c = _mount()
    r = c.get("/api/kline/daily?ts_code=000001.SZ&to=20260131")
    assert r.status_code == 200
