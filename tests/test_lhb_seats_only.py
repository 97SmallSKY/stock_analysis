"""backend.domain.lhb.daily_job 的 seats-only 分支 + conn helper 单测 (STO-8).

目标: 把 daily_job.py 覆盖率从 50% 抬到 ≥ 80%,重点覆盖:
  - run_seats_only_single: 无效 date / 空主表 / get_lhb_seats 异常 / top_n 裁剪 / 席位去重
  - run_seats_only_range: 进度回调 / skip_existing / 单日失败不阻断
  - _get_conn / _maybe_close: ctx manager 分支 + 裸 conn 分支 + close 异常吞咽

不依赖真实 PostgreSQL。
"""
from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from backend.domain.lhb import daily_job as DJ


# --------------------------------------------------------------------
# Fake cursor / conn
# --------------------------------------------------------------------


class _FakeCursor:
    def __init__(self, rows=None):
        self._rows = rows or []
        self.calls: list[tuple[str, tuple]] = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.calls.append((sql, params))

    def fetchall(self):
        return list(self._rows)


class _FakeConn:
    def __init__(self, cursor):
        self._cur = cursor
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def cursor(self):
        return self._cur

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


class _FakeManager:
    """注入 get_lhb_seats / get_lhb_seats_for_codes 结果或异常."""

    def __init__(self, seats_df=None, raise_on_seats=False, seats_for_codes_df=None):
        self._seats = seats_df
        self._raise = raise_on_seats
        self._seats_for_codes = seats_for_codes_df
        self.calls_get_lhb_seats: list[str] = []
        self.calls_get_lhb_seats_for_codes: list[tuple[str, list]] = []

    def get_lhb_seats(self, trade_date):
        self.calls_get_lhb_seats.append(trade_date)
        if self._raise:
            raise RuntimeError("simulated akshare timeout")
        return self._seats

    def get_lhb_seats_for_codes(self, trade_date, codes):
        self.calls_get_lhb_seats_for_codes.append((trade_date, list(codes)))
        if self._raise:
            raise RuntimeError("simulated akshare timeout")
        return self._seats_for_codes


# --------------------------------------------------------------------
# run_seats_only_single
# --------------------------------------------------------------------


