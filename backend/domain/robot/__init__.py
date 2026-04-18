"""机器人领域层.

模块:
  state    机器人状态机 (RobotState 枚举 + 合法转换)
  ledger   资金台账 (LedgerEntry + apply_*)
  risk     风控 (RiskRule / RiskEvaluator)
  robot    机器人快照 (RobotSnapshot) + 行为(buy / sell)
"""
from .state import RobotState, RobotStateMachine, IllegalStateTransition
from .ledger import (
    LedgerEntry,
    LedgerType,
    apply_deposit,
    apply_buy,
    apply_sell,
    apply_commission,
)
from .risk import (
    RiskRule,
    RiskEvaluator,
    RiskDecision,
    RiskViolation,
)
from .robot import RobotSnapshot, PositionState
from .events import (
    TradeEvent, EventType, EventLevel, TriggerType,
    SelectionLog, TradePhase, SelectionExecStatus,
    AuditLog, AuditAction,
    write_trade_event, write_selection_log, write_audit_log,
    make_signal_event, make_risk_alert_event, make_state_change_audit,
)

__all__ = [
    "RobotState", "RobotStateMachine", "IllegalStateTransition",
    "LedgerEntry", "LedgerType",
    "apply_deposit", "apply_buy", "apply_sell", "apply_commission",
    "RiskRule", "RiskEvaluator", "RiskDecision", "RiskViolation",
    "RobotSnapshot", "PositionState",
    "TradeEvent", "EventType", "EventLevel", "TriggerType",
    "SelectionLog", "TradePhase", "SelectionExecStatus",
    "AuditLog", "AuditAction",
    "write_trade_event", "write_selection_log", "write_audit_log",
    "make_signal_event", "make_risk_alert_event", "make_state_change_audit",
]
