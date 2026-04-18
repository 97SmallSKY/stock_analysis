"""/api/data-sources/* — 适配器管理接口

端点:
    GET  /status
    POST /priority         { category, chain: [adapter_name, ...] }
    POST /adapter/{name}/retry       —— 重初始化
    POST /adapter/{name}/circuit/close
    POST /adapter/{name}/circuit/open
"""
from __future__ import annotations

import logging
from typing import List

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from backend.data_gateway.category import AdapterName, DataCategory

logger = logging.getLogger(__name__)
router = APIRouter()


# --------------------------------------------------------------------
# 帮助函数
# --------------------------------------------------------------------


def _get_manager(request: Request):
    mgr = getattr(request.app.state, "manager", None)
    if mgr is None:
        raise HTTPException(500, "DataSourceManager 未初始化")
    return mgr


def _get_hc(request: Request):
    return getattr(request.app.state, "health_checker", None)


# --------------------------------------------------------------------
# 请求/响应模型
# --------------------------------------------------------------------


class PriorityChainReq(BaseModel):
    category: str = Field(..., description="DataCategory 值, 如 DAILY_KLINE")
    chain: List[str] = Field(..., description="按优先级排列的 adapter 名,"
                                              " 如 ['akshare','sina']")


# --------------------------------------------------------------------
# GET /status
# --------------------------------------------------------------------


@router.get("/status")
def get_status(request: Request):
    mgr = _get_manager(request)
    hc = _get_hc(request)

    # 1. 适配器清单 + CB + 最近健康
    router_obj = mgr._router
    adapters_info = []
    health_snap = hc.get_last_snapshot() if hc else {}
    for name, ad in router_obj._adapters.items():
        cb = ad.circuit_breaker.get_status()
        h = health_snap.get(name.value)
        adapters_info.append({
            "name": name.value,
            "initialized": ad.initialized,
            "circuit": cb,
            "last_health": h.to_dict() if h else None,
        })

    # 2. 优先级链
    chains = {
        cat.value: [n.value for n in lst]
        for cat, lst in router_obj._priority_chains.items()
    }

    return {
        "adapters": adapters_info,
        "priority_chains": chains,
    }


# --------------------------------------------------------------------
# POST /priority
# --------------------------------------------------------------------


@router.post("/priority")
def set_priority(req: PriorityChainReq, request: Request):
    mgr = _get_manager(request)
    try:
        cat = DataCategory(req.category)
    except ValueError:
        raise HTTPException(400, f"未知 category: {req.category}")
    try:
        chain = [AdapterName(n) for n in req.chain]
    except ValueError as e:
        raise HTTPException(400, f"未知 adapter: {e}")

    mgr._router.set_priority_chain(cat, chain)
    logger.info(f"[API] 设置 {cat.value} 优先级链 = {req.chain}")
    return {"ok": True, "category": cat.value, "chain": [n.value for n in chain]}


# --------------------------------------------------------------------
# POST /adapter/{name}/retry
# --------------------------------------------------------------------


@router.post("/adapter/{name}/retry")
def retry_adapter(name: str, request: Request):
    mgr = _get_manager(request)
    try:
        adapter_name = AdapterName(name)
    except ValueError:
        raise HTTPException(400, f"未知 adapter: {name}")
    ad = mgr._router._adapters.get(adapter_name)
    if ad is None:
        raise HTTPException(404, f"适配器 {name} 未注册")

    ok = ad.initialize()
    return {
        "ok": ok,
        "name": name,
        "initialized": ad.initialized,
    }


# --------------------------------------------------------------------
# POST /adapter/{name}/circuit/{action}
# --------------------------------------------------------------------


@router.post("/adapter/{name}/circuit/{action}")
def circuit_action(name: str, action: str, request: Request):
    mgr = _get_manager(request)
    try:
        adapter_name = AdapterName(name)
    except ValueError:
        raise HTTPException(400, f"未知 adapter: {name}")
    ad = mgr._router._adapters.get(adapter_name)
    if ad is None:
        raise HTTPException(404, f"适配器 {name} 未注册")

    if action == "close":
        # 强制关闭熔断(让这个源立即恢复可用)
        ad.circuit_breaker.force_close()
    elif action == "open":
        # 手动打开熔断(让这个源暂停使用)
        ad.circuit_breaker.force_open()
    else:
        raise HTTPException(400, f"action 必须是 close/open, 当前: {action}")

    return {
        "ok": True,
        "name": name,
        "action": action,
        "status": ad.circuit_breaker.get_status(),
    }
