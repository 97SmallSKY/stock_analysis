# backend/data_gateway/router.py

import logging
from typing import Optional, Dict, List, Any

import pandas as pd

from .category import DataCategory, AdapterName
from .base_adapter import BaseDataAdapter

logger = logging.getLogger(__name__)

# 默认优先级链配置
# 当前 TuShare 仅 120 积分 / 5 次每日, pro_bar 等核心接口无权限访问,
# 暂不在任何品类中用作主链。升级到 2000 积分后, 再把 TuShare 插回
# DAILY_KLINE / FINANCIAL_DATA / INDEX_DAILY / STOCK_INFO 链首即可。
DEFAULT_PRIORITY_CHAINS: Dict[DataCategory, List[AdapterName]] = {
    DataCategory.DAILY_KLINE:      [AdapterName.AKSHARE, AdapterName.SINA, AdapterName.CRAWLER],
    DataCategory.LHB_DETAIL:       [AdapterName.AKSHARE, AdapterName.SINA, AdapterName.CRAWLER],
    DataCategory.LHB_BROKER:       [AdapterName.AKSHARE, AdapterName.CRAWLER],
    DataCategory.INSTITUTION:      [AdapterName.SINA, AdapterName.AKSHARE, AdapterName.CRAWLER],
    DataCategory.REALTIME_QUOTE:   [AdapterName.AKSHARE, AdapterName.SINA],
    DataCategory.MARKET_SENTIMENT: [AdapterName.AKSHARE, AdapterName.SINA],
    DataCategory.FINANCIAL_DATA:   [AdapterName.AKSHARE],
    DataCategory.STOCK_INFO:       [AdapterName.AKSHARE, AdapterName.SINA],
    DataCategory.TRADE_CALENDAR:   [AdapterName.AKSHARE],
    DataCategory.INDEX_DAILY:      [AdapterName.AKSHARE, AdapterName.SINA],
}


