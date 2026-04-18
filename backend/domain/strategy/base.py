"""策略基类与数据契约.

本文件是整个交易引擎和机器人的核心接口. 为了保持纯净,
不依赖 DB / 不依赖 pandas, 只用 dataclass + 基本类型.

设计:
  BaseStrategy 三个核心方法
    - select_stocks(ctx) -> list[Candidate]       盘后选股
    - calc_buy_amount(candidate, robot_state)      算单票买入金额(元)
    - check_sell(position, ctx) -> SellSignal | None   逐持仓盘中检查

  BaseStrategy 暴露 params (dict) 让子类通过 self.params['xx'] 访问,
  实例化时可以 override 默认值(strategy_params 表覆盖逻辑由上层处理).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Optional


# --------------------------------------------------------------------
# 数据契约
# --------------------------------------------------------------------


@dataclass
class Candidate:
    """选股产出: 一个候选买入标的."""
    ts_code: str
    score: float                                # 0-100 综合分
    reason: str = ""                            # 选中原因(可审计)
    signal_detail: dict[str, Any] = field(default_factory=dict)
    lhb_daily_id: Optional[int] = None          # 若来自龙虎榜, 关联记录 id
    # 辅助字段(供 calc_buy_amount 用)
    reference_price: Optional[float] = None     # 参考价(通常用收盘价)


@dataclass
class SellSignal:
    """卖出信号."""
    reason: str                                 # STOP_LOSS / TAKE_PROFIT / MAX_HOLD / CUSTOM
    price_hint: Optional[float] = None          # 建议卖出价(None 表示市价)
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class Position:
    """当前持仓 (供 check_sell 用)."""
    ts_code: str
    shares: int
    cost_price: float
    current_price: float
    open_date: date
    hold_days: int
    highest_price: Optional[float] = None        # 供 trailing stop

    @property
    def pnl_pct(self) -> float:
        if self.cost_price == 0:
            return 0.0
        return (self.current_price - self.cost_price) / self.cost_price


@dataclass
class MarketContext:
    """策略运行上下文.

    as_of: 当前决策日 (T 日). select_stocks 用 T 日收盘后数据, check_sell 用盘中 snapshot.
    data_provider: 回调接口, 策略通过它读取历史 K / 龙虎榜 / 快照, 不直接依赖 DB.
    """
    as_of: date
    data_provider: Any = None                   # 需实现 get_kline / get_lhb / ...
    extras: dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------
# 策略基类
# --------------------------------------------------------------------


class BaseStrategy(ABC):
    """所有策略的基类.

    子类必须实现 select_stocks / calc_buy_amount / check_sell.
    子类可通过 class 属性 DEFAULT_PARAMS 定义默认参数字典;
    实例化 `MyStrat(params={...})` 时 dict 合并 (入参优先).
    """

    # 标识信息 (子类可 override)
    name: str = "base"                           # 唯一标识, 注册用
    category: str = "LHB"                        # LHB / MOMENTUM / CUSTOM
    description: str = ""

    DEFAULT_PARAMS: dict[str, Any] = {}

    def __init__(self, params: Optional[dict[str, Any]] = None):
        # 合并默认参数 (深 copy 避免子类实例共享)
        merged = dict(self.DEFAULT_PARAMS)
        if params:
            merged.update(params)
        self.params: dict[str, Any] = merged

    # ------------------------------------------------------------------
    # 抽象方法
    # ------------------------------------------------------------------

    @abstractmethod
    def select_stocks(self, ctx: MarketContext) -> list[Candidate]:
        """盘后 / 收盘后选股. 返回候选列表, 交给机器人买入."""

    @abstractmethod
    def calc_buy_amount(
        self,
        candidate: Candidate,
        robot_cash: float,
        robot_total_assets: float,
    ) -> float:
        """基于候选 + 当前资金, 返回计划买入金额(元). 返回 0 表示跳过."""

    @abstractmethod
    def check_sell(
        self,
        position: Position,
        ctx: MarketContext,
    ) -> Optional[SellSignal]:
        """盘中对单个持仓检查是否该卖. 不卖返回 None."""

    # ------------------------------------------------------------------
    # 公共工具
    # ------------------------------------------------------------------

    def get_param(self, key: str, default: Any = None) -> Any:
        return self.params.get(key, default)

    def __repr__(self) -> str:  # pragma: no cover - 打印辅助
        return f"<{self.__class__.__name__} name={self.name} params={self.params}>"
