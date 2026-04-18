"""domain.robot 状态机 + ledger + risk + 行为测试 (不连 DB)."""
from __future__ import annotations

from datetime import date

import pytest

from backend.domain.robot import (
    IllegalStateTransition,
    LedgerType,
    PositionState,
    RiskDecision,
    RiskEvaluator,
    RiskRule,
    RiskViolation,
    RobotSnapshot,
    RobotState,
    RobotStateMachine,
    apply_buy,
    apply_commission,
    apply_deposit,
    apply_sell,
)
from backend.domain.robot.risk import BuyRequest, RiskRuleKind


TODAY = date(2024, 10, 15)


# ====================================================================
# 状态机
# ====================================================================


class TestStateMachine:
    def test_default_idle(self):
        sm = RobotStateMachine()
        assert sm.state == RobotState.IDLE

    def test_start_from_idle(self):
        sm = RobotStateMachine().start()
        assert sm.state == RobotState.RUNNING

    def test_pause_resume(self):
        sm = RobotStateMachine(RobotState.RUNNING).pause().resume()
        assert sm.state == RobotState.RUNNING

    def test_cool_down_then_idle(self):
        sm = RobotStateMachine(RobotState.RUNNING).cool_down()
        assert sm.state == RobotState.COOL_DOWN
        sm2 = sm.reset_to_idle()
        assert sm2.state == RobotState.IDLE

    def test_illegal_transition_raises(self):
        sm = RobotStateMachine(RobotState.IDLE)
        with pytest.raises(IllegalStateTransition):
            sm.pause()  # IDLE -> PAUSED 不允许
        with pytest.raises(IllegalStateTransition):
            RobotStateMachine(RobotState.STOPPED).start()
        with pytest.raises(IllegalStateTransition):
            RobotStateMachine(RobotState.COOL_DOWN).start()

    def test_transition_to_same_state_noop(self):
        sm = RobotStateMachine(RobotState.RUNNING)
        assert sm.transition(RobotState.RUNNING).state == RobotState.RUNNING

    def test_string_state_input(self):
        sm = RobotStateMachine('IDLE')
        assert sm.state == RobotState.IDLE
        assert sm.can_transit('RUNNING') is True

    def test_error_path(self):
        sm = RobotStateMachine(RobotState.RUNNING).error()
        assert sm.state == RobotState.ERROR
        assert sm.reset_to_idle().state == RobotState.IDLE


# ====================================================================
# Ledger
# ====================================================================


class TestLedger:
    def test_deposit(self):
        bal, e = apply_deposit(1, 1000.0, 500, TODAY)
        assert bal == 1500.0
        assert e.direction == 'IN'
        assert e.ledger_type == LedgerType.DEPOSIT
        assert e.balance_before == 1000.0
        assert e.balance_after == 1500.0

    def test_deposit_negative_raises(self):
        with pytest.raises(ValueError):
            apply_deposit(1, 1000.0, -1, TODAY)

    def test_buy_deducts_cash(self):
        bal, e = apply_buy(1, 10_000.0, 3000, TODAY, ts_code='A.SZ')
        assert bal == 7000.0
        assert e.direction == 'OUT'
        assert e.ledger_type == LedgerType.BUY_FREEZE
        assert e.ts_code == 'A.SZ'

    def test_buy_insufficient_cash(self):
        with pytest.raises(ValueError):
            apply_buy(1, 100.0, 1000, TODAY, ts_code='A.SZ')

    def test_sell_adds_cash(self):
        bal, e = apply_sell(1, 500.0, 2000, TODAY, ts_code='A.SZ')
        assert bal == 2500.0
        assert e.direction == 'IN'
        assert e.ledger_type == LedgerType.SELL_RETURN

    def test_commission(self):
        bal, e = apply_commission(1, 500.0, 5.5, TODAY)
        assert bal == pytest.approx(494.5)
        assert e.direction == 'OUT'
        assert e.ledger_type == LedgerType.COMMISSION

    def test_to_db_row(self):
        _, e = apply_deposit(42, 0.0, 100, TODAY, description='init')
        row = e.to_db_row()
        assert row['robot_id'] == 42
        assert row['ledger_type'] == 'DEPOSIT'
        assert row['direction'] == 'IN'
        assert row['amount'] == 100.0
        assert row['biz_date'] == TODAY
        assert row['description'] == 'init'


# ====================================================================
# Risk
# ====================================================================


