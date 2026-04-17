"""
交易状态机模块

状态定义（严格按 STATE_MACHINE.md 实现，不得新增状态）：
- pending: 预买入（Phase A 选股后）
- open: 持仓中
- partial: 部分卖出
- closed: 已结束（终态）
- abandoned: 已放弃（终态）
- eliminated: 机器人淘汰时强制关闭（终态）

状态转换规则：
- pending → open: Phase B 确认买入
- pending → abandoned: 停牌/ST/偏离度/涨停跌停/资金不足
- open → partial: 部分卖出（止盈）
- open → closed: 全部卖出（止损/到期/连板开板/手动）
- open → abandoned: 手动放弃持仓
- open → eliminated: 机器人被淘汰
- partial → partial: 继续卖出
- partial → closed: 全部卖出
- partial → abandoned: 手动放弃剩余
- partial → eliminated: 机器人被淘汰
- closed → *: 终态不可逆转
- abandoned → *: 终态不可逆转
- eliminated → *: 终态不可逆转

参考：docs/lhb-quant-v2/STATE_MACHINE.md
"""
from datetime import datetime
from typing import Optional
from dataclasses import dataclass
from enum import Enum

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend import RobotTrade, TradeEvent


# ============================================================
# 状态转换定义
# ============================================================
TRADE_TRANSITIONS = {
    'pending':    ['open', 'abandoned'],
    'open':       ['partial', 'closed', 'abandoned', 'eliminated'],
    'partial':    ['partial', 'closed', 'abandoned', 'eliminated'],
    'closed':     [],     # 终态
    'abandoned':  [],     # 终态
    'eliminated': [],     # 终态
}

ROBOT_TRANSITIONS = {
    'paper_trading': ['running', 'paused', 'stopped'],
    'running':       ['paused', 'stopped', 'eliminated'],
    'paused':        ['running', 'stopped'],
    'stopped':       ['running'],           # 可恢复
    'eliminated':    ['stopped'],          # 只能先到 stopped
}


class TradeStatus(str, Enum):
    """交易状态枚举"""
    PENDING = 'pending'
    OPEN = 'open'
    PARTIAL = 'partial'
    CLOSED = 'closed'
    ABANDONED = 'abandoned'
    ELIMINATED = 'eliminated'


class RobotStatus(str, Enum):
    """机器人状态枚举"""
    PAPER_TRADING = 'paper_trading'
    RUNNING = 'running'
    PAUSED = 'paused'
    STOPPED = 'stopped'
    ELIMINATED = 'eliminated'


class SellReason(str, Enum):
    """卖出原因枚举"""
    STOP_LOSS = 'stop_loss'
    EXPIRE = 'expire'
    LIANBAN_OPEN = 'lianban_open'
    TAKE_PROFIT_1 = 'take_profit_1'
    TAKE_PROFIT_2 = 'take_profit_2'
    TAKE_PROFIT_3 = 'take_profit_3'
    MANUAL = 'manual'
    ELIMINATED = 'eliminated'


class AbandonReason(str, Enum):
    """放弃原因枚举"""
    SUSPENDED = 'suspended'          # 停牌
    ST_DETECTED = 'st_detected'      # ST检测
    PRICE_DEVIATION = 'price_deviation'  # 开盘价偏离度
    LIMIT_UP = 'limit_up'            # 涨停封板
    LIMIT_DOWN = 'limit_down'        # 跌停
    INSUFFICIENT_FUNDS = 'insufficient_funds'  # 资金不足
    MANUAL = 'manual'                # 手动放弃


# ============================================================
# 异常类定义
# ============================================================
class InvalidStateTransition(Exception):
    """
    非法状态转换异常
    
    Attributes:
        current: 当前状态
        target: 目标状态
        allowed: 允许的目标状态列表
    """
    
    def __init__(self, current: str, target: str, allowed: list):
        self.current = current
        self.target = target
        self.allowed = allowed
        super().__init__(
            f"非法状态转换: {current} → {target}，"
            f"允许的目标: {allowed}"
        )
    
    def to_dict(self) -> dict:
        """转换为字典（用于 API 响应）"""
        return {
            'error': 'invalid_state_transition',
            'current': self.current,
            'target': self.target,
            'allowed': self.allowed,
            'message': str(self)
        }


