"""Router fetch 审计: 记录 (category, adapter, latency, ok, err, rows) 到 DB.

设计:
- `FetchAudit` 单例持有内存缓冲 + 后台 flush 线程
- `Router.fetch` 每次循环尝试/结束时调用 `record(...)`
- flush 时批量 INSERT data_fetch_log; DB 不可用时静默丢弃(不影响主流程)

为什么用缓冲:
- router.fetch 是热路径, 每次 INSERT 一条会拖慢
- 缓冲每 2 秒 flush 一次, page_size=200, 正常情况下 < 1ms 开销
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class FetchLogEntry:
    category: str
    method_name: str
    adapter_name: str
    latency_ms: int
    ok: bool
    rows: Optional[int]
    error_msg: Optional[str]
    fetched_at: datetime

    def to_db_row(self) -> tuple:
        return (
            self.category,
            self.method_name,
            self.adapter_name,
            self.latency_ms,
            self.ok,
            self.rows,
            self.error_msg,
            self.fetched_at,
        )


class FetchAudit:
    """缓冲 + 定期 flush 的审计记录器。线程安全。"""

    _instance: Optional["FetchAudit"] = None
    _instance_lock = threading.Lock()

    def __init__(
        self, flush_interval_sec: float = 2.0,
        max_buffer: int = 1000,
        enabled: bool = True,
    ):
        self._buffer: list[FetchLogEntry] = []
        self._buffer_lock = threading.Lock()
        self._flush_interval = flush_interval_sec
        self._max_buffer = max_buffer
        self._enabled = enabled
        self._stop_evt = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------
    # 单例访问
    # ------------------------------------------------------------------

    @classmethod
    def get_instance(cls) -> "FetchAudit":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    @classmethod
    def reset_instance(cls):
        with cls._instance_lock:
            if cls._instance is not None:
                cls._instance.stop()
            cls._instance = None

    # ------------------------------------------------------------------
    # 公开 API
    # ------------------------------------------------------------------

    def set_enabled(self, enabled: bool):
        self._enabled = enabled

    def record(
        self,
        category: str,
        method_name: str,
        adapter_name: str,
        latency_ms: int,
        ok: bool,
        rows: Optional[int] = None,
        error_msg: Optional[str] = None,
    ):
        if not self._enabled:
            return
        entry = FetchLogEntry(
            category=category,
            method_name=method_name,
            adapter_name=adapter_name,
            latency_ms=latency_ms,
            ok=ok,
            rows=rows,
            error_msg=(str(error_msg)[:500] if error_msg else None),
            fetched_at=datetime.now(),
        )
        with self._buffer_lock:
            self._buffer.append(entry)
            # 超限时立刻同步 flush(避免内存无限增长)
            if len(self._buffer) >= self._max_buffer:
                self._drain_and_persist_locked()

    def flush(self) -> int:
        """强制同步 flush, 返回落库条数。"""
        with self._buffer_lock:
            return self._drain_and_persist_locked()

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop_evt.clear()
        self._thread = threading.Thread(
            target=self._loop, name="fetch-audit-flush", daemon=True,
        )
        self._thread.start()
        logger.info(
            f"[FetchAudit] 启动, flush_interval={self._flush_interval}s, "
            f"enabled={self._enabled}"
        )

    def stop(self):
        self._stop_evt.set()
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None
        # 最后一次 flush
        self.flush()

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _drain_and_persist_locked(self) -> int:
        """调用方必须已持有 _buffer_lock。"""
        if not self._buffer:
            return 0
        batch = self._buffer
        self._buffer = []
        # 解锁 I/O
        try:
            return _persist(batch)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[FetchAudit] 落库失败(丢弃): {e}")
            return 0

    def _loop(self):
        while not self._stop_evt.is_set():
            if self._stop_evt.wait(self._flush_interval):
                break
            try:
                self.flush()
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[FetchAudit] flush 循环异常: {e}")


def _persist(entries: list[FetchLogEntry]) -> int:
    if not entries:
        return 0
    try:
        from backend.db.session import pg_conn
        from psycopg2.extras import execute_values

        with pg_conn() as conn, conn.cursor() as cur:
            execute_values(
                cur,
                """
                INSERT INTO data_fetch_log
                    (category, method_name, adapter_name,
                     latency_ms, ok, rows, error_msg, fetched_at)
                VALUES %s
                """,
                [e.to_db_row() for e in entries],
                page_size=200,
            )
        return len(entries)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[FetchAudit._persist] 失败: {e}")
        return 0
