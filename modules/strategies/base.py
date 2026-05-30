from dataclasses import dataclass, field
from abc import ABC, abstractmethod


@dataclass
class SellSignal:
    action: str = "SELL"
    reason: str = ""
    price_type: str = "close"  # open | close | stop_trigger
    component: str = ""        # Exit 组件名


@dataclass
class BuyOrder:
    code: str
    action: str = "BUY"
    price_type: str = "next_open"
    reason: str = ""
    meta: dict = field(default_factory=dict)


@dataclass
class Signal:
    """策略扫描产出的标准化信号"""
    date: str           # 信号日期 YYYYMMDD
    code: str           # 股票代码
    meta: dict = field(default_factory=dict)  # 策略自定义元数据


class BaseStrategy(ABC):
    name: str = "base"
    description: str = ""
    param_schema: dict = {}
    mode: str = "event"     # "event" | "bar"
    data_requirements: dict = {"kline": True, "lhb": False, "sentiment": False}

    def __init__(self, params: dict = None):
        defaults = {
            k: v.get("default") for k, v in self.param_schema.items()
        }
        defaults.update(params or {})
        self.params = defaults

    # ═══ 新接口 ═══

    def prepare(self, ctx: "BacktestContext"):
        """预加载数据，策略可在 scan_signals 前调用"""
        pass

    def scan_signals(self, ctx: "BacktestContext") -> list[Signal]:
        """
        扫描信号（事件驱动模式）。
        默认回退到 select() 旧接口。
        """
        return []

    def build_exits(self) -> "BaseExit":
        """构建退出组件链（默认：固定止损止盈 + 时间止损）

        新策略**应当 override 本方法**，按方案 §8.1 选用 ATR/Trailing/Sentiment 等动态 exit。
        未 override 时记录 warning 让接入者及时发现遗漏。
        """
        from loguru import logger
        from modules.backtest.exits import FixedPctStop, TimeStop, CompositeExit
        if self.__class__ is not BaseStrategy and "build_exits" not in self.__class__.__dict__:
            logger.warning(
                f"策略 {self.name} 未 override build_exits()，回退到默认 FixedPctStop+TimeStop。"
                f"如需 ATR/Trailing/Sentiment exit，请实现 build_exits() 方法。"
            )
        stop_loss = self.params.get("stop_loss", -0.05)
        stop_profit = self.params.get("stop_profit", 0.08)
        max_hold_days = self.params.get("max_hold_days", 10)
        return CompositeExit([
            FixedPctStop(stop_loss=stop_loss, stop_profit=stop_profit),
            TimeStop(max_days=max_hold_days),
        ])

    # ═══ 旧接口（兼容层）═══

    @abstractmethod
    def select(self, trade_date, lhb_df, kline_cache) -> list[BuyOrder]:
        """旧选股接口，仅 LHB 策略需要实现"""
        ...

    def should_sell(self, position: dict) -> SellSignal | None:
        """旧卖出判断，新策略通过 build_exits() 替代"""
        return None
