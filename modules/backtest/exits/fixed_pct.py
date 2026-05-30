"""固定百分比止盈止损"""

from .base import BaseExit
from modules.backtest.portfolio import Position, Bar
from modules.strategies.base import SellSignal


class FixedPctStop(BaseExit):
    """死板的固定百分比止盈止损（保持旧逻辑兼容）"""

    name = "fixed_pct"

    def __init__(self, stop_loss: float = -0.05, stop_profit: float = 0.08):
        self.stop_loss = stop_loss
        self.stop_profit = stop_profit

    def check(self, position: Position, bar: Bar, ctx: "BacktestContext" = None) -> SellSignal | None:
        # 开盘止损：持有超过 1 天，开盘即跌破止损线
        if position.hold_days > 1 and position.open_return <= self.stop_loss:
            return SellSignal(reason="开盘止损", price_type="open")
        # 开盘止盈
        if position.open_return >= self.stop_profit:
            return SellSignal(reason="开盘止盈", price_type="open")
        # 盘中止损
        if position.min_return <= self.stop_loss:
            return SellSignal(reason="盘中止损", price_type="stop_trigger")
        # 盘中止盈
        if position.max_return >= self.stop_profit:
            return SellSignal(reason="盘中止盈", price_type="stop_trigger")
        return None
