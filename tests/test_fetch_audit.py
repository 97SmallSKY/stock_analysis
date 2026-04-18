"""FetchAudit 单元测试 + Router 审计 hook 集成测试。"""
from __future__ import annotations

import time
from unittest.mock import patch, MagicMock

import pandas as pd
import pytest

from backend.data_gateway.fetch_audit import (
    FetchAudit,
    FetchLogEntry,
    _persist,
)


@pytest.fixture(autouse=True)
def _reset_instance():
    """每个测试后重置单例, 避免线程/buffer 污染"""
    yield
    FetchAudit.reset_instance()


# --------------------------------------------------------------------
# _persist / DB 契约
# --------------------------------------------------------------------


class TestPersist:
    def test_empty_returns_zero(self):
        assert _persist([]) == 0

    def test_db_unavailable_silent(self):
        entry = _make_entry()
        with patch(
            "backend.db.session.pg_conn",
            side_effect=RuntimeError("db down"),
        ):
            n = _persist([entry])
        assert n == 0


# --------------------------------------------------------------------
# FetchAudit 记录/缓冲
# --------------------------------------------------------------------


def _make_entry(adapter_name="akshare", ok=True, error_msg=None):
    from datetime import datetime
    return FetchLogEntry(
        category="DAILY_KLINE",
        method_name="get_daily_kline",
        adapter_name=adapter_name,
        latency_ms=50,
        ok=ok,
        rows=100 if ok else None,
        error_msg=error_msg,
        fetched_at=datetime.now(),
    )


class TestAuditRecord:
    def test_record_adds_to_buffer(self):
        audit = FetchAudit(flush_interval_sec=60)  # 关 auto flush
        audit.record("DAILY_KLINE", "get_daily_kline", "akshare",
                     10, True, 5, None)
        assert len(audit._buffer) == 1
        e = audit._buffer[0]
        assert e.adapter_name == "akshare"
        assert e.ok is True
        assert e.rows == 5

    def test_disabled_no_record(self):
        audit = FetchAudit(enabled=False)
        audit.record("DAILY_KLINE", "get_daily_kline", "akshare",
                     10, True, 5, None)
        assert audit._buffer == []

    def test_error_message_truncated(self):
        audit = FetchAudit(flush_interval_sec=60)
        long_err = "x" * 1000
        audit.record("CAT", "m", "akshare", 1, False, None, long_err)
        assert len(audit._buffer[0].error_msg) == 500

    def test_none_error_stays_none(self):
        audit = FetchAudit(flush_interval_sec=60)
        audit.record("CAT", "m", "akshare", 1, True, 5, None)
        assert audit._buffer[0].error_msg is None

    def test_max_buffer_triggers_sync_flush(self):
        audit = FetchAudit(flush_interval_sec=60, max_buffer=3)
        with patch(
            "backend.data_gateway.fetch_audit._persist",
            return_value=3,
        ) as p:
            for _ in range(3):
                audit.record("CAT", "m", "akshare", 1, True, 5, None)
            # 第 3 条到达阈值 -> 立刻 flush
            p.assert_called_once()
        assert audit._buffer == []


# --------------------------------------------------------------------
# flush + start/stop
# --------------------------------------------------------------------


class TestFlush:
    def test_flush_empty_is_zero(self):
        audit = FetchAudit(flush_interval_sec=60)
        assert audit.flush() == 0

    def test_flush_calls_persist(self):
        audit = FetchAudit(flush_interval_sec=60)
        audit.record("CAT", "m", "akshare", 1, True, 5, None)
        audit.record("CAT", "m", "sina", 2, False, 0, "empty")
        with patch(
            "backend.data_gateway.fetch_audit._persist",
            return_value=2,
        ) as p:
            n = audit.flush()
        assert n == 2
        p.assert_called_once()
        assert audit._buffer == []

    def test_flush_persist_failure_drops_buffer(self):
        """_persist 异常时 buffer 清空(避免反复重试挤爆),返回 0"""
        audit = FetchAudit(flush_interval_sec=60)
        audit.record("CAT", "m", "akshare", 1, True, 5, None)
        with patch(
            "backend.data_gateway.fetch_audit._persist",
            side_effect=RuntimeError("db down"),
        ):
            n = audit.flush()
        assert n == 0
        assert audit._buffer == []  # 已 drain, 不重试


class TestStartStop:
    def test_start_then_stop_flushes(self):
        audit = FetchAudit(flush_interval_sec=0.1)
        with patch(
            "backend.data_gateway.fetch_audit._persist",
            return_value=1,
        ) as p:
            audit.start()
            audit.record("CAT", "m", "akshare", 1, True, 5, None)
            time.sleep(0.25)  # 至少触发一次 auto flush
            audit.stop()
            assert p.called

    def test_double_start_idempotent(self):
        audit = FetchAudit(flush_interval_sec=30)
        audit.start()
        t1 = audit._thread
        audit.start()
        t2 = audit._thread
        audit.stop()
        assert t1 is t2


# --------------------------------------------------------------------
# 单例
# --------------------------------------------------------------------


class TestSingleton:
    def test_get_instance_same(self):
        a = FetchAudit.get_instance()
        b = FetchAudit.get_instance()
        assert a is b

    def test_reset_instance(self):
        a = FetchAudit.get_instance()
        FetchAudit.reset_instance()
        b = FetchAudit.get_instance()
        assert a is not b


# --------------------------------------------------------------------
# Router 集成(record 被调用)
# --------------------------------------------------------------------


class TestRouterIntegration:
    def _make_router(self):
        from backend.data_gateway.router import DataRouter
        from backend.data_gateway.category import AdapterName, DataCategory
        from backend.data_gateway.base_adapter import BaseDataAdapter

        r = DataRouter()

        # 造一个 ok adapter + 一个返空 adapter + 一个抛错 adapter
        class OkAd:
            name = AdapterName.AKSHARE
            initialized = True
            circuit_breaker = MagicMock()

            def get_daily_kline(self, *a, **kw):
                return pd.DataFrame({"ts_code": ["x"], "close": [10]})

        class EmptyAd:
            name = AdapterName.SINA
            initialized = True
            circuit_breaker = MagicMock()

            def get_daily_kline(self, *a, **kw):
                return None

        class BoomAd:
            name = AdapterName.CRAWLER
            initialized = True
            circuit_breaker = MagicMock()

            def get_daily_kline(self, *a, **kw):
                raise RuntimeError("boom")

        r._adapters[AdapterName.SINA] = EmptyAd()
        r._adapters[AdapterName.CRAWLER] = BoomAd()
        r._adapters[AdapterName.AKSHARE] = OkAd()
        r.set_priority_chain(
            DataCategory.DAILY_KLINE,
            [AdapterName.SINA, AdapterName.CRAWLER, AdapterName.AKSHARE],
        )
        return r

    def test_records_all_tries(self):
        from backend.data_gateway.category import DataCategory

        r = self._make_router()
        audit = FetchAudit.get_instance()
        audit.set_enabled(True)

        df = r.fetch(DataCategory.DAILY_KLINE, "get_daily_kline")
        assert df is not None and len(df) == 1

        # 期待 3 条记录: sina (empty), crawler (exception), akshare (ok)
        entries = audit._buffer
        assert len(entries) == 3
        assert entries[0].adapter_name == "sina" and entries[0].ok is False
        assert entries[1].adapter_name == "crawler" and entries[1].ok is False
        assert "boom" in (entries[1].error_msg or "")
        assert entries[2].adapter_name == "akshare" and entries[2].ok is True
        assert entries[2].rows == 1
