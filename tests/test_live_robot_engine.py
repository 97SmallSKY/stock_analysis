"""tests/test_live_robot_engine.py — W9-1

覆盖:
  LiveRobotEngine
    pause/resume: 幂等; IDLE->RUNNING; PAUSED->RUNNING
    tick():
      - state != RUNNING -> skipped_reason
      - 止盈 10% 自动卖出 + 修改 snapshot + 产生 LedgerEntry
      - 止损 -7% 自动卖出
      - 最大持仓 5d 触发兜底卖
      - 买入: score 高者优先, 跳过已持仓, 受限 max_buys_per_tick
      - Phase A: 从 candidates 挑 max_positions-已持仓 个建仓
      - Phase B: 只卖不买 (max_buys_per_tick=0)
  score_from_zt_pool: 连板数 / 开板次数 映射到 score, 上下界
  /api/robots/{id}/{pause,resume,status,list}
"""
from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta

import pytest

from backend.domain.robot.live_engine import (
    Candidate, LiveRobotEngine as _RawLiveRobotEngine,
    TickDecision, default_sell_policy,
    score_from_zt_pool,
)

# W12-1: LiveRobotEngine 默认 enabled=False. 既有业务测试都假设会跑,
# 这里包一层让未显式 enabled 的创建默认 True, 单独的灰度用例再显式传 False.
def LiveRobotEngine(*args, **kwargs):
    kwargs.setdefault("enabled", True)
    return _RawLiveRobotEngine(*args, **kwargs)
from backend.domain.robot.robot import PositionState, RobotSnapshot
from backend.domain.robot.state import RobotState


# --------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------


def make_snap(*, state=RobotState.RUNNING, cash=100_000.0, positions=()):
    snap = RobotSnapshot(
        robot_id=1, state=state,
        cash=cash, total_assets=cash + sum(p.market_value for p in positions),
        positions=tuple(positions), max_positions=3, per_position_pct=0.3,
    )
    return snap


def pos(ts_code="A.SZ", shares=100, cost=10.0, days_ago=0, current=None):
    return PositionState(
        ts_code=ts_code, shares=shares, cost_price=cost,
        open_date=date.today() - timedelta(days=days_ago),
        current_price=current or cost,
    )


# --------------------------------------------------------------------
# pause / resume
# --------------------------------------------------------------------


class TestEnableDisable:
    def test_default_disabled_tick_skipped(self):
        # 用原始类, 不自动 enable
        eng = _RawLiveRobotEngine(make_snap(state=RobotState.RUNNING))
        assert eng.enabled is False
        r = eng.tick({})
        assert r.skipped_reason == "disabled"

    def test_enable_then_tick_runs(self):
        eng = _RawLiveRobotEngine(make_snap(state=RobotState.RUNNING))
        eng.enable()
        assert eng.enabled is True
        r = eng.tick({})
        # RUNNING + enabled + 无订阅/候选: 无决策但不算 disabled
        assert r.skipped_reason is None

    def test_disable_roundtrip(self):
        eng = _RawLiveRobotEngine(make_snap(), enabled=True)
        eng.disable()
        assert eng.enabled is False
        assert eng.tick({}).skipped_reason == "disabled"

    def test_phase_a_disabled(self):
        eng = _RawLiveRobotEngine(make_snap(), enabled=False)
        r = eng.run_phase_a([Candidate("A", 80)], {"A": 10})
        assert r.skipped_reason == "disabled"


class TestPauseResume:
    def test_pause_running_to_paused(self):
        eng = LiveRobotEngine(make_snap())
        s = eng.pause()
        assert s.state == RobotState.PAUSED

    def test_pause_idempotent(self):
        eng = LiveRobotEngine(make_snap(state=RobotState.PAUSED))
        s = eng.pause()
        assert s.state == RobotState.PAUSED

    def test_resume_paused_to_running(self):
        eng = LiveRobotEngine(make_snap(state=RobotState.PAUSED))
        s = eng.resume()
        assert s.state == RobotState.RUNNING

    def test_resume_idle_starts(self):
        eng = LiveRobotEngine(make_snap(state=RobotState.IDLE))
        s = eng.resume()
        assert s.state == RobotState.RUNNING

    def test_resume_running_idempotent(self):
        eng = LiveRobotEngine(make_snap(state=RobotState.RUNNING))
        s = eng.resume()
        assert s.state == RobotState.RUNNING


