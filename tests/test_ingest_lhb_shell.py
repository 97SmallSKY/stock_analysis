"""P3: 验证 backend/ingest/lhb.py 作为薄壳正确委托给 domain.lhb.daily_job.run_range。

背景:
  W2 时期 ingest/lhb.py 自带完整入库实现,W3 之后领域层 backend/domain/lhb/
  统一接管,ingest/lhb.py 退化为 CLI 薄壳。本测试锁定该契约:
    - `run()` 必须调用 `domain.lhb.daily_job.run_range`,参数直通
    - `_load_trading_days` 返回空时回退到 `_daterange`
    - `_load_trading_days` / `_daterange` 仍对 sentiment 等模块可用
"""
from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import MagicMock

import backend.ingest.lhb as L


# ----------------------------------------------------------------------
# _daterange:纯函数
# ----------------------------------------------------------------------


def test_daterange_inclusive_both_ends():
    out = list(L._daterange("20240101", "20240103"))
    assert out == ["20240101", "20240102"[:8], "20240103"]


def test_daterange_single_day():
    assert list(L._daterange("20240501", "20240501")) == ["20240501"]


# ----------------------------------------------------------------------
# _load_trading_days:SQL 查询正确
# ----------------------------------------------------------------------


class _Cur:
    def __init__(self, rows):
        self._rows = rows
        self.last_sql = None
        self.last_params = None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params):
        self.last_sql = sql
        self.last_params = params

    def fetchall(self):
        return list(self._rows)


class _Conn:
    def __init__(self, cur):
        self._cur = cur

    def cursor(self):
        return self._cur


def test_load_trading_days_returns_YYYYMMDD_and_passes_iso_params(monkeypatch):
    cur = _Cur([("20240102",), ("20240103",), ("20240104",)])
    conn = _Conn(cur)

    @contextmanager
    def fake_pg_conn():
        yield conn

    monkeypatch.setattr(L, "pg_conn", fake_pg_conn)
    days = L._load_trading_days("20240101", "20240105")

    assert days == ["20240102", "20240103", "20240104"]
    # 参数必须转成 ISO 日期字符串
    assert cur.last_params == ("2024-01-01", "2024-01-05")
    # SQL 必须带 exchange=SSE + is_open=TRUE 过滤
    assert "exchange='SSE'" in cur.last_sql
    assert "is_open=TRUE" in cur.last_sql


# ----------------------------------------------------------------------
# run():核心契约 —— 委托给 domain.lhb.daily_job.run_range
# ----------------------------------------------------------------------


def _install_fakes(monkeypatch, trading_days, run_range_ret=None):
    """装配 lhb.run() 所需的全部依赖为 Mock。

    返回 (fake_run_range, fake_manager_cls),方便断言。
    """
    # _load_trading_days
    monkeypatch.setattr(L, "_load_trading_days", lambda s, e: list(trading_days))

    # DataSourceManager 的构造
    fake_manager = MagicMock(name="DataSourceManager_instance")
    fake_manager_cls = MagicMock(return_value=fake_manager)
    import backend.data_gateway.manager as mgr_mod
    monkeypatch.setattr(mgr_mod, "DataSourceManager", fake_manager_cls)

    # domain.lhb.daily_job.run_range
    fake_run_range = MagicMock(return_value=run_range_ret or {
        "total_days": len(trading_days) or 0,
        "done_days": len(trading_days) or 0,
        "skipped": 0,
        "fail": 0,
        "main_rows": 0,
        "seat_rows": 0,
    })
    import backend.domain.lhb.daily_job as dj
    monkeypatch.setattr(dj, "run_range", fake_run_range)

    return fake_run_range, fake_manager_cls, fake_manager


def test_run_delegates_to_domain_run_range(monkeypatch):
    fake_run_range, _, fake_manager = _install_fakes(
        monkeypatch,
        trading_days=["20240102", "20240103"],
        run_range_ret={
            "total_days": 2, "done_days": 2, "skipped": 0,
            "fail": 0, "main_rows": 42, "seat_rows": 7,
        },
    )

    ret = L.run("20240101", "20240105")

    assert ret["main_rows"] == 42
    assert ret["seat_rows"] == 7

    # run_range 必须被调用且参数对
    fake_run_range.assert_called_once()
    args, kwargs = fake_run_range.call_args
    # 第 1 参 = conn_factory (pg_conn), 第 2 参 = manager, 第 3 参 = trade_days
    assert args[0] is L.pg_conn
    assert args[1] is fake_manager
    assert args[2] == ["20240102", "20240103"]
    # 默认 kwargs
    assert kwargs["fetch_seats"] is True
    assert kwargs["aggregate_at_end"] is True
    assert kwargs["skip_existing"] is True


def test_run_passes_skip_existing_false(monkeypatch):
    fake_run_range, _, _ = _install_fakes(
        monkeypatch, trading_days=["20240102"]
    )
    L.run("20240101", "20240103", skip_existing=False)
    _, kwargs = fake_run_range.call_args
    assert kwargs["skip_existing"] is False


def test_run_falls_back_to_daterange_when_calendar_empty(monkeypatch):
    """trade_calendar 查不到交易日时,应使用自然日区间作为回退。"""
    fake_run_range, _, _ = _install_fakes(monkeypatch, trading_days=[])
    L.run("20240301", "20240303")
    args, _ = fake_run_range.call_args
    trade_days_arg = args[2]
    # 自然日回退 = 3 天
    assert trade_days_arg == ["20240301", "20240302", "20240303"]


# ----------------------------------------------------------------------
# STO-72 回归:run() 必须显式调 manager.initialize(),否则 get_lhb_detail
# 抛「未初始化」被 catch 成 warning,入库 0 行 exit 0,静默数据丢失。
# ----------------------------------------------------------------------


def test_run_initializes_manager_sto72(monkeypatch):
    """STO-72 防回归:run() 必须调 manager.initialize()。"""
    _, _, fake_manager = _install_fakes(monkeypatch, trading_days=["20240102"])
    L.run("20240101", "20240103")
    # 未调 initialize 会让 manager 的 get_lhb_detail 抛 RuntimeError,静默失败
    fake_manager.initialize.assert_called_once()