class DataRouter:
    """
    数据路由分发器

    职责:
    1. 管理数据类别 → 适配器优先级链的映射
    2. 按优先级依次尝试获取数据
    3. 某个适配器失败时自动降级到下一个
    4. 所有适配器失败时返回None

    流程:
        请求 → 查优先级链 → 适配器1(熔断检查→请求→成功?)
                                              ↓失败
                           适配器2(熔断检查→请求→成功?)
                                              ↓失败
                           适配器3 → ... → 全部失败 → None
    """

    def __init__(self):
        self._adapters: Dict[AdapterName, BaseDataAdapter] = {}
        self._priority_chains: Dict[DataCategory, List[AdapterName]] = DEFAULT_PRIORITY_CHAINS.copy()

    # ================================================================
    # 适配器管理
    # ================================================================

    def register_adapter(self, adapter: BaseDataAdapter):
        """注册适配器"""
        self._adapters[adapter.name] = adapter
        logger.info(f"[Router] 注册适配器: {adapter.name.value}")

    def get_adapter(self, name: AdapterName) -> Optional[BaseDataAdapter]:
        """获取适配器实例"""
        return self._adapters.get(name)

    def get_all_adapters(self) -> Dict[AdapterName, BaseDataAdapter]:
        """获取所有适配器"""
        return self._adapters.copy()

    # ================================================================
    # 优先级链管理
    # ================================================================

    def set_priority_chain(self, category: DataCategory, chain: List[AdapterName]):
        """设置某个数据类别的优先级链"""
        self._priority_chains[category] = chain
        logger.info(
            f"[Router] 更新优先级链: {category.value} → "
            f"{[a.value for a in chain]}"
        )

    def get_priority_chain(self, category: DataCategory) -> List[AdapterName]:
        """获取某个数据类别的优先级链"""
        return self._priority_chains.get(category, [])

    # ================================================================
    # 核心: 路由请求
    # ================================================================

    def fetch(
            self,
            category: DataCategory,
            method_name: str,
            *args,
            **kwargs
    ) -> Optional[pd.DataFrame]:
        """
        按优先级链路由请求

        Args:
            category: 数据类别
            method_name: 适配器方法名 (如 "get_daily_kline")
            *args, **kwargs: 传递给方法的参数

        Returns:
            DataFrame 或 None (全部失败)
        """
        from backend.data_gateway.fetch_audit import FetchAudit
        import time as _time

        audit = FetchAudit.get_instance()

        chain = self._priority_chains.get(category, [])

        if not chain:
            logger.error(f"[Router] 数据类别 {category.value} 未配置优先级链")
            return None

        last_error = None

        for adapter_name in chain:
            adapter = self._adapters.get(adapter_name)

            if adapter is None:
                logger.debug(f"[Router] 适配器 {adapter_name.value} 未注册，跳过")
                continue

            if not adapter.initialized:
                logger.debug(f"[Router] 适配器 {adapter_name.value} 未初始化，跳过")
                continue

            # 获取对应方法
            method = getattr(adapter, method_name, None)
            if method is None:
                logger.debug(
                    f"[Router] 适配器 {adapter_name.value} 不支持 {method_name}，跳过"
                )
                continue

            t0 = _time.time()
            try:
                logger.debug(
                    f"[Router] 尝试 {adapter_name.value}.{method_name}"
                    f"({args}, {kwargs})"
                )

                result = method(*args, **kwargs)
                latency_ms = int((_time.time() - t0) * 1000)

                if result is not None and isinstance(result, pd.DataFrame) and not result.empty:
                    logger.info(
                        f"[Router] ✅ {category.value} 通过 {adapter_name.value} "
                        f"获取成功 ({len(result)}行)"
                    )
                    audit.record(
                        category=category.value,
                        method_name=method_name,
                        adapter_name=adapter_name.value,
                        latency_ms=latency_ms,
                        ok=True,
                        rows=len(result),
                        error_msg=None,
                    )
                    # 标记数据来源
                    result["_source"] = adapter_name.value
                    return result

                # 空数据也算一次"尝试", 标记 ok=False 便于统计各源空率
                audit.record(
                    category=category.value,
                    method_name=method_name,
                    adapter_name=adapter_name.value,
                    latency_ms=latency_ms,
                    ok=False,
                    rows=0,
                    error_msg="empty",
                )
                logger.debug(
                    f"[Router] {adapter_name.value} 返回空数据，降级下一个"
                )

            except Exception as e:
                latency_ms = int((_time.time() - t0) * 1000)
                last_error = str(e)
                audit.record(
                    category=category.value,
                    method_name=method_name,
                    adapter_name=adapter_name.value,
                    latency_ms=latency_ms,
                    ok=False,
                    rows=None,
                    error_msg=last_error,
                )
                logger.warning(
                    f"[Router] {adapter_name.value}.{method_name} 异常: {e}，"
                    f"降级下一个"
                )
                continue

        # 全部失败
        logger.error(
            f"[Router] ❌ {category.value}.{method_name} 全部数据源失败! "
            f"链路: {[a.value for a in chain]}, "
            f"最后错误: {last_error}"
        )
        return None

    # ================================================================
    # 便捷方法: 按数据类别直接调用
    # ================================================================

    def fetch_daily_kline(
        self, ts_code: str, start_date: str, end_date: str
    ) -> Optional[pd.DataFrame]:
        """获取日K线"""
        return self.fetch(
            DataCategory.DAILY_KLINE,
            "get_daily_kline",
            ts_code=ts_code,
            start_date=start_date,
            end_date=end_date,
        )

    def fetch_lhb_detail(self, trade_date: str) -> Optional[pd.DataFrame]:
        """获取龙虎榜明细"""
        return self.fetch(
            DataCategory.LHB_DETAIL,
            "get_lhb_detail",
            trade_date=trade_date,
        )

    def fetch_lhb_seats(self, trade_date: str) -> Optional[pd.DataFrame]:
        """获取龙虎榜席位"""
        return self.fetch(
            DataCategory.LHB_BROKER,
            "get_lhb_seats",
            trade_date=trade_date,
        )

    def fetch_realtime_quote(self, ts_codes: list) -> Optional[pd.DataFrame]:
        """获取实时行情"""
        return self.fetch(
            DataCategory.REALTIME_QUOTE,
            "get_realtime_quote",
            ts_codes=ts_codes,
        )

    def fetch_market_sentiment(self, trade_date: str) -> Optional[pd.DataFrame]:
        """获取市场情绪"""
        return self.fetch(
            DataCategory.MARKET_SENTIMENT,
            "get_market_sentiment",
            trade_date=trade_date,
        )

    def fetch_stock_info(self) -> Optional[pd.DataFrame]:
        """获取股票基础信息"""
        return self.fetch(
            DataCategory.STOCK_INFO,
            "get_stock_info",
        )

    def fetch_trade_calendar(
        self, exchange: str = "SSE", start_date: str = "20200101", end_date: str = "20261231"
    ) -> Optional[pd.DataFrame]:
        """获取交易日历"""
        return self.fetch(
            DataCategory.TRADE_CALENDAR,
            "get_trade_calendar",
            exchange=exchange,
            start_date=start_date,
            end_date=end_date,
        )

    def fetch_index_daily(
        self, ts_code: str, start_date: str, end_date: str
    ) -> Optional[pd.DataFrame]:
        """获取指数日线"""
        return self.fetch(
            DataCategory.INDEX_DAILY,
            "get_index_daily",
            ts_code=ts_code,
            start_date=start_date,
            end_date=end_date,
        )

    def fetch_financial_data(
        self, ts_code: str, period: str = ""
    ) -> Optional[pd.DataFrame]:
        """获取财务数据"""
        return self.fetch(
            DataCategory.FINANCIAL_DATA,
            "get_financial_data",
            ts_code=ts_code,
            period=period,
        )

    def fetch_institution(self, trade_date: str) -> Optional[pd.DataFrame]:
        """获取机构数据"""
        return self.fetch(
            DataCategory.INSTITUTION,
            "get_lhb_institution",
            trade_date=trade_date,
        )

    # ================================================================
    # 状态查询
    # ================================================================

    def get_status(self) -> dict:
        """获取路由器完整状态"""
        adapters_status = {}
        for name, adapter in self._adapters.items():
            adapters_status[name.value] = {
                "initialized": adapter.initialized,
                "circuit_breaker": adapter.circuit_breaker.get_status(),
            }

        chains_status = {}
        for category, chain in self._priority_chains.items():
            chains_status[category.value] = [a.value for a in chain]

        return {
            "adapters": adapters_status,
            "priority_chains": chains_status,
        }