# --------------------------------------------------------------------
# sell policy 边界
# --------------------------------------------------------------------


class TestDefaultSellPolicy:
    def test_take_profit_10pct(self):
        p = pos(cost=10)
        snap = make_snap(positions=(p,))
        r = default_sell_policy(snap, 11.0, p, now=date.today())
        assert r == "TP_10%"

    def test_stop_loss_minus_7pct(self):
        p = pos(cost=10)
        snap = make_snap(positions=(p,))
        r = default_sell_policy(snap, 9.29, p, now=date.today())
        assert r == "SL_-7%"

    def test_max_hold_5d(self):
        p = pos(cost=10, days_ago=6)
        snap = make_snap(positions=(p,))
        r = default_sell_policy(snap, 10.2, p, now=date.today())
        assert r == "MAX_HOLD_5D"

    def test_none_when_no_trigger(self):
        p = pos(cost=10, days_ago=1)
        snap = make_snap(positions=(p,))
        r = default_sell_policy(snap, 10.2, p, now=date.today())
        assert r is None


# --------------------------------------------------------------------
# tick()
# --------------------------------------------------------------------


class TestTick:
    def test_skipped_when_not_running(self):
        eng = LiveRobotEngine(make_snap(state=RobotState.PAUSED))
        r = eng.tick({})
        assert r.skipped_reason == "state=PAUSED"
        assert r.decisions == []

    def test_take_profit_triggers_sell(self):
        p = pos("A.SZ", 100, 10.0)
        snap = make_snap(positions=(p,))
        eng = LiveRobotEngine(snap)
        r = eng.tick({"A.SZ": {"price": 11.5}})  # +15%
        assert len(r.decisions) == 1
        d = r.decisions[0]
        assert d.action == "SELL"
        assert d.ts_code == "A.SZ"
        assert d.reason == "TP_10%"
        assert d.entries  # LedgerEntry 存在
        # snapshot 持仓清空
        assert r.snapshot.position_count() == 0

    def test_buy_picks_highest_score_and_respects_budget(self):
        snap = make_snap(cash=100_000.0)
        eng = LiveRobotEngine(snap, max_buys_per_tick=1)
        cands = [
            Candidate("X.SZ", score=70),
            Candidate("Y.SZ", score=90),  # 应优先
            Candidate("Z.SZ", score=80),
        ]
        ticks = {
            "X.SZ": {"price": 10},
            "Y.SZ": {"price": 10},
            "Z.SZ": {"price": 10},
        }
        r = eng.tick(ticks, candidates=cands)
        assert len(r.decisions) == 1
        assert r.decisions[0].ts_code == "Y.SZ"
        assert r.decisions[0].action == "BUY"

    def test_tick_ignores_already_holding(self):
        p = pos("A.SZ")
        snap = make_snap(positions=(p,))
        eng = LiveRobotEngine(snap)
        r = eng.tick(
            {"A.SZ": {"price": 10.1}},   # 不触发卖
            candidates=[Candidate("A.SZ", score=99)],  # 已持有忽略
        )
        assert [d for d in r.decisions if d.action == "BUY"] == []

    def test_tick_nan_price_ignored(self):
        snap = make_snap()
        eng = LiveRobotEngine(snap)
        r = eng.tick(
            {"X.SZ": {"price": float("nan")}},
            candidates=[Candidate("X.SZ", score=80)],
        )
        assert r.decisions == []


# --------------------------------------------------------------------
# Phase A / B
# --------------------------------------------------------------------


