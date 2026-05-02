"""STO-82 / STO-87: 四大指数实时行情 API。

直接调 akshare.stock_zh_index_spot_em() 取实时快照。
bootstrap() 清代理保证 jd 服务器上能连国内源;10s 硬超时防挂死。

STO-87 加固:
- 远端失败 1 次重试(间隔 1s)
- 成功响应入 Redis cache 30s,远端失败时走 cache
- cache miss + 远端失败 → 200 + items=[] + warning(不再 502)
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any

from fastapi import APIRouter

from backend.cache import cache_get, cache_set
from backend.core.startup import bootstrap

bootstrap()

logger = logging.getLogger(__name__)
router = APIRouter()

_CACHE_KEY = "index:snapshot:em"
_CACHE_TTL = 30
_RETRY_DELAY = 1.0
_FETCH_TIMEOUT = 4.0  # 节假日/周末 AkShare 会持续挂起，4s 快速失败让前端在 5s 内得到降级响应

_TARGET: dict[str, dict[str, Any]] = {
    "sh000001": {"code": "sh000001", "name": "上证指数"},
    "sz399001": {"code": "sz399001", "name": "深证成指"},
    "sz399006": {"code": "sz399006", "name": "创业板指"},
    "sh000300": {"code": "sh000300", "name": "沪深300"},
}


def _run_with_timeout(fn, timeout: float):
    """在子线程中跑 fn,超过 timeout 秒抛 TimeoutError。akshare 不支持 timeout 参数,只能硬切。"""
    result: dict[str, Any] = {}

    def _target():
        try:
            result["value"] = fn()
        except Exception as e:  # pragma: no cover - 走到这里说明 akshare 抛了
            result["error"] = e

    t = threading.Thread(target=_target, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        raise TimeoutError(f"akshare 调用超过 {timeout}s")
    if "error" in result:
        raise result["error"]
    return result.get("value")


def _fetch_index_spot_once() -> dict:
    """单次调 akshare,失败抛异常。"""
    import akshare as ak

    df = _run_with_timeout(ak.stock_zh_index_spot_em, timeout=_FETCH_TIMEOUT)
    if df is None or df.empty:
        return {"items": []}

    rename_map = {
        "代码": "code",
        "名称": "name",
        "最新价": "latest",
        "涨跌额": "change",
        "涨跌幅": "pct",
    }
    df = df.rename(columns=rename_map)
    items = []
    for _, row in df.iterrows():
        code = str(row.get("code") or "").lower()
        if code in _TARGET:
            try:
                items.append({
                    "code": code,
                    "name": _TARGET[code]["name"],
                    "latest": float(row.get("latest") or 0),
                    "change": float(row.get("change") or 0),
                    "pct": float(row.get("pct") or 0),
                })
            except (TypeError, ValueError):
                continue
    order = ["sh000001", "sz399001", "sz399006", "sh000300"]
    items.sort(key=lambda x: order.index(x["code"]) if x["code"] in order else 99)
    return {"items": items}


def _fetch_with_retry() -> dict:
    """试 2 次(初次 + 1 次重试,间隔 1s),都失败抛最后一次异常。"""
    last_err: Exception | None = None
    for attempt in range(2):
        try:
            return _fetch_index_spot_once()
        except Exception as e:  # noqa: BLE001
            last_err = e
            logger.warning("[api/index] attempt %d failed: %s", attempt + 1, e)
            if attempt == 0:
                time.sleep(_RETRY_DELAY)
    assert last_err is not None
    raise last_err




@router.get("/snapshot")
def get_index_snapshot():
    """GET /api/index/snapshot —— 四大指数实时行情快照。

    成功:``{"items": [...]}`` (30s 缓存)
    远端瞬断 + cache 命中:返回缓存值,附 ``warning`` 字段提示是缓存。
    远端失败 + cache miss:返回 ``{"items": [], "warning": "..."}`` (200,不再 502)。
    """
    try:
        result = _fetch_with_retry()
    except Exception as e:  # noqa: BLE001
        logger.warning("[api/index] all attempts failed: %s", e)
        cached = cache_get(_CACHE_KEY)
        if cached and cached.get("items"):
            return {**cached, "warning": f"远端失败,返回缓存: {e}"}
        return {"items": [], "warning": f"指数数据源不可用: {e}"}

    cache_set(_CACHE_KEY, result, ttl=_CACHE_TTL)
    return result
