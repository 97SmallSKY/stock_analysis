"""
交易服务层 - 更新
"""
from .state_machine import (
    TradeStatus,
    RobotStatus,
    SellReason,
    AbandonReason,
    TRADE_TRANSITIONS,
    ROBOT_TRANSITIONS,
    InvalidStateTransition,
    TerminalStateError,
    validate_transition,
    transition_trade_status,
    transition_robot_status,
    abandon_trade,
    close_trade,
    partial_sell,
)
from .executor import (
    TradeExecutor,
    ExecutionResult,
    ExecutionType,
    execute_buy,
    execute_sell,
)
from .position_manager import (
    PositionManager,
    PositionInfo,
    PortfolioSummary,
    get_positions,
    get_portfolio_summary,
    update_nav,
)

__all__ = [
    # State Machine
    'TradeStatus',
    'RobotStatus',
    'SellReason',
    'AbandonReason',
    'TRADE_TRANSITIONS',
    'ROBOT_TRANSITIONS',
    'InvalidStateTransition',
    'TerminalStateError',
    'validate_transition',
    'transition_trade_status',
    'transition_robot_status',
    'abandon_trade',
    'close_trade',
    'partial_sell',
    # Executor
    'TradeExecutor',
    'ExecutionResult',
    'ExecutionType',
    'execute_buy',
    'execute_sell',
    # Position Manager
    'PositionManager',
    'PositionInfo',
    'PortfolioSummary',
    'get_positions',
    'get_portfolio_summary',
    'update_nav',
]
