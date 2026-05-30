"""
数据源降级管理器

优先级顺序：Tushare(1) → AKShare(2) → Sina(3)
"""
import logging
import threading
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Type
from functools import lru_cache

from .base import BaseDataSource, FetchResult
from .tushare_fetcher import TushareDataSource
from .akshare_fetcher import AKShareDataSource
from .sina_fetcher import SinaDataSource

logger = logging.getLogger(__name__)


class DataSourceManager:
    """
    数据源降级管理器
    
    功能：
    1. 按优先级依次尝试各数据源
    2. 记录数据源健康状态
    3. 自动跳过已知不可用的数据源
    4. 提供统一的获取入口
    """
    
    # 默认数据源优先级配置
    DEFAULT_SOURCES = [
        ("tushare", TushareDataSource, 1),
        ("akshare", AKShareDataSource, 2),
        ("sina", SinaDataSource, 3),
    ]
    
    # 健康重试间隔（秒）
    HEALTH_RETRY_SECS = 300  # 5分钟后重试
    
    def __init__(self, tushare_token: Optional[str] = None):
        """
        初始化数据源管理器
        
        Args:
            tushare_token: Tushare API token（可选，从环境变量 TS_TOKEN 获取）
        """
        self._lock = threading.Lock()
        self._sources: Dict[str, BaseDataSource] = {}
        self._last_fail_time: Dict[str, datetime] = {}
        self._tushare_token = tushare_token
        
        # 初始化数据源实例
        self._init_sources()
    
    def _init_sources(self):
        """初始化所有数据源实例"""
        for name, cls, priority in self.DEFAULT_SOURCES:
            try:
                if name == "tushare":
                    self._sources[name] = cls(token=self._tushare_token)
                else:
                    self._sources[name] = cls()
                logger.debug(f"初始化数据源: {name} (优先级={priority})")
            except Exception as e:
                logger.warning(f"初始化数据源 {name} 失败: {e}")
    
    def get_daily_kline(
        self,
        stock_code: str,
        start_date: str,
        end_date: str,
        sources: Optional[List[str]] = None
    ) -> FetchResult:
        """
        获取日K线数据（带降级）
        
        Args:
            stock_code: 股票代码
            start_date: 开始日期 (YYYYMMDD)
            end_date: 结束日期 (YYYYMMDD)
            sources: 指定数据源列表（可选，默认全部按优先级）
        
        Returns:
            FetchResult
        """
        # 默认使用全部数据源
        if sources is None:
            sources = ["tushare", "akshare"]
        
        errors = []
        
        for name in sources:
            # 检查数据源是否初始化
            if name not in self._sources:
                continue
            
            # 检查是否在健康恢复期
            if self._is_in_cooldown(name):
                logger.debug(f"跳过数据源 {name}（冷却中）")
                continue
            
            source = self._sources[name]
            
            # 尝试获取数据
            result = source.get_daily_kline(stock_code, start_date, end_date)
            
            if result.is_valid():
                logger.info(f"✅ {name} 获取日K线成功")
                return result
            else:
                errors.append(f"{name}: {result.error}")
                self._record_failure(name)
                logger.warning(f"⚠️ {name} 获取失败: {result.error}")
        
        # 全部失败
        return FetchResult(
            data=None,
            source="error",
            is_realtime=False,
            error=" | ".join(errors)
        )
    
    def get_realtime_quote(
        self,
        stock_code: str,
        sources: Optional[List[str]] = None
    ) -> FetchResult:
        """
        获取实时行情（带降级）
        
        实时行情优先级：AKShare → Sina
        （Tushare 120积分不支持实时行情）
        """
        # 默认使用 AKShare 和 Sina
        if sources is None:
            sources = ["akshare", "sina"]
        
        errors = []
        
        for name in sources:
            if name not in self._sources:
                continue
            
            if self._is_in_cooldown(name):
                continue
            
            source = self._sources[name]
            result = source.get_realtime_quote(stock_code)
            
            if result.is_valid():
                logger.debug(f"✅ {name} 获取实时行情成功")
                return result
            else:
                errors.append(f"{name}: {result.error}")
                self._record_failure(name)
        
        return FetchResult(
            data=None,
            source="error",
            is_realtime=False,
            error=" | ".join(errors)
        )
    
    def health_status(self) -> Dict[str, str]:
        """
        获取所有数据源健康状态
        
        Returns:
            {source_name: "ok" | "cooldown" | "unavailable"}
        """
        status = {}
        for name in self._sources:
            if self._is_in_cooldown(name):
                status[name] = "cooldown"
            elif self._sources[name].is_available():
                status[name] = "ok"
            else:
                status[name] = "unavailable"
        return status
    
    def _is_in_cooldown(self, name: str) -> bool:
        """检查数据源是否在冷却期"""
        if name not in self._last_fail_time:
            return False
        
        last_fail = self._last_fail_time[name]
        return datetime.now() - last_fail < timedelta(seconds=self.HEALTH_RETRY_SECS)
    
    def _record_failure(self, name: str):
        """记录数据源失败时间"""
        with self._lock:
            self._last_fail_time[name] = datetime.now()


# 单例模式
_manager_instance: Optional[DataSourceManager] = None


def get_data_manager(tushare_token: Optional[str] = None) -> DataSourceManager:
    """
    获取数据源管理器单例
    
    Args:
        tushare_token: Tushare API token（可选）
    """
    global _manager_instance
    if _manager_instance is None:
        _manager_instance = DataSourceManager(tushare_token)
    return _manager_instance
