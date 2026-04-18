"""domain.lhb.daily_job + domain.lhb.scheduler 纯函数/组合逻辑测试.

不连 DB / 不连 akshare: 用 FakeManager + FakeConn mock 所有外部依赖.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Optional

import pandas as pd
import pytest

from backend.domain.lhb import daily_job as dj
from backend.domain.lhb.parser import LhbRecord, LhbSeat
from backend.domain.lhb.scheduler import LhbDailyScheduler


# --------------------------------------------------------------------
# Fakes
# --------------------------------------------------------------------


class FakeManager:
    """只实现 get_lhb_detail / get_lhb_seats 的 stub."""

    def __init__(self, detail_df=None, seats_df=None,
                 raise_detail: Optional[Exception] = None,
                 raise_seats: Optional[Exception] = None):
        self._detail = detail_df
        self._seats = seats_df
        self._raise_detail = raise_detail
        self._raise_seats = raise_seats
        self.detail_calls: list[str] = []
        self.seats_calls: list[str] = []

    def get_lhb_detail(self, td):
        self.detail_calls.append(td)
        if self._raise_detail:
            raise self._raise_detail
        return self._detail

    def get_lhb_seats(self, td):
        self.seats_calls.append(td)
        if self._raise_seats:
            raise self._raise_seats
        return self._seats


class FakeConn:
    def __init__(self):
        self.committed = 0
        self.rolled_back = 0
        self.closed = False

    def cursor(self):
        raise AssertionError("FakeConn.cursor 不应被调用 (ingest/aggregate 应被 mock)")

    def commit(self):
        self.committed += 1

    def rollback(self):
        self.rolled_back += 1

    def close(self):
        self.closed = True


def _detail_df():
    return pd.DataFrame([
        {
            "ts_code": "000001.SZ", "trade_date": "2024-10-15", "name": "平安银行",
            "close_price": 11.23, "pct_change": 5.1, "l_buy": 100, "l_sell": 30,
            "l_net": 70, "reason": "日涨幅偏离7%",
        },
        {
            "ts_code": "600519.SH", "trade_date": "2024-10-15", "name": "贵州茅台",
            "close_price": 1500.0, "pct_change": -3.0, "l_buy": 200, "l_sell": 500,
            "l_net": -300, "reason": "换手率异常",
        },
    ])


def _seats_df():
    return pd.DataFrame([
        {
            "ts_code": "000001.SZ", "trade_date": "2024-10-15",
            "broker_name": "机构专用", "direction": "BUY",
            "buy_amount": 50, "sell_amount": 0, "net_amount": 50,
            "rank": 1, "broker_type": "INSTITUTION", "is_institution": True,
        },
        {
            "ts_code": "000001.SZ", "trade_date": "2024-10-15",
            "broker_name": "游资营业部A", "direction": "BUY",
            "buy_amount": 30, "sell_amount": 0, "net_amount": 30,
            "rank": 2, "broker_type": "HOT_MONEY", "is_institution": False,
        },
    ])


# --------------------------------------------------------------------
# _to_date
# --------------------------------------------------------------------


class TestToDate:
    def test_none(self):
        assert dj._to_date(None) is None

    def test_yyyymmdd(self):
        assert dj._to_date("20241015") == date(2024, 10, 15)

    def test_iso(self):
        assert dj._to_date("2024-10-15") == date(2024, 10, 15)

    def test_date_passthrough(self):
        d = date(2024, 10, 15)
        assert dj._to_date(d) == d

    def test_datetime_strip(self):
        dt = datetime(2024, 10, 15, 18, 30)
        assert dj._to_date(dt) == date(2024, 10, 15)

    def test_invalid(self):
        assert dj._to_date("bad") is None


# --------------------------------------------------------------------
# attach_seats
# --------------------------------------------------------------------


class TestAttachSeats:
    def test_empty(self):
        assert dj.attach_seats([], {}) == 0
        recs = [LhbRecord(ts_code="000001.SZ", trade_date="2024-10-15")]
        assert dj.attach_seats(recs, {}) == 0

    def test_match_by_str_date(self):
        recs = [LhbRecord(ts_code="000001.SZ", trade_date="2024-10-15")]
        seats = {
            ("000001.SZ", "2024-10-15"): [
                LhbSeat(broker_name="机构", direction="BUY",
                        buy_amount=1, is_institution=True),
            ],
        }
        hit = dj.attach_seats(recs, seats)
        assert hit == 1
        assert len(recs[0].seats) == 1
        assert recs[0].seats[0].broker_name == "机构"

    def test_match_by_mixed_date(self):
        """records 用 date, seats key 用 str, 应互通"""
        recs = [LhbRecord(ts_code="000001.SZ", trade_date=date(2024, 10, 15))]
        seats = {("000001.SZ", "20241015"): [LhbSeat("A", "BUY")]}
        assert dj.attach_seats(recs, seats) == 1

    def test_no_match(self):
        recs = [LhbRecord(ts_code="000001.SZ", trade_date="2024-10-15")]
        seats = {("600519.SH", "2024-10-15"): [LhbSeat("A", "BUY")]}
        assert dj.attach_seats(recs, seats) == 0
        assert recs[0].seats == []


# --------------------------------------------------------------------
# run_single_day
# --------------------------------------------------------------------


class TestRunSingleDay:
    def _patch_ingest(self, monkeypatch, main_rows=2, seat_rows=2, skel=0):
        def fake_ingest(conn, records):
            # 至少断言: parse_lhb_dataframe 已产出 records 且 attach 过的席位存在
            total_seats = sum(len(r.seats) for r in records)
            return {
                "main_rows": len(records),
                "seat_rows": total_seats,
                "skel_rows": skel,
            }
        monkeypatch.setattr(
            "backend.domain.lhb.daily_job.ingest_records", fake_ingest,
        )

    def test_happy_path(self, monkeypatch):
        self._patch_ingest(monkeypatch)
        conn = FakeConn()
        mgr = FakeManager(detail_df=_detail_df(), seats_df=_seats_df())
        agg_calls: list = []

        def fake_agg(conn_, as_of=None, **kw):
            agg_calls.append(as_of)

        ret = dj.run_single_day(
            conn, mgr, "20241015", fetch_seats=True,
            aggregate=True, aggregator=fake_agg,
        )
        assert ret["main_rows"] == 2
        assert ret["seat_rows"] == 2  # 两席位都 attach 到 000001.SZ
        assert ret["seats_attached"] == 1
        assert ret["aggregated"] is True
        assert conn.committed == 1
        assert mgr.detail_calls == ["20241015"]
        assert mgr.seats_calls == ["20241015"]
        assert agg_calls == [date(2024, 10, 15)]

    def test_no_detail_short_circuit(self, monkeypatch):
        self._patch_ingest(monkeypatch)
        conn = FakeConn()
        mgr = FakeManager(detail_df=None)
        ret = dj.run_single_day(conn, mgr, "20241015")
        assert ret["main_rows"] == 0
        assert ret["seat_rows"] == 0
        assert mgr.seats_calls == []  # 没 detail 不会再抓 seats
        assert conn.committed == 0

    def test_detail_raises(self, monkeypatch):
        self._patch_ingest(monkeypatch)
        conn = FakeConn()
        mgr = FakeManager(raise_detail=RuntimeError("boom"))
        ret = dj.run_single_day(conn, mgr, "20241015")
        assert ret["main_rows"] == 0
        assert mgr.seats_calls == []

    def test_seats_fail_does_not_block_main(self, monkeypatch):
        self._patch_ingest(monkeypatch)
        conn = FakeConn()
        mgr = FakeManager(detail_df=_detail_df(),
                          raise_seats=RuntimeError("seat 400"))
        ret = dj.run_single_day(conn, mgr, "20241015", fetch_seats=True)
        assert ret["main_rows"] == 2
        assert ret["seat_rows"] == 0
        assert ret["seats_attached"] == 0
        assert conn.committed == 1

    def test_skip_seats(self, monkeypatch):
        self._patch_ingest(monkeypatch)
        conn = FakeConn()
        mgr = FakeManager(detail_df=_detail_df(), seats_df=_seats_df())
        ret = dj.run_single_day(conn, mgr, "20241015", fetch_seats=False)
        assert ret["main_rows"] == 2
        assert mgr.seats_calls == []

    def test_aggregate_failure_is_soft(self, monkeypatch):
        self._patch_ingest(monkeypatch)
        conn = FakeConn()
        mgr = FakeManager(detail_df=_detail_df())

        def bad_agg(*a, **kw):
            raise RuntimeError("agg fail")

        ret = dj.run_single_day(
            conn, mgr, "20241015",
            fetch_seats=False, aggregate=True, aggregator=bad_agg,
        )
        assert ret["main_rows"] == 2
        assert ret["aggregated"] is False


# --------------------------------------------------------------------
# run_range  (skip_existing 关掉, 避免走 DB)
# --------------------------------------------------------------------


class TestRunRange:
    def test_progress_and_summary(self, monkeypatch):
        monkeypatch.setattr(
            "backend.domain.lhb.daily_job.ingest_records",
            lambda conn, records: {
                "main_rows": len(records), "seat_rows": 0, "skel_rows": 0,
            },
        )
        mgr = FakeManager(detail_df=_detail_df())
        conns: list[FakeConn] = []

        def factory():
            c = FakeConn()
            conns.append(c)
            return c

        progress: list[tuple] = []

        def cb(idx, total, ret):
            progress.append((idx, total, ret["trade_date"]))

        summary = dj.run_range(
            factory, mgr, ["20241014", "20241015", "20241016"],
            fetch_seats=False, aggregate_at_end=False,
            skip_existing=False, progress_cb=cb,
        )
        assert summary["total_days"] == 3
        assert summary["done_days"] == 3
        assert summary["main_rows"] == 6  # 3 days * 2 rows
        assert summary["fail"] == 0
        assert len(progress) == 3
        # 每天一个 conn, 每个 conn 均 close
        assert len(conns) == 3
        assert all(c.closed for c in conns)

    def test_single_day_failure_counted(self, monkeypatch):
        calls = {"n": 0}

        def flaky_ingest(conn, records):
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("db down")
            return {"main_rows": len(records), "seat_rows": 0, "skel_rows": 0}

        monkeypatch.setattr(
            "backend.domain.lhb.daily_job.ingest_records", flaky_ingest,
        )
        mgr = FakeManager(detail_df=_detail_df())
        summary = dj.run_range(
            FakeConn, mgr, ["20241014", "20241015", "20241016"],
            fetch_seats=False, aggregate_at_end=False,
            skip_existing=False,
        )
        assert summary["done_days"] == 2
        assert summary["fail"] == 1

    def test_end_aggregate_called(self, monkeypatch):
        monkeypatch.setattr(
            "backend.domain.lhb.daily_job.ingest_records",
            lambda conn, records: {
                "main_rows": len(records), "seat_rows": 0, "skel_rows": 0,
            },
        )
        mgr = FakeManager(detail_df=_detail_df())
        agg_calls: list = []

        def fake_agg(conn, as_of=None, **kw):
            agg_calls.append(as_of)

        dj.run_range(
            FakeConn, mgr, ["20241014", "20241015"],
            fetch_seats=False, aggregate_at_end=True,
            skip_existing=False, aggregator=fake_agg,
        )
        assert agg_calls == [date(2024, 10, 15)]


# --------------------------------------------------------------------
# LhbDailyScheduler
# --------------------------------------------------------------------


class TestScheduler:
    def test_next_run_at_future_today(self):
        frozen = datetime(2024, 10, 15, 10, 0, 0)
        sched = LhbDailyScheduler(
            manager=FakeManager(), run_at_hour=18, run_at_minute=0,
            clock=lambda: frozen,
        )
        nxt = sched.next_run_at(frozen)
        assert nxt == datetime(2024, 10, 15, 18, 0, 0)

    def test_next_run_at_roll_to_tomorrow(self):
        frozen = datetime(2024, 10, 15, 19, 0, 0)
        sched = LhbDailyScheduler(
            manager=FakeManager(), run_at_hour=18, run_at_minute=0,
            clock=lambda: frozen,
        )
        nxt = sched.next_run_at(frozen)
        assert nxt == datetime(2024, 10, 16, 18, 0, 0)

    def test_run_once_uses_injected_job(self):
        frozen = datetime(2024, 10, 15, 18, 5, 0)
        seen = {}

        def fake_job(conn, manager, td, *, fetch_seats, aggregate):
            seen["td"] = td
            seen["fetch_seats"] = fetch_seats
            seen["aggregate"] = aggregate
            return {"trade_date": td, "main_rows": 3, "seat_rows": 1}

        sched = LhbDailyScheduler(
            manager=FakeManager(),
            conn_factory=FakeConn,
            job_fn=fake_job,
            clock=lambda: frozen,
        )
        ret = sched.run_once()
        assert ret["main_rows"] == 3
        assert seen["td"] == "20241015"
        assert seen["aggregate"] is True
        assert sched.get_last_result() == ret

    def test_run_once_explicit_date(self):
        seen = {}

        def fake_job(conn, manager, td, **kw):
            seen["td"] = td
            return {"trade_date": td}

        sched = LhbDailyScheduler(
            manager=FakeManager(),
            conn_factory=FakeConn,
            job_fn=fake_job,
        )
        sched.run_once(trade_date="20200101")
        assert seen["td"] == "20200101"

    def test_run_once_job_raises_returns_error(self):
        def bad_job(*a, **kw):
            raise RuntimeError("nope")

        sched = LhbDailyScheduler(
            manager=FakeManager(),
            conn_factory=FakeConn,
            job_fn=bad_job,
            clock=lambda: datetime(2024, 10, 15, 18, 0),
        )
        ret = sched.run_once()
        assert "error" in ret
        assert ret["trade_date"] == "20241015"
