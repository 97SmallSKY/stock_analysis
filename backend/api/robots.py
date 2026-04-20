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

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from backend.core.auth import require_api_key
from backend.core.rate_limit import default_limit, write_limit

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


def _snapshot_to_dict(snap, engine=None) -> dict:
    d = {
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
    if engine is not None:
        d["enabled"] = bool(getattr(engine, "enabled", False))
        # P16.3a: 暴露策略绑定
        d["strategy_name"] = getattr(engine, "strategy_name", None)
        d["strategy_params"] = getattr(engine, "strategy_params", {}) or {}
    return d


@router.post("/{robot_id}/pause", dependencies=[Depends(require_api_key)])
@write_limit
def pause_robot(robot_id: int, request: Request):
    eng = _get_engine(request, robot_id)
    snap = eng.pause()
    logger.info(f"[API] robot {robot_id} paused -> {snap.state.value}")
    return {"ok": True, "snapshot": _snapshot_to_dict(snap, eng)}


@router.post("/{robot_id}/resume", dependencies=[Depends(require_api_key)])
@write_limit
def resume_robot(robot_id: int, request: Request):
    eng = _get_engine(request, robot_id)
    snap = eng.resume()
    logger.info(f"[API] robot {robot_id} resumed -> {snap.state.value}")
    return {"ok": True, "snapshot": _snapshot_to_dict(snap, eng)}


# W12-1 灰度开关
@router.post("/{robot_id}/enable", dependencies=[Depends(require_api_key)])
@write_limit
def enable_robot(robot_id: int, request: Request):
    eng = _get_engine(request, robot_id)
    eng.enable()
    logger.info(f"[API] robot {robot_id} enabled")
    return {"ok": True, "snapshot": _snapshot_to_dict(eng.snapshot, eng)}


@router.post("/{robot_id}/disable", dependencies=[Depends(require_api_key)])
@write_limit
def disable_robot(robot_id: int, request: Request):
    eng = _get_engine(request, robot_id)
    eng.disable()
    logger.info(f"[API] robot {robot_id} disabled")
    return {"ok": True, "snapshot": _snapshot_to_dict(eng.snapshot, eng)}


@router.get("/{robot_id}/status")
@default_limit
def robot_status(robot_id: int, request: Request):
    eng = _get_engine(request, robot_id)
    return {"ok": True, "snapshot": _snapshot_to_dict(eng.snapshot, eng)}


@router.get("")
@default_limit
def list_robots(request: Request):
    reg = _get_registry(request)
    return {
        "robots": [
            _snapshot_to_dict(eng.snapshot, eng) for eng in reg.values()
        ],
    }


# ---------------------------------------------------------------
# P12: 手动添加机器人 (仅内存注册, 不落库; 供对话框即时创建)
# ---------------------------------------------------------------

class CreateRobotReq(BaseModel):
    robot_id: Optional[int] = Field(None, ge=1, le=10**8, description="留空自动分配")
    cash: float = Field(1_000_000.0, ge=0)
    max_positions: int = Field(5, ge=1, le=20)
    per_position_pct: float = Field(0.20, gt=0, le=1.0)
    enabled: bool = False
    # P16.3a: 机器人采纳的策略
    strategy_name: Optional[str] = Field(None, description="策略名 (见 GET /strategies); None 表示不绑定")
    strategy_params: dict = Field(default_factory=dict, description="策略参数 override, 合并到 DEFAULT_PARAMS")


def _alloc_robot_id(reg: dict) -> int:
    return (max(reg.keys()) + 1) if reg else 1


@router.post("/create", dependencies=[Depends(require_api_key)])
@write_limit
def create_robot(body: CreateRobotReq, request: Request):
    reg = _get_registry(request)
    # 本地 import 规避循环 & 保证 test 可 monkeypatch
    from backend.domain.robot.live_engine import LiveRobotEngine
    from backend.domain.robot.robot import RobotSnapshot
    from backend.domain.robot.state import RobotState
    # P16.3a: 策略实例化
    import backend.domain.strategy.builtins  # noqa: F401 -- 触发装饰器注册
    from backend.domain.strategy.registry import get_strategy

    rid = body.robot_id if body.robot_id is not None else _alloc_robot_id(reg)
    if rid in reg:
        raise HTTPException(409, f"robot {rid} 已存在")

    strat = None
    if body.strategy_name:
        try:
            cls = get_strategy(body.strategy_name)
            strat = cls(params=body.strategy_params or None)
        except KeyError as e:
            raise HTTPException(400, str(e))

    snap = RobotSnapshot(
        robot_id=rid,
        state=RobotState.IDLE,
        cash=body.cash,
        total_assets=body.cash,
        max_positions=body.max_positions,
        per_position_pct=body.per_position_pct,
    )
    eng = LiveRobotEngine(snap, enabled=body.enabled, strategy=strat)
    reg[rid] = eng
    logger.info(
        f"[API] robot {rid} created (cash={body.cash}, enabled={body.enabled}, "
        f"strategy={body.strategy_name})"
    )
    return {"ok": True, "snapshot": _snapshot_to_dict(eng.snapshot, eng)}


# ---------------------------------------------------------------
# P16.3c: 列出可用策略 (供前端下拉)
# ---------------------------------------------------------------


@router.get("/strategies")
@default_limit
def list_strategies_api(request: Request):
    # 本地 import 触发装饰器注册 (registry 是模块级 dict, 需 import 子类所在模块)
    import backend.domain.strategy.builtins  # noqa: F401
    from backend.domain.strategy.registry import _REGISTRY, list_strategies

    names = list_strategies()
    out = []
    for n in names:
        cls = _REGISTRY[n]
        out.append({
            "name": n,
            "category": getattr(cls, "category", ""),
            "description": getattr(cls, "description", ""),
            "default_params": dict(getattr(cls, "DEFAULT_PARAMS", {}) or {}),
        })
    return {"strategies": out}
