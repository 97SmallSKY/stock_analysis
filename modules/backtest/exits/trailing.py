"""追踪止盈"""

from .base import BaseExit
from modules.backtest.portfolio import Position, Bar
from modules.strategies.base import SellSignal


class TrailingStop(BaseExit):
    """追踪止盈：浮盈达到 activate 后启动，回撤 giveback 比例退出"""

    name = "trailing_stop"

    def __init__(self, activate: float = 0.05, giveback: float = 0.4):
        self.activate = activate
        self.giveback = giveback
        self._peak: dict[str, float] = {}  # code -> peak high price

    def check(self, position: Position, bar: Bar, ctx: "BacktestContext" = None) -> SellSignal | None:
        code = position.code
        if position.max_return < self.activate:
            return None
        # 追踪最高价
        peak = self._peak.get(code, position.buy_price)
        self._peak[code] = max(peak, bar.high)
        # 回撤触发
        drawdown_from_peak = (bar.low - self._peak[code]) / self._peak[code]
        if drawdown_from_peak <= -self.giveback * self.activate:
            return SellSignal(reason=f"追踪止盈({self.giveback*100:.0f}%)", price_type="close")
        return None

    def reset(self, code: str = None):
        if code:
            self._peak.pop(code, None)
        else:
            self._peak.clear()
