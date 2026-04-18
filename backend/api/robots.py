"""/api/robots/* — 盘中机器人控制 (W9)

端点:
    POST /api/robots/{id}/pause       -> 暂停 (RUNNING->PAUSED; 幂等)
    POST /api/robots/{id}/resume      -> 恢复 (PAUSED|IDLE->RUNNING; 幂等)
    GET  /api/robots/{id}/status      -> 当前 state + cash + 持仓列表

依赖:
    app.state.robot_registry 是一个 dict[int, LiveRobotEngine] 的可变引用,
    由 bootstrap (或测试) 在启动时注入. 这一层 HTTP 只做状态切换,
    不直接持有 engine 对象 (便于替换成 Celery/Redis 存储).
"""
from __future__ import annotations

import logging
from dataclasses import asdict
from typing import Optional

from fastapi import APIRouter, HTTPException, Request

logger = logging.getLogger(__name__)
router = APIRouter()


def _get_registry(request: Request) -> dict:
    reg = getattr(request.app.state, "robot_registry", None)
    if reg is None:
        raise HTTPException(500, "robot_registry 未初始化")
    return reg


def _get_engine(request: Request, robot_id: int):
    reg = _get_registry(request)
    eng = reg.get(robot_id)
    if eng is None:
        raise HTTPException(404, f"robot {robot_id} 不存在")
    return eng


def _snapshot_to_dict(snap) -> dict:
    return {
        "robot_id": snap.robot_id,
        "state": snap.state.value if hasattr(snap.state, "value") else str(snap.state),
        "cash": snap.cash,
        "total_assets": snap.total_assets,
        "daily_pnl_pct": snap.daily_pnl_pct,
        "position_count": snap.position_count(),
        "positions": [
            {
                "ts_code": p.ts_code,
                "shares": p.shares,
                "cost_price": p.cost_price,
                "current_price": p.current_price,
                "market_value": p.market_value,
                "open_date": p.open_date.isoformat() if p.open_date else None,
            }
            for p in snap.positions
        ],
    }


@router.post("/{robot_id}/pause")
def pause_robot(robot_id: int, request: Request):
    eng = _get_engine(request, robot_id)
    snap = eng.pause()
    logger.info(f"[API] robot {robot_id} paused -> {snap.state.value}")
    return {"ok": True, "snapshot": _snapshot_to_dict(snap)}


@router.post("/{robot_id}/resume")
def resume_robot(robot_id: int, request: Request):
    eng = _get_engine(request, robot_id)
    snap = eng.resume()
    logger.info(f"[API] robot {robot_id} resumed -> {snap.state.value}")
    return {"ok": True, "snapshot": _snapshot_to_dict(snap)}


@router.get("/{robot_id}/status")
def robot_status(robot_id: int, request: Request):
    eng = _get_engine(request, robot_id)
    return {"ok": True, "snapshot": _snapshot_to_dict(eng.snapshot)}


@router.get("")
def list_robots(request: Request):
    reg = _get_registry(request)
    return {
        "robots": [
            _snapshot_to_dict(eng.snapshot) for eng in reg.values()
        ],
    }
