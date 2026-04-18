"""trade_events / selection_logs / audit_logs writer + 综合风控场景测试.

不连 DB: 使用 FakeCursor 记录 SQL + 参数, 断言 shape 合理.
"""
from __future__ import annotations

import json
from datetime import date

import pytest

from backend.domain.robot import (
    AuditAction,
    AuditLog,
    EventLevel,
    EventType,
    PositionState,
    RobotSnapshot,
    RobotState,
    SelectionExecStatus,
    SelectionLog,
    TradeEvent,
    TradePhase,
    TriggerType,
    make_risk_alert_event,
    make_signal_event,
    make_state_change_audit,
    write_audit_log,
    write_selection_log,
    write_trade_event,
)

TODAY = date(2024, 10, 15)


class FakeCursor:
    def __init__(self, return_id: int = 42):
        self.executed: list[tuple] = []
        self._return_id = return_id

    def execute(self, sql: str, params=None):
        self.executed.append((sql, params))

    def fetchone(self):
        return (self._return_id,)


# ====================================================================
# trade_events writer
# ====================================================================


class TestWriteTradeEvent:
    def test_basic(self):
        cur = FakeCursor(return_id=101)
        ev = TradeEvent(
            robot_id=1, event_type=EventType.SIGNAL_GENERATED,
            title='test', ts_code='A.SZ',
            detail={'score': 85}, trigger_type=TriggerType.SIGNAL,
            trade_date=TODAY,
        )
        out = write_trade_event(cur, ev)
        assert out == 101
        sql, params = cur.executed[0]
        assert 'INSERT INTO trade_events' in sql
        assert params['robot_id'] == 1
        assert params['event_type'] == 'SIGNAL_GENERATED'
        assert params['event_level'] == 'INFO'
        assert json.loads(params['detail']) == {'score': 85}
        assert params['trigger_type'] == 'SIGNAL'
        assert params['trade_date'] == TODAY

    def test_trigger_value_optional(self):
        cur = FakeCursor()
        ev = TradeEvent(robot_id=1, event_type=EventType.DAILY_SETTLE,
                        title='settle')
        write_trade_event(cur, ev)
        _, params = cur.executed[0]
        assert params['trigger_type'] is None
        assert params['trigger_value'] is None

    def test_returns_none_on_exception(self):
        class BadCur:
            def execute(self, *a, **kw):
                raise RuntimeError('db down')

            def fetchone(self):
                return None

        ev = TradeEvent(robot_id=1, event_type=EventType.RISK_ALERT,
                        title='t')
        assert write_trade_event(BadCur(), ev) is None

    def test_helper_make_signal_event(self):
        ev = make_signal_event(
            robot_id=7, ts_code='B.SZ', score=77, trade_date=TODAY,
            reason='lhb_strong',
        )
        assert ev.event_type == EventType.SIGNAL_GENERATED
        assert ev.detail['score'] == 77
        assert ev.detail['reason'] == 'lhb_strong'
        assert ev.trigger_type == TriggerType.SIGNAL
        assert ev.ts_code == 'B.SZ'

    def test_helper_make_risk_alert(self):
        from backend.domain.robot.risk import RiskRuleKind, RiskViolation

        vs = [
            RiskViolation(rule=RiskRuleKind.MAX_POSITIONS,
                          message='满仓', severity='BLOCK'),
            RiskViolation(rule=RiskRuleKind.SCORE_THRESHOLD,
                          message='评分低', severity='BLOCK'),
        ]
        ev = make_risk_alert_event(robot_id=1, violations=vs,
                                    trade_date=TODAY, ts_code='A.SZ')
        assert ev.event_type == EventType.RISK_ALERT
        assert ev.event_level == EventLevel.WARN
        assert '满仓' in ev.message
        assert len(ev.detail['violations']) == 2
        assert ev.detail['violations'][0]['rule'] == 'max_positions'


# ====================================================================
# selection_logs writer
# ====================================================================


class TestWriteSelectionLog:
    def test_basic(self):
        cur = FakeCursor(return_id=55)
        log = SelectionLog(
            trade_date=TODAY, phase=TradePhase.PHASE_A,
            robot_id=1, strategy_id=2,
            candidate_count=10,
            candidates=[{'ts_code': 'A.SZ', 'score': 80}],
            selected_count=1,
            selected_stocks=[{'ts_code': 'A.SZ', 'score': 80, 'reason': 'lhb'}],
            filter_steps=[{'step': 'score', 'before': 10, 'after': 3}],
            lhb_daily_ids=[101, 102],
            market_sentiment=65.5,
        )
        out = write_selection_log(cur, log)
        assert out == 55
        _, params = cur.executed[0]
        assert params['phase'] == 'PHASE_A'
        assert params['candidate_count'] == 10
        assert json.loads(params['candidates']) == [
            {'ts_code': 'A.SZ', 'score': 80}
        ]
        assert params['lhb_daily_ids'] == [101, 102]
        assert params['market_sentiment'] == 65.5
        assert params['exec_status'] == 'PENDING'

    def test_empty_lhb_ids_becomes_null(self):
        cur = FakeCursor()
        log = SelectionLog(trade_date=TODAY, phase=TradePhase.PHASE_A)
        write_selection_log(cur, log)
        _, params = cur.executed[0]
        assert params['lhb_daily_ids'] is None


# ====================================================================
# audit_logs writer
# ====================================================================


