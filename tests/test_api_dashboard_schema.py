"""STO-123 Phase 1c: dashboard API Pydantic schema 契约测试.

验证 response_model=DashboardResp 的类型校验生效。
"""
from __future__ import annotations

import datetime as dt
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError


class _FakeCursor:
    """每次 execute 从队列里取下一组 (rows, cols)."""

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


def _make_pg_conn(canned_results):
    """返回 contextmanager,每次 yield _FakeConn(下一组 result_queue)."""
    results_iter = iter(canned_results)

    @contextmanager
    def _pg_conn():
        yield _FakeConn(next(results_iter))

    return _pg_conn


def test_dashboard_schema_valid_response(monkeypatch):
    """正常响应应通过 DashboardResp 校验。"""
    # _latest_sentiment 返回的 dict
    sent_row = (
        dt.date(2026, 4, 18),  # trade_date
        10, 5,  # limit_up_count, limit_down_count
        3, 2, 5,  # first_limit_up, cont_limit_up, max_cont_board
        1.5, 0.8, 0.5,  # up_down_ratio, avg_pct_change, median_pct_change
        100, 50,  # strong_stock_count, weak_stock_count
        20, 1500.0, 7.5,  # lhb_stock_count, lhb_total_net, lhb_avg_score
        65.0, "强势", 2.0,  # sentiment_score, sentiment_level, sentiment_change
    )
    sent_cols = [
        "trade_date", "limit_up_count", "limit_down_count",
        "first_limit_up", "cont_limit_up", "max_cont_board",
        "up_down_ratio", "avg_pct_change", "median_pct_change",
        "strong_stock_count", "weak_stock_count",
        "lhb_stock_count", "lhb_total_net", "lhb_avg_score",
        "sentiment_score", "sentiment_level", "sentiment_change",
    ]

    # _latest_sentiment 内部还会查 kline_daily 聚合 total_stocks/up_count/down_count
    kline_max_row = (dt.date(2026, 4, 18),)
    kline_agg_row = (5000, 3200, 1500)
    kline_agg_cols = ["total", "up_cnt", "down_cnt"]

    # _latest_sentiment 内部还会查 lhb_stock_daily 聚合 (已在 sent_row 里,但代码会再查一次)
    lhb_agg_row = (20, 1500.0, 7.5)
    lhb_agg_cols = ["cnt", "total_net", "avg_score"]

    # _latest_sentiment 内部还会查 sentiment_change (前一日)
    prev_sent_row = (63.0,)
    prev_sent_cols = ["sentiment_score"]

    # _top_lhb 返回的 list[dict]
    lhb_rows = [
        ("000001.SZ", "平安银行", dt.date(2026, 4, 18), "连续三日涨停",
         15.0, 2.5, 120.0, 100.0, 3000.0, 8.5),
    ]
    lhb_cols = [
        "ts_code", "name", "trade_date", "reason", "close", "pct_change",
        "net_amount", "buy_amount", "turnover", "score",
    ]

    # _latest_sentiment 会做 5 次 execute:
    # 1. SELECT trade_date, ... FROM market_sentiment ORDER BY trade_date DESC LIMIT 1
    # 2. SELECT MAX(trade_date) FROM kline_daily WHERE ...
    # 3. SELECT COUNT(*) ... FROM kline_daily WHERE trade_date = ...
    # 4. SELECT COUNT(*), SUM(l_net), AVG(score) FROM lhb_stock_daily WHERE trade_date = ...
    # 5. SELECT sentiment_score FROM market_sentiment WHERE trade_date < ... (前一日)
    canned = [
        [
            ([sent_row], sent_cols),
            ([kline_max_row], ["trade_date"]),
            ([kline_agg_row], kline_agg_cols),
            ([lhb_agg_row], lhb_agg_cols),
            ([prev_sent_row], prev_sent_cols),
        ],
        [(lhb_rows, lhb_cols)],
    ]
    monkeypatch.setattr("backend.api.dashboard.pg_conn", _make_pg_conn(canned))

    from backend.api.dashboard import router as dash_router
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(dash_router, prefix="/api/dashboard")
    app.state.manager = None  # _adapter_overview 需要,置 None 返回 []
    c = TestClient(app)

    r = c.get("/api/dashboard")
    assert r.status_code == 200, r.text
    body = r.json()

    # 验证 sentiment 字段
    assert body["sentiment"]["trade_date"] == "2026-04-18"
    assert body["sentiment"]["limit_up_count"] == 10
    assert body["sentiment"]["sentiment_score"] == 65.0
    assert body["sentiment"]["total_stocks"] == 5000
    assert body["sentiment"]["up_count"] == 3200

    # 验证 top_lhb 字段
    assert len(body["top_lhb"]) == 1
    assert body["top_lhb"][0]["ts_code"] == "000001.SZ"
    assert body["top_lhb"][0]["score"] == 8.5

    # 验证 adapters 字段
    assert body["adapters"] == []


def test_dashboard_schema_type_mismatch():
    """故意构造类型不匹配的数据,验证 Pydantic 校验触发。"""
    from backend.api.schemas.dashboard import DashboardResp, SentimentSchema

    # sentiment_score 应为 float,传 str 应报错
    with pytest.raises(ValidationError) as exc_info:
        DashboardResp(
            sentiment=SentimentSchema(
                trade_date="2026-04-18",
                sentiment_score="not_a_number",  # 类型错误
            ),
            top_lhb=[],
            adapters=[],
        )
    assert "sentiment_score" in str(exc_info.value)


def test_dashboard_schema_extra_fields_ignored():
    """宽模式 extra='ignore' 应忽略多余字段。"""
    from backend.api.schemas.dashboard import DashboardResp, SentimentSchema

    # 传入 schema 未定义的字段,应被忽略而不报错
    resp = DashboardResp(
        sentiment=SentimentSchema(
            trade_date="2026-04-18",
            sentiment_score=65.0,
            unknown_field="should_be_ignored",  # 多余字段
        ),
        top_lhb=[],
        adapters=[],
    )
    # 验证对象创建成功
    assert resp.sentiment.sentiment_score == 65.0
    # 验证多余字段未被保留
    assert not hasattr(resp.sentiment, "unknown_field")
