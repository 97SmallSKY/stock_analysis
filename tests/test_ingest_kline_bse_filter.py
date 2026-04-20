"""STO-31 Phase 1: `_load_all_ts_codes` 过滤 bse_unavailable 股票。

验证:
1. SQL WHERE 条件包含 `data_source_status != 'bse_unavailable'`
2. 返回列表中不含被标记的股票
3. 跳过数量被日志记录(INFO 级别)
4. COALESCE 兼容 NULL 记录(未迁移的老库)
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from unittest.mock import patch

import pytest


class _FakeCursor:
    """假 cursor:按 execute 顺序返回预设结果;记录每次 SQL 字符串。"""

    def __init__(self, canned: list):
        # canned 形如 [(rows, cols_dummy), ...],仅需 rows
        self._queue = list(canned)
        self._current: list = []
        self.executed_sqls: list[str] = []

    def execute(self, sql, params=None):
        self.executed_sqls.append(sql)
        if self._queue:
            self._current = self._queue.pop(0)
        else:
            self._current = []

    def fetchone(self):
        return self._current[0] if self._current else None

    def fetchall(self):
        return self._current

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeConn:
    def __init__(self, canned: list):
        # 共享同一 cursor 供多次 execute
        self.cursor_obj = _FakeCursor(canned)

    def cursor(self):
        return self.cursor_obj

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def commit(self):
        pass


def _fake_pg_conn(canned: list):
    conn = _FakeConn(canned)

    @contextmanager
    def _ctx():
        yield conn

    return _ctx, conn


# ============================================================
# 1. SQL 中含 bse_unavailable 过滤条件
# ============================================================


def test_load_all_ts_codes_sql_filters_bse_unavailable():
    """SQL 必须 WHERE 含 `data_source_status != 'bse_unavailable'`,
    这是 STO-31 Phase 1 的核心红线;漏此过滤会回退事故。
    """
    # 第一次 execute 返回股票列表;第二次 execute 返回跳过计数
    canned = [
        [("000001.SZ", "000001"), ("600519.SH", "600519")],  # 查询结果
        [(0,)],  # COUNT(*) of skipped
    ]
    ctx, conn = _fake_pg_conn(canned)

    from backend.ingest import kline as mod
    with patch.object(mod, "pg_conn", ctx):
        mod._load_all_ts_codes()

    sql0 = conn.cursor_obj.executed_sqls[0]
    assert "data_source_status" in sql0, \
        "WHERE 子句必须含 data_source_status 过滤"
    assert "bse_unavailable" in sql0, \
        "WHERE 子句必须过滤 bse_unavailable"
    assert "COALESCE" in sql0.upper(), \
        "必须用 COALESCE 兼容 NULL(未迁移的老库)"


# ============================================================
# 2. 返回列表正确过滤并保留白名单前缀
# ============================================================


def test_load_all_ts_codes_returns_filtered_list():
    canned = [
        [
            ("000001.SZ", "000001"),   # OK,白名单前缀
            ("600519.SH", "600519"),   # OK
            ("688008.SH", "688008"),   # OK 科创
            ("400001.BJ", "400001"),   # 非白名单前缀,被 Python 侧过滤
        ],
        [(0,)],
    ]
    ctx, _ = _fake_pg_conn(canned)

    from backend.ingest import kline as mod
    with patch.object(mod, "pg_conn", ctx):
        result = mod._load_all_ts_codes()

    assert result == ["000001.SZ", "600519.SH", "688008.SH"]
    assert "400001.BJ" not in result


# ============================================================
# 3. 跳过数量落日志(INFO)
# ============================================================


def test_load_all_ts_codes_logs_skipped_count(caplog):
    canned = [
        [("000001.SZ", "000001")],
        [(307,)],  # 307 只 BJ 股被跳过
    ]
    ctx, _ = _fake_pg_conn(canned)

    from backend.ingest import kline as mod
    with caplog.at_level(logging.INFO, logger="backend.ingest.kline"):
        with patch.object(mod, "pg_conn", ctx):
            mod._load_all_ts_codes()

    # 验证日志包含 count + STO-31 标识
    matched = [r for r in caplog.records
               if "skip bse_unavailable stocks" in r.message]
    assert matched, "必须有 bse_unavailable 跳过日志"
    assert "count=307" in matched[0].message
    assert "STO-31" in matched[0].message  # 便于 grep 定位


def test_load_all_ts_codes_no_log_when_zero_skipped(caplog):
    """没有被跳过的股票时不打日志,避免噪音。"""
    canned = [
        [("000001.SZ", "000001")],
        [(0,)],
    ]
    ctx, _ = _fake_pg_conn(canned)

    from backend.ingest import kline as mod
    with caplog.at_level(logging.INFO, logger="backend.ingest.kline"):
        with patch.object(mod, "pg_conn", ctx):
            mod._load_all_ts_codes()

    assert not [r for r in caplog.records
                if "skip bse_unavailable" in r.message], \
        "零跳过时不应打日志(避免噪音)"
