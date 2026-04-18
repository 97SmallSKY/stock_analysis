"""W8-4 Redis Pub/Sub 快照广播

两个角色:
  - Publisher: 把一行快照 JSON 推到 channel `snapshot:{ts_code}`,
               由 IntradaySnapshotScheduler 的 publish 回调调用.
  - Subscriber 侧: 由 /ws/snapshot WebSocket 端负责 (api/ws_snapshot.py).

设计:
  - 统一用 settings.REDIS_URL 构造客户端; 支持 inject 客户端, 便于 fakeredis 测试.
  - JSON 字段: {ts_code, ts(ISO), price, pct_change, volume, amount, source}
    datetime/Decimal/np-scalar 都走 _json_default fallback 成字符串/float.
  - publish 出错只 warning, 绝不中断 snapshot job.
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional

logger = logging.getLogger(__name__)

CHANNEL_PREFIX = "snapshot:"


def _json_default(o: Any):
    if isinstance(o, (datetime, date)):
        return o.isoformat()
    if isinstance(o, Decimal):
        return float(o)
    # numpy scalar
    to_item = getattr(o, "item", None)
    if callable(to_item):
        try:
            return to_item()
        except Exception:  # noqa: BLE001
            pass
    return str(o)


def _clean_row(row: dict) -> dict:
    """剔除 NaN/None 之外无关字段, 保留数值/字符串."""
    out = {}
    for k, v in row.items():
        if v is None:
            out[k] = None
            continue
        try:
            import pandas as pd
            if isinstance(v, float) and pd.isna(v):
                out[k] = None
                continue
        except Exception:  # noqa: BLE001
            pass
        out[k] = v
    return out


def channel_for(ts_code: str) -> str:
    return f"{CHANNEL_PREFIX}{ts_code.upper()}"


def code_from_channel(channel: str) -> Optional[str]:
    if not channel:
        return None
    if channel.startswith(CHANNEL_PREFIX):
        return channel[len(CHANNEL_PREFIX):]
    return None


class SnapshotPublisher:
    """把一行快照推到 Redis."""

    def __init__(self, redis_client=None, *, redis_url: Optional[str] = None):
        if redis_client is not None:
            self._r = redis_client
        else:
            import redis
            url = redis_url or "redis://localhost:6379/0"
            self._r = redis.Redis.from_url(url, decode_responses=True)

    def publish(self, ts_code: str, row: dict) -> int:
        """返回收到消息的订阅者数 (Redis PUBLISH 原生语义).

        出错返回 0, 只 warning.
        """
        try:
            payload = json.dumps(_clean_row(row), default=_json_default, ensure_ascii=False)
            return int(self._r.publish(channel_for(ts_code), payload))
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[SnapshotPublisher] publish {ts_code} failed: {e}")
            return 0

    def __call__(self, ts_code: str, row: dict) -> int:
        """便于直接作为 scheduler(publish=...) 回调."""
        return self.publish(ts_code, row)
