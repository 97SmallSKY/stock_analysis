"""tests/test_fetch_audit_degrade.py — OPT-1

验证 FetchAudit 在目标表不存在时会:
  1) 首次失败打一条 warning
  2) 设置 _persist_disabled = True
  3) 后续 _persist 直接 return 0, 不再走 pg_conn(), 不再打 warning
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest

import backend.data_gateway.fetch_audit as FA


@pytest.fixture(autouse=True)
def _reset():
    FA.reset_persist_state_for_tests()
    yield
    FA.reset_persist_state_for_tests()


def _make_entry():
    from datetime import datetime
    return FA.FetchLogEntry(
        category="DAILY_KLINE",
        method_name="get_daily_kline",
        adapter_name="akshare",
        latency_ms=123,
        ok=True,
        rows=100,
        error_msg=None,
        fetched_at=datetime.now(),
    )


def test_permanent_error_disables_after_first(monkeypatch, caplog):
    """relation does not exist 这种永久性错 -> 禁用 + 只 warn 一次."""
    calls = []

    @contextmanager
    def bad_pg():
        calls.append(1)
        raise RuntimeError(
            'relation "data_fetch_log" does not exist'
        )
        yield None  # pragma: no cover

    # monkeypatch pg_conn 于 _persist 内部 import 的位置
    import backend.db.session as db_session
    monkeypatch.setattr(db_session, "pg_conn", bad_pg)

    caplog.set_level(logging.WARNING, logger=FA.logger.name)

    # 第一次: 尝试连接 -> 写表 -> 报错 -> 禁用 + warn
    assert FA._persist([_make_entry()]) == 0
    assert FA._persist_disabled is True
    warns = [r for r in caplog.records if "目标表不可用" in r.getMessage()]
    assert len(warns) == 1

    # 第二次: 直接返回 0, 不再调 pg_conn
    caplog.clear()
    assert FA._persist([_make_entry()]) == 0
    # pg_conn 只被调用过一次
    assert len(calls) == 1
    # 无新 warning
    assert not caplog.records


def test_transient_error_still_warns_each_time(monkeypatch, caplog):
    """普通连接错 (非永久) 不禁用, 但每次都 warn (现有行为)."""
    calls = []

    @contextmanager
    def flaky_pg():
        calls.append(1)
        raise RuntimeError("connection reset by peer")
        yield None  # pragma: no cover

    import backend.db.session as db_session
    monkeypatch.setattr(db_session, "pg_conn", flaky_pg)

    caplog.set_level(logging.WARNING, logger=FA.logger.name)
    FA._persist([_make_entry()])
    FA._persist([_make_entry()])
    assert FA._persist_disabled is False
    assert len(calls) == 2
    assert len([r for r in caplog.records if "失败" in r.getMessage()]) == 2


def test_empty_entries_noop():
    assert FA._persist([]) == 0
    assert FA._persist_disabled is False
