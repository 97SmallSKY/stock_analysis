"""
    优先级配置(DB持久化)

"""

from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional
from enum import Enum
import json
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


class DataSource(str, Enum):
    TUSHARE  = "tushare"
    AKSHARE  = "akshare"
    SINA     = "sina"
    CRAWLER  = "crawler"


class DataCategory(str, Enum):
    DAILY_KLINE      = "kline_daily"       # 日K数据
    LHB_DETAIL       = "lhb_stock_daily"   # 龙虎榜明细
    LHB_BROKER       = "lhb_seats"        # 龙虎榜营业部
    INSTITUTION      = "institution"        # 机构数据
    REALTIME_QUOTE   = "realtime_quote"    # 实时行情
    MARKET_SENTIMENT = "market_sentiment"  # 市场情绪
    FINANCIAL_DATA   = "financial_data"    # 财务数据
    BLOCK_TRADE      = "block_trade"       # 大宗交易


# ── 默认优先级表(业务语义写死, 但可被DB覆盖) ─────────────────────────
DEFAULT_PRIORITY: Dict[DataCategory, List[DataSource]] = {
    DataCategory.DAILY_KLINE: [
        DataSource.TUSHARE,
        DataSource.AKSHARE,
        DataSource.SINA,
        DataSource.CRAWLER,
    ],
    DataCategory.LHB_DETAIL: [
        DataSource.AKSHARE,
        DataSource.SINA,
        DataSource.CRAWLER,
    ],
    DataCategory.LHB_BROKER: [
        DataSource.AKSHARE,
        DataSource.CRAWLER,
    ],
    DataCategory.INSTITUTION: [
        DataSource.SINA,
        DataSource.CRAWLER,
    ],
    DataCategory.REALTIME_QUOTE: [
        DataSource.AKSHARE,
        DataSource.SINA,
    ],
    DataCategory.MARKET_SENTIMENT: [
        DataSource.AKSHARE,
        DataSource.SINA,
    ],
    DataCategory.FINANCIAL_DATA: [
        DataSource.TUSHARE,
        DataSource.AKSHARE,
    ],
    DataCategory.BLOCK_TRADE: [
        DataSource.AKSHARE,
        DataSource.CRAWLER,
    ],
}


@dataclass
class SourceMeta:
    """单个数据源的运行时元数据"""
    source: DataSource
    enabled: bool = True
    rate_limit: int = 60          # 次/分钟
    timeout: int = 30             # 秒
    max_retries: int = 3
    base_delay: float = 2.0       # 重试初始等待
    credentials: Dict = field(default_factory=dict)
    note: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["source"] = self.source.value
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "SourceMeta":
        d = d.copy()
        d["source"] = DataSource(d["source"])
        return cls(**d)


