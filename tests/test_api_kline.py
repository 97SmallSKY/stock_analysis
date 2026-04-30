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


# ---------------------------------------------------------------------
# P19.1: /api/kline/daterange
# ---------------------------------------------------------------------
def test_kline_daterange_daily_ok(monkeypatch):
    # 模拟 kline_daily MIN/MAX/COUNT 返回
    mn, mx, cnt = dt.date(2020, 1, 2), dt.date(2026, 4, 17), 1520
    monkeypatch.setattr(
        "backend.api.kline.pg_conn",
        _make_pg_conn([[([(mn, mx, cnt)], ["min", "max", "count"])]])
    )
    c = _mount()
    r = c.get("/api/kline/daterange?ts_code=000001.SZ&freq=daily")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ts_code"] == "000001.SZ"
    assert body["freq"] == "daily"
    assert body["min"] == "2020-01-02"
    assert body["max"] == "2026-04-17"
    assert body["count"] == 1520
    assert body["empty_reason"] is None


def test_kline_daterange_daily_empty(monkeypatch):
    # 无 K 线数据(新股/无效代码):MIN/MAX 为 None, COUNT=0
    monkeypatch.setattr(
        "backend.api.kline.pg_conn",
        _make_pg_conn([[([(None, None, 0)], ["min", "max", "count"])]])
    )
    c = _mount()
    r = c.get("/api/kline/daterange?ts_code=999999.SZ&freq=daily")
    assert r.status_code == 200
    body = r.json()
    assert body["min"] is None
    assert body["max"] is None
    assert body["count"] == 0
    assert body["empty_reason"] == "no_data"


def test_kline_daterange_minute_no_source():
    """minute freq 暂无数据源,直接返回 no_source_table,不查 DB。"""
    c = _mount()
    r = c.get("/api/kline/daterange?ts_code=000001.SZ&freq=minute")
    assert r.status_code == 200
    body = r.json()
    assert body["freq"] == "minute"
    assert body["empty_reason"] == "no_source_table"
    assert body["min"] is None
    assert body["count"] == 0


def test_kline_daterange_bad_code():
    c = _mount()
    r = c.get("/api/kline/daterange?ts_code=BAD")
    assert r.status_code == 400


def test_kline_daterange_bad_freq():
    c = _mount()
    # FastAPI Query pattern 校验失败 → 422
    r = c.get("/api/kline/daterange?ts_code=000001.SZ&freq=hour")
    assert r.status_code == 422


# ---------------------------------------------------------------------
# P19.13: limit 边界回归。前端会传 2000,若后端上限太低会 422 → 前端看不到 K 线
# ---------------------------------------------------------------------
def test_kline_daily_limit_2000_ok(monkeypatch):
    cols = ["trade_date", "open", "high", "low", "close", "vol"]
    monkeypatch.setattr(
        "backend.api.kline.pg_conn", _make_pg_conn([[([], cols)]])
    )
    c = _mount()
    r = c.get("/api/kline/daily?ts_code=000001.SZ&limit=2000")
    assert r.status_code == 200, r.text  # P19.12 回归:曾因上限 1000 导致 422


def test_kline_daily_limit_5000_ok(monkeypatch):
    cols = ["trade_date", "open", "high", "low", "close", "vol"]
    monkeypatch.setattr(
        "backend.api.kline.pg_conn", _make_pg_conn([[([], cols)]])
    )
    c = _mount()
    r = c.get("/api/kline/daily?ts_code=000001.SZ&limit=5000")
    assert r.status_code == 200


def test_kline_daily_limit_over_upper():
    """limit=5001 仍应被 FastAPI 拒绝(保护 DB)。"""
    c = _mount()
    r = c.get("/api/kline/daily?ts_code=000001.SZ&limit=5001")
    assert r.status_code == 422


