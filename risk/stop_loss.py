"""
止损管理器
"""
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class StopLossManager:
    """止损管理器"""
    
    def __init__(self, stop_loss_pct: float = -0.5, stop_profit_pct: Optional[float] = None):
        self.stop_loss_pct = stop_loss_pct
        self.stop_profit_pct = stop_profit_pct
    
    def check_stop_loss(self, buy_price: float, current_price: float) -> bool:
        """检查是否触发止损"""
        if buy_price <= 0:
            return False
        
        profit_pct = (current_price - buy_price) / buy_price
        return profit_pct <= self.stop_loss_pct
    
    def check_stop_profit(self, buy_price: float, current_price: float) -> bool:
        """检查是否触发止盈"""
        if self.stop_profit_pct is None or buy_price <= 0:
            return False
        
        profit_pct = (current_price - buy_price) / buy_price
        return profit_pct >= self.stop_profit_pct