def _req(**kw):
    defaults = dict(
        ts_code='A.SZ', planned_amount=100_000,
        score=80, robot_state=RobotState.RUNNING,
        cash=500_000, total_assets=1_000_000,
        current_positions=0, daily_pnl_pct=0.0,
        already_holding=False,
    )
    defaults.update(kw)
    return BuyRequest(**defaults)


class TestRiskRules:
    def test_cool_down_blocks(self):
        ev = RiskEvaluator([RiskRule(RiskRuleKind.COOL_DOWN)])
        d = ev.evaluate_buy(_req(robot_state=RobotState.COOL_DOWN))
        assert d.blocked
        assert d.violations[0].rule == RiskRuleKind.COOL_DOWN

    def test_paused_blocks(self):
        ev = RiskEvaluator([RiskRule(RiskRuleKind.COOL_DOWN)])
        d = ev.evaluate_buy(_req(robot_state=RobotState.PAUSED))
        assert d.blocked

    def test_running_passes_cool_down(self):
        ev = RiskEvaluator([RiskRule(RiskRuleKind.COOL_DOWN)])
        d = ev.evaluate_buy(_req(robot_state=RobotState.RUNNING))
        assert d.allow

    def test_max_positions_blocks(self):
        ev = RiskEvaluator([RiskRule(RiskRuleKind.MAX_POSITIONS, 5)])
        d = ev.evaluate_buy(_req(current_positions=5))
        assert d.blocked

    def test_max_positions_allows_add_to_existing(self):
        ev = RiskEvaluator([RiskRule(RiskRuleKind.MAX_POSITIONS, 5)])
        d = ev.evaluate_buy(_req(current_positions=5, already_holding=True))
        assert d.allow

    def test_per_position_pct_caps(self):
        ev = RiskEvaluator([RiskRule(RiskRuleKind.PER_POSITION_PCT, 0.2)])
        d = ev.evaluate_buy(_req(planned_amount=500_000, total_assets=1_000_000))
        assert d.allow  # CAP 不 block
        assert d.adjusted_amount == 200_000
        assert d.violations[0].severity == 'CAP'

    def test_per_position_pct_cap_clamped_by_cash(self):
        ev = RiskEvaluator([RiskRule(RiskRuleKind.PER_POSITION_PCT, 0.2)])
        d = ev.evaluate_buy(_req(
            planned_amount=500_000, total_assets=1_000_000, cash=50_000,
        ))
        assert d.adjusted_amount == 50_000  # 被 cash 压低

    def test_daily_loss_limit_blocks(self):
        ev = RiskEvaluator([RiskRule(RiskRuleKind.DAILY_LOSS_LIMIT, -0.05)])
        d = ev.evaluate_buy(_req(daily_pnl_pct=-0.06))
        assert d.blocked

    def test_score_threshold_blocks(self):
        ev = RiskEvaluator([RiskRule(RiskRuleKind.SCORE_THRESHOLD, 70)])
        d = ev.evaluate_buy(_req(score=65))
        assert d.blocked

    def test_from_robot_config(self):
        ev = RiskEvaluator.from_robot_config(
            {'daily_loss_limit': -0.05, 'score_threshold': 70},
            max_positions=3, per_position_pct=0.15,
        )
        kinds = {r.kind for r in ev.rules}
        assert kinds == {
            RiskRuleKind.COOL_DOWN, RiskRuleKind.MAX_POSITIONS,
            RiskRuleKind.PER_POSITION_PCT, RiskRuleKind.DAILY_LOSS_LIMIT,
            RiskRuleKind.SCORE_THRESHOLD,
        }


# ====================================================================
# RobotSnapshot plan_buy / plan_sell
# ====================================================================


def _snap(**kw):
    defaults = dict(
        robot_id=1, state=RobotState.RUNNING,
        cash=1_000_000.0, total_assets=1_000_000.0,
        positions=(),
        max_positions=5, per_position_pct=0.20,
        risk_config={'daily_loss_limit': -0.05, 'score_threshold': 60},
    )
    defaults.update(kw)
    return RobotSnapshot(**defaults)


