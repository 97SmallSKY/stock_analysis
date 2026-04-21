"""backend.domain.lhb.scheduler 的 start/stop/_loop 单测 (STO-8).

目标: 把 scheduler.py 覆盖率从 67% 抬到 ≥ 80%,重点:
  - start(): 首次启动 thread,重复调用幂等
  - stop(): 触发 _stop_evt 并 join
  - _loop(): 走到 target 时调 run_once;中途 stop 事件响应
  - _open_conn / _close_conn: ctx manager 与裸 conn

不依赖真实 PostgreSQL / DataGatewayManager,用 fake job_fn 和注入 clock.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest

from backend.domain.lhb.scheduler import LhbDailyScheduler


class TestStartStopLifecycle:
    def test_start_twice_idempotent(self):
        sched = LhbDailyScheduler(
            manager=object(),
            run_at_hour=23,
            run_at_minute=59,
            conn_factory=lambda: MagicMock(close=MagicMock()),
            job_fn=lambda *a, **kw: {"trade_date": "x"},
            clock=lambda: datetime(2024, 10, 15, 10, 0, 0),
        )
        sched.start()
        t1 = sched._thread
        assert t1 is not None and t1.is_alive()
        # 再调 start 不应开新线程
        sched.start()
        assert sched._thread is t1
        sched.stop()
        assert sched._thread is None

    def test_stop_without_start_noop(self):
        sched = LhbDailyScheduler(
            manager=object(),
            conn_factory=lambda: MagicMock(close=MagicMock()),
            job_fn=lambda *a, **kw: {},
        )
        # 没 start,stop 也不应 raise
        sched.stop()
        assert sched._thread is None

    def test_loop_triggers_run_once_when_clock_crosses_target(self):
        """_loop 分支: 等待到 target 时触发 run_once。

        做法: clock 前半段返回 now,_stop_evt.wait 返回 False,
        之后 clock 推进到 target 之后,应该触发 run_once(我们计数).
        """
        call_log: list[dict] = []

        def _job(*a, **kw):
            call_log.append(kw)
            return {"trade_date": kw.get("trade_date", "x"), "ok": True}

        # Target = 10:00:00
        # _loop 内每轮调 clock 两次: 开头 `now = self._clock()` 和 `if self._clock() >= target`
        # 构造:第 1 轮 now=9:59:59, 第 1 轮判定 clock=10:00:05 (过 target → run_once)
        times = [
            datetime(2024, 10, 15, 9, 59, 59),   # now
            datetime(2024, 10, 15, 10, 0, 5),    # 判定 clock
        ]
        idx = {"i": 0}

        def _clock():
            i = min(idx["i"], len(times) - 1)
            idx["i"] += 1
            return times[i]

        sched = LhbDailyScheduler(
            manager=object(),
            run_at_hour=10,
            run_at_minute=0,
            conn_factory=lambda: MagicMock(close=MagicMock()),
            job_fn=_job,
            clock=_clock,
        )
        # 直接跑一次 _loop: stop 信号在第 1 次 wait 后即刻置上
        # 让 wait 立刻返回 False 一次,然后 return True
        real_wait = sched._stop_evt.wait
        wait_calls = {"n": 0}

        def _fake_wait(timeout):
            wait_calls["n"] += 1
            if wait_calls["n"] == 1:
                return False  # 第一次:表示没 stop, 继续看 clock
            return True  # 第二次: stop 信号触发, 跳出 loop

        sched._stop_evt.wait = _fake_wait  # type: ignore[assignment]
        sched._loop()
        # run_once 应该被调一次(clock 越过 target)
        assert len(call_log) == 1

    def test_loop_exception_in_run_once_does_not_kill_loop(self):
        """run_once 抛异常被 _loop 捕获,loop 仍可退出."""
        def _job(*a, **kw):
            raise RuntimeError("job boom")

        # 与上一个测试相同构造:now=9:59:59, 下一次判定 clock=10:00:05 (过 target)
        times = [
            datetime(2024, 10, 15, 9, 59, 59),
            datetime(2024, 10, 15, 10, 0, 5),
        ]
        idx = {"i": 0}

        def _clock():
            i = min(idx["i"], len(times) - 1)
            idx["i"] += 1
            return times[i]

        sched = LhbDailyScheduler(
            manager=object(),
            run_at_hour=10,
            run_at_minute=0,
            conn_factory=lambda: MagicMock(close=MagicMock()),
            job_fn=_job,
            clock=_clock,
        )

        wait_calls = {"n": 0}

        def _fake_wait(timeout):
            wait_calls["n"] += 1
            if wait_calls["n"] == 1:
                return False
            return True

        sched._stop_evt.wait = _fake_wait  # type: ignore[assignment]
        # 不应抛异常(run_once 内部自己 try/except 把 job 的异常兜成 error dict)
        sched._loop()
        # _last_result 应记录 error
        last = sched.get_last_result()
        assert last is not None
        assert "error" in last


class TestRunOnceConnLifecycle:
    def test_open_conn_ctx_manager(self):
        class _Ctx:
            def __init__(self):
                self.entered = False

            def __enter__(self):
                self.entered = True
                return MagicMock()

            def __exit__(self, *a):
                return False

        ctx = _Ctx()

        def _factory():
            return ctx

        # job_fn 签名: (conn, manager, trade_date, **kw)
        sched = LhbDailyScheduler(
            manager=object(),
            conn_factory=_factory,
            job_fn=lambda conn, mgr, td, **kw: {"trade_date": td},
            clock=lambda: datetime(2024, 10, 15, 10, 0, 0),
        )
        result = sched.run_once(trade_date="20241015")
        assert ctx.entered is True
        assert result["trade_date"] == "20241015"

    def test_close_conn_swallows_exception(self):
        # 裸 conn(spec 限定只有 close 方法,无 __enter__,走 _close_conn 分支)
        conn = MagicMock(spec=["close"])
        conn.close.side_effect = RuntimeError("close boom")
        sched = LhbDailyScheduler(
            manager=object(),
            conn_factory=lambda: conn,
            job_fn=lambda *a, **kw: {},
            clock=lambda: datetime(2024, 10, 15, 10, 0, 0),
        )
        # 不应 raise
        sched.run_once(trade_date="20241015")
        conn.close.assert_called_once()

    def test_default_job_fn_import_path(self):
        """当不传 job_fn 时,会 import backend.domain.lhb.daily_job.run_single_day.

        验证 import 路径可达即可(真正的 job 用 monkeypatch 替掉,避免 DB 访问).
        """
        conn = MagicMock()
        conn.close = MagicMock()
        sched = LhbDailyScheduler(
            manager=object(),
            conn_factory=lambda: conn,
            clock=lambda: datetime(2024, 10, 15, 10, 0, 0),
        )

        import backend.domain.lhb.daily_job as DJ
        called = {}

        def _fake_run_single_day(c, m, td, **kw):
            called["td"] = td
            return {"trade_date": td, "ok": True}

        # monkeypatch 真正的 run_single_day
        orig = DJ.run_single_day
        DJ.run_single_day = _fake_run_single_day  # type: ignore[assignment]
        try:
            result = sched.run_once(trade_date="20241015")
        finally:
            DJ.run_single_day = orig  # type: ignore[assignment]
        assert called["td"] == "20241015"
        assert result["ok"] is True