class TerminalStateError(Exception):
    """
    终态操作异常
    
    当尝试对终态进行非法操作时抛出
    """
    
    def __init__(self, status: str, operation: str):
        self.status = status
        self.operation = operation
        super().__init__(
            f"终态 {status} 不可执行操作: {operation}"
        )


# ============================================================
# 验证函数
# ============================================================
def validate_transition(
    current: str,
    target: str,
    transitions: dict = TRADE_TRANSITIONS
) -> bool:
    """
    验证状态转换是否合法
    
    Args:
        current: 当前状态
        target: 目标状态
        transitions: 状态转换表
    
    Returns:
        True 表示转换合法
    
    Raises:
        InvalidStateTransition: 转换非法时抛出
    """
    # 自转换允许（如 partial → partial）
    if current == target:
        return True
    
    allowed = transitions.get(current, [])
    
    if target not in allowed:
        raise InvalidStateTransition(current, target, allowed)
    
    return True


def is_terminal_status(status: str, transitions: dict = TRADE_TRANSITIONS) -> bool:
    """
    判断是否为终态
    
    Args:
        status: 状态
        transitions: 状态转换表
    
    Returns:
        True 表示终态
    """
    return len(transitions.get(status, [])) == 0


def get_allowed_transitions(current: str, transitions: dict = TRADE_TRANSITIONS) -> list:
    """
    获取允许的转换目标状态列表
    
    Args:
        current: 当前状态
        transitions: 状态转换表
    
    Returns:
        允许的目标状态列表
    """
    return transitions.get(current, [])


# ============================================================
# 状态转换函数
# ============================================================
@dataclass
class TransitionResult:
    """状态转换结果"""
    success: bool
    old_status: str
    new_status: str
    event_id: Optional[int] = None
    error: Optional[str] = None


async def transition_trade_status(
    db: AsyncSession,
    trade_id: int,
    target_status: str,
    event_type: str,
    event_data: dict,
    reason: Optional[str] = None,
    validate_only: bool = False
) -> TransitionResult:
    """
    交易状态转换（含事件记录）
    
    Args:
        db: 数据库会话
        trade_id: 交易ID
        target_status: 目标状态
        event_type: 事件类型
        event_data: 事件数据
        reason: 原因（可选，用于日志）
        validate_only: 仅验证不执行（用于预检查）
    
    Returns:
        TransitionResult: 转换结果
    """
    # 查询交易
    result = await db.execute(
        select(RobotTrade).where(RobotTrade.id == trade_id)
    )
    trade = result.scalar_one_or_none()
    
    if trade is None:
        return TransitionResult(
            success=False,
            old_status='',
            new_status='',
            error=f"交易不存在: {trade_id}"
        )
    
    old_status = trade.status
    
    # 验证转换
    try:
        validate_transition(old_status, target_status, TRADE_TRANSITIONS)
    except InvalidStateTransition as e:
        logger.warning(
            f"[StateMachine] 非法转换: trade_id={trade_id}, "
            f"{old_status} → {target_status}"
        )
        return TransitionResult(
            success=False,
            old_status=old_status,
            new_status=target_status,
            error=str(e)
        )
    
    if validate_only:
        return TransitionResult(
            success=True,
            old_status=old_status,
            new_status=target_status,
            error=None
        )
    
    # 执行转换
    trade.status = target_status
    trade.updated_at = datetime.now()
    
    # 更新原因字段
    if reason:
        trade.sell_reason = reason
    
    # 记录事件
    event = TradeEvent(
        trade_id=trade_id,
        event_type=event_type,
        event_time=datetime.now(),
        event_data={
            **event_data,
            'old_status': old_status,
            'new_status': target_status,
            'reason': reason,
        }
    )
    
    db.add(event)
    await db.flush()
    
    logger.info(
        f"[StateMachine] 状态转换成功: trade_id={trade_id}, "
        f"{old_status} → {target_status}, event={event_type}"
    )
    
    return TransitionResult(
        success=True,
        old_status=old_status,
        new_status=target_status,
        event_id=event.id
    )


