"""BaseExit 抽象基类"""

from abc import ABC, abstractmethod
from modules.backtest.portfolio import Position, Bar


class BaseExit(ABC):
    """退出组件基类"""

    name: str = "base_exit"

    @abstractmethod
    def check(self, position: Position, bar: Bar, ctx: "BacktestContext") -> "SellSignal | None":
        """检查是否应卖出，返回 SellSignal 或 None（继续持有）"""
        ...


# 延迟导入避免循环依赖
from modules.strategies.base import SellSignal
