"""验证 db/writer 的 SQL 构造 / 批量切片逻辑。

不连 PG:monkeypatch psycopg2.extras.execute_values 捕获最终 SQL + values。
"""

from __future__ import annotations

from typing import Any, List, Tuple

import pytest

from backend.db import writer as w


class FakeCursor:
    def __init__(self):
        self.executed: List[Tuple[str, Any]] = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, vars=None):
        self.executed.append((sql, vars))


class FakeConn:
    def __init__(self):
        self._cur = FakeCursor()

    def cursor(self):
        return self._cur


@pytest.fixture
def captured_exec(monkeypatch):
    """拦截 execute_values,不真跑;收集 (cur, sql, values, page_size) 调用。"""
    calls: list[dict] = []

    def fake_execute_values(cur, sql, argslist, page_size=None):
        calls.append({
            "cur": cur,
            "sql": sql,
            "values": list(argslist),
            "page_size": page_size,
        })

    monkeypatch.setattr(w, "execute_values", fake_execute_values)
    return calls


# ---------------------------------------------------------------------------
# _bulk_upsert
# ---------------------------------------------------------------------------

def test_bulk_upsert_empty_rows(captured_exec):
    assert w._bulk_upsert(FakeConn(), "t", ["a"], [], ["a"]) == 0
    assert captured_exec == []


def test_bulk_upsert_builds_update_clause(captured_exec):
    rows = [{"a": 1, "b": 2}]
    n = w._bulk_upsert(
        FakeConn(), "t", ["a", "b"], rows,
        conflict_cols=["a"], update_cols=["b"],
    )
    assert n == 1
    sql = captured_exec[0]["sql"]
    assert "INSERT INTO t (a,b)" in sql
    assert "ON CONFLICT (a) DO UPDATE SET b=EXCLUDED.b" in sql


def test_bulk_upsert_do_nothing_when_no_update_cols(captured_exec):
    w._bulk_upsert(
        FakeConn(), "t", ["a", "b"], [{"a": 1, "b": 2}],
        ["a"], update_cols=None,
    )
    sql = captured_exec[0]["sql"]
    assert "ON CONFLICT (a) DO NOTHING" in sql


def test_bulk_upsert_missing_keys_become_none(captured_exec):
    # row 缺列 b,应补 None
    w._bulk_upsert(FakeConn(), "t", ["a", "b"], [{"a": 1}], ["a"], ["b"])
    values = captured_exec[0]["values"]
    assert values == [(1, None)]


def test_bulk_upsert_batches(captured_exec):
    rows = [{"a": i} for i in range(5)]
    w._bulk_upsert(FakeConn(), "t", ["a"], rows, ["a"], ["a"], batch_size=2)
    # 5 / 2 → 3 次切片
    assert len(captured_exec) == 3
    assert [len(c["values"]) for c in captured_exec] == [2, 2, 1]


# ---------------------------------------------------------------------------
# 表封装
# ---------------------------------------------------------------------------

def test_upsert_stock_info_sql(captured_exec):
    w.upsert_stock_info(
        FakeConn(),
        [{"ts_code": "000001.SZ", "symbol": "000001", "name": "平安银行"}],
    )
    sql = captured_exec[0]["sql"]
    assert "INSERT INTO stock_info" in sql
    assert "ON CONFLICT (ts_code)" in sql
    assert "name=EXCLUDED.name" in sql
    assert "ts_code=EXCLUDED.ts_code" not in sql  # 主键不应 update


def test_upsert_trade_calendar_sql(captured_exec):
    w.upsert_trade_calendar(
        FakeConn(),
        [{"exchange": "SSE", "cal_date": "2024-01-02", "is_open": True}],
    )
    sql = captured_exec[0]["sql"]
    assert "INSERT INTO trade_calendar" in sql
    assert "ON CONFLICT (exchange,cal_date)" in sql


def test_upsert_kline_daily_sql(captured_exec):
    w.upsert_kline_daily(
        FakeConn(),
        [{"ts_code": "000001.SZ", "trade_date": "2024-01-02", "close": 10.0}],
    )
    sql = captured_exec[0]["sql"]
    assert "INSERT INTO kline_daily" in sql
    assert "ON CONFLICT (ts_code,trade_date)" in sql
    assert "close=EXCLUDED.close" in sql


def test_upsert_lhb_stock_daily_sql(captured_exec):
    w.upsert_lhb_stock_daily(
        FakeConn(),
        [{"ts_code": "000001.SZ", "trade_date": "2024-01-02", "l_net": 1000.0}],
    )
    sql = captured_exec[0]["sql"]
    assert "INSERT INTO lhb_stock_daily" in sql
    assert "ON CONFLICT (ts_code,trade_date)" in sql


def test_upsert_market_sentiment_delete_then_insert(captured_exec):
    """market_sentiment 先 DELETE 再 INSERT,保证幂等。"""
    conn = FakeConn()
    rows = [
        {"trade_date": "2024-01-02", "limit_up_count": 50},
        {"trade_date": "2024-01-03", "limit_up_count": 60},
    ]
    n = w.upsert_market_sentiment(conn, rows)
    assert n == 2

    # DELETE 走的是 cur.execute(),会落在 FakeCursor.executed
    delete_sqls = [sql for sql, _ in conn._cur.executed]
    assert any(s.startswith("DELETE FROM market_sentiment") for s in delete_sqls)

    # INSERT 走的是 execute_values → captured_exec
    assert len(captured_exec) == 1
    assert "INSERT INTO market_sentiment" in captured_exec[0]["sql"]


def test_upsert_market_sentiment_empty(captured_exec):
    assert w.upsert_market_sentiment(FakeConn(), []) == 0
    assert captured_exec == []
