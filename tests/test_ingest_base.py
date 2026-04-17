"""ingest.base / ingest 各模块的纯函数/数据转换单测。

不依赖数据库、不依赖外网数据源,全部 mock。
本地即可运行:
    pytest tests/test_ingest_base.py -q
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from backend.ingest.base import chunked, df_to_rows, normalize_date


# ---------------------------------------------------------------------------
# normalize_date
# ---------------------------------------------------------------------------

class TestNormalizeDate:
    def test_none(self):
        assert normalize_date(None) is None

    def test_empty_string(self):
        assert normalize_date("") is None
        assert normalize_date("   ") is None

    def test_yyyymmdd_string(self):
        assert normalize_date("20240115") == "2024-01-15"

    def test_dashed_string_passthrough(self):
        assert normalize_date("2024-01-15") == "2024-01-15"

    def test_datetime(self):
        assert normalize_date(dt.datetime(2024, 1, 15, 10, 30)) == "2024-01-15"

    def test_date(self):
        assert normalize_date(dt.date(2024, 1, 15)) == "2024-01-15"

    def test_non_date_string(self):
        # 非 YYYYMMDD,按原样返回(让上游 SQL/PG 自己解析)
        assert normalize_date("2024/01/15") == "2024/01/15"


# ---------------------------------------------------------------------------
# df_to_rows
# ---------------------------------------------------------------------------

class TestDfToRows:
    def test_none(self):
        assert df_to_rows(None, {"a": "b"}) == []

    def test_empty_df(self):
        assert df_to_rows(pd.DataFrame(), {"a": "b"}) == []

    def test_basic_rename(self):
        df = pd.DataFrame({"ts_code": ["000001.SZ"], "pct_chg": [5.23]})
        rows = df_to_rows(df, {"ts_code": "ts_code", "pct_chg": "pct_change"})
        assert rows == [{"ts_code": "000001.SZ", "pct_change": 5.23}]

    def test_ignores_unknown_src_columns(self):
        df = pd.DataFrame({"a": [1]})
        rows = df_to_rows(df, {"a": "a", "missing": "x"})
        assert rows == [{"a": 1}]

    def test_nan_becomes_none(self):
        df = pd.DataFrame({"a": [1, None], "b": ["x", None]})
        rows = df_to_rows(df, {"a": "a", "b": "b"})
        assert rows[1]["a"] is None
        assert rows[1]["b"] is None


# ---------------------------------------------------------------------------
# chunked
# ---------------------------------------------------------------------------

class TestChunked:
    def test_exact(self):
        assert list(chunked([1, 2, 3, 4], 2)) == [[1, 2], [3, 4]]

    def test_remainder(self):
        assert list(chunked([1, 2, 3, 4, 5], 2)) == [[1, 2], [3, 4], [5]]

    def test_empty(self):
        assert list(chunked([], 3)) == []


# ---------------------------------------------------------------------------
# ingest.kline._normalize_pct
# ---------------------------------------------------------------------------

from backend.ingest.kline import _normalize_pct  # noqa: E402


class TestNormalizePct:
    def test_percentage_to_decimal(self):
        rows = [{"pct_change": 5.23, "turnover_rate": 2.5}]
        _normalize_pct(rows)
        assert rows[0]["pct_change"] == pytest.approx(0.0523)
        assert rows[0]["turnover_rate"] == pytest.approx(0.025)

    def test_already_decimal_kept(self):
        # 已经 <=1 视为小数,不再 /100
        rows = [{"pct_change": 0.05, "turnover_rate": 0.03}]
        _normalize_pct(rows)
        assert rows[0]["pct_change"] == 0.05
        assert rows[0]["turnover_rate"] == 0.03

    def test_negative_percentage(self):
        rows = [{"pct_change": -7.5, "turnover_rate": None}]
        _normalize_pct(rows)
        assert rows[0]["pct_change"] == pytest.approx(-0.075)
        assert rows[0]["turnover_rate"] is None

    def test_none_preserved(self):
        rows = [{"pct_change": None, "turnover_rate": None}]
        _normalize_pct(rows)
        assert rows[0]["pct_change"] is None
        assert rows[0]["turnover_rate"] is None

    def test_invalid_becomes_none(self):
        rows = [{"pct_change": "bad", "turnover_rate": "oops"}]
        _normalize_pct(rows)
        assert rows[0]["pct_change"] is None
        assert rows[0]["turnover_rate"] is None


# ---------------------------------------------------------------------------
# ingest.stock_info.run —— 全 mock
# ---------------------------------------------------------------------------

class _FakeMgr:
    def __init__(self, df):
        self._df = df

    def get_stock_info(self):
        return self._df

    def get_trade_calendar(self, *a, **kw):
        return self._df


class _FakeConn:
    def __init__(self):
        self.rows_received = None


class _FakeCtx:
    def __init__(self, conn):
        self._conn = conn

    def __enter__(self):
        return self._conn

    def __exit__(self, exc_type, exc, tb):
        return False


def test_stock_info_run_happy(monkeypatch):
    from backend.ingest import stock_info

    df = pd.DataFrame([{
        "ts_code": "000001.SZ",
        "symbol": "000001",
        "name": "平安银行",
        "area": "深圳",
        "industry": "银行",
        "market": "主板",
        "list_date": "19910403",
        "delist_date": None,
        "is_hs": "S",
        "exchange": "SZSE",
    }])

    fake_conn = _FakeConn()
    captured: dict = {}

    def fake_upsert(conn, rows):
        captured["rows"] = rows
        return len(rows)

    monkeypatch.setattr(stock_info, "get_manager", lambda: _FakeMgr(df))
    monkeypatch.setattr(stock_info, "pg_conn", lambda: _FakeCtx(fake_conn))
    monkeypatch.setattr(stock_info, "upsert_stock_info", fake_upsert)

    n = stock_info.run()
    assert n == 1
    row = captured["rows"][0]
    assert row["ts_code"] == "000001.SZ"
    assert row["list_date"] == "1991-04-03"  # normalize_date
    assert row["is_active"] is True  # setdefault 生效


def test_stock_info_run_empty(monkeypatch, caplog):
    from backend.ingest import stock_info

    monkeypatch.setattr(stock_info, "get_manager", lambda: _FakeMgr(pd.DataFrame()))
    # 不应该进到 pg_conn/upsert
    monkeypatch.setattr(stock_info, "pg_conn", lambda: pytest.fail("should not open conn"))

    assert stock_info.run() == 0


# ---------------------------------------------------------------------------
# ingest.calendar.run —— 全 mock
# ---------------------------------------------------------------------------

def test_calendar_run_happy(monkeypatch):
    from backend.ingest import calendar as cal_mod

    df = pd.DataFrame([
        {"exchange": "SSE", "cal_date": "20240102", "is_open": 1, "pretrade_date": "20231229"},
        {"exchange": "SSE", "cal_date": "20240103", "is_open": 0, "pretrade_date": "20240102"},
    ])

    captured: dict = {}

    def fake_upsert(conn, rows):
        captured["rows"] = rows
        return len(rows)

    monkeypatch.setattr(cal_mod, "get_manager", lambda: _FakeMgr(df))
    monkeypatch.setattr(cal_mod, "pg_conn", lambda: _FakeCtx(_FakeConn()))
    monkeypatch.setattr(cal_mod, "upsert_trade_calendar", fake_upsert)

    n = cal_mod.run("20240101", "20240105", "SSE")
    assert n == 2
    rows = captured["rows"]
    assert rows[0]["cal_date"] == "2024-01-02"
    assert rows[0]["is_open"] is True
    assert rows[1]["is_open"] is False
    assert rows[0]["pretrade_date"] == "2023-12-29"