class PriorityConfig:
    """
    优先级配置管理器
    - 内存中维护当前优先级表
    - 支持从 DB / JSON 文件持久化加载与保存
    - 提供增删改查接口供 UI 调用
    """

    _TABLE = "data_source_config"      # PostgreSQL 表名

    def __init__(self, storage):
        self._storage = storage
        # category -> 有序 DataSource 列表
        self._priority: Dict[DataCategory, List[DataSource]] = {}
        # source -> 元数据
        self._meta: Dict[DataSource, SourceMeta] = {}
        self._load()

    # ── 加载 ─────────────────────────────────────────────────────────
    def _load(self):
        """从持久化存储加载, 不存在则使用默认值"""
        try:
            raw = self._load_from_storage()
            if raw:
                self._priority = {
                    DataCategory(k): [DataSource(s) for s in v["order"]]
                    for k, v in raw.get("priority", {}).items()
                }
                self._meta = {
                    DataSource(k): SourceMeta.from_dict(v)
                    for k, v in raw.get("meta", {}).items()
                }
                logger.info("✅ 数据源配置从存储加载成功")
                return
        except Exception as e:
            logger.warning(f"加载数据源配置失败, 使用默认值: {e}")

        # 使用默认值初始化
        self._priority = {k: list(v) for k, v in DEFAULT_PRIORITY.items()}
        self._meta = {
            DataSource.TUSHARE: SourceMeta(DataSource.TUSHARE, rate_limit=200, max_retries=3),
            DataSource.AKSHARE: SourceMeta(DataSource.AKSHARE, rate_limit=100, max_retries=5),
            DataSource.SINA:    SourceMeta(DataSource.SINA,    rate_limit=60,  max_retries=3),
            DataSource.CRAWLER: SourceMeta(DataSource.CRAWLER, rate_limit=20,  max_retries=2),
        }

    def _load_from_storage(self) -> Optional[dict]:
        import os, json
        if os.path.exists(self._JSON_PATH):
            with open(self._JSON_PATH) as f:
                return json.load(f)
        return None

    # ── 保存 ─────────────────────────────────────────────────────────
    def save(self):
        """持久化当前配置"""
        payload = {
            "priority": {
                k.value: {"order": [s.value for s in v]}
                for k, v in self._priority.items()
            },
            "meta": {
                k.value: v.to_dict()
                for k, v in self._meta.items()
            },
            "saved_at": datetime.now().isoformat(),
        }
        import json, os
        os.makedirs("data", exist_ok=True)
        with open(self._JSON_PATH, "w") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        logger.info("✅ 数据源配置已持久化")

    # ── 查询接口 ─────────────────────────────────────────────────────
    def get_ordered_sources(
        self, category: DataCategory
    ) -> List[DataSource]:
        """返回指定类别的启用数据源列表(按优先级)"""
        all_sources = self._priority.get(category, DEFAULT_PRIORITY.get(category, []))
        return [s for s in all_sources if self._meta.get(s, SourceMeta(s)).enabled]

    def get_meta(self, source: DataSource) -> SourceMeta:
        return self._meta.get(source, SourceMeta(source))
    # ── 修改接口(供UI调用) ────────────────────────────────────────────
    def set_order(
        self,
        category: DataCategory,
        ordered_sources: List[DataSource],
    ):
        """重新设置某类别的优先级顺序"""
        self._priority[category] = ordered_sources
        self.save()
        logger.info(f"优先级更新 [{category.value}]: {[s.value for s in ordered_sources]}")

    def move_up(self, category: DataCategory, source: DataSource):
        """将某数据源在指定类别中上移一位"""
        order = self._priority.get(category, [])
        idx = order.index(source) if source in order else -1
        if idx > 0:
            order[idx], order[idx - 1] = order[idx - 1], order[idx]
            self._priority[category] = order
            self.save()

    def move_down(self, category: DataCategory, source: DataSource):
        """将某数据源在指定类别中下移一位"""
        order = self._priority.get(category, [])
        idx = order.index(source) if source in order else -1
        if idx != -1 and idx < len(order) - 1:
            order[idx], order[idx + 1] = order[idx + 1], order[idx]
            self._priority[category] = order
            self.save()

    def toggle_source(self, source: DataSource, enabled: bool):
        """启用/禁用某数据源(全局生效)"""
        if source in self._meta:
            self._meta[source].enabled = enabled
            self._meta[source].updated_at = datetime.now().isoformat()
            self.save()

    def update_meta(self, source: DataSource, **kwargs):
        """更新数据源元数据: rate_limit/timeout/credentials等"""
        if source in self._meta:
            for k, v in kwargs.items():
                if hasattr(self._meta[source], k):
                    setattr(self._meta[source], k, v)
            self._meta[source].updated_at = datetime.now().isoformat()
            self.save()

    def reset_to_default(self, category: Optional[DataCategory] = None):
        """重置为默认优先级"""
        if category:
            self._priority[category] = list(DEFAULT_PRIORITY.get(category, []))
        else:
            self._priority = {k: list(v) for k, v in DEFAULT_PRIORITY.items()}
        self.save()

    def get_all_config(self) -> dict:
        """返回全量配置快照(供UI展示)"""
        return {
            "priority": {
                cat.value: [s.value for s in sources]
                for cat, sources in self._priority.items()
            },
            "meta": {
                src.value: self._meta[src].to_dict()
                for src in self._meta
            },
        }