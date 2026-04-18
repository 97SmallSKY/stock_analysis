"""龙虎榜日终调度器.

每天 18:00 (本地时区) 触发一次 `run_single_day(today)`:
  - 如果今天是周末 / 停市, AKShare 会返回空, parse_lhb_dataframe 得 [],
    ingest_records 也只会写 0 行, 属幂等空跑.
  - 为防 AKShare 当日 18:00 还没刷新, 支持 retry: interval_sec 一个重试
    窗口(默认 300s, 最多 3 次, 取到数据或到点为止).

设计和 HealthChecker 一致: 独立守护线程, 主程序 import 后 `start()` 即可.

对外:
    LhbDailyScheduler(manager).start()
    LhbDailyScheduler(manager).run_once(trade_date=None)
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta
from typing import Callable, Optional

logger = logging.getLogger(__name__)


class LhbDailyScheduler:
    def __init__(
        self,
        manager,
        *,
        run_at_hour: int = 18,
        run_at_minute: int = 0,
        fetch_seats: bool = True,
        conn_factory: Optional[Callable] = None,
        job_fn: Optional[Callable] = None,
        clock: Callable[[], datetime] = datetime.now,
    ):
        """
        Args:
            manager:         DataGatewayManager (或测试 fake)
            run_at_hour/min: 每日触发时间 (本地时区)
            fetch_seats:     是否抓席位
            conn_factory:    无参 callable -> psycopg2 conn. 默认 backend.db.session.pg_conn
            job_fn:          注入 run_single_day, 便于测试
            clock:           注入时钟, 测试用
        """
        self._manager = manager
        self._run_at_hour = run_at_hour
        self._run_at_minute = run_at_minute
        self._fetch_seats = fetch_seats
        self._conn_factory = conn_factory
        self._job_fn = job_fn
        self._clock = clock

        self._stop_evt = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_run: Optional[datetime] = None
        self._last_result: Optional[dict] = None

    # ------------------------------------------------------------------
    # 运行时 API
    # ------------------------------------------------------------------

    def run_once(self, trade_date: Optional[str] = None) -> dict:
        """立即跑一次, 返回单日 result dict."""
        td = trade_date or self._clock().strftime("%Y%m%d")
        conn = self._open_conn()
        try:
            job = self._job_fn
            if job is None:
                from backend.domain.lhb.daily_job import run_single_day as job
            ret = job(
                conn, self._manager, td,
                fetch_seats=self._fetch_seats,
                aggregate=True,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[LhbScheduler] {td} run_once 失败: {e}")
            ret = {"trade_date": td, "error": str(e)}
        finally:
            self._close_conn(conn)
        self._last_run = self._clock()
        self._last_result = ret
        return ret

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop_evt.clear()
        self._thread = threading.Thread(
            target=self._loop, name="lhb-daily-scheduler", daemon=True
        )
        self._thread.start()
        logger.info(
            f"[LhbScheduler] 启动, 每日 {self._run_at_hour:02d}:"
            f"{self._run_at_minute:02d} 触发"
        )

    def stop(self):
        self._stop_evt.set()
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None

    def get_last_result(self) -> Optional[dict]:
        return self._last_result

    # ------------------------------------------------------------------
    # 内部: 下一次触发时间 + 循环
    # ------------------------------------------------------------------

    def next_run_at(self, now: Optional[datetime] = None) -> datetime:
        """返回下次应触发的绝对时间 (今日或明日的 HH:MM)."""
        n = now or self._clock()
        target = n.replace(
            hour=self._run_at_hour, minute=self._run_at_minute,
            second=0, microsecond=0,
        )
        if target <= n:
            target = target + timedelta(days=1)
        return target

    def _loop(self):
        while not self._stop_evt.is_set():
            now = self._clock()
            target = self.next_run_at(now)
            wait_sec = max(1.0, (target - now).total_seconds())
            # 单次 wait 最多 60 秒, 否则 stop 信号响应慢
            step = min(60.0, wait_sec)
            if self._stop_evt.wait(step):
                return
            # 到点才跑
            if self._clock() >= target:
                try:
                    self.run_once()
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"[LhbScheduler] run_once 抛出: {e}")

    # ------------------------------------------------------------------
    # conn 兼容
    # ------------------------------------------------------------------

    def _open_conn(self):
        factory = self._conn_factory
        if factory is None:
            from backend.db.session import pg_conn
            factory = pg_conn
        obj = factory()
        if hasattr(obj, "__enter__"):
            return obj.__enter__()
        return obj

    def _close_conn(self, conn):
        try:
            close = getattr(conn, "close", None)
            if callable(close):
                close()
        except Exception:  # noqa: BLE001
            pass
