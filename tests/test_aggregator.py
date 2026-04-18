"""backend.domain.lhb.aggregator 补充测试(P4 抬覆盖率)。

原覆盖率 ~30%,本文件用 fake conn/cursor 覆盖:
  - `_aggregate_stock` / `_aggregate_industry` / `_aggregate_broker` 的 SQL 参数组装与结果形状
  - `_upsert_agg` 的空列表短路 / 非空调用 execute_values
  - `aggregate_all` 的 orchestration:period 循环、include_broker 开关、broker 异常降级

不依赖真实 PostgreSQL。
"""
from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from backend.domain.lhb import aggregator as A


# --------------------------------------------------------------------
# 共用 fake
# --------------------------------------------------------------------


class _FakeCursor:
    """execute 只记录 sql/params;fetchall 返回预置 rows。"""

    def __init__(self, rows=None):
        self._rows = rows or []
        self.calls: list[tuple[str, list]] = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params):
        self.calls.append((sql, list(params)))

    def fetchall(self):
        return list(self._rows)


class _FakeConn:
    def __init__(self, cursor):
        self._cur = cursor
        self.committed = False

    def cursor(self):
        return self._cur

    def commit(self):
        self.committed = True


# --------------------------------------------------------------------
# _aggregate_stock
# --------------------------------------------------------------------


class TestAggregateStock:
    def test_7d_includes_start_bound(self):
        cur = _FakeCursor(rows=[
            # (ts_code, cnt, buy, sell, tb, ts, tn, avg_sc)
            ("000001.SZ", 3, 2, 1, 100, 50, 50, 80.0),
            ("600000.SH", 1, 1, 0, 10, 0, 10, None),
        ])
        conn = _FakeConn(cur)
        rows = A._aggregate_stock(conn, date(2024, 10, 15), "7D")
        # 带上 start 参数
        sql, params = cur.calls[0]
        assert "trade_date >= %s" in sql
        assert params == [date(2024, 10, 15), date(2024, 10, 8)]
        # 形状: 前缀 ('STOCK', ts_code, '7D')
        assert rows[0][:3] == ("STOCK", "000001.SZ", "7D")
        assert rows[1][:3] == ("STOCK", "600000.SH", "7D")
        assert len(rows) == 2

    def test_all_drops_start_bound(self):
        cur = _FakeCursor(rows=[])
        conn = _FakeConn(cur)
        rows = A._aggregate_stock(conn, date(2024, 10, 15), "ALL")
        sql, params = cur.calls[0]
        assert "trade_date >= %s" not in sql
        assert params == [date(2024, 10, 15)]
        assert rows == []


# --------------------------------------------------------------------
# _aggregate_industry
# --------------------------------------------------------------------


class TestAggregateIndustry:
    def test_filters_null_industry(self):
        cur = _FakeCursor(rows=[
            ("银行", 5, 3, 2, 200, 100, 100, 75.0),
        ])
        conn = _FakeConn(cur)
        rows = A._aggregate_industry(conn, date(2024, 10, 15), "30D")
        sql, params = cur.calls[0]
        assert "industry IS NOT NULL" in sql
        assert params == [date(2024, 10, 15), date(2024, 9, 15)]
        assert rows[0][:3] == ("INDUSTRY", "银行", "30D")

    def test_all_period_no_start(self):
        cur = _FakeCursor(rows=[])
        conn = _FakeConn(cur)
        A._aggregate_industry(conn, date(2024, 10, 15), "ALL")
        sql, params = cur.calls[0]
        assert "trade_date >= %s" not in sql
        assert params == [date(2024, 10, 15)]


# --------------------------------------------------------------------
# _aggregate_broker
# --------------------------------------------------------------------


class TestAggregateBroker:
    def test_rows_shape(self):
        cur = _FakeCursor(rows=[
            # (broker_name, cnt, buy, sell, tb, ts, tn)
            ("华泰证券", 10, 6, 4, 500, 300, 200),
        ])
        conn = _FakeConn(cur)
        rows = A._aggregate_broker(conn, date(2024, 10, 15), "90D")
        sql, params = cur.calls[0]
        assert "FROM lhb_seats" in sql
        assert params == [date(2024, 10, 15), date(2024, 7, 17)]
        # BROKER 最后一位是 None (avg_score 占位)
        assert rows[0] == ("BROKER", "华泰证券", "90D", 10, 6, 4, 500, 300, 200, None)