class TestRunSeatsOnlySingle:
    def test_invalid_trade_date_early_return(self):
        conn = _FakeConn(_FakeCursor())
        mgr = _FakeManager()
        out = DJ.run_seats_only_single(conn, mgr, "not-a-date")
        assert out == {"trade_date": "not-a-date", "main_ids": 0, "seat_rows": 0}
        # 没 query 主表
        assert conn._cur.calls == []

    def test_empty_main_table_short_circuit(self):
        cur = _FakeCursor(rows=[])
        conn = _FakeConn(cur)
        mgr = _FakeManager()
        out = DJ.run_seats_only_single(conn, mgr, "20241015")
        assert out["main_ids"] == 0
        assert out["seat_rows"] == 0
        # 查了主表,但没调 manager
        assert mgr.calls_get_lhb_seats == []
        assert mgr.calls_get_lhb_seats_for_codes == []

    def test_get_seats_raises_returns_main_ids_only(self):
        cur = _FakeCursor(rows=[
            (1, "000001.SZ", date(2024, 10, 15)),
            (2, "600000.SH", date(2024, 10, 15)),
        ])
        conn = _FakeConn(cur)
        mgr = _FakeManager(raise_on_seats=True)
        out = DJ.run_seats_only_single(conn, mgr, "20241015")
        assert out["main_ids"] == 2
        assert out["seat_rows"] == 0
        assert mgr.calls_get_lhb_seats == ["20241015"]

    def test_top_n_routes_to_for_codes(self):
        """top_n > 0 且 manager 支持 get_lhb_seats_for_codes 时用后者."""
        cur = _FakeCursor(rows=[
            (1, "000001.SZ", date(2024, 10, 15)),
            (2, "600000.SH", date(2024, 10, 15)),
            (3, "000858.SZ", date(2024, 10, 15)),
        ])
        conn = _FakeConn(cur)
        # 席位 df 返回 None(也即 parse 后空 dict),不走 upsert
        mgr = _FakeManager(seats_for_codes_df=None)
        out = DJ.run_seats_only_single(conn, mgr, "20241015", top_n=2)
        # top_n 裁剪后 main_ids=2
        assert out["main_ids"] == 2
        # 调用了 for_codes,而不是全量
        assert len(mgr.calls_get_lhb_seats_for_codes) == 1
        _, codes = mgr.calls_get_lhb_seats_for_codes[0]
        assert codes == ["000001.SZ", "600000.SH"]

    def test_seats_empty_short_circuit(self):
        cur = _FakeCursor(rows=[(1, "000001.SZ", date(2024, 10, 15))])
        conn = _FakeConn(cur)
        # 返回空 DataFrame
        mgr = _FakeManager(seats_df=pd.DataFrame())
        out = DJ.run_seats_only_single(conn, mgr, "20241015")
        assert out["main_ids"] == 1
        assert out["seat_rows"] == 0

    def test_dedup_by_broker_and_direction_keeps_higher_score(self):
        """同股同席位重复时,本地去重保留 buy/sell/net 绝对值和更大的一条."""
        cur = _FakeCursor(rows=[(1, "000001.SZ", date(2024, 10, 15))])
        conn = _FakeConn(cur)
        # 构造两条同 (broker_name, direction) 的席位记录,选更大的那条
        seats_df = pd.DataFrame([
            {
                "ts_code": "000001.SZ", "trade_date": date(2024, 10, 15),
                "broker_name": "A", "direction": "BUY",
                "buy_amount": 10.0, "sell_amount": 0.0, "net_amount": 10.0,
                "rank": 1, "is_institution": False, "broker_type": "HOT_MONEY",
            },
            {
                "ts_code": "000001.SZ", "trade_date": date(2024, 10, 15),
                "broker_name": "A", "direction": "BUY",
                "buy_amount": 100.0, "sell_amount": 0.0, "net_amount": 100.0,
                "rank": 1, "is_institution": False, "broker_type": "HOT_MONEY",
            },
        ])
        mgr = _FakeManager(seats_df=seats_df)
        with patch("backend.domain.lhb.ingestor.upsert_seats", return_value=1) as mock_us:
            out = DJ.run_seats_only_single(conn, mgr, "20241015")
        assert out["seat_rows"] == 1
        assert conn.committed is True
        # upsert_seats 被调,传入的 recs 里 seats 只剩 1 条(去重后)
        args, _ = mock_us.call_args
        recs = args[1]
        assert len(recs) == 1
        assert len(recs[0].seats) == 1
        # 保留的是 amount 更大的一条
        assert recs[0].seats[0].buy_amount == 100.0


# --------------------------------------------------------------------
# run_seats_only_range
# --------------------------------------------------------------------


