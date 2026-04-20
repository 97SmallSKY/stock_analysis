"""响应级缓存 (STO-43)。

设计目标
--------
- **场景**:`lhb_stock_daily` / `lhb_tn_price` 是 T+1 日级数据,盘中不变。
  同一交易日内相同参数的 `GET /api/lhb` 每次都打 DB 是纯浪费。
- **粒度**:响应级(整个 JSON dict 缓存到内存),key 由全部过滤参数决定。
- **TTL 策略**:默认到「**当日 19:00**」失效 —— daily 管道 18:40 跑完,
  19:00 后首请求必然 MISS,拿到最新数据。如果当前已过 19:00,TTL 顺延到次日 19:00。
  同时有 ``ttl_cap_seconds`` 硬上限(默认 3600s),防时钟错乱。
- **替换策略**:进程内 dict + 软 LRU(超过 ``max_size`` 按最旧 insert 顺序淘汰)。
  单 uvicorn 进程足够,多副本部署建议改 Redis backend(当前 issue 不做,
  见 STO-43 红线:「若打算引入 Redis,需先 @用户确认」)。
- **线程安全**:用 ``threading.Lock`` 保护 dict,FastAPI BackgroundTasks / 多请求并发场景安全。
- **观测**:每次读写在 logger 打 `cache=HIT|MISS|BYPASS` + key 前 8 位,
  便于 `grep cache= /var/log/...` 做命中率统计。

用法
----

    from backend.utils.response_cache import ResponseCache

    _lhb_cache = ResponseCache(name="lhb_list", max_size=256)

    def list_lhb(date_from, ..., skip_total):
        key = _lhb_cache.make_key(
            date_from=date_from, ..., skip_total=skip_total
        )
        hit = _lhb_cache.get(key)
        if hit is not None:
            return hit
        result = _query_db(...)
        _lhb_cache.set(key, result)
        return result

不要把 ``Response`` 对象存进缓存(对其他请求共享时 FastAPI 会出问题),
只存 **纯 dict / list**(JSON-serializable)。
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Optional

logger = logging.getLogger(__name__)

# 每天缓存统一失效时刻(北京时间 19:00,daily 管道 18:40 跑完后给 20min buffer)
DEFAULT_DAILY_FLUSH_HOUR = 19
DEFAULT_TTL_CAP_SECONDS = 3600  # 兜底 TTL,防时钟异常
DEFAULT_MAX_SIZE = 256


@dataclass
class _Entry:
    value: Any
    expires_at: float  # epoch seconds


def _ttl_until_daily_flush(
    now: Optional[datetime] = None,
    flush_hour: int = DEFAULT_DAILY_FLUSH_HOUR,
    cap_seconds: int = DEFAULT_TTL_CAP_SECONDS,
) -> float:
    """算从 ``now`` 到下个 ``flush_hour:00:00`` 的秒数,但不超过 ``cap_seconds``。

    - ``now`` 小于 flush_hour:今天 flush_hour 点到期
    - ``now`` 大于等于 flush_hour:明天 flush_hour 点到期
    - 再与 ``cap_seconds`` 取 min(默认 1h),防意外长缓存
    """
    now = now or datetime.now()
    target = now.replace(hour=flush_hour, minute=0, second=0, microsecond=0)
    if now >= target:
        target = target + timedelta(days=1)
    delta = (target - now).total_seconds()
    return float(min(delta, cap_seconds))


def _stable_key(params: dict) -> str:
    """把 params 变成稳定 sha256 hex 字符串,参数顺序无关。

    - 排序 key,``json.dumps`` 用 ``sort_keys=True`` + ``default=str``
      (date / datetime 自动 ISO 化)
    - 只保留有意义的 value(None 也保留,None 本身是合法过滤 "不限")
    """
    raw = json.dumps(params, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class ResponseCache:
    """进程内响应缓存 (TTL + 软 LRU + 线程安全)。

    Attributes
    ----------
    name : 用于日志标识(不同端点建不同 cache 实例)
    max_size : 超过则按 insert 顺序淘汰最旧的
    flush_hour : 每日强制失效小时(24h 制)
    ttl_cap : TTL 硬上限秒数
    enabled : 全局开关,``False`` 时 get/set 全 BYPASS
    """

    def __init__(
        self,
        name: str = "default",
        max_size: int = DEFAULT_MAX_SIZE,
        flush_hour: int = DEFAULT_DAILY_FLUSH_HOUR,
        ttl_cap: int = DEFAULT_TTL_CAP_SECONDS,
        enabled: bool = True,
    ):
        self.name = name
        self.max_size = max_size
        self.flush_hour = flush_hour
        self.ttl_cap = ttl_cap
        self.enabled = enabled
        self._store: "OrderedDict[str, _Entry]" = OrderedDict()
        self._lock = threading.Lock()
        # 统计(非线程安全读,只用于观测/测试,不担心轻微偏差)
        self.hits = 0
        self.misses = 0
        self.bypasses = 0

    # ------------------------------------------------------------------ key
    def make_key(self, **params) -> str:
        """根据命名参数生成稳定 key。测试里可直接对比 key 是否相等。"""
        return _stable_key(params)

    # ------------------------------------------------------------------ get
    def get(self, key: str) -> Optional[Any]:
        """返回命中值,否则返回 None。过期项会被顺手清理。

        **注意**:若被缓存的真实值就是 None,这里也会回 None 导致二次穿透。
        为避免歧义,建议 value 恒为 dict(API 响应天然是 dict)。
        """
        if not self.enabled:
            self.bypasses += 1
            logger.debug("[cache:%s] BYPASS disabled key=%s", self.name, key[:8])
            return None
        now = time.time()
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                self.misses += 1
                logger.info("[cache:%s] MISS key=%s", self.name, key[:8])
                return None
            if entry.expires_at <= now:
                # 过期,剔除
                self._store.pop(key, None)
                self.misses += 1
                logger.info(
                    "[cache:%s] MISS (expired) key=%s", self.name, key[:8]
                )
                return None
            # 命中:把 key 移到末尾(最近使用)
            self._store.move_to_end(key)
            self.hits += 1
            remain = int(entry.expires_at - now)
            logger.info(
                "[cache:%s] HIT key=%s ttl_remain=%ds", self.name, key[:8], remain
            )
            return entry.value

    # ------------------------------------------------------------------ set
    def set(self, key: str, value: Any, ttl: Optional[float] = None) -> None:
        """写入缓存。``ttl=None`` 时用 ``_ttl_until_daily_flush`` 计算。"""
        if not self.enabled:
            return
        if ttl is None:
            ttl = _ttl_until_daily_flush(
                flush_hour=self.flush_hour, cap_seconds=self.ttl_cap
            )
        expires_at = time.time() + ttl
        with self._lock:
            if key in self._store:
                # 覆盖:刷 ttl 并移到末尾
                self._store.move_to_end(key)
            self._store[key] = _Entry(value=value, expires_at=expires_at)
            # 淘汰最旧
            while len(self._store) > self.max_size:
                evicted_key, _ = self._store.popitem(last=False)
                logger.debug(
                    "[cache:%s] EVICT key=%s (size>%d)",
                    self.name, evicted_key[:8], self.max_size,
                )

    # ------------------------------------------------------------------ admin
    def invalidate(self, key: str) -> bool:
        """显式让一条 key 失效。返回是否存在。"""
        with self._lock:
            return self._store.pop(key, None) is not None

    def clear(self) -> int:
        """清空全缓存,返回清掉的条数(用于测试 / 管道跑完主动 flush)。"""
        with self._lock:
            n = len(self._store)
            self._store.clear()
            return n

    def stats(self) -> dict:
        with self._lock:
            size = len(self._store)
        total = self.hits + self.misses
        hit_rate = (self.hits / total) if total else 0.0
        return {
            "name": self.name,
            "size": size,
            "max_size": self.max_size,
            "hits": self.hits,
            "misses": self.misses,
            "bypasses": self.bypasses,
            "hit_rate": round(hit_rate, 4),
        }