# --------------------------------------------------------------------
# _upsert_agg
# --------------------------------------------------------------------


class TestUpsertAgg:
    def test_empty_rows_noop(self):
        cur = _FakeCursor()
        conn = _FakeConn(cur)
        assert A._upsert_agg(conn, []) == 0
        # 不应进 cursor
        assert cur.calls == []

    def test_non_empty_calls_execute_values(self):
        cur = _FakeCursor()
        conn = _FakeConn(cur)
        rows = [("STOCK", "000001.SZ", "7D", 3, 2, 1, 100, 50, 50, 80.0)]
        with patch.object(A, "execute_values") as ev:
            n = A._upsert_agg(conn, rows)
        assert n == 1
        ev.assert_called_once()
        # 构造的 SQL 要带 ON CONFLICT 分支
        sql_arg = ev.call_args.args[1]
        assert "ON CONFLICT (stat_type, stat_key, stat_period) DO UPDATE" in sql_arg
        # set_clause 要排除主键
        for pk in ("stat_type", "stat_key", "stat_period"):
            assert f"{pk}=EXCLUDED.{pk}" not in sql_arg


# --------------------------------------------------------------------
# aggregate_all
# --------------------------------------------------------------------


class TestAggregateAll:
    def _patch_all(self, monkeypatch, stock_ret=1, industry_ret=2, broker_ret=3,
                   broker_raises=False):
        monkeypatch.setattr(
            A, "_aggregate_stock",
            lambda conn, d, p: [("STOCK", f"S{p}", p)] * stock_ret,
        )
        monkeypatch.setattr(
            A, "_aggregate_industry",
            lambda conn, d, p: [("INDUSTRY", f"I{p}", p)] * industry_ret,
        )
        if broker_raises:
            def _br(*a, **k):
                raise RuntimeError("no lhb_seats")
            monkeypatch.setattr(A, "_aggregate_broker", _br)
        else:
            monkeypatch.setattr(
                A, "_aggregate_broker",
                lambda conn, d, p: [("BROKER", f"B{p}", p)] * broker_ret,
            )
        # _upsert_agg 直接返回 len(rows)
        monkeypatch.setattr(A, "_upsert_agg", lambda conn, rows: len(rows))

    def test_default_periods_and_types(self, monkeypatch):
        self._patch_all(monkeypatch, stock_ret=1, industry_ret=1, broker_ret=1)
        conn = _FakeConn(_FakeCursor())
        ret = A.aggregate_all(conn, as_of=date(2024, 10, 15))
        # 4 个 period * 各维度 1 行
        assert ret == {"stock": 4, "industry": 4, "broker": 4}
        assert conn.committed is True

    def test_exclude_broker(self, monkeypatch):
        self._patch_all(monkeypatch, stock_ret=1, industry_ret=1, broker_ret=5)
        conn = _FakeConn(_FakeCursor())
        ret = A.aggregate_all(
            conn, as_of=date(2024, 10, 15), include_broker=False,
        )
        assert ret == {"stock": 4, "industry": 4, "broker": 0}

    def test_broker_failure_degrades_gracefully(self, monkeypatch):
        self._patch_all(monkeypatch, stock_ret=1, industry_ret=1, broker_raises=True)
        conn = _FakeConn(_FakeCursor())
        ret = A.aggregate_all(conn, as_of=date(2024, 10, 15))
        # broker 全 fail,但 stock/industry 不受影响
        assert ret["stock"] == 4
        assert ret["industry"] == 4
        assert ret["broker"] == 0

    def test_as_of_defaults_to_today(self, monkeypatch):
        captured = {}

        def fake_stock(conn, d, p):
            captured["d"] = d
            return []

        monkeypatch.setattr(A, "_aggregate_stock", fake_stock)
        monkeypatch.setattr(A, "_aggregate_industry", lambda *a, **k: [])
        monkeypatch.setattr(A, "_aggregate_broker", lambda *a, **k: [])
        monkeypatch.setattr(A, "_upsert_agg", lambda *a, **k: 0)
        conn = _FakeConn(_FakeCursor())
        A.aggregate_all(conn, as_of=None, periods=("7D",))
        assert captured["d"] == date.today()
