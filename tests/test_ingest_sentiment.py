"""tests/test_ingest_sentiment.py — W11-3 抬覆盖率

覆盖 backend/ingest/sentiment.py:
  - _calc_from_kline: 空 / 满 / 极端涨跌停数量导致 score clamp
  - _is_empty_sentiment
  - _load_covered_days
  - run(): 无交易日 / 外部源返回空壳 -> DB 反算 / NotImplementedError 退出
"""
from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest

import backend.ingest.sentiment as S


class _Cur:
    def __init__(self, one=None, rows=None):
        self._one = one
        self._rows = rows or []
        self.executed = []
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def execute(self, sql, params=()): self.executed.append((sql, params))
    def fetchone(self): return self._one
    def fetchall(self): return list(self._rows)


class _Conn:
    def __init__(self, cursors):
        self._it = iter(cursors)
        self.rolled_back = 0
        self.committed = 0
    def cursor(self, *a, **k): return next(self._it)
    def rollback(self): self.rolled_back += 1
    def commit(self): self.committed += 1
    def __enter__(self): return self
    def __exit__(self, *a): return False


# --------------------------------------------------------------------
# _calc_from_kline
# --------------------------------------------------------------------


class TestCalcFromKline:
    def test_no_rows_returns_none(self):
        conn = _Conn([_Cur(one=(0, 0, 0, 0, 0, 0, 0, None))])
        assert S._calc_from_kline(conn, "20241015") is None

    def test_normal_day(self):
        # total=4000, limit_up=50, limit_down=5, up=2300, down=1500, strong=400, weak=100, avg=0.01
        row = (4000, 50, 5, 2300, 1500, 400, 100, 0.01)
        conn = _Conn([_Cur(one=row)])
        out = S._calc_from_kline(conn, "20241015")
        assert out["trade_date"] == "2024-10-15"
        assert out["limit_up_count"] == 50
        assert out["limit_down_count"] == 5
        assert out["up_down_ratio"] == round(2300 / max(1500, 1), 4)
        # score = 50 + (ratio-1)*20 + 0.01*600 + (50-5)*0.5; 位于合法范围
        assert 0.0 <= out["sentiment_score"] <= 100.0
        assert out["sentiment_level"] in ("HOT", "WARM", "NEUTRAL", "COOL", "COLD")

    def test_clamp_cold(self):
        # 大幅下跌, 均幅 -0.05, 涨跌停都给 low
        row = (4000, 0, 200, 200, 3000, 50, 500, -0.05)
        conn = _Conn([_Cur(one=row)])
        out = S._calc_from_kline(conn, "20241015")
        assert out["sentiment_score"] <= 30      # COLD level
        assert out["sentiment_level"] == "COLD"

    def test_clamp_hot(self):
        row = (4000, 200, 0, 3500, 200, 800, 50, 0.04)
        conn = _Conn([_Cur(one=row)])
        out = S._calc_from_kline(conn, "20241015")
        assert out["sentiment_score"] >= 70
        assert out["sentiment_level"] == "HOT"


# --------------------------------------------------------------------
# _is_empty_sentiment
# --------------------------------------------------------------------


class TestIsEmpty:
    def test_all_zero(self):
        assert S._is_empty_sentiment({
            "limit_up_count": 0, "limit_down_count": 0,
            "up_down_ratio": 0, "avg_pct_change": 0,
        })

    def test_some_non_zero(self):
        assert S._is_empty_sentiment({
            "limit_up_count": 10, "limit_down_count": 0,
            "up_down_ratio": 0, "avg_pct_change": 0,
        }) is False

    def test_none_treated_as_empty(self):
        assert S._is_empty_sentiment({
            "limit_up_count": None, "limit_down_count": None,
            "up_down_ratio": None, "avg_pct_change": None,
        })


# --------------------------------------------------------------------
# _load_covered_days
# --------------------------------------------------------------------


def test_load_covered_days(monkeypatch):
    cur = _Cur(rows=[("20241014",), ("20241015",)])
    conn = _Conn([cur])

    @contextmanager
    def fake_pg():
        yield conn
    monkeypatch.setattr(S, "pg_conn", fake_pg)
    out = S._load_covered_days("20241014", "20241016")
    assert out == {"20241014", "20241015"}


# --------------------------------------------------------------------
# run()
# --------------------------------------------------------------------


class TestRun:
    def test_no_trade_days(self, monkeypatch):
        monkeypatch.setattr(S, "get_manager", lambda: MagicMock())
        monkeypatch.setattr(S, "_load_trading_days", lambda s, e: [])
        n = S.run("20241015", "20241016")
        assert n == 0

    def test_external_empty_fallback_to_kline(self, monkeypatch):
        """外部源抛异常 / 返回空 -> 走 _calc_from_kline."""
        monkeypatch.setattr(S, "_load_trading_days", lambda s, e: ["20241015"])
        monkeypatch.setattr(S, "_load_covered_days", lambda s, e: set())

        mgr = MagicMock()
        mgr.get_market_sentiment.return_value = None  # 导致 df_to_rows 返回 []
        monkeypatch.setattr(S, "get_manager", lambda: mgr)
        monkeypatch.setattr(S, "df_to_rows", lambda df, cm: [])

        # _calc_from_kline 返回一个合法 dict
        monkeypatch.setattr(S, "_calc_from_kline", lambda conn, d: {
            "trade_date": "2024-10-15", "limit_up_count": 30,
            "limit_down_count": 5, "up_down_ratio": 1.5,
            "avg_pct_change": 0.01, "strong_stock_count": 200,
            "weak_stock_count": 50, "sentiment_score": 65.0,
            "sentiment_level": "WARM",
        })
        monkeypatch.setattr(S, "upsert_market_sentiment", lambda conn, rows: len(rows))

        # pg_conn 返回一个空 conn
        fake_conn = MagicMock()
        fake_conn.commit = MagicMock()

        @contextmanager
        def fake_pg():
            yield fake_conn
        monkeypatch.setattr(S, "pg_conn", fake_pg)

        n = S.run("20241015", "20241015")
        assert n == 1

    def test_not_implemented_breaks(self, monkeypatch):
        monkeypatch.setattr(S, "_load_trading_days", lambda s, e: ["20241015", "20241016"])
        monkeypatch.setattr(S, "_load_covered_days", lambda s, e: set())

        mgr = MagicMock()
        mgr.get_market_sentiment.side_effect = NotImplementedError("nope")
        monkeypatch.setattr(S, "get_manager", lambda: mgr)

        fake_conn = MagicMock()

        @contextmanager
        def fake_pg():
            yield fake_conn
        monkeypatch.setattr(S, "pg_conn", fake_pg)

        n = S.run("20241015", "20241016")
        assert n == 0  # 第一天就 break
