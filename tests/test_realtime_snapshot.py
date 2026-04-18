"""tests/test_realtime_snapshot.py — W8-2

覆盖:
  - snapshot_writer.build_rows: DataFrame / list[dict] / 过滤无 ts_code/ts / NaN→None
  - snapshot_writer.insert_snapshots: 空输入 no-op, 非空调 execute_values + commit
                                      SQL 含 ON CONFLICT DO UPDATE
  - subscription_pool.upsert / replace_by_reason / remove / cleanup_expired /
                     get_active_codes / refresh_zt_today / refresh_lhb_recent
  所有 DB 调用都用 MonkeyPatch mock (不连真库)
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pandas as pd
import pytest

from backend.domain.realtime import snapshot_writer as sw
from backend.domain.realtime import subscription_pool as sp


# --------------------------------------------------------------------
# fake pg_conn + execute_values
# --------------------------------------------------------------------


def make_fake_pg(fetchall_rows: list[tuple] | None = None):
    """构造 (pg_conn_factory, cursor_mock)."""
    cur = MagicMock()
    cur.__enter__ = lambda self: self
    cur.__exit__ = lambda *a: False
    cur.fetchall = MagicMock(return_value=fetchall_rows or [])
    # cursor.description: 每列带 .name (与 psycopg2 一致)
    cur.description = [MagicMock(name="ts_code"), MagicMock(name="reason"),
                       MagicMock(name="added_at"), MagicMock(name="expires_at")]
    for i, nm in enumerate(["ts_code", "reason", "added_at", "expires_at"]):
        cur.description[i].name = nm

    conn = MagicMock()
    conn.cursor.return_value = cur
    conn.__enter__ = lambda self: self
    conn.__exit__ = lambda *a: False

    @contextmanager
    def factory():
        yield conn

    return factory, cur, conn


# ====================================================================
# snapshot_writer
# ====================================================================


class TestBuildRows:
    def test_from_dataframe(self):
        df = pd.DataFrame([
            {"ts_code": "600000.SH",
             "ts": datetime(2026, 4, 18, 10, 0, 0),
             "price": 10.25, "pct_change": 1.5, "change": 0.15,
             "volume": 1234, "amount": 1234567.0,
             "open": 10.15, "high": 10.30, "low": 10.10, "prev_close": 10.10},
        ])
        rows = sw.build_rows(df)
        assert len(rows) == 1
        r = rows[0]
        assert r[0] == "600000.SH"
        assert isinstance(r[1], datetime)
        assert r[2] == datetime(2026, 4, 18).date()
        assert r[3] == "REALTIME"
        assert r[4] == 10.25
        # source 固定 crawler
        assert r[-1] == "crawler"

    def test_from_list_of_dict_drops_invalid(self):
        records = [
            {"ts_code": "X.SZ", "ts": datetime(2026, 4, 18, 10, 0)},
            {"ts_code": "", "ts": datetime(2026, 4, 18, 10, 0)},     # 丢
            {"ts_code": "Y.SZ", "ts": None},                          # 丢
            {"ts_code": "Z.SH", "ts": "2026-04-18T10:00:00"},         # 字符串解析
            {"ts_code": "W.SZ", "ts": "not-a-date"},                  # 解析失败 丢
        ]
        rows = sw.build_rows(records)
        assert [r[0] for r in rows] == ["X.SZ", "Z.SH"]

    def test_pandas_nan_becomes_none(self):
        import numpy as np
        df = pd.DataFrame([
            {"ts_code": "A.SZ", "ts": datetime(2026, 4, 18, 10, 0),
             "price": np.nan, "pct_change": np.nan, "change": 0.0,
             "volume": np.nan, "amount": np.nan},
        ])
        rows = sw.build_rows(df)
        assert rows[0][4] is None  # price
        assert rows[0][5] is None  # pct_change
        assert rows[0][7] is None  # volume
        assert rows[0][8] is None  # amount

    def test_custom_snapshot_type_and_source(self):
        rec = {"ts_code": "A.SZ", "ts": datetime(2026, 4, 18, 15, 0)}
        rows = sw.build_rows([rec], snapshot_type="CLOSE", source="akshare")
        assert rows[0][3] == "CLOSE"
        assert rows[0][-1] == "akshare"


class TestInsertSnapshots:
    def test_empty_input_noop(self):
        factory, cur, conn = make_fake_pg()
        n = sw.insert_snapshots(factory, [])
        assert n == 0
        # 空数据不应建连接 / 调 execute_values
        conn.cursor.assert_not_called()

    def test_non_empty_inserts(self, monkeypatch):
        factory, cur, conn = make_fake_pg()
        calls = {}

        def fake_ev(cur_arg, sql, rows, page_size=500):
            calls["sql"] = sql
            calls["rows"] = list(rows)
            calls["page_size"] = page_size

        # psycopg2.extras 在函数内部 import, 需给 sys.modules 打补丁
        import types
        fake_mod = types.ModuleType("psycopg2.extras")
        fake_mod.execute_values = fake_ev
        monkeypatch.setitem(__import__("sys").modules,
                            "psycopg2.extras", fake_mod)

        recs = [{"ts_code": "A.SZ", "ts": datetime(2026, 4, 18, 10, 0),
                 "price": 1.0}]
        n = sw.insert_snapshots(factory, recs)
        assert n == 1
        assert "ON CONFLICT (ts, ts_code) DO UPDATE" in calls["sql"]
        assert len(calls["rows"]) == 1
        conn.commit.assert_called_once()


# ====================================================================
# subscription_pool
# ====================================================================


@pytest.fixture
def fake_execute_values(monkeypatch):
    """给 psycopg2.extras.execute_values 打 fake."""
    calls = []

    def fake_ev(cur, sql, rows, page_size=100):
        calls.append({"sql": sql, "rows": list(rows)})

    import types, sys
    mod = types.ModuleType("psycopg2.extras")
    mod.execute_values = fake_ev
    monkeypatch.setitem(sys.modules, "psycopg2.extras", mod)
    return calls


class TestSubscriptionPool:
    def test_upsert_codes_normalizes_case_and_writes_one_batch(self, fake_execute_values):
        factory, cur, conn = make_fake_pg()
        exp = datetime(2026, 5, 1, tzinfo=timezone.utc)
        n = sp.upsert(factory, ["000001.sz", "600000.SH", ""], "LHB_RECENT", expires_at=exp)
        assert n == 2
        call = fake_execute_values[0]
        assert "ON CONFLICT (ts_code, reason) DO UPDATE" in call["sql"]
        assert call["rows"] == [
            ("000001.SZ", "LHB_RECENT", exp),
            ("600000.SH", "LHB_RECENT", exp),
        ]
        conn.commit.assert_called_once()

    def test_upsert_empty_list_no_call(self, fake_execute_values):
        factory, cur, conn = make_fake_pg()
        n = sp.upsert(factory, [], "LHB_RECENT")
        assert n == 0
        assert fake_execute_values == []
        conn.commit.assert_not_called()

    def test_replace_by_reason_deletes_then_inserts(self, fake_execute_values):
        factory, cur, conn = make_fake_pg()
        n = sp.replace_by_reason(factory, ["A.SZ"], "ZT_TODAY")
        assert n == 1
        # 必有一次 DELETE
        deletes = [c for c in cur.execute.call_args_list
                   if "DELETE FROM subscription_pool" in str(c[0][0])]
        assert len(deletes) == 1
        assert fake_execute_values[0]["rows"] == [("A.SZ", "ZT_TODAY", None)]

    def test_replace_by_reason_with_empty_list_only_deletes(self, fake_execute_values):
        factory, cur, conn = make_fake_pg()
        n = sp.replace_by_reason(factory, [], "ZT_TODAY")
        assert n == 0
        assert fake_execute_values == []
        deletes = [c for c in cur.execute.call_args_list
                   if "DELETE FROM subscription_pool" in str(c[0][0])]
        assert len(deletes) == 1

    def test_remove_specific_reason(self):
        factory, cur, conn = make_fake_pg()
        cur.rowcount = 1
        n = sp.remove(factory, "a.sz", reason="MANUAL")
        assert n == 1
        args, _ = cur.execute.call_args
        assert "ts_code = %s AND reason = %s" in args[0]
        assert args[1] == ("A.SZ", "MANUAL")

    def test_remove_all_reasons(self):
        factory, cur, conn = make_fake_pg()
        cur.rowcount = 3
        n = sp.remove(factory, "A.SZ")
        assert n == 3
        args, _ = cur.execute.call_args
        assert "reason" not in args[0]

    def test_cleanup_expired(self):
        factory, cur, conn = make_fake_pg()
        cur.rowcount = 5
        n = sp.cleanup_expired(factory)
        assert n == 5
        args, _ = cur.execute.call_args
        assert "expires_at IS NOT NULL" in args[0]

    def test_get_active_codes(self):
        factory, cur, conn = make_fake_pg(fetchall_rows=[("A.SZ",), ("B.SH",)])
        codes = sp.get_active_codes(factory)
        assert codes == ["A.SZ", "B.SH"]
        args, _ = cur.execute.call_args
        assert "expires_at IS NULL OR expires_at > NOW()" in args[0]

    def test_refresh_lhb_recent_sets_ttl(self, fake_execute_values):
        factory, cur, conn = make_fake_pg()
        before = datetime.now(tz=timezone.utc)
        sp.refresh_lhb_recent(factory, ["A.SZ"], ttl=timedelta(days=10))
        call = fake_execute_values[0]
        _, _, exp = call["rows"][0]
        assert exp is not None
        diff = (exp - before).total_seconds()
        assert 9 * 86400 < diff < 11 * 86400

    def test_refresh_zt_today_clears_and_sets_eod(self, fake_execute_values):
        factory, cur, conn = make_fake_pg()
        sp.refresh_zt_today(factory, ["X.SZ", "Y.SZ"])
        # 第一步: DELETE
        deletes = [c for c in cur.execute.call_args_list
                   if "DELETE FROM subscription_pool" in str(c[0][0])]
        assert len(deletes) == 1
        # 第二步: execute_values 写入, expires_at 是当日 16:00 +08
        call = fake_execute_values[0]
        _, reason, exp = call["rows"][0]
        assert reason == "ZT_TODAY"
        assert exp.hour == 16
        # UTC+8 tz
        assert exp.utcoffset() == timedelta(hours=8)