class TestPhases:
    def test_phase_a_fills_up_to_max_positions(self):
        snap = make_snap(cash=300_000.0)  # max_positions=3 per_position_pct=0.3
        eng = LiveRobotEngine(snap)
        cands = [
            Candidate("A", score=80),
            Candidate("B", score=70),
            Candidate("C", score=60),
            Candidate("D", score=50),  # 多出, 不应建仓
        ]
        prices = {"A": 10, "B": 10, "C": 10, "D": 10}
        r = eng.run_phase_a(cands, prices)
        codes = [d.ts_code for d in r.decisions]
        assert codes == ["A", "B", "C"]  # 按 score 排序
        assert r.snapshot.position_count() == 3

    def test_phase_a_respects_existing_positions(self):
        p = pos("A", 100, 10)
        snap = make_snap(cash=300_000.0, positions=(p,))
        eng = LiveRobotEngine(snap)
        cands = [Candidate("A", 99), Candidate("B", 70), Candidate("C", 60)]
        r = eng.run_phase_a(cands, {"A": 10, "B": 10, "C": 10})
        new_codes = {d.ts_code for d in r.decisions}
        assert "A" not in new_codes      # 已持
        assert new_codes == {"B", "C"}   # 只剩 2 个 slot

    def test_phase_b_only_sells(self):
        p = pos("A.SZ", 100, 10)
        snap = make_snap(positions=(p,))
        eng = LiveRobotEngine(snap)
        r = eng.run_phase_b(
            {"A.SZ": {"price": 11.2}},   # 触发 TP
        )
        assert [d.action for d in r.decisions] == ["SELL"]
        # 即使有 candidates 也不会买 (但 phase_b 不收 candidates 参数)
        assert eng.max_buys_per_tick == 1  # 恢复

    def test_phase_a_skips_when_stopped(self):
        snap = make_snap(state=RobotState.STOPPED)
        eng = LiveRobotEngine(snap)
        r = eng.run_phase_a([Candidate("A", 80)], {"A": 10})
        assert r.skipped_reason == "state=STOPPED"


# --------------------------------------------------------------------
# score_from_zt_pool
# --------------------------------------------------------------------


class TestScoreFromZtPool:
    def test_streak_boosts_score(self):
        out = score_from_zt_pool(["A", "B"], {"A": {"streak": 3}, "B": {"streak": 0}})
        a = next(c for c in out if c.ts_code == "A")
        b = next(c for c in out if c.ts_code == "B")
        assert a.score == 90.0
        assert b.score == 60.0

    def test_open_times_penalizes(self):
        out = score_from_zt_pool(["A"], {"A": {"streak": 2, "open_times": 3}})
        assert out[0].score == 74.0  # 60 + 20 - 6

    def test_score_clamped(self):
        out = score_from_zt_pool(["A"], {"A": {"streak": 100}})
        assert out[0].score == 100.0

    def test_empty_input(self):
        assert score_from_zt_pool([]) == []

    def test_missing_code_filtered(self):
        assert score_from_zt_pool([None, ""]) == []


# --------------------------------------------------------------------
# FastAPI /api/robots/*
# --------------------------------------------------------------------


@pytest.fixture
def api_client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.api.robots import router
    app = FastAPI()
    eng = LiveRobotEngine(make_snap(state=RobotState.RUNNING))
    app.state.robot_registry = {1: eng}
    app.include_router(router, prefix="/api/robots")
    return TestClient(app), eng


class TestRobotsApi:
    def test_pause_then_resume(self, api_client):
        client, eng = api_client
        r = client.post("/api/robots/1/pause")
        assert r.status_code == 200
        assert r.json()["snapshot"]["state"] == "PAUSED"
        # 幂等
        r = client.post("/api/robots/1/pause")
        assert r.json()["snapshot"]["state"] == "PAUSED"
        # resume
        r = client.post("/api/robots/1/resume")
        assert r.json()["snapshot"]["state"] == "RUNNING"

    def test_status(self, api_client):
        client, eng = api_client
        r = client.get("/api/robots/1/status")
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert body["snapshot"]["robot_id"] == 1

    def test_404(self, api_client):
        client, _ = api_client
        r = client.post("/api/robots/999/pause")
        assert r.status_code == 404

    def test_list_robots(self, api_client):
        client, eng = api_client
        r = client.get("/api/robots")
        assert r.status_code == 200
        rows = r.json()["robots"]
        assert len(rows) == 1 and rows[0]["robot_id"] == 1
        # W12-1 snapshot 字段带 enabled
        assert "enabled" in rows[0]

    # W12-1 灰度 API
    def test_enable_disable(self, api_client):
        client, eng = api_client
        # 初始 disabled (未调 enable)
        r = client.get("/api/robots/1/status")
        assert r.json()["snapshot"]["enabled"] is True    # helper 已默认 enabled
        # 先 disable
        r = client.post("/api/robots/1/disable")
        assert r.status_code == 200
        assert r.json()["snapshot"]["enabled"] is False
        assert eng.enabled is False
        # 再 enable
        r = client.post("/api/robots/1/enable")
        assert r.json()["snapshot"]["enabled"] is True
        assert eng.enabled is True
        # 幂等: 再 enable
        r = client.post("/api/robots/1/enable")
        assert r.json()["snapshot"]["enabled"] is True
