"""
策略基类 - 所有策略必须继承此类

新增策略步骤：
1. 在 modules/ 下创建 strategy_xxx.py
2. 继承 BaseStrategy 实现所有抽象方法
3. 在 strategy_registry 表中注册
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional, List


@dataclass
class SelectionSignal:
    """选股信号"""
    stock_code: str
    stock_name: str
    score: float
    buy_amount: float
    reason: str
    extra: dict = field(default_factory=dict)


@dataclass
class TradeSignal:
    """交易信号"""
    stock_code: str
    direction: str           # BUY / SELL
    price: float
    shares: int
    reason: str
    urgency: str = 'normal'  # normal / urgent
    batch_index: int = 0
    batch_total: int = 1


class BaseStrategy(ABC):
    """策略基类"""
    
    def __init__(self, params: dict = None):
        self.params = params or {}
    
    @abstractmethod
    def get_name(self) -> str:
        """策略名称"""
        pass
    
    @abstractmethod
    def get_id(self) -> str:
        """策略ID"""
        pass
    
    @abstractmethod
    def select_stocks(self, trade_date: str, context: dict) -> List[SelectionSignal]:
        """选股（Phase A 调用）
        
        Args:
            trade_date: 交易日期 (YYYYMMDD)
            context: 上下文信息，包含：
                - robot: 机器人配置
                - cash: 当前可用现金
                - holdings: 当前持仓列表
        
        Returns:
            选股信号列表
        """
        pass
    
    @abstractmethod
    def should_buy(self, signal: SelectionSignal, realtime: dict, context: dict) -> Optional[TradeSignal]:
        """买入判断（Phase B / 实时监控调用）
        
        Args:
            signal: 选股信号
            realtime: 实时行情数据
            context: 上下文信息
        
        Returns:
            TradeSignal 或 None（不买入）
        """
        pass
    
    @abstractmethod
    def should_sell(self, position: dict, realtime: dict, context: dict) -> Optional[TradeSignal]:
        """卖出判断（实时监控调用）
        
        Args:
            position: 持仓信息
            realtime: 实时行情数据
            context: 上下文信息
        
        Returns:
            TradeSignal 或 None（不卖出）
        """
        pass
    
    def on_batch_buy(self, position: dict, realtime: dict, context: dict) -> Optional[TradeSignal]:
        """分批建仓判断（默认不支持）
        
        子类可覆盖此方法实现分批建仓逻辑
        """
        return None
    
    def __repr__(self):
        return f"<{self.__class__.__name__} id={self.get_id()} name={self.get_name()}>"