class TestWriteAuditLog:
    def test_basic(self):
        cur = FakeCursor(return_id=7)
        log = AuditLog(
            action=AuditAction.START, entity_type='robot', entity_id=1,
            operator='admin', detail={'reason': 'manual'},
        )
        out = write_audit_log(cur, log)
        assert out == 7
        _, params = cur.executed[0]
        assert params['action'] == 'START'
        assert params['entity_type'] == 'robot'
        assert params['entity_id'] == 1
        assert json.loads(params['detail']) == {'reason': 'manual'}

    def test_state_change_helper(self):
        log = make_state_change_audit(
            robot_id=9, old_state='RUNNING', new_state='PAUSED',
            operator='ops',
        )
        assert log.action == AuditAction.PAUSE
        assert log.entity_id == 9
        assert log.new_value == {'status': 'PAUSED'}
        assert log.operator == 'ops'

    def test_state_change_helper_unknown_pair(self):
        log = make_state_change_audit(1, 'RUNNING', 'COOL_DOWN')
        assert log.action == AuditAction.UPDATE  # 未映射 fallback


# ====================================================================
# 综合风控场景 (按 v2 §W5 交付标准: 超仓位 / 爆亏停止 / 手动暂停)
# ====================================================================


def _running_snap(**kw):
    defaults = dict(
        robot_id=1, state=RobotState.RUNNING,
        cash=1_000_000, total_assets=1_000_000,
        positions=(),
        max_positions=5, per_position_pct=0.20,
        risk_config={'daily_loss_limit': -0.05, 'score_threshold': 60},
    )
    defaults.update(kw)
    return RobotSnapshot(**defaults)


class TestRiskScenarios:
    def test_scenario_over_positions(self):
        """场景: 持仓已满, 新信号应被拒绝 + 产生 RISK_ALERT."""
        positions = tuple(
            PositionState(f'X{i}.SZ', 100, 1.0, TODAY, current_price=1.0)
            for i in range(5)
        )
        s = _running_snap(positions=positions).recompute_total_assets()
        r = s.plan_buy('NEW.SZ', 100_000, price=10.0, score=80, biz_date=TODAY)
        assert not r.ok
        assert any(v.rule.value == 'max_positions' for v in r.decision.violations)

        ev = make_risk_alert_event(
            robot_id=s.robot_id,
            violations=r.decision.violations,
            trade_date=TODAY, ts_code='NEW.SZ',
        )
        cur = FakeCursor()
        write_trade_event(cur, ev)
        _, params = cur.executed[0]
        assert params['event_type'] == 'RISK_ALERT'
        assert '已达最大持仓数' in params['message']

    def test_scenario_daily_loss_triggers_cool_down(self):
        """场景: 日亏 >= 限额 -> 买入被 block, 接着切 COOL_DOWN 状态 + audit."""
        s = _running_snap(daily_pnl_pct=-0.06)
        r = s.plan_buy('A.SZ', 10_000, price=10.0, score=80, biz_date=TODAY)
        assert not r.ok
        assert any(v.rule.value == 'daily_loss_limit' for v in r.decision.violations)

        # 上层收到 block 后, 把状态切 COOL_DOWN
        s2 = s.with_state(RobotState.COOL_DOWN)
        assert s2.state == RobotState.COOL_DOWN

        # 再买入, 这次应该是 cool_down 规则 block (而不是 daily_loss_limit)
        r2 = s2.plan_buy('B.SZ', 10_000, price=10.0, score=80,
                         biz_date=TODAY)
        assert not r2.ok
        # COOL_DOWN 规则在 rules 列表里排第一, 先命中
        assert r2.decision.violations[0].rule.value == 'cool_down'

        # audit: 记录状态切换
        audit = make_state_change_audit(
            s.robot_id, 'RUNNING', 'COOL_DOWN', operator='SYSTEM',
        )
        assert audit.action == AuditAction.UPDATE
        assert audit.new_value == {'status': 'COOL_DOWN'}

    def test_scenario_manual_pause(self):
        """场景: 用户手动 PAUSE, 之后任何买入都被 cool_down 规则拦截."""
        s = _running_snap()
        paused = s.with_state(RobotState.PAUSED)

        r = paused.plan_buy('A.SZ', 10_000, price=10.0, score=80,
                            biz_date=TODAY)
        assert not r.ok
        assert r.decision.violations[0].rule.value == 'cool_down'

        audit = make_state_change_audit(
            s.robot_id, 'RUNNING', 'PAUSED', operator='admin',
        )
        assert audit.action == AuditAction.PAUSE
        assert audit.operator == 'admin'

    def test_scenario_per_position_cap_still_allowed(self):
        """场景: planned_amount 超 per_position_pct, 应 CAP 而非 BLOCK, 走正常下单."""
        s = _running_snap(total_assets=1_000_000, cash=1_000_000)
        r = s.plan_buy('A.SZ', 500_000, price=10.0, score=80, biz_date=TODAY)
        assert r.ok
        # filled 应该是 20% * 总资产 = 200000
        assert r.filled_amount == 200_000
        assert r.decision.violations[0].severity == 'CAP'

    def test_scenario_low_score_rejected(self):
        s = _running_snap()
        r = s.plan_buy('A.SZ', 100_000, price=10.0, score=40, biz_date=TODAY)
        assert not r.ok
        assert any(v.rule.value == 'score_threshold' for v in r.decision.violations)