class TestRunSeatsOnlyRange:
    def test_progress_cb_and_summary(self):
        def _factory():
            return _FakeConn(_FakeCursor())

        # 每日都走 run_seats_only_single,我们 patch 掉它避免实际查表
        calls = []

        def _progress(idx, total, ret):
            calls.append((idx, total, ret["trade_date"]))

        with patch.object(
            DJ, "run_seats_only_single",
            return_value={"trade_date": "x", "main_ids": 2, "seat_rows": 5},
        ):
            summary = DJ.run_seats_only_range(
                _factory, object(), ["20241015", "20241016"],
                skip_existing=False, progress_cb=_progress,
            )
        assert summary["done_days"] == 2
        assert summary["skipped"] == 0
        assert summary["fail"] == 0
        assert summary["main_ids"] == 4
        assert summary["seat_rows"] == 10
        assert len(calls) == 2

    def test_skip_existing_filters_days(self):
        # 被 covered 查询返回的日期应被 skip
        cur = _FakeCursor(rows=[("20241015",)])

        def _factory():
            # 每次 factory 都返回全新 conn
            return _FakeConn(cur)

        with patch.object(
            DJ, "run_seats_only_single",
            return_value={"trade_date": "x", "main_ids": 0, "seat_rows": 0},
        ) as m:
            summary = DJ.run_seats_only_range(
                _factory, object(), ["20241015", "20241016"],
                skip_existing=True,
            )
        assert summary["skipped"] == 1
        assert summary["done_days"] == 1
        # run_seats_only_single 只跑了 1 天(20241016)
        assert m.call_count == 1

    def test_single_day_exception_counted_not_fatal(self):
        def _factory():
            return _FakeConn(_FakeCursor())

        def _side_effect(conn, mgr, d, *, top_n=None):
            if d == "20241015":
                raise RuntimeError("boom")
            return {"trade_date": d, "main_ids": 1, "seat_rows": 2}

        with patch.object(DJ, "run_seats_only_single", side_effect=_side_effect):
            summary = DJ.run_seats_only_range(
                _factory, object(), ["20241015", "20241016"],
                skip_existing=False,
            )
        assert summary["fail"] == 1
        assert summary["done_days"] == 1
        assert summary["seat_rows"] == 2

    def test_empty_days_returns_zero_summary(self):
        summary = DJ.run_seats_only_range(
            lambda: None, object(), [], skip_existing=True,
        )
        assert summary["total_days"] == 0
        assert summary["done_days"] == 0

    def test_covered_query_failure_soft(self):
        """查 covered 失败只是 warn,不阻断后续 for 循环."""

        class _BadCursor:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def execute(self, *a, **kw):
                raise RuntimeError("db down")

            def fetchall(self):
                return []

        def _factory():
            conn = MagicMock()
            conn.cursor.return_value = _BadCursor()
            return conn

        with patch.object(
            DJ, "run_seats_only_single",
            return_value={"trade_date": "x", "main_ids": 0, "seat_rows": 0},
        ) as m:
            summary = DJ.run_seats_only_range(
                _factory, object(), ["20241015"], skip_existing=True,
            )
        # covered 查询失败 → covered=空 → 正常跑 1 天
        assert summary["done_days"] == 1
        assert m.call_count == 1


# --------------------------------------------------------------------
# _get_conn / _maybe_close
# --------------------------------------------------------------------


class TestConnHelpers:
    def test_get_conn_ctx_manager(self):
        entered = []
        exited = []

        class _Ctx:
            def __enter__(self_inner):
                entered.append(True)
                conn = MagicMock()
                conn._marker = "from_ctx"
                return conn

            def __exit__(self_inner, *a):
                exited.append(True)
                return False

        conn = DJ._get_conn(lambda: _Ctx())
        assert conn._marker == "from_ctx"
        assert entered == [True]
        assert id(conn) in DJ._ACTIVE_CTXS
        # _maybe_close 调用 ctx.__exit__ 并清掉 _ACTIVE_CTXS
        DJ._maybe_close(conn)
        assert exited == [True]
        assert id(conn) not in DJ._ACTIVE_CTXS

    def test_get_conn_bare_conn(self):
        raw = MagicMock(spec=["close"])
        conn = DJ._get_conn(lambda: raw)
        assert conn is raw
        DJ._maybe_close(conn)
        raw.close.assert_called_once()

    def test_maybe_close_swallows_exception(self):
        # 裸 conn 关闭时抛异常, _maybe_close 静默吞掉
        conn = MagicMock()
        conn.close.side_effect = RuntimeError("close failed")
        # 没有对应 ctx,走裸 close 分支
        DJ._maybe_close(conn)  # 不应 raise

    def test_maybe_close_ctx_exit_exception_swallowed(self):
        class _Ctx:
            def __enter__(self_inner):
                conn = MagicMock()
                return conn

            def __exit__(self_inner, *a):
                raise RuntimeError("ctx exit failed")

        conn = DJ._get_conn(lambda: _Ctx())
        # 不应 raise
        DJ._maybe_close(conn)
        assert id(conn) not in DJ._ACTIVE_CTXS
