"""trade_events / selection_logs / audit_logs 写入器.

设计:
- 数据类 TradeEvent / SelectionLog / AuditLog 封装要写入 DB 的一行
- write_* 函数接收 psycopg2 cursor, 执行单行 INSERT (批量由上层循环负责)
- 所有函数都幂等 / 不抛非预期异常: 失败时返回 None 并 logger.warning

本层故意不直接起 pg_conn, 让调用方决定事务边界.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------
# trade_events
# --------------------------------------------------------------------


class EventType(str, Enum):
    SIGNAL_GENERATED = 'SIGNAL_GENERATED'
    ORDER_PLACED = 'ORDER_PLACED'
    ORDER_FILLED = 'ORDER_FILLED'
    STOP_LOSS = 'STOP_LOSS'
    TAKE_PROFIT = 'TAKE_PROFIT'
    RISK_ALERT = 'RISK_ALERT'
    CIRCUIT_BREAK = 'CIRCUIT_BREAK'
    DAILY_SETTLE = 'DAILY_SETTLE'
    POSITION_EXPIRE = 'POSITION_EXPIRE'
    AHS_UPDATE = 'AHS_UPDATE'
    MANUAL_ACTION = 'MANUAL_ACTION'


class EventLevel(str, Enum):
    DEBUG = 'DEBUG'
    INFO = 'INFO'
    WARN = 'WARN'
    ERROR = 'ERROR'
    CRITICAL = 'CRITICAL'


class TriggerType(str, Enum):
    SIGNAL = 'SIGNAL'
    RISK = 'RISK'
    SCHEDULE = 'SCHEDULE'
    MANUAL = 'MANUAL'
    SYSTEM = 'SYSTEM'


@dataclass
class TradeEvent:
    robot_id: int
    event_type: EventType
    title: str
    message: str = ''
    event_level: EventLevel = EventLevel.INFO
    ts_code: Optional[str] = None
    trade_id: Optional[int] = None
    detail: dict[str, Any] = field(default_factory=dict)
    trigger_type: Optional[TriggerType] = None
    trigger_value: Optional[dict[str, Any]] = None
    trade_date: Optional[date] = None

    def to_db_row(self) -> dict:
        return {
            'robot_id': self.robot_id,
            'event_type': self.event_type.value,
            'event_level': self.event_level.value,
            'ts_code': self.ts_code,
            'trade_id': self.trade_id,
            'title': self.title,
            'message': self.message,
            'detail': json.dumps(self.detail, ensure_ascii=False),
            'trigger_type': self.trigger_type.value if self.trigger_type else None,
            'trigger_value': (
                json.dumps(self.trigger_value, ensure_ascii=False)
                if self.trigger_value is not None else None
            ),
            'trade_date': self.trade_date,
        }


def write_trade_event(cur, event: TradeEvent) -> Optional[int]:
    row = event.to_db_row()
    try:
        cur.execute(
            '''
            INSERT INTO trade_events
                (robot_id, event_type, event_level, ts_code, trade_id,
                 title, message, detail, trigger_type, trigger_value,
                 trade_date)
            VALUES (%(robot_id)s, %(event_type)s, %(event_level)s, %(ts_code)s,
                    %(trade_id)s, %(title)s, %(message)s,
                    %(detail)s::jsonb, %(trigger_type)s,
                    %(trigger_value)s::jsonb, %(trade_date)s)
            RETURNING id
            ''',
            row,
        )
        result = cur.fetchone()
        return int(result[0]) if result else None
    except Exception as e:  # noqa: BLE001
        logger.warning(f'[events.write_trade_event] 失败: {e}')
        return None


# --------------------------------------------------------------------
# selection_logs
# --------------------------------------------------------------------


class TradePhase(str, Enum):
    PHASE_A = 'PHASE_A'       # 盘后选股
    PHASE_B = 'PHASE_B'       # 盘中执行
    MANUAL = 'MANUAL'


class SelectionExecStatus(str, Enum):
    PENDING = 'PENDING'
    EXECUTED = 'EXECUTED'
    SKIPPED = 'SKIPPED'
    FAILED = 'FAILED'


@dataclass
class SelectionLog:
    trade_date: date
    phase: TradePhase
    robot_id: Optional[int] = None
    strategy_id: Optional[int] = None
    candidate_count: int = 0
    candidates: list[dict] = field(default_factory=list)
    selected_count: int = 0
    selected_stocks: list[dict] = field(default_factory=list)
    filter_steps: list[dict] = field(default_factory=list)
    lhb_daily_ids: list[int] = field(default_factory=list)
    market_sentiment: Optional[float] = None
    market_snapshot: Optional[dict] = None
    exec_status: SelectionExecStatus = SelectionExecStatus.PENDING
    exec_message: Optional[str] = None

    def to_db_row(self) -> dict:
        return {
            'robot_id': self.robot_id,
            'strategy_id': self.strategy_id,
            'trade_date': self.trade_date,
            'phase': self.phase.value,
            'candidate_count': self.candidate_count,
            'candidates': json.dumps(self.candidates, ensure_ascii=False),
            'selected_count': self.selected_count,
            'selected_stocks': json.dumps(self.selected_stocks, ensure_ascii=False),
            'filter_steps': json.dumps(self.filter_steps, ensure_ascii=False),
            'lhb_daily_ids': self.lhb_daily_ids or None,
            'market_sentiment': self.market_sentiment,
            'market_snapshot': (
                json.dumps(self.market_snapshot, ensure_ascii=False)
                if self.market_snapshot is not None else None
            ),
            'exec_status': self.exec_status.value,
            'exec_message': self.exec_message,
        }


def write_selection_log(cur, log: SelectionLog) -> Optional[int]:
    row = log.to_db_row()
    try:
        cur.execute(
            '''
            INSERT INTO selection_logs
                (robot_id, strategy_id, trade_date, phase,
                 candidate_count, candidates, selected_count,
                 selected_stocks, filter_steps, lhb_daily_ids,
                 market_sentiment, market_snapshot,
                 exec_status, exec_message)
            VALUES (%(robot_id)s, %(strategy_id)s, %(trade_date)s, %(phase)s,
                    %(candidate_count)s, %(candidates)s::jsonb,
                    %(selected_count)s, %(selected_stocks)s::jsonb,
                    %(filter_steps)s::jsonb, %(lhb_daily_ids)s,
                    %(market_sentiment)s, %(market_snapshot)s::jsonb,
                    %(exec_status)s, %(exec_message)s)
            RETURNING id
            ''',
            row,
        )
        result = cur.fetchone()
        return int(result[0]) if result else None
    except Exception as e:  # noqa: BLE001
        logger.warning(f'[events.write_selection_log] 失败: {e}')
        return None


# --------------------------------------------------------------------
# audit_logs
# --------------------------------------------------------------------


class AuditAction(str, Enum):
    CREATE = 'CREATE'
    UPDATE = 'UPDATE'
    DELETE = 'DELETE'
    START = 'START'
    STOP = 'STOP'
    PAUSE = 'PAUSE'
    RESUME = 'RESUME'
    EXECUTE = 'EXECUTE'
    ERROR = 'ERROR'


@dataclass
class AuditLog:
    action: AuditAction
    entity_type: str
    entity_id: Optional[int] = None
    operator: str = 'SYSTEM'
    detail: Optional[dict] = None
    old_value: Optional[dict] = None
    new_value: Optional[dict] = None
    ip_address: Optional[str] = None

    def to_db_row(self) -> dict:
        return {
            'action': self.action.value,
            'entity_type': self.entity_type,
            'entity_id': self.entity_id,
            'operator': self.operator,
            'detail': (
                json.dumps(self.detail, ensure_ascii=False)
                if self.detail is not None else None
            ),
            'old_value': (
                json.dumps(self.old_value, ensure_ascii=False)
                if self.old_value is not None else None
            ),
            'new_value': (
                json.dumps(self.new_value, ensure_ascii=False)
                if self.new_value is not None else None
            ),
            'ip_address': self.ip_address,
        }


def write_audit_log(cur, log: AuditLog) -> Optional[int]:
    row = log.to_db_row()
    try:
        cur.execute(
            '''
            INSERT INTO audit_logs
                (action, entity_type, entity_id, operator,
                 detail, old_value, new_value, ip_address)
            VALUES (%(action)s, %(entity_type)s, %(entity_id)s, %(operator)s,
                    %(detail)s::jsonb, %(old_value)s::jsonb,
                    %(new_value)s::jsonb, %(ip_address)s)
            RETURNING id
            ''',
            row,
        )
        result = cur.fetchone()
        return int(result[0]) if result else None
    except Exception as e:  # noqa: BLE001
        logger.warning(f'[events.write_audit_log] 失败: {e}')
        return None


# --------------------------------------------------------------------
# 辅助构造 (场景常用)
# --------------------------------------------------------------------


def make_signal_event(robot_id: int, ts_code: str, score: float,
                      trade_date: date, **detail) -> TradeEvent:
    return TradeEvent(
        robot_id=robot_id,
        event_type=EventType.SIGNAL_GENERATED,
        event_level=EventLevel.INFO,
        ts_code=ts_code,
        title=f'信号生成 {ts_code} score={score}',
        detail={'score': score, **detail},
        trigger_type=TriggerType.SIGNAL,
        trade_date=trade_date,
    )


def make_risk_alert_event(robot_id: int, violations: list,
                          trade_date: date,
                          ts_code: Optional[str] = None) -> TradeEvent:
    msgs = [v.message for v in violations] if violations else []
    return TradeEvent(
        robot_id=robot_id,
        event_type=EventType.RISK_ALERT,
        event_level=EventLevel.WARN,
        ts_code=ts_code,
        title=f'风控拦截 {ts_code or "-"}',
        message='; '.join(msgs),
        detail={'violations': [
            {'rule': getattr(v.rule, 'value', str(v.rule)),
             'severity': v.severity,
             'message': v.message}
            for v in (violations or [])
        ]},
        trigger_type=TriggerType.RISK,
        trade_date=trade_date,
    )


def make_state_change_audit(robot_id: int, old_state: str,
                            new_state: str,
                            operator: str = 'SYSTEM') -> AuditLog:
    action_map = {
        ('IDLE', 'RUNNING'): AuditAction.START,
        ('RUNNING', 'PAUSED'): AuditAction.PAUSE,
        ('PAUSED', 'RUNNING'): AuditAction.RESUME,
    }
    action = action_map.get((old_state, new_state), AuditAction.UPDATE)
    return AuditLog(
        action=action,
        entity_type='robot',
        entity_id=robot_id,
        operator=operator,
        old_value={'status': old_state},
        new_value={'status': new_state},
    )
