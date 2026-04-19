"""W7-1: /api/lhb /api/dashboard 接口单测.

用 FastAPI TestClient + monkeypatch 替换 pg_conn, 避免真实 DB 依赖.
"""
from __future__ import annotations

import datetime as dt
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient


class _FakeCursor:
    """每次 execute 从队列里取下一组 (rows, cols), 支持一 cursor 多 execute."""

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
        self._queue = result_queue
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
    """返回一个 pg_conn 替身. canned_list 中每个元素是一次 pg_conn() 调用
    对应的 execute 结果序列, 形如 [(rows1, cols1), (rows2, cols2), ...]."""
    call_count = {"n": 0}

    @contextmanager
    def fake():
        idx = call_count["n"]
        call_count["n"] += 1
        conn = _FakeConn(canned_list[idx])
        yield conn

    return fake


@pytest.fixture
def client_factory(monkeypatch):
    """懒加载 app, 允许每个测试先 monkeypatch 再建 client."""

    def _make():
        # 防止 bootstrap 真连 DB
        monkeypatch.setenv("DATABASE_URL", "postgresql://localhost:5432/noop")
        from backend.api.main import create_app
        app = create_app()
        return TestClient(app), app

    return _make


# ---------------------------------------------------------------------
# /api/lhb — 列表
# ---------------------------------------------------------------------
def test_list_lhb_pagination(monkeypatch):
    # 预设: count=3 + 3 行;P10.1 后 SQL JOIN stock_info, cols 增加 name
    rows = [
        ("000001.SZ", "平安银行", dt.date(2026, 4, 18), "日振幅值达到7%",
         1000.0, 500.0, 500.0, 3000.0, 8.5, {"x": 1}),
    ] * 3
    cols = ["ts_code", "name", "trade_date", "reason",
            "buy_amount", "sell_amount", "net_amount", "turnover",
            "score", "score_detail"]
    canned = [  # 一次 pg_conn(), 内部两次 cursor()
        [([(3,)], ["count"]), (rows, cols)],
    ]
    monkeypatch.setattr(
        "backend.api.lhb.pg_conn", _make_pg_conn(canned)
    )
    from backend.api.lhb import router as lhb_router
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(lhb_router, prefix="/api/lhb")
    c = TestClient(app)

    r = c.get("/api/lhb?date_from=20260418&limit=10&offset=0")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 3
    assert len(body["rows"]) == 3
    assert body["rows"][0]["ts_code"] == "000001.SZ"
    assert body["rows"][0]["name"] == "平安银行"  # P10.1
    assert body["rows"][0]["trade_date"] == "2026-04-18"  # ISO 化


def test_list_lhb_bad_date():
    from backend.api.lhb import router as lhb_router
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(lhb_router, prefix="/api/lhb")
    c = TestClient(app)

    r = c.get("/api/lhb?date_from=abc")
    assert r.status_code == 400


def test_list_seats(monkeypatch):
    # P10.1 后 cols 增加 name
    rows = [
        ("000001.SZ", "平安银行", dt.date(2026, 4, 18),
         "国泰君安上海分公司", "BUY", 1e8, 0, 1e8, 1),
    ]
    cols = ["ts_code", "name", "trade_date", "broker_name", "direction",
            "buy_amount", "sell_amount", "net_amount", "rank_no"]
    canned = [[(rows, cols)]]
    monkeypatch.setattr(
        "backend.api.lhb.pg_conn", _make_pg_conn(canned)
    )
    from backend.api.lhb import router as lhb_router
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(lhb_router, prefix="/api/lhb")
    c = TestClient(app)

    r = c.get("/api/lhb/seats?trade_date=20260418&direction=BUY")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["n"] == 1
    assert body["rows"][0]["broker_name"] == "国泰君安上海分公司"


def test_get_stat_404(monkeypatch):
    canned = [[([], ["stat_type", "stat_key", "stat_period",
                    "payload", "updated_at"])]]
    monkeypatch.setattr(
        "backend.api.lhb.pg_conn", _make_pg_conn(canned)
    )
    from backend.api.lhb import router as lhb_router
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(lhb_router, prefix="/api/lhb")
    c = TestClient(app)

    r = c.get("/api/lhb/stat/broker/国泰君安")
    assert r.status_code == 404


