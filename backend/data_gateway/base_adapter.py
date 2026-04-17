"""数据源适配器基类"""

import time
import logging
from abc import ABC, abstractmethod
from typing import Optional, Dict, Any, Callable

import pandas as pd

from .category import DataCategory, AdapterName
from .circuit_breaker import CircuitBreaker

logger = logging.getLogger(__name__)


class BaseDataAdapter(ABC):
    """
    数据源适配器基类

    所有数据源适配器必须继承此类并实现对应方法。
    每个 get_xxx 方法返回 pd.DataFrame 或 None (失败时)。
    """

    def __init__(
        self,
        name: AdapterName,
        config: Optional[Dict[str, Any]] = None,
        rate_limit: int = 60,
        timeout: int = 30,
    ):
        self.name = name
        self.config = config or {}
        self.rate_limit = rate_limit  # 每分钟最大请求数
        self.timeout = timeout  # 单次请求超时(秒)
        self._last_request_time: float = 0
        self._request_count: int = 0
        self._minute_start: float = 0

        # 每个适配器自带熔断器
        self.circuit_breaker = CircuitBreaker(
            name=f"{self.name.value}",
            failure_threshold=5,
            reset_timeout=300,
        )

        self._initialized = False

    # ================================================================
    # 生命周期
    # ================================================================

    def initialize(self) -> bool:
        """初始化适配器(登录/鉴权等)"""
        try:
            self._do_initialize()
            self._initialized = True
            logger.info(f"[{self.name.value}] 初始化成功")
            return True
        except Exception as e:
            logger.error(f"[{self.name.value}] 初始化失败: {e}")
            return False

    @abstractmethod
    def _do_initialize(self):
        """子类实现: 具体的初始化逻辑"""

    @abstractmethod
    def health_check(self) -> bool:
        """健康检查，返回是否可用"""

    @property
    def initialized(self) -> bool:
        return self._initialized

    # ================================================================
    # 数据获取方法(子类按需覆写)
    # ================================================================

    def get_daily_kline(
        self, ts_code: str, start_date: str, end_date: str
    ) -> Optional[pd.DataFrame]:
        return None

    def get_lhb_detail(self, trade_date: str) -> Optional[pd.DataFrame]:
        return None

    def get_lhb_seats(self, trade_date: str) -> Optional[pd.DataFrame]:
        return None

    def get_lhb_institution(self, trade_date: str) -> Optional[pd.DataFrame]:
        return None

    def get_realtime_quote(self, ts_codes: list) -> Optional[pd.DataFrame]:
        return None

    def get_market_sentiment(self, trade_date: str) -> Optional[pd.DataFrame]:
        return None

    def get_financial_data(
        self, ts_code: str, period: str
    ) -> Optional[pd.DataFrame]:
        return None

    def get_stock_info(self) -> Optional[pd.DataFrame]:
        return None

    def get_trade_calendar(
        self, exchange: str, start_date: str, end_date: str
    ) -> Optional[pd.DataFrame]:
        return None

    def get_index_daily(
        self, ts_code: str, start_date: str, end_date: str
    ) -> Optional[pd.DataFrame]:
        return None

    # ================================================================
    # 限流控制
    # ================================================================

    def _check_rate_limit(self):
        """限流检查，超限则等待到下一个窗口"""
        now = time.time()

        if now - self._minute_start >= 60:
            self._minute_start = now
            self._request_count = 0

        self._request_count += 1

        if self._request_count > self.rate_limit:
            wait_time = 60 - (now - self._minute_start)
            if wait_time > 0:
                logger.warning(
                    f"[{self.name.value}] 触发限流，等待 {wait_time:.1f}s "
                    f"({self._request_count}/{self.rate_limit} per min)"
                )
                time.sleep(wait_time)
                self._minute_start = time.time()
                self._request_count = 1

    def _safe_request(
        self, func: Callable, *args, max_retries: int = 2, **kwargs
    ) -> Optional[pd.DataFrame]:
        """
        安全请求封装: 熔断 + 限流 + 重试 + 异常捕获

        所有子类的数据获取方法应通过此方法调用实际 API。
        """
        # 1. 熔断检查
        if not self.circuit_breaker.allow_request():
            status = self.circuit_breaker.get_status()
            logger.debug(
                f"[{self.name.value}] 熔断中，跳过请求 "
                f"(state={status['state']}, "
                f"retry_in={status['time_until_retry']:.0f}s)"
            )
            return None

        # 2. 限流
        self._check_rate_limit()

        # 3. 请求 + 重试
        last_error: Optional[str] = None
        for attempt in range(max_retries + 1):
            start = time.time()
            try:
                result = func(*args, **kwargs)
                elapsed = time.time() - start

                self.circuit_breaker.record_success()

                if isinstance(result, pd.DataFrame) and not result.empty:
                    logger.debug(
                        f"[{self.name.value}] 请求成功 "
                        f"({len(result)}行, {elapsed:.2f}s)"
                    )
                else:
                    logger.debug(f"[{self.name.value}] 返回空数据 ({elapsed:.2f}s)")
                return result

            except Exception as e:
                elapsed = time.time() - start
                last_error = f"{type(e).__name__}: {str(e)[:200]}"

                if attempt < max_retries:
                    wait = (attempt + 1) * 2  # 退避: 2s, 4s
                    logger.warning(
                        f"[{self.name.value}] 请求失败(第{attempt + 1}/{max_retries + 1}次), "
                        f"{wait}s后重试: {last_error}"
                    )
                    time.sleep(wait)
                    continue

                self.circuit_breaker.record_failure(last_error)
                logger.error(
                    f"[{self.name.value}] 请求最终失败 "
                    f"(共{max_retries + 1}次, {elapsed:.2f}s): {last_error}"
                )
                return None

        return None

    # ================================================================
    # 元信息
    # ================================================================

    _CATEGORY_METHOD_MAP = {
        DataCategory.DAILY_KLINE: "get_daily_kline",
        DataCategory.LHB_DETAIL: "get_lhb_detail",
        DataCategory.LHB_BROKER: "get_lhb_seats",
        DataCategory.INSTITUTION: "get_lhb_institution",
        DataCategory.REALTIME_QUOTE: "get_realtime_quote",
        DataCategory.MARKET_SENTIMENT: "get_market_sentiment",
        DataCategory.FINANCIAL_DATA: "get_financial_data",
        DataCategory.STOCK_INFO: "get_stock_info",
        DataCategory.TRADE_CALENDAR: "get_trade_calendar",
        DataCategory.INDEX_DAILY: "get_index_daily",
    }

    def get_method_for_category(self, category: DataCategory) -> Optional[Callable]:
        method_name = self._CATEGORY_METHOD_MAP.get(category)
        return getattr(self, method_name, None) if method_name else None

    def supports(self, category: DataCategory) -> bool:
        """判断此适配器是否支持某数据类别(子类覆写了对应方法)"""
        method_name = self._CATEGORY_METHOD_MAP.get(category)
        if not method_name:
            return False
        base_method = getattr(BaseDataAdapter, method_name, None)
        sub_method = getattr(type(self), method_name, None)
        return sub_method is not None and sub_method is not base_method

    def __repr__(self):
        return (
            f"<{self.__class__.__name__} "
            f"name={self.name.value} "
            f"initialized={self._initialized} "
            f"circuit={self.circuit_breaker.state.value}>"
        )
