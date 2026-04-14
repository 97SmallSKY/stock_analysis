"""
机器人模块兼容层

从新架构 re-export，确保向后兼容
"""
from robot.manager import RobotManager
from robot.cash_manager import CashManager
from robot.trade_cost import (
    CostConfig,
    DEFAULT_COST_CONFIG,
    calc_slippage_price,
    calc_buy_cost,
    calc_sell_cost,
    calc_trade_profit,
)
from robot.position_calc import calculate_buy_shares

__all__ = [
    'RobotManager',
    'CashManager',
    'CostConfig',
    'DEFAULT_COST_CONFIG',
    'calc_slippage_price',
    'calc_buy_cost',
    'calc_sell_cost',
    'calc_trade_profit',
    'calculate_buy_shares',
]
