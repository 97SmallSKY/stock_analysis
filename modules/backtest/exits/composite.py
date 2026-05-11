"""退出组件组合器"""

from .base import BaseExit
from modules.backtest.portfolio import Position, Bar
from modules.strategies.base import SellSignal


class CompositeExit(BaseExit):
    """按优先级顺序链式检查多个 Exit，第一个命中的生效"""

    name = "composite"

    def __init__(self, exits: list[BaseExit]):
        self.exits = exits

    def check(self, position: Position, bar: Bar, ctx: "BacktestContext" = None) -> SellSignal | None:
        for exit_component in self.exits:
            signal = exit_component.check(position, bar, ctx)
            if signal is not None:
                return signal
        return None
