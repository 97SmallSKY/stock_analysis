# backend/data_gateway/manager.py

import logging
from typing import Optional, Dict, Any

import pandas as pd

from .category import DataCategory, AdapterName
from .router import DataRouter
from .base_adapter import BaseDataAdapter, AdapterNotConfigured  # noqa: F401
from .adapters import TuShareAdapter, AKShareAdapter, SinaAdapter, CrawlerAdapter

logger = logging.getLogger(__name__)


class DataSourceManager:
    """
    数据源管理器(单例)

    系统唯一入口，统一管理:
    1. 适配器初始化与注册
    2. 路由分发
    3. 熔断器状态
    4. 优先级配置

    使用:
        manager = DataSourceManager.get_instance()
        manager.initialize(config)

        df = manager.get_daily_kline("000001.SZ", "20240101", "20241231")
        df = manager.get_lhb_detail("20240115")
    """

    _instance: Optional["DataSourceManager"] = None

    def __init__(self):
        self._router = DataRouter()
        self._initialized = False
        self._config: Dict[str, Any] = {}

    # ================================================================
    # 单例
    # ================================================================

    @classmethod
    def get_instance(cls) -> "DataSourceManager":
        """获取单例实例"""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls):
        """重置单例(测试用)"""
        cls._instance = None

    # ================================================================
    # 初始化
    # ================================================================

    def initialize(self, config: Optional[Dict[str, Any]] = None):
        """
        初始化所有数据源

        Args:
            config: {
                "tushare": {"token": "xxx", "rate_limit": 200},
                "akshare": {"rate_limit": 60},
                "sina":    {"rate_limit": 30},
                "crawler": {"rate_limit": 20},
                "priority_chains": {  # 可选，覆盖默认优先级
                    "DAILY_KLINE": ["tushare", "akshare", "sina"],
                    ...
                }
            }
        """
        if self._initialized:
            logger.warning("[Manager] 已经初始化过，跳过")
            return

        self._config = config or {}
        logger.info("[Manager] ========== 开始初始化数据源 ==========")

        # 1. 创建并注册适配器
        adapters_config = {
            AdapterName.TUSHARE: (TuShareAdapter, self._config.get("tushare", {})),
            AdapterName.AKSHARE: (AKShareAdapter, self._config.get("akshare", {})),
            AdapterName.SINA: (SinaAdapter, self._config.get("sina", {})),
            AdapterName.CRAWLER: (CrawlerAdapter, self._config.get("crawler", {})),
        }

        for adapter_name, (adapter_cls, adapter_config) in adapters_config.items():
            try:
                adapter = adapter_cls(config=adapter_config)
                success = adapter.initialize()

                if success:
                    self._router.register_adapter(adapter)
                    logger.info(f"[Manager] ✅ {adapter_name.value} 初始化成功")
                else:
                    # 初始化失败也注册，但标记为未初始化
                    # 后续可以通过重试恢复
                    self._router.register_adapter(adapter)
                    # 无 token 等"未启用"场景已在 base_adapter 层降级为 info,
                    # Manager 这里用 info 同级,避免 WARNING 噪音
                    if not adapter_config.get("token") and adapter_name == AdapterName.TUSHARE:
                        logger.info(f"[Manager] ⏭️ {adapter_name.value} 未配置, 已跳过")
                    else:
                        logger.warning(
                            f"[Manager] ⚠️ {adapter_name.value} 初始化失败,已注册但不可用"
                        )

            except Exception as e:
                logger.error(f"[Manager] ❌ {adapter_name.value} 创建失败: {e}")

        # 2. 加载自定义优先级链(如果有)
        custom_chains = self._config.get("priority_chains", {})
        for category_str, chain_list in custom_chains.items():
            try:
                category = DataCategory(category_str)
                chain = [AdapterName(name) for name in chain_list]
                self._router.set_priority_chain(category, chain)
            except (ValueError, KeyError) as e:
                logger.warning(f"[Manager] 无效的优先级链配置: {category_str} → {e}")

        self._initialized = True

        # 3. 打印状态摘要
        status = self.get_status()
        active = sum(
            1 for a in status["adapters"].values() if a["initialized"]
        )
        total = len(status["adapters"])
        logger.info(
            f"[Manager] ========== 初始化完成 "
            f"({active}/{total} 数据源可用) =========="
        )

    # ================================================================
    # 数据获取(代理到Router)
    # ================================================================

    def get_daily_kline(
            self, ts_code: str, start_date: str, end_date: str
    ) -> Optional[pd.DataFrame]:
        """获取日K线"""
        self._ensure_initialized()
        return self._router.fetch_daily_kline(ts_code, start_date, end_date)

    def get_lhb_detail(self, trade_date: str) -> Optional[pd.DataFrame]:
        """获取龙虎榜明细"""
        self._ensure_initialized()
        return self._router.fetch_lhb_detail(trade_date)

    def get_lhb_seats(self, trade_date: str) -> Optional[pd.DataFrame]:
        """获取龙虎榜席位"""
        self._ensure_initialized()
        return self._router.fetch_lhb_seats(trade_date)

    def get_realtime_quote(self, ts_codes: list) -> Optional[pd.DataFrame]:
        """获取实时行情"""
        self._ensure_initialized()
        return self._router.fetch_realtime_quote(ts_codes)

    def get_market_sentiment(self, trade_date: str) -> Optional[pd.DataFrame]:
        """获取市场情绪"""
        self._ensure_initialized()
        return self._router.fetch_market_sentiment(trade_date)

    def get_stock_info(self) -> Optional[pd.DataFrame]:
        """获取股票基础信息"""
        self._ensure_initialized()
        return self._router.fetch_stock_info()

    def get_trade_calendar(
            self, exchange: str = "SSE", start_date: str = "20200101", end_date: str = "20261231"
    ) -> Optional[pd.DataFrame]:
        """获取交易日历"""
        self._ensure_initialized()
        return self._router.fetch_trade_calendar(exchange, start_date, end_date)

    def get_index_daily(
            self, ts_code: str, start_date: str, end_date: str
    ) -> Optional[pd.DataFrame]:
        """获取指数日线"""
        self._ensure_initialized()
        return self._router.fetch_index_daily(ts_code, start_date, end_date)

    def get_financial_data(
            self, ts_code: str, period: str = ""
    ) -> Optional[pd.DataFrame]:
        """获取财务数据"""
        self._ensure_initialized()
        return self._router.fetch_financial_data(ts_code, period)

    def get_institution(self, trade_date: str) -> Optional[pd.DataFrame]:
        """获取机构数据"""
        self._ensure_initialized()
        return self._router.fetch_institution(trade_date)

    # ================================================================
    # 管理方法
    # ================================================================

    def retry_adapter(self, adapter_name: str) -> bool:
        """重新初始化某个适配器"""
        try:
            name = AdapterName(adapter_name)
            adapter = self._router.get_adapter(name)
            if adapter is None:
                logger.error(f"[Manager] 适配器 {adapter_name} 未注册")
                return False

            success = adapter.initialize()
            if success:
                adapter.circuit_breaker.force_close()
                logger.info(f"[Manager] ✅ {adapter_name} 重新初始化成功")
            return success

        except Exception as e:
            logger.error(f"[Manager] 重新初始化 {adapter_name} 失败: {e}")
            return False

    def force_close_circuit(self, adapter_name: str):
        """手动关闭某个适配器的熔断器"""
        try:
            name = AdapterName(adapter_name)
            adapter = self._router.get_adapter(name)
            if adapter:
                adapter.circuit_breaker.force_close()
        except Exception as e:
            logger.error(f"[Manager] 关闭熔断器失败: {e}")

    def force_open_circuit(self, adapter_name: str):
        """手动打开某个适配器的熔断器"""
        try:
            name = AdapterName(adapter_name)
            adapter = self._router.get_adapter(name)
            if adapter:
                adapter.circuit_breaker.force_open()
        except Exception as e:
            logger.error(f"[Manager] 打开熔断器失败: {e}")

    # backend/data_gateway/manager.py (续)

    def update_priority_chain(
            self, category: str, chain: list
    ) -> bool:
        """
        更新某个数据类别的优先级链

        Args:
            category: "DAILY_KLINE"
            chain: ["tushare", "akshare"]
        """
        try:
            cat = DataCategory(category)
            adapter_chain = [AdapterName(name) for name in chain]
            self._router.set_priority_chain(cat, adapter_chain)
            return True
        except (ValueError, KeyError) as e:
            logger.error(f"[Manager] 更新优先级链失败: {e}")
            return False

    def health_check_all(self) -> Dict[str, bool]:
        """检查所有适配器健康状态"""
        results = {}
        for name, adapter in self._router.get_all_adapters().items():
            try:
                results[name.value] = adapter.health_check()
            except Exception:
                results[name.value] = False
        return results

    def get_status(self) -> dict:
        """获取完整状态(用于API展示)"""
        return self._router.get_status()

    def _ensure_initialized(self):
        """确保已初始化"""
        if not self._initialized:
            raise RuntimeError(
                "DataSourceManager 未初始化，请先调用 initialize()"
            )

