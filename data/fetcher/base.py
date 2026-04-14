"""
数据源基础模块

定义统一的数据源接口，支持多数据源降级
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional
import pandas as pd


@dataclass
class FetchResult:
    """
    统一的数据获取结果包装
    
    UI 层通过 source 字段决定显示的来源标记：
      - "tushare" → 蓝色「Tushare」小标签
      - "akshare" → 不显示标记（默认）
      - "sina"    → 灰色「新浪」小标签
      - "cache"   → 灰色「缓存 HH:MM」
      - "error"   → 红色「数据不可用」
    """
    data: Any                          # 实际数据（DataFrame / dict / list）
    source: str                        # "tushare" | "akshare" | "sina" | "cache" | "error"
    is_realtime: bool                  # 是否为盘中实时数据
    fetched_at: datetime = field(default_factory=datetime.now)
    error: Optional[str] = None        # 错误信息
    
    def is_valid(self) -> bool:
        """数据是否有效"""
        return self.data is not None and self.source != "error"
    
    def source_label(self) -> str:
        """返回 UI 显示用的来源标签"""
        mapping = {
            "tushare": "Tushare",
            "akshare": "",
            "sina": "新浪",
            "cache": f"缓存 {self.fetched_at.strftime('%H:%M')}",
            "error": "数据不可用",
        }
        return mapping.get(self.source, self.source)


class BaseDataSource(ABC):
    """数据源基类"""
    
    # 数据源名称
    NAME: str = "base"
    # 优先级（越小越优先）
    PRIORITY: int = 100
    
    @abstractmethod
    def get_daily_kline(
        self, 
        stock_code: str, 
        start_date: str, 
        end_date: str
    ) -> FetchResult:
        """
        获取日K线数据
        
        Args:
            stock_code: 股票代码（如 600001）
            start_date: 开始日期 (YYYYMMDD)
            end_date: 结束日期 (YYYYMMDD)
        
        Returns:
            FetchResult 包含 DataFrame，列包括：
            - trade_date: 交易日期
            - open: 开盘价
            - high: 最高价
            - low: 最低价
            - close: 收盘价
            - volume: 成交量
            - amount: 成交额
        """
        pass
    
    @abstractmethod
    def get_realtime_quote(self, stock_code: str) -> FetchResult:
        """
        获取实时行情
        
        Args:
            stock_code: 股票代码
        
        Returns:
            FetchResult 包含 dict，键包括：
            - price: 当前价
            - open: 开盘价
            - high: 最高价
            - low: 最低价
            - pre_close: 昨收
            - volume: 成交量
            - amount: 成交额
            - bid_price: 买一价
            - ask_price: 卖一价
        """
        pass
    
    @abstractmethod
    def is_available(self) -> bool:
        """检查数据源是否可用"""
        pass
    
    def _make_result(
        self, 
        data: Any, 
        is_realtime: bool = True,
        error: Optional[str] = None
    ) -> FetchResult:
        """构造正常来源的 FetchResult"""
        return FetchResult(
            data=data,
            source=self.NAME,
            is_realtime=is_realtime,
            error=error,
        )
    
    def _make_error(self, error: str) -> FetchResult:
        """构造错误 FetchResult"""
        return FetchResult(
            data=None,
            source="error",
            is_realtime=False,
            error=error,
        )
