"""到期卖出"""

from .base import BaseExit
from modules.backtest.portfolio import Position, Bar
from modules.strategies.base import SellSignal


class TimeStop(BaseExit):
    """持有天数到期自动卖出"""

    name = "time_stop"

    def __init__(self, max_days: int = 10):
        self.max_days = max_days

    def check(self, position: Position, bar: Bar, ctx: "BacktestContext" = None) -> SellSignal | None:
        if position.hold_days >= self.max_days:
            return SellSignal(reason="到期卖出", price_type="close")
        return None
