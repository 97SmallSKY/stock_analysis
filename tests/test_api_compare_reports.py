"""tests/test_api_compare_reports.py — W10-2

覆盖:
  /api/backtest/compare
    - 两策略对比, 返回 ComparisonReport.to_dict shape
    - 未知策略 400
    - end < start 400
  /api/reports/weekly  /api/reports/weekly/generate
    - 用 fake conn_factory 注入到 app.state.report_conn_factory
    - 三段查询 (lhb/robot/backtest) 解析为 JSON
  WeeklyReportScheduler
    - run_once 走注入的 build_fn + sink
    - sink 抛错被吞, run_once 仍返回 dict
    - start/stop 幂等 + 注册 weekly_report job
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.domain.reports.weekly import WeeklyReport, build_weekly_report
from backend.domain.reports.scheduler import WeeklyReportScheduler


# --------------------------------------------------------------------
# /api/backtest/compare
# --------------------------------------------------------------------


@pytest.fixture
def compare_client(monkeypatch):
    from backend.api.backtest import router, set_compare_provider_factory
    from datetime import timedelta

    TODAY = date(2024, 10, 15)
    DAYS = [TODAY + timedelta(days=i) for i in range(5)]

    class FakeProvider:
        def list_trade_days(self, start, end):
            return [d for d in DAYS if start <= d <= end]

        def get_daily(self, ts_code, trade_date):
            return {"open": 10, "high": 10.3, "low": 9.9, "close": 10.2}

        def list_lhb_records(self, as_of):
            return []

    # 保证 builtins 已注册
    import backend.domain.strategy.builtins  # noqa: F401
    from backend.domain.strategy import list_strategies
    assert list_strategies(), "未注册任何策略, 无法测"

    set_compare_provider_factory(lambda: FakeProvider())
    app = FastAPI()
    app.include_router(router, prefix="/api/backtest")
    yield TestClient(app), list_strategies()


class TestCompareApi:
    def test_two_strategies(self, compare_client):
        client, strategies = compare_client
        name = strategies[0]
        body = {
            "start_date": "2024-10-15",
            "end_date": "2024-10-19",
            "initial_capital": 500000,
            "strategies": [
                {"name": "A", "strategy_name": name},
                {"name": "B", "strategy_name": name,
                 "params": {"score_threshold": 70}},
            ],
        }
        r = client.post("/api/backtest/compare", json=body)
        assert r.status_code == 200, r.text
        d = r.json()
        assert set(d) >= {"start_date", "end_date", "runs", "winner"}
        names = {row["name"] for row in d["runs"]}
        assert names == {"A", "B"}

    def test_unknown_strategy_400(self, compare_client):
        client, _ = compare_client
        r = client.post("/api/backtest/compare", json={
            "start_date": "2024-10-15",
            "end_date": "2024-10-19",
            "strategies": [{"name": "x", "strategy_name": "__not_exist__"}],
        })
        assert r.status_code == 400

    def test_bad_date_range_400(self, compare_client):
        client, strategies = compare_client
        r = client.post("/api/backtest/compare", json={
            "start_date": "2024-10-19",
            "end_date": "2024-10-15",
            "strategies": [{"name": "x", "strategy_name": strategies[0]}],
        })
        assert r.status_code == 400


# --------------------------------------------------------------------
# weekly build + /api/reports/weekly
# --------------------------------------------------------------------


class _Cur:
    def __init__(self, script: list):
        # script: 每个元素是一次 execute 后应当返回的 fetchall 结果 (list[tuple])
        self._script = list(script)
        self._idx = -1
        self.description = None

    def __enter__(self): return self
    def __exit__(self, *a): return False

    def execute(self, sql, params=()):
        self._idx += 1
        entry = self._script[self._idx] if self._idx < len(self._script) else ([], [])
        cols, rows = entry
        self.description = [type("C", (), {"name": c})() for c in cols]
        self._rows = rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class _Conn:
    def __init__(self, script):
        self._cur = _Cur(script)

    def cursor(self):
        return self._cur

    def __enter__(self): return self
    def __exit__(self, *a): return False


def make_conn_factory(script):
    @contextmanager
    def f():
        yield _Conn(script)
    return f


def _weekly_script():
    # 1. lhb count
    # 2. lhb top reasons
    # 3. lhb top net
    # 4. robot summary
    # 5. backtest top
    return [
        (["n"], [(42,)]),
        (["reason", "cnt"], [("日换手率", 12), ("机构净买", 8)]),
        (["ts_code", "trade_date", "reason", "net_amount", "score"], [
            ("600000.SH", date(2024, 10, 15), "日换手率", 1.2e8, 80.0),
        ]),
        (["robot_id", "sells", "buys", "fees", "entries"], [
            (1, 200000.0, 180000.0, 100.0, 5),
        ]),
        (["task_id", "strategy_name", "annual_return", "sharpe_ratio",
          "max_drawdown", "win_rate", "finished_at"], [
            ("abc", "s1", 0.25, 1.3, -0.08, 0.6,
             date(2024, 10, 14)),
        ]),
    ]


class TestWeeklyBuild:
    def test_build_weekly_shapes(self):
        factory = make_conn_factory(_weekly_script())
        rep = build_weekly_report(factory, end=date(2024, 10, 15), days=7)
        assert rep.lhb_total == 42
        assert rep.lhb_top_reasons[0]["reason"] == "日换手率"
        assert rep.lhb_top_net[0]["ts_code"] == "600000.SH"
        assert rep.lhb_top_net[0]["trade_date"] == "2024-10-15"
        rs = rep.robot_summary[0]
        assert rs["net_pnl"] == round(200000 - 180000 - 100, 2)
        assert rep.backtest_top[0]["strategy_name"] == "s1"
        assert rep.errors == []

    def test_build_weekly_section_failure_collected(self):
        # 让第一段 (lhb count) 的 execute 就抛
        @contextmanager
        def factory():
            class C:
                def __enter__(self): return self
                def __exit__(self, *a): return False
                def cursor(self_):
                    class CU:
                        def __enter__(self): return self
                        def __exit__(self, *a): return False
                        def execute(self_, *a, **k): raise RuntimeError("pg")
                    return CU()
            yield C()
        rep = build_weekly_report(factory, end=date(2024, 10, 15), days=7)
        assert any("lhb_section" in e for e in rep.errors)
        # 其它段也会失败但每段独立记录
        assert len(rep.errors) >= 1


class TestReportsApi:
    def test_weekly_get_and_post(self):
        from backend.api.reports import router
        app = FastAPI()
        app.state.report_conn_factory = make_conn_factory(_weekly_script())
        app.include_router(router, prefix="/api/reports")
        client = TestClient(app)

        r = client.get("/api/reports/weekly?end=2024-10-15&days=7")
        assert r.status_code == 200
        d = r.json()
        assert d["lhb_total"] == 42

        # POST generate 用今天 end
        app.state.report_conn_factory = make_conn_factory(_weekly_script())
        r2 = client.post("/api/reports/weekly/generate?days=7")
        assert r2.status_code == 200


# --------------------------------------------------------------------
# WeeklyReportScheduler
# --------------------------------------------------------------------


class TestWeeklyScheduler:
    def test_run_once_invokes_sink(self):
        sunk = []
        build_stub = MagicMock(return_value=WeeklyReport(
            start_date=date(2024, 10, 1), end_date=date(2024, 10, 7),
            lhb_total=1,
        ))
        s = WeeklyReportScheduler(
            conn_factory=lambda: None,
            sink=lambda d: sunk.append(d),
            build_fn=build_stub,
        )
        out = s.run_once(end=date(2024, 10, 7))
        assert out["lhb_total"] == 1
        assert sunk and sunk[0]["lhb_total"] == 1
        assert s.last_report is out

    def test_sink_error_swallowed(self):
        def boom(_d): raise RuntimeError("sink")
        s = WeeklyReportScheduler(
            conn_factory=lambda: None,
            sink=boom,
            build_fn=lambda cf, end=None, days=7: WeeklyReport(
                start_date=date(2024, 1, 1), end_date=date(2024, 1, 7),
            ),
        )
        d = s.run_once()
        assert "start_date" in d

    def test_build_error_returned(self):
        def bad(cf, end=None, days=7): raise RuntimeError("bp")
        s = WeeklyReportScheduler(conn_factory=lambda: None, build_fn=bad)
        d = s.run_once()
        assert d["error"] == "bp"

    def test_start_stop_registers_job(self):
        s = WeeklyReportScheduler(
            conn_factory=lambda: None,
            build_fn=lambda cf, end=None, days=7: WeeklyReport(
                start_date=date(2024, 1, 1), end_date=date(2024, 1, 7),
            ),
        )
        sched = s.start()
        try:
            ids = {j.id for j in sched.get_jobs()}
            assert "weekly_report" in ids
            assert s.start() is sched  # 幂等
        finally:
            s.stop()
            assert s._scheduler is None
        s.stop()  # 再次 stop 不崩