class TestRobotSnapshot:
    def test_plan_buy_happy(self):
        s = _snap()
        r = s.plan_buy('600519.SH', 200_000, price=100.0, score=80,
                       biz_date=TODAY)
        assert r.ok
        # 100 元 * 100 股 = 10000 元/手, 20w 可买 20 手 = 2000 股
        assert r.filled_shares == 2000
        assert r.filled_amount == 200_000
        assert r.snapshot is not None
        # cash = 100w - 20w - commission
        assert r.snapshot.cash < 800_000
        assert r.snapshot.position_count() == 1
        assert len(r.entries) == 2  # BUY_FREEZE + COMMISSION

    def test_plan_buy_blocked_by_state(self):
        s = _snap(state=RobotState.PAUSED)
        r = s.plan_buy('A.SZ', 10_000, price=10.0, score=80, biz_date=TODAY)
        assert not r.ok
        assert 'PAUSED' in r.reason

    def test_plan_buy_blocked_by_score(self):
        s = _snap()
        r = s.plan_buy('A.SZ', 10_000, price=10.0, score=50, biz_date=TODAY)
        assert not r.ok

    def test_plan_buy_cap_by_per_position_pct(self):
        s = _snap(total_assets=1_000_000, cash=1_000_000)
        # 计划 50w, per_position_pct=20%, 应被 cap 到 20w
        r = s.plan_buy('A.SZ', 500_000, price=10.0, score=80, biz_date=TODAY)
        assert r.ok
        assert r.filled_amount == 200_000

    def test_plan_buy_blocked_by_daily_loss(self):
        s = _snap(daily_pnl_pct=-0.07)
        r = s.plan_buy('A.SZ', 10_000, price=10.0, score=80, biz_date=TODAY)
        assert not r.ok

    def test_plan_buy_blocked_by_max_positions(self):
        pos = tuple(
            PositionState(ts_code=f'X{i}.SZ', shares=100, cost_price=1.0,
                          open_date=TODAY, current_price=1.0)
            for i in range(5)
        )
        s = _snap(positions=pos).recompute_total_assets()
        r = s.plan_buy('NEW.SZ', 10_000, price=10.0, score=80, biz_date=TODAY)
        assert not r.ok

    def test_plan_sell_happy(self):
        pos = PositionState('A.SZ', shares=1000, cost_price=10.0,
                            open_date=TODAY, current_price=11.0)
        s = _snap(cash=0, positions=(pos,)).recompute_total_assets()
        r = s.plan_sell('A.SZ', price=12.0, biz_date=TODAY)
        assert r.ok
        assert r.filled_shares == 1000
        assert r.filled_amount == 12_000
        assert r.snapshot.position_count() == 0
        assert len(r.entries) == 3  # SELL_RETURN + COMMISSION + STAMP

    def test_plan_sell_partial(self):
        pos = PositionState('A.SZ', shares=1000, cost_price=10.0,
                            open_date=TODAY, current_price=11.0)
        s = _snap(cash=0, positions=(pos,)).recompute_total_assets()
        r = s.plan_sell('A.SZ', price=12.0, biz_date=TODAY, shares=400)
        assert r.ok
        assert r.filled_shares == 400
        assert r.snapshot.holding('A.SZ').shares == 600

    def test_plan_sell_nothing_to_sell(self):
        s = _snap(cash=0, positions=())
        r = s.plan_sell('A.SZ', price=12.0, biz_date=TODAY)
        assert not r.ok

    def test_add_position_weighted_cost(self):
        s = _snap()
        # 100_000 / (10*100 元/手) = 100 手 = 10000 股 @ 10.0
        r1 = s.plan_buy('A.SZ', 100_000, price=10.0, score=80, biz_date=TODAY)
        s2 = r1.snapshot
        assert r1.filled_shares == 10_000
        # 再买 100_000 @ 12.0 -> 100_000 // 1200 = 83手 = 8300 股
        r2 = s2.plan_buy('A.SZ', 100_000, price=12.0, score=80, biz_date=TODAY)
        assert r2.ok
        pos = r2.snapshot.holding('A.SZ')
        assert pos is not None
        assert pos.shares == 18_300
        # 加权: (10000*10 + 8300*12) / 18300 = (100000+99600)/18300 ~= 10.9071
        assert abs(pos.cost_price - 10.9071) < 0.001

    def test_recompute_total_assets(self):
        pos = PositionState('A.SZ', 1000, 10.0, TODAY, current_price=12.0)
        s = _snap(cash=100_000, positions=(pos,), total_assets=0).recompute_total_assets()
        assert s.total_assets == 112_000  # 100000 + 12*1000

    def test_with_state(self):
        s = _snap(state=RobotState.RUNNING)
        s2 = s.with_state(RobotState.PAUSED)
        assert s2.state == RobotState.PAUSED
        with pytest.raises(IllegalStateTransition):
            s.with_state(RobotState.IDLE)  # RUNNING -> IDLE 非法