# ---------------------------------------------------------------------
# /api/dashboard
# ---------------------------------------------------------------------
def test_dashboard_minimal(monkeypatch):
    """dashboard 两次 pg_conn 分别拿 sentiment 和 top_lhb."""
    sent_row = (dt.date(2026, 4, 18), 5200, 3000, 2000, 80, 20, 0.012, 55.3)
    sent_cols = ["trade_date", "total_stocks", "up_count", "down_count",
                 "limit_up_count", "limit_down_count",
                 "avg_pct_change", "sentiment_score"]
    # P10.1 dashboard top_lhb SELECT 已加 name + buy/sell_amount
    lhb_rows = [("000001.SZ", "平安银行", dt.date(2026, 4, 18), "r",
                 100.0, 120.0, 20.0, 8.0)]
    lhb_cols = ["ts_code", "name", "trade_date", "reason",
                "net_amount", "buy_amount", "sell_amount", "score"]

    canned = [
        [([sent_row], sent_cols)],
        [(lhb_rows, lhb_cols)],
    ]
    monkeypatch.setattr(
        "backend.api.dashboard.pg_conn", _make_pg_conn(canned)
    )

    from backend.api.dashboard import router as dash_router
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(dash_router, prefix="/api/dashboard")
    # adapter overview 需要 app.state.manager, 这里置 None -> 返回 []
    app.state.manager = None
    c = TestClient(app)

    r = c.get("/api/dashboard")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["sentiment"]["trade_date"] == "2026-04-18"
    assert body["sentiment"]["sentiment_score"] == 55.3
    assert len(body["top_lhb"]) == 1
    assert body["top_lhb"][0]["ts_code"] == "000001.SZ"
    assert body["adapters"] == []


# ---------------------------------------------------------------------
# P18.11: skip_total 消除深分页 COUNT 开销
# ---------------------------------------------------------------------
def test_list_lhb_skip_total(monkeypatch):
    """skip_total=true 时应返回 total=-1,且不触发 COUNT SQL。"""
    rows = [
        ("000001.SZ", "平安银行", dt.date(2026, 4, 18), "xxx",
         1000.0, 500.0, 500.0, 3000.0, 8.5, {}),
    ] * 2
    cols = ["ts_code", "name", "trade_date", "reason",
            "buy_amount", "sell_amount", "net_amount", "turnover",
            "score", "score_detail"]
    # skip_total=True 时只应有 1 次 cursor.execute(数据查询),不跑 count。
    # 这里 canned 只放 1 条,若代码错误地跑了 count 会导致 rows 数据错位或空。
    canned = [[(rows, cols)]]
    monkeypatch.setattr("backend.api.lhb.pg_conn", _make_pg_conn(canned))
    from backend.api.lhb import router as lhb_router
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(lhb_router, prefix="/api/lhb")
    c = TestClient(app)

    r = c.get("/api/lhb?limit=2&offset=0&skip_total=true")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == -1  # P18.11 语义:跳过 count
    assert len(body["rows"]) == 2
    assert body["rows"][0]["ts_code"] == "000001.SZ"


def test_list_lhb_skip_total_default_false(monkeypatch):
    """不传 skip_total 应保持旧行为(真实 count)。"""
    rows = [("000001.SZ", "平安银行", dt.date(2026, 4, 18), "xxx",
             1.0, 1.0, 0.0, 0.0, 5.0, {})]
    cols = ["ts_code", "name", "trade_date", "reason",
            "buy_amount", "sell_amount", "net_amount", "turnover",
            "score", "score_detail"]
    # 默认路径:先 count 再 select
    canned = [[([(42,)], ["count"]), (rows, cols)]]
    monkeypatch.setattr("backend.api.lhb.pg_conn", _make_pg_conn(canned))
    from backend.api.lhb import router as lhb_router
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(lhb_router, prefix="/api/lhb")
    c = TestClient(app)

    r = c.get("/api/lhb?limit=5")
    assert r.status_code == 200
    assert r.json()["total"] == 42
