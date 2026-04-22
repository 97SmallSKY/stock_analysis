"""适配器健康轮询器。

设计:
- 每 5 分钟跑一轮所有 adapter 的 health_check()
- 探测结果 (is_healthy, latency_ms, cb_state, cb_failures) 写 adapter_health 表
- 对外暴露 get_last_status() 供 API 查询
- 不依赖 APScheduler 库本身, 自己起一个守护线程(简单稳定)

对外入口:
    HealthChecker.start()  # 启动后台线程
    HealthChecker.stop()
    HealthChecker.check_once() -> dict[adapter_name -> HealthRecord]
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Dict, Optional

from backend.data_gateway.base_adapter import BaseDataAdapter

logger = logging.getLogger(__name__)


@dataclass
class HealthRecord:
    adapter_name: str
    is_healthy: bool
    latency_ms: int
    error_msg: Optional[str]
    cb_state: str
    cb_failures: int
    checked_at: datetime

    def to_db_row(self) -> tuple:
        return (
            self.adapter_name,
            self.is_healthy,
            self.latency_ms,
            self.error_msg,
            self.cb_state,
            self.cb_failures,
            self.checked_at,
        )

    def to_dict(self) -> dict:
        d = asdict(self)
        d["checked_at"] = self.checked_at.isoformat()
        return d


def _probe(adapter: BaseDataAdapter) -> HealthRecord:
    """对单个 adapter 跑一次 health_check."""
    start = time.time()
    err_msg: Optional[str] = None
    try:
        healthy = bool(adapter.health_check())
    except Exception as e:  # noqa: BLE001
        healthy = False
        err_msg = f"{type(e).__name__}: {str(e)[:200]}"
    latency = int((time.time() - start) * 1000)

    cb_status = adapter.circuit_breaker.get_status()
    return HealthRecord(
        adapter_name=adapter.name.value,
        is_healthy=healthy,
        latency_ms=latency,
        error_msg=err_msg,
        cb_state=str(cb_status.get("state", "")),
        cb_failures=int(cb_status.get("failure_count", 0) or 0),
        checked_at=datetime.now(),
    )


def _persist(records: list[HealthRecord]) -> int:
    """把记录写入 adapter_health 表. DB 不可用时静默 skip(保证不影响主流程)."""
    if not records:
        return 0
    try:
        from backend.db.session import pg_conn
        from psycopg2.extras import execute_values

        with pg_conn() as conn, conn.cursor() as cur:
            execute_values(
                cur,
                """
                INSERT INTO adapter_health
                    (adapter_name, is_healthy, latency_ms, error_msg,
                     cb_state, cb_failures, checked_at)
                VALUES %s
                """,
                [r.to_db_row() for r in records],
                page_size=50,
            )
        return len(records)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[HealthChecker] 写 adapter_health 失败(忽略): {e}")
        return 0


class HealthChecker:
    """简单守护线程实现的轮询器。"""

    def __init__(self, manager, interval_sec: int = 300):
        """
        Args:
            manager: DataSourceManager 实例(从中取已注册的 adapter)
            interval_sec: 轮询间隔
        """
        self._manager = manager
        self._interval = interval_sec
        self._stop_evt = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_snapshot: Dict[str, HealthRecord] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # 公开 API
    # ------------------------------------------------------------------

    def check_once(self) -> Dict[str, HealthRecord]:
        """跑一次探测, 返回 {adapter_name -> HealthRecord}, 并更新内存快照 + 落库。"""
        adapters = self._get_adapters()
        records: list[HealthRecord] = []
        for ad in adapters:
            try:
                rec = _probe(ad)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[HealthChecker] _probe({ad.name.value}) 异常: {e}")
                continue
            records.append(rec)

        _persist(records)

        with self._lock:
            self._last_snapshot = {r.adapter_name: r for r in records}

        if records:
            logger.info(
                "[HealthChecker] 完成一轮: "
                + ", ".join(
                    f"{r.adapter_name}={'OK' if r.is_healthy else 'DOWN'}({r.latency_ms}ms)"
                    for r in records
                )
            )
        return self._last_snapshot.copy()

    def get_last_snapshot(self) -> Dict[str, HealthRecord]:
        with self._lock:
            return dict(self._last_snapshot)

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop_evt.clear()
        self._thread = threading.Thread(
            target=self._loop, name="health-checker", daemon=True
        )
        self._thread.start()
        logger.info(f"[HealthChecker] 启动, interval={self._interval}s")

    def stop(self):
        self._stop_evt.set()
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None
        logger.info("[HealthChecker] 已停止")

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _get_adapters(self) -> list[BaseDataAdapter]:
        """从 manager 的 router 拿已注册适配器。

        STO-57: 只探 `initialized=True` 的 adapter,与 router.fetch 的
        skip-uninitialized 语义对齐(router.py:139)。未配 token 的
        TuShareAdapter 注册后 `_initialized=False`,过去每轮都 probe 一次
        并记一条 DOWN 噪音;现在直接跳过。后续若配上 token 恢复初始化,
        自动回归探测。
        """
        router = getattr(self._manager, "_router", None)
        if router is None:
            return []
        adapters_map = getattr(router, "_adapters", {})
        return [
            ad for ad in adapters_map.values()
            if getattr(ad, "initialized", True)
        ]

    def _loop(self):
        # 立即跑一次 + 之后按间隔
        while not self._stop_evt.is_set():
            try:
                self.check_once()
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[HealthChecker] loop 异常: {e}")
            # 可中断 sleep
            if self._stop_evt.wait(self._interval):
                break