async def transition_robot_status(
    db: AsyncSession,
    robot_id: int,
    target_status: str,
    event_data: Optional[dict] = None
) -> TransitionResult:
    """
    机器人状态转换
    
    Args:
        db: 数据库会话
        robot_id: 机器人ID
        target_status: 目标状态
        event_data: 事件数据（可选）
    
    Returns:
        TransitionResult: 转换结果
    """
    from backend import Robot
    from backend import AuditLog
    
    # 查询机器人
    result = await db.execute(
        select(Robot).where(Robot.id == robot_id)
    )
    robot = result.scalar_one_or_none()
    
    if robot is None:
        return TransitionResult(
            success=False,
            old_status='',
            new_status='',
            error=f"机器人不存在: {robot_id}"
        )
    
    old_status = robot.status
    
    # 验证转换
    try:
        validate_transition(old_status, target_status, ROBOT_TRANSITIONS)
    except InvalidStateTransition as e:
        logger.warning(
            f"[StateMachine] 非法转换: robot_id={robot_id}, "
            f"{old_status} → {target_status}"
        )
        return TransitionResult(
            success=False,
            old_status=old_status,
            new_status=target_status,
            error=str(e)
        )
    
    # 执行转换
    robot.status = target_status
    robot.updated_at = datetime.now()
    
    # 记录审计日志
    audit = AuditLog(
        user_id='system',
        action='robot_status_change',
        resource_type='robot',
        resource_id=robot_id,
        old_value={'status': old_status},
        new_value={'status': target_status},
    )
    
    db.add(audit)
    await db.flush()
    
    logger.info(
        f"[StateMachine] 机器人状态转换: robot_id={robot_id}, "
        f"{old_status} → {target_status}"
    )
    
    return TransitionResult(
        success=True,
        old_status=old_status,
        new_status=target_status
    )


# ============================================================
# 便捷函数
# ============================================================
async def abandon_trade(
    db: AsyncSession,
    trade_id: int,
    reason: str
) -> TransitionResult:
    """
    放弃交易
    
    Args:
        db: 数据库会话
        trade_id: 交易ID
        reason: 放弃原因
    
    Returns:
        TransitionResult: 转换结果
    """
    return await transition_trade_status(
        db=db,
        trade_id=trade_id,
        target_status='abandoned',
        event_type='abandon',
        event_data={'reason': reason},
        reason=reason
    )


async def close_trade(
    db: AsyncSession,
    trade_id: int,
    reason: str,
    sell_data: dict
) -> TransitionResult:
    """
    关闭交易
    
    Args:
        db: 数据库会话
        trade_id: 交易ID
        reason: 关闭原因
        sell_data: 卖出数据（价格、数量等）
    
    Returns:
        TransitionResult: 转换结果
    """
    return await transition_trade_status(
        db=db,
        trade_id=trade_id,
        target_status='closed',
        event_type='close',
        event_data={**sell_data, 'reason': reason},
        reason=reason
    )


async def partial_sell(
    db: AsyncSession,
    trade_id: int,
    sell_tier: int,
    sell_data: dict
) -> TransitionResult:
    """
    部分卖出
    
    Args:
        db: 数据库会话
        trade_id: 交易ID
        sell_tier: 卖出档位（1/2/3）
        sell_data: 卖出数据
    
    Returns:
        TransitionResult: 转换结果
    """
    # 查询当前状态
    result = await db.execute(
        select(RobotTrade).where(RobotTrade.id == trade_id)
    )
    trade = result.scalar_one_or_none()
    
    if trade is None:
        return TransitionResult(
            success=False,
            old_status='',
            new_status='',
            error=f"交易不存在: {trade_id}"
        )
    
    # open → partial 或 partial → partial
    target_status = 'partial' if trade.status == 'open' else 'partial'
    
    return await transition_trade_status(
        db=db,
        trade_id=trade_id,
        target_status=target_status,
        event_type=f'sell_{sell_tier}',
        event_data=sell_data
    )


def check_trade_can_buy(status: str) -> bool:
    """检查交易是否可以执行买入"""
    return status == 'pending'


def check_trade_can_sell(status: str) -> bool:
    """检查交易是否可以执行卖出"""
    return status in ['open', 'partial']


def check_trade_is_closed(status: str) -> bool:
    """检查交易是否已关闭"""
    return status in ['closed', 'abandoned', 'eliminated']


def check_trade_is_terminal(status: str) -> bool:
    """检查交易是否为终态"""
    return is_terminal_status(status, TRADE_TRANSITIONS)
