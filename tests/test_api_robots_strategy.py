"""P16.3a/c: /api/robots/create 支持 strategy_name + GET /strategies."""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.robots import router


def _make_app() -> FastAPI:
    app = FastAPI()
    app.state.robot_registry = {}
    app.include_router(router, prefix="/api/robots")
    return app


def test_list_strategies_has_builtins():
    app = _make_app()
    client = TestClient(app)
    r = client.get("/api/robots/strategies")
    assert r.status_code == 200
    names = [s["name"] for s in r.json()["strategies"]]
    assert "lhb_hot_money" in names
    assert "lhb_institution" in names
    for s in r.json()["strategies"]:
        assert "default_params" in s
        assert "category" in s


def test_create_robot_with_strategy_binds_and_exposes_in_snapshot():
    app = _make_app()
    client = TestClient(app)
    r = client.post("/api/robots/create", json={
        "cash": 500_000,
        "strategy_name": "lhb_hot_money",
        "strategy_params": {"score_threshold": 85},
    })
    assert r.status_code == 200, r.text
    snap = r.json()["snapshot"]
    assert snap["strategy_name"] == "lhb_hot_money"
    # 参数合并: override 生效
    assert snap["strategy_params"]["score_threshold"] == 85
    # 未 override 的保持默认
    assert snap["strategy_params"]["max_candidates"] == 10


def test_create_robot_without_strategy_is_none():
    app = _make_app()
    client = TestClient(app)
    r = client.post("/api/robots/create", json={"cash": 100_000})
    assert r.status_code == 200
    snap = r.json()["snapshot"]
    assert snap["strategy_name"] is None
    assert snap["strategy_params"] == {}


def test_create_robot_unknown_strategy_returns_400():
    app = _make_app()
    client = TestClient(app)
    r = client.post("/api/robots/create", json={
        "cash": 100_000,
        "strategy_name": "does_not_exist_xxx",
    })
    assert r.status_code == 400
    assert "does_not_exist_xxx" in r.json()["detail"]


def test_engine_select_candidates_with_strategy_none_returns_empty():
    """P16.3b: 未绑定策略时 select_candidates 返回空列表."""
    from datetime import date
    from backend.domain.robot.live_engine import LiveRobotEngine
    from backend.domain.robot.robot import RobotSnapshot
    from backend.domain.strategy.base import MarketContext

    snap = RobotSnapshot(robot_id=1, cash=1e6, total_assets=1e6)
    eng = LiveRobotEngine(snap)
    ctx = MarketContext(as_of=date(2026, 1, 15))
    assert eng.select_candidates(ctx) == []


def test_engine_select_candidates_uses_strategy():
    """P16.3b: 绑定策略后 select_candidates 走 strategy.select_stocks."""
    from datetime import date
    from backend.domain.robot.live_engine import LiveRobotEngine
    from backend.domain.robot.robot import RobotSnapshot
    from backend.domain.strategy.base import (
        BaseStrategy, Candidate as SCandidate, MarketContext,
    )

    class DummyStrategy(BaseStrategy):
        name = "dummy"
        def select_stocks(self, ctx):
            return [SCandidate(ts_code="600000.SH", score=80, reason="stub")]
        def calc_buy_amount(self, c, cash, total):
            return 0.0
        def check_sell(self, position, ctx):
            return None

    snap = RobotSnapshot(robot_id=2, cash=1e6, total_assets=1e6)
    eng = LiveRobotEngine(snap, strategy=DummyStrategy())
    assert eng.strategy_name == "dummy"
    out = eng.select_candidates(MarketContext(as_of=date(2026, 1, 15)))
    assert len(out) == 1
    assert out[0].ts_code == "600000.SH"
    assert out[0].score == 80
    assert out[0].reason == "stub"
