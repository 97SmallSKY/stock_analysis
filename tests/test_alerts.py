"""STO-9 告警中心测试 (alert_log 落库 + 熔断/批任务接入)。

用 fake conn/cursor, 不依赖真实 PostgreSQL。
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from backend.core.alerts import (
    AlertCategory,
    AlertLevel,
    send_alert,
)
from backend.data_gateway.circuit_breaker import CircuitBreaker, CircuitState


class _FakeCursor:
    def __init__(self):
        self.calls: list[tuple[str, tuple]] = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params):
        self.calls.append((sql, tuple(params)))


class _FakeConn:
    def __init__(self):
        self.cur = _FakeCursor()
        self.committed = False
        self.closed = False

    def cursor(self):
        return self.cur

    def commit(self):
        self.committed = True

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.closed = True
        return False


# --------------------------------------------------------------------
# send_alert 基本路径
# --------------------------------------------------------------------


class TestSendAlert:
    def test_writes_row_to_alert_log(self):
        conn = _FakeConn()
        ok = send_alert(
            AlertLevel.ERROR,
            AlertCategory.API,
            source="tushare",
            message="额度耗尽",
            details={"date": "20260424"},
            conn_factory=lambda: conn,
        )
        assert ok is True
        assert conn.committed is True
        assert len(conn.cur.calls) == 1
        sql, params = conn.cur.calls[0]
        assert "INSERT INTO alert_log" in sql
        assert params[0] == "ERROR"
        assert params[1] == "API"
        assert params[2] == "tushare"
        assert params[3] == "额度耗尽"
        # details 应为合法 JSON
        assert json.loads(params[4]) == {"date": "20260424"}

    def test_no_details_persists_null(self):
        conn = _FakeConn()
        ok = send_alert(
            AlertLevel.WARN,
            AlertCategory.NETWORK,
            source="akshare",
            message="瞬时",
            conn_factory=lambda: conn,
        )
        assert ok is True
        _, params = conn.cur.calls[0]
        assert params[4] is None

    def test_db_failure_returns_false_no_raise(self):
        def _broken_factory():
            raise RuntimeError("db down")

        ok = send_alert(
            AlertLevel.CRITICAL,
            AlertCategory.NETWORK,
            source="x",
            message="y",
            conn_factory=_broken_factory,
        )
        assert ok is False  # 不抛, 静默降级

    def test_all_levels_dispatch(self):
        for lv in AlertLevel:
            conn = _FakeConn()
            ok = send_alert(
                lv,
                AlertCategory.DATA_QUALITY,
                source="x",
                message="m",
                conn_factory=lambda c=conn: c,
            )
            assert ok is True


# --------------------------------------------------------------------
# 熔断器 OPEN 时触发告警
# --------------------------------------------------------------------


class TestCircuitBreakerAlert:
    def test_trip_emits_alert(self):
        cb = CircuitBreaker(name="akshare", failure_threshold=3)
        with patch("backend.core.alerts.send_alert") as m:
            for _ in range(2):
                cb.record_failure("timeout")
            assert cb.state == CircuitState.CLOSED
            assert m.call_count == 0
            cb.record_failure("timeout")  # 第 3 次 → OPEN
            assert cb.state == CircuitState.OPEN
            assert m.call_count == 1
            args, kwargs = m.call_args
            assert args[0] == AlertLevel.CRITICAL
            assert args[1] == AlertCategory.NETWORK
            assert kwargs["source"] == "akshare"
            assert kwargs["details"]["failure_count"] == 3

    def test_trip_alert_failure_does_not_break_state(self):
        cb = CircuitBreaker(name="x", failure_threshold=1)
        with patch(
            "backend.core.alerts.send_alert",
            side_effect=RuntimeError("alert broken"),
        ):
            cb.record_failure("e")
        assert cb.state == CircuitState.OPEN  # 状态机不受影响


# --------------------------------------------------------------------
# lhb daily_job run_range 失败时触发告警
# --------------------------------------------------------------------


class TestLhbDailyJobAlert:
    def test_run_range_fail_triggers_alert(self):
        from backend.domain.lhb import daily_job

        # mock _get_conn 返回 fake conn; run_single_day 抛异常制造 fail
        conn = _FakeConn()
        with patch.object(daily_job, "_get_conn", return_value=conn), \
             patch.object(daily_job, "_maybe_close"), \
             patch.object(daily_job, "run_single_day", side_effect=RuntimeError("boom")), \
             patch("backend.core.alerts.send_alert") as m:
            summary = daily_job.run_range(
                conn_factory=lambda: conn,
                manager=MagicMock(),
                trade_days=["20260420", "20260421"],
                skip_existing=False,
                aggregate_at_end=False,
            )
        assert summary["fail"] == 2
        assert summary["done_days"] == 0
        assert m.call_count == 1
        args, kwargs = m.call_args
        # 全失败 → CRITICAL
        assert args[0] == AlertLevel.CRITICAL
        assert args[1] == AlertCategory.BATCH_JOB
        assert kwargs["source"] == "lhb.daily_job.run_range"
        assert kwargs["details"]["summary"]["fail"] == 2

    def test_run_range_partial_fail_uses_error_level(self):
        from backend.domain.lhb import daily_job

        conn = _FakeConn()
        call_count = {"n": 0}

        def _flaky(*a, **kw):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return {"main_rows": 1, "seat_rows": 0}
            raise RuntimeError("boom")

        with patch.object(daily_job, "_get_conn", return_value=conn), \
             patch.object(daily_job, "_maybe_close"), \
             patch.object(daily_job, "run_single_day", side_effect=_flaky), \
             patch("backend.core.alerts.send_alert") as m:
            summary = daily_job.run_range(
                conn_factory=lambda: conn,
                manager=MagicMock(),
                trade_days=["20260420", "20260421"],
                skip_existing=False,
                aggregate_at_end=False,
            )
        assert summary["fail"] == 1
        assert summary["done_days"] == 1
        args, _ = m.call_args
        assert args[0] == AlertLevel.ERROR  # 部分失败 → ERROR

    def test_run_range_all_success_no_alert(self):
        from backend.domain.lhb import daily_job

        conn = _FakeConn()
        with patch.object(daily_job, "_get_conn", return_value=conn), \
             patch.object(daily_job, "_maybe_close"), \
             patch.object(daily_job, "run_single_day", return_value={"main_rows": 1, "seat_rows": 2}), \
             patch("backend.core.alerts.send_alert") as m:
            summary = daily_job.run_range(
                conn_factory=lambda: conn,
                manager=MagicMock(),
                trade_days=["20260420"],
                skip_existing=False,
                aggregate_at_end=False,
            )
        assert summary["fail"] == 0
        assert m.call_count == 0