def test_kline_weekly_limit_2000_ok(monkeypatch):
    cols = ["trade_date", "open", "high", "low", "close", "vol"]
    # Weekly 会做聚合,cursor 调用两次(所以 canned 传 2 次)
    monkeypatch.setattr(
        "backend.api.kline.pg_conn",
        _make_pg_conn([[([], cols), ([], cols)]])
    )
    c = _mount()
    r = c.get("/api/kline/weekly?ts_code=000001.SZ&limit=2000")
    assert r.status_code == 200


# ======================================================================
# STO-40 前后端接口契约边界测试「范式」
#
# 每个新 API 端点都应有至少 2 个越界 case(上界 +1 / 下界 -1),返回 422。
# 这里对 /api/kline/daily 和 /api/kline/weekly 做示范,后续端点照抄即可。
# 详见 CLAUDE.md §11「前后端接口契约同步」。
# ======================================================================


def test_kline_daily_limit_below_min_returns_422():
    """契约:limit=0 < ge=1,FastAPI 应拒绝为 422(不是把 0 当成「不限」)。"""
    c = _mount()
    r = c.get("/api/kline/daily?ts_code=000001.SZ&limit=0")
    assert r.status_code == 422
    # 报文应能让前端定位到是哪个字段 / 哪个约束
    assert "limit" in r.text


def test_kline_weekly_limit_over_upper_returns_422():
    """契约:weekly limit=2001 > le=2000,FastAPI 应拒绝为 422。

    对应 P19.13 事故类型:前端未看后端 le= 就放大 limit → 422 全挂。
    """
    c = _mount()
    r = c.get("/api/kline/weekly?ts_code=000001.SZ&limit=2001")
    assert r.status_code == 422
    assert "limit" in r.text


def test_kline_weekly_limit_below_min_returns_422():
    """契约:weekly limit=0 < ge=1,422。"""
    c = _mount()
    r = c.get("/api/kline/weekly?ts_code=000001.SZ&limit=0")
    assert r.status_code == 422
    assert "limit" in r.text


def test_kline_daily_ts_code_missing_returns_422():
    """契约:ts_code 是 required,缺失应 422(不是默认空字符串当空查询)。"""
    c = _mount()
    r = c.get("/api/kline/daily?limit=100")
    assert r.status_code == 422
    assert "ts_code" in r.text


def test_kline_daily_with_indicators(monkeypatch):
    """STO-115: indicators=dma,macd 时响应包含 indicators 字段."""
    # 生成 60 天数据（跨 1-2 月）
    base = dt.date(2026, 1, 1)
    rows = [(base + dt.timedelta(days=i), 10.0 + i, 11.0 + i, 9.5 + i, 10.8 + i, 123456.0) for i in range(60)]
    cols = ["trade_date", "open", "high", "low", "close", "vol"]
    monkeypatch.setattr(
        "backend.api.kline.pg_conn", _make_pg_conn([[(rows, cols)]])
    )
    c = _mount()
    r = c.get("/api/kline/daily?ts_code=000001.SZ&from=20260101&indicators=dma,macd")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "indicators" in body
    assert "dma" in body["indicators"]
    assert "macd" in body["indicators"]
    dma = body["indicators"]["dma"]
    assert "ma10" in dma and "ma50" in dma
    assert len(dma["ma10"]) == 60
    macd = body["indicators"]["macd"]
    assert "diff" in macd and "dea" in macd and "bar" in macd
    assert len(macd["diff"]) == 60


def test_kline_daily_no_indicators_without_param(monkeypatch):
    """STO-115: 不传 indicators 参数时响应不含 indicators 字段."""
    rows = [(dt.date(2026, 1, i + 1), 10.0 + i, 11.0 + i, 9.5 + i, 10.8 + i, 123456.0) for i in range(10)]
    cols = ["trade_date", "open", "high", "low", "close", "vol"]
    monkeypatch.setattr(
        "backend.api.kline.pg_conn", _make_pg_conn([[(rows, cols)]])
    )
    c = _mount()
    r = c.get("/api/kline/daily?ts_code=000001.SZ&from=20260101")
    assert r.status_code == 200
    assert "indicators" not in r.json()
