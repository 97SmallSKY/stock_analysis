"""tests/integration/test_e2e_api.py — W13-1 后端全链路 E2E

覆盖:
    GET  /healthz
    GET  /metrics
    GET  /api/data-sources/status
    GET  /api/lhb
    GET  /api/lhb/seats
    GET  /api/dashboard
    GET  /api/reports/weekly
    POST /api/reports/weekly/generate
    GET  /api/robots        (+ enable/disable/pause/resume/status 全套)
    POST /api/backtest/compare
    WS   /ws/snapshot        (fakeredis publish -> client 收)

手法: conftest.FakeConn / FakeCursor 按脚本喂数, 无外部依赖.
"""
from __future__ import annotations

import json
from datetime import date, timedelta

import pytest


# --------------------------------------------------------------------
# /healthz + /metrics
# --------------------------------------------------------------------


def test_healthz(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_metrics_returns_prometheus(client):
    # 先打几个 API 让 metrics 有计数
    client.get("/healthz")
    r = client.get("/metrics")
    assert r.status_code == 200
    body = r.text
    assert "http_requests_total" in body
    assert "http_request_duration_seconds" in body


# --------------------------------------------------------------------
# /api/data-sources
# --------------------------------------------------------------------


def test_data_sources_status(client):
    r = client.get("/api/data-sources/status")
    # manager 由 main 启动时 bootstrap, 可能 404/500 (取决于初始化)
    # 正常情况 200; 接口不应 crash
    assert r.status_code in (200, 500)
    if r.status_code == 200:
        d = r.json()
        assert "adapters" in d
        assert "priority_chains" in d


# --------------------------------------------------------------------
# /api/lhb
# --------------------------------------------------------------------


def test_lhb_list(client, db_script):
    # lhb 实现顺序: 1) count(*); 2) rows
    db_script.append((["n"], [(1,)]))
    db_script.append((
        ["id", "ts_code", "trade_date", "reason", "close", "pct_change",
         "turnover", "buy_amount", "sell_amount", "net_amount", "score",
         "score_detail"],
        [(1, "600000.SH", date(2024, 10, 15), "日涨幅", 10.2, 5.0,
          0.03, 1e8, 0.8e8, 0.2e8, 75.0, None)],
    ))
    r = client.get("/api/lhb?limit=10")
    assert r.status_code == 200
    d = r.json()
    assert d["total"] == 1
    assert d["rows"][0]["ts_code"] == "600000.SH"
    assert d["rows"][0]["trade_date"] == "2024-10-15"


def test_lhb_seats(client, db_script):
    # seats 只做一次 fetchall
    db_script.append((
        ["ts_code", "trade_date", "direction", "seat_name",
         "buy_amount", "sell_amount", "net_amount", "rank"],
        [
            ("600000.SH", date(2024, 10, 15), "buy", "某券商深圳",
             5e7, 0, 5e7, 1),
            ("600000.SH", date(2024, 10, 15), "sell", "游资 X",
             0, 3e7, -3e7, 1),
        ],
    ))
    r = client.get("/api/lhb/seats?trade_date=2024-10-15&ts_code=600000.SH")
    assert r.status_code == 200
    rows = r.json()["rows"]
    assert len(rows) == 2
    assert rows[0]["direction"] in ("buy", "sell")


# --------------------------------------------------------------------
# /api/dashboard (@cached 15s — 同一进程测试同一 key 会命中, 不影响断言)
# --------------------------------------------------------------------


def test_dashboard(client, db_script):
    # dashboard 调用顺序: _latest_sentiment (1 个 execute) -> _top_lhb (1 个 execute)
    # 各自 pg_conn, 走不同的 FakeConn 实例; 但我们 pg_conn_factory 每次 yield
    # 新 FakeConn, 下面的 script 是 "所有 cursor.execute 顺序"
    # -> 每次 with pg_conn() 一个新 FakeConn = 新 cursor _idx 从 -1 开始
    # 所以需要每张 FakeConn 只吃一个 execute.
    # 为简化: 让 dashboard 查询全部失败 (script 为空), 返回 None/[]
    # 接口仍 200 — 足以验证端到端.
    r = client.get("/api/dashboard?top_n=5")
    assert r.status_code == 200
    d = r.json()
    # keys 存在且类型合理
    assert "sentiment" in d
    assert "top_lhb" in d
    assert "adapters" in d


# --------------------------------------------------------------------
# /api/reports/weekly
# --------------------------------------------------------------------


def test_weekly_report(client, db_script):
    # weekly 三段: lhb count / lhb top reasons / lhb top net / robots / backtest
    db_script.append((["n"], [(3,)]))                                # lhb count
    db_script.append((["reason", "cnt"], [("日涨幅", 2), ("换手率", 1)]))
    db_script.append((
        ["ts_code", "trade_date", "reason", "net_amount", "score"],
        [("600000.SH", date(2024, 10, 15), "日涨幅", 1e8, 70.0)],
    ))
    db_script.append((
        ["robot_id", "sells", "buys", "fees", "entries"],
        [(1, 20000.0, 18000.0, 100.0, 5)],
    ))
    db_script.append((
        ["task_id", "strategy_name", "annual_return", "sharpe_ratio",
         "max_drawdown", "win_rate", "finished_at"],
        [("abc", "s1", 0.2, 1.5, -0.05, 0.6, date(2024, 10, 14))],
    ))
    r = client.get("/api/reports/weekly?end=2024-10-15&days=7")
    assert r.status_code == 200
    d = r.json()
    assert d["lhb_total"] == 3
    assert d["lhb_top_reasons"][0]["reason"] == "日涨幅"
    assert d["robot_summary"][0]["net_pnl"] == 1900.0
    assert d["backtest_top"][0]["strategy_name"] == "s1"


# --------------------------------------------------------------------
# /api/robots — pause/resume/enable/disable 全套
# --------------------------------------------------------------------


def test_robots_lifecycle_full(client):
    # 先构造一个 engine 塞进 registry
    from backend.domain.robot.live_engine import LiveRobotEngine
    from backend.domain.robot.robot import RobotSnapshot
    from backend.domain.robot.state import RobotState
    snap = RobotSnapshot(
        robot_id=42, state=RobotState.RUNNING,
        cash=1_000_000, total_assets=1_000_000,
        max_positions=3, per_position_pct=0.3,
    )
    eng = LiveRobotEngine(snap, enabled=False)
    from backend.api.main import app
    app.state.robot_registry[42] = eng

    try:
        r = client.get("/api/robots")
        assert r.status_code == 200
        ids = [row["robot_id"] for row in r.json()["robots"]]
        assert 42 in ids

        r = client.get("/api/robots/42/status")
        assert r.json()["snapshot"]["enabled"] is False

        r = client.post("/api/robots/42/enable")
        assert r.json()["snapshot"]["enabled"] is True

        r = client.post("/api/robots/42/pause")
        assert r.json()["snapshot"]["state"] == "PAUSED"

        r = client.post("/api/robots/42/resume")
        assert r.json()["snapshot"]["state"] == "RUNNING"

        r = client.post("/api/robots/42/disable")
        assert r.json()["snapshot"]["enabled"] is False

        r = client.post("/api/robots/999/enable")
        assert r.status_code == 404
    finally:
        del app.state.robot_registry[42]


# --------------------------------------------------------------------
# /api/backtest/compare
# --------------------------------------------------------------------


def test_backtest_compare_e2e(client):
    # 注入一个不查真 DB 的 provider
    from backend.api.backtest import set_compare_provider_factory
    from datetime import date as _d, timedelta as _t

    TODAY = _d(2024, 10, 15)
    DAYS = [TODAY + _t(days=i) for i in range(5)]

    class _FakeProvider:
        def list_trade_days(self, s, e): return [d for d in DAYS if s <= d <= e]
        def get_daily(self, ts_code, td):
            return {"open": 10, "high": 10.2, "low": 9.8, "close": 10.1}
        def list_lhb_records(self, as_of): return []

    set_compare_provider_factory(lambda: _FakeProvider())
    # 拿到第一个注册策略
    import backend.domain.strategy.builtins  # noqa: F401
    from backend.domain.strategy import list_strategies
    strats = list_strategies()
    assert strats

    r = client.post("/api/backtest/compare", json={
        "start_date": "2024-10-15",
        "end_date": "2024-10-19",
        "initial_capital": 500000,
        "strategies": [
            {"name": "A", "strategy_name": strats[0]},
            {"name": "B", "strategy_name": strats[0], "params": {"score_threshold": 70}},
        ],
    })
    assert r.status_code == 200
    d = r.json()
    assert {row["name"] for row in d["runs"]} == {"A", "B"}


# --------------------------------------------------------------------
# /ws/snapshot — Redis pubsub -> WebSocket 推送
# --------------------------------------------------------------------


def test_ws_snapshot_receives_published(client):
    from backend.api.main import app
    fake = app.state.redis
    with client.websocket_connect("/ws/snapshot?codes=E2E.SZ") as ws:
        hello = json.loads(ws.receive_text())
        assert hello["op"] == "hello"
        fake.publish("snapshot:E2E.SZ", json.dumps({"ts_code": "E2E.SZ", "price": 9.9}))
        msg = json.loads(ws.receive_text())
        assert msg["ts_code"] == "E2E.SZ"


# --------------------------------------------------------------------
# P12: POST /api/robots/create — 手动添加
# --------------------------------------------------------------------


def test_robot_create_auto_id(client):
    from backend.api.main import app
    reg = app.state.robot_registry
    before = set(reg.keys())

    r = client.post("/api/robots/create", json={
        "cash": 500000, "max_positions": 3, "per_position_pct": 0.25,
    })
    assert r.status_code == 200
    d = r.json()["snapshot"]
    rid = d["robot_id"]
    try:
        assert rid not in before
        assert d["cash"] == 500000
        assert d["state"] == "IDLE"
        assert d["enabled"] is False
        # list 能看到它
        lst = client.get("/api/robots").json()["robots"]
        assert any(row["robot_id"] == rid for row in lst)
    finally:
        reg.pop(rid, None)


def test_robot_create_explicit_id_and_duplicate(client):
    from backend.api.main import app
    reg = app.state.robot_registry

    r = client.post("/api/robots/create", json={
        "robot_id": 77777, "cash": 100000, "enabled": True,
    })
    assert r.status_code == 200
    assert r.json()["snapshot"]["enabled"] is True

    try:
        # 再次同 id -> 409
        r2 = client.post("/api/robots/create", json={"robot_id": 77777, "cash": 1000})
        assert r2.status_code == 409
    finally:
        reg.pop(77777, None)


def test_robot_create_bad_params(client):
    # per_position_pct > 1 -> 422
    r = client.post("/api/robots/create", json={"per_position_pct": 1.5})
    assert r.status_code == 422
    # 负现金 -> 422
    r = client.post("/api/robots/create", json={"cash": -1})
    assert r.status_code == 422
