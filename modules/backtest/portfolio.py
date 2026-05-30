"""Position/Portfolio 数据类"""

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Bar:
    """单根日 K 线"""
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


@dataclass
class Position:
    """单笔持仓"""
    code: str
    buy_price: float
    buy_date: str
    hold_days: int = 1
    open_return: float = 0.0
    max_return: float = 0.0
    min_return: float = 0.0
    close_return: float = 0.0
    current_high: float = 0.0
    current_low: float = 0.0
    current_close: float = 0.0
    signal_meta: dict = field(default_factory=dict)

    @classmethod
    def from_bar(cls, code: str, buy_price: float, buy_date: str, bar: Bar, meta: dict = None):
        return cls(
            code=code,
            buy_price=buy_price,
            buy_date=buy_date,
            hold_days=1,
            open_return=(bar.open - buy_price) / buy_price,
            max_return=(bar.high - buy_price) / buy_price,
            min_return=(bar.low - buy_price) / buy_price,
            close_return=(bar.close - buy_price) / buy_price,
            current_high=bar.high,
            current_low=bar.low,
            current_close=bar.close,
            signal_meta=meta or {},
        )

    def update(self, bar: Bar):
        """更新到新的 bar"""
        self.hold_days += 1
        self.open_return = (bar.open - self.buy_price) / self.buy_price
        self.max_return = max(self.max_return, (bar.high - self.buy_price) / self.buy_price)
        self.min_return = min(self.min_return, (bar.low - self.buy_price) / self.buy_price)
        self.close_return = (bar.close - self.buy_price) / self.buy_price
        self.current_high = bar.high
        self.current_low = bar.low
        self.current_close = bar.close

    def to_dict(self) -> dict:
        return {
            "code": self.code, "buy_price": self.buy_price, "buy_date": self.buy_date,
            "hold_days": self.hold_days, "open_return": self.open_return,
            "max_return": self.max_return, "min_return": self.min_return,
            "close_return": self.close_return, "current_open": self.current_close,
            "current_high": self.current_high, "current_low": self.current_low,
            "current_close": self.current_close,
        }


class Portfolio:
    """简单持仓（单股单笔模式，不处理重叠）"""

    def __init__(self):
        self._positions: dict[str, Position] = {}
        self._history: list[Position] = []

    def open_position(self, position: Position):
        self._positions[position.code] = position

    def close_position(self, code: str) -> Position | None:
        pos = self._positions.pop(code, None)
        if pos:
            self._history.append(pos)
        return pos

    def get_position(self, code: str) -> Position | None:
        return self._positions.get(code)

    def active_positions(self) -> list[Position]:
        return list(self._positions.values())

    def closed_positions(self) -> list[Position]:
        return list(self._history)
