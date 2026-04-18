"""tests/test_intraday_scheduler.py — W8-3

单元测试 IntradaySnapshotScheduler:
  - snapshot_job: 正常路径 / 空订阅池 / fetch 返回 None / insert 抛异常不崩
  - zt_pool_job 正常与异常
  - cleanup_job 正常与异常
  - CB open 时 _maybe_degrade 切到 30s, closed 后回到 10s
  - start()/stop() 幂等(不真连 APScheduler 定时器, 只校验 job 能被手动调用)

不连真 APScheduler 定时触发 (避免 flaky). 直接调 scheduler.snapshot_job() 等.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pandas as pd
import pytest

from backend.domain.realtime.scheduler import (
    IntradaySnapshotScheduler,
    CRON_SECOND_NORMAL,
    CRON_SECOND_DEGRADED,
)


# --------------------------------------------------------------------
# helpers: fake manager with CrawlerAdapter circuit_breaker
# --------------------------------------------------------------------


def make_manager(cb_state: str = "closed"):
    from backend.data_gateway.category import AdapterName
    mgr = MagicMock()
    ad = MagicMock()
    ad.circuit_breaker.state.value = cb_state
    mgr._router._adapters = {AdapterName.CRAWLER: ad}
    return mgr, ad


# --------------------------------------------------------------------
# snapshot_job
# --------------------------------------------------------------------


class TestSnapshotJob:
    def test_empty_subscription_skips_fetch(self):
        fetch = MagicMock()
        insert = MagicMock()
        s = IntradaySnapshotScheduler(
            get_subscription_codes=lambda: [],
            fetch_snapshot=fetch,
            insert_snapshots=insert,
        )
        r = s.snapshot_job()
        assert r.ok and r.codes == 0 and r.rows == 0
        fetch.assert_not_called()
        insert.assert_not_called()

    def test_happy_path_inserts_rows(self):
        df = pd.DataFrame([{"ts_code": "A.SZ", "price": 1}])
        s = IntradaySnapshotScheduler(
            get_subscription_codes=lambda: ["A.SZ", "B.SZ"],
            fetch_snapshot=lambda codes: df,
            insert_snapshots=lambda d: len(d),
        )
        r = s.snapshot_job()
        assert r.ok and r.codes == 2 and r.rows == 1

    def test_fetch_returns_none_no_insert(self):
        insert = MagicMock()
        s = IntradaySnapshotScheduler(
            get_subscription_codes=lambda: ["A.SZ"],
            fetch_snapshot=lambda codes: None,
            insert_snapshots=insert,
        )
        r = s.snapshot_job()
        assert r.ok and r.rows == 0
        insert.assert_not_called()

    def test_insert_raises_marks_fail_not_crash(self):
        def boom(d):
            raise RuntimeError("pg down")
        s = IntradaySnapshotScheduler(
            get_subscription_codes=lambda: ["A.SZ"],
            fetch_snapshot=lambda codes: pd.DataFrame([{"ts_code": "A.SZ"}]),
            insert_snapshots=boom,
        )
        r = s.snapshot_job()
        assert r.ok is False
        assert "pg down" in (r.error or "")

    def test_publish_called_per_row(self):
        pubs = []
        df = pd.DataFrame([
            {"ts_code": "A.SZ", "price": 1},
            {"ts_code": "B.SZ", "price": 2},
        ])
        s = IntradaySnapshotScheduler(
            get_subscription_codes=lambda: ["A.SZ", "B.SZ"],
            fetch_snapshot=lambda c: df,
            insert_snapshots=lambda d: len(d),
            publish=lambda code, row: pubs.append((code, row["price"])),
        )
        r = s.snapshot_job()
        assert r.ok
        assert set(pubs) == {("A.SZ", 1), ("B.SZ", 2)}


# --------------------------------------------------------------------
# zt_pool_job / cleanup_job
# --------------------------------------------------------------------


class TestAuxJobs:
    def test_zt_pool_job_writes(self):
        seen = {}
        s = IntradaySnapshotScheduler(
            fetch_zt_pool=lambda: ["A.SZ", "B.SZ"],
            refresh_zt_today=lambda codes: seen.setdefault("codes", list(codes)),
        )
        r = s.zt_pool_job()
        assert r.ok and r.codes == 2
        assert seen["codes"] == ["A.SZ", "B.SZ"]

    def test_zt_pool_job_no_callbacks_ok(self):
        s = IntradaySnapshotScheduler()
        r = s.zt_pool_job()
        assert r.ok and r.codes == 0

    def test_zt_pool_job_failure_captured(self):
        def boom():
            raise ValueError("net")
        s = IntradaySnapshotScheduler(
            fetch_zt_pool=boom,
            refresh_zt_today=lambda c: 0,
        )
        r = s.zt_pool_job()
        assert r.ok is False and "net" in (r.error or "")

    def test_cleanup_job_ok(self):
        s = IntradaySnapshotScheduler(cleanup_expired=lambda: 7)
        r = s.cleanup_job()
        assert r.ok and r.rows == 7

    def test_cleanup_job_raises(self):
        def boom():
            raise RuntimeError("oops")
        s = IntradaySnapshotScheduler(cleanup_expired=boom)
        r = s.cleanup_job()
        assert r.ok is False


# --------------------------------------------------------------------
# CB 降级 / 恢复
# --------------------------------------------------------------------


class TestCircuitBreakerDegrade:
    def test_cb_open_sets_degraded_expr(self):
        mgr, ad = make_manager("open")
        s = IntradaySnapshotScheduler(
            manager=mgr,
            get_subscription_codes=lambda: [],
        )
        # 伪装 scheduler 存在, 用 MagicMock 接住 reschedule_job
        s._scheduler = MagicMock()
        s._maybe_degrade()
        assert s._current_second_expr == CRON_SECOND_DEGRADED
        assert s._scheduler.reschedule_job.call_count == 1

    def test_cb_recover_reverts_to_normal(self):
        mgr, ad = make_manager("closed")
        s = IntradaySnapshotScheduler(
            manager=mgr,
            get_subscription_codes=lambda: [],
        )
        s._scheduler = MagicMock()
        # 先模拟处于 degraded
        s._current_second_expr = CRON_SECOND_DEGRADED
        s._maybe_degrade()
        assert s._current_second_expr == CRON_SECOND_NORMAL
        assert s._scheduler.reschedule_job.call_count == 1

    def test_cb_no_change_no_reschedule(self):
        mgr, ad = make_manager("closed")
        s = IntradaySnapshotScheduler(
            manager=mgr,
            get_subscription_codes=lambda: [],
        )
        s._scheduler = MagicMock()
        # 已处于 normal, CB closed, 不该触发
        s._maybe_degrade()
        assert s._scheduler.reschedule_job.call_count == 0

    def test_degrade_disabled_flag(self):
        mgr, ad = make_manager("open")
        s = IntradaySnapshotScheduler(
            manager=mgr,
            get_subscription_codes=lambda: [],
            degrade_when_cb_open=False,
        )
        s._scheduler = MagicMock()
        s._maybe_degrade()
        assert s._current_second_expr == CRON_SECOND_NORMAL
        assert s._scheduler.reschedule_job.call_count == 0

    def test_no_manager_no_crash(self):
        s = IntradaySnapshotScheduler(
            get_subscription_codes=lambda: [],
        )
        s._scheduler = MagicMock()
        # 无 manager, 不崩, 也不 reschedule
        s._maybe_degrade()
        assert s._current_second_expr == CRON_SECOND_NORMAL


# --------------------------------------------------------------------
# start/stop
# --------------------------------------------------------------------


class TestLifecycle:
    def test_start_registers_three_jobs(self):
        s = IntradaySnapshotScheduler(
            get_subscription_codes=lambda: [],
            fetch_snapshot=lambda c: None,
            insert_snapshots=lambda d: 0,
            fetch_zt_pool=lambda: [],
            refresh_zt_today=lambda c: 0,
            cleanup_expired=lambda: 0,
        )
        sched = s.start()
        try:
            ids = {j.id for j in sched.get_jobs()}
            assert ids == {"intraday_snapshot", "intraday_zt_pool", "intraday_cleanup_subs"}
            # 重复 start 幂等
            assert s.start() is sched
        finally:
            s.stop()
            assert s._scheduler is None

    def test_stop_without_start_is_noop(self):
        s = IntradaySnapshotScheduler()
        s.stop()
