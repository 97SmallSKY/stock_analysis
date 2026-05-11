"""ATR 动态止损"""

from .base import BaseExit
from modules.backtest.portfolio import Position, Bar
from modules.strategies.base import SellSignal


class ATRStop(BaseExit):
    """基于 ATR 的动态止损：止损价 = 买入价 - mult * ATR(period)"""

    name = "atr_stop"

    def __init__(self, period: int = 14, mult: float = 2.0):
        self.period = period
        self.mult = mult

    def check(self, position: Position, bar: Bar, ctx: "BacktestContext" = None) -> SellSignal | None:
        if ctx is None or ctx.data_feed is None:
            return None
        atr_val = ctx.data_feed.atr(position.code, bar.date, self.period)
        if atr_val <= 0:
            return None
        stop_price = position.buy_price - self.mult * atr_val
        if bar.low <= stop_price:
            return SellSignal(reason=f"ATR止损(-{self.mult}σ)", price_type="stop_trigger")
        return None
