"""W8-4 WebSocket /ws/snapshot — 盘中快照实时推送

用法:
    ws://host/ws/snapshot?codes=600000.SH,000001.SZ

实现:
    后端订阅 Redis PSUBSCRIBE snapshot:*,
    收到消息按 client 订阅集合过滤后 JSON 转发给 WebSocket 客户端.
    客户端可随时发一条 JSON {"op": "set_codes", "codes": [...]}
    变更订阅集合, 连接无需断开.

错误处理:
    - Redis 订阅异常: 关闭 WS (code=1011 internal error)
    - 客户端断开: 优雅取消 pubsub 任务
    - 接收到非 JSON: 忽略
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from backend.domain.realtime.pubsub import (
    CHANNEL_PREFIX, code_from_channel,
)

logger = logging.getLogger(__name__)
router = APIRouter()


def _parse_codes(raw: Optional[str]) -> set[str]:
    if not raw:
        return set()
    return {c.strip().upper() for c in raw.split(",") if c.strip()}


async def _pump_redis_to_ws(
    ws: WebSocket,
    codes_ref: dict,
    redis_client,
):
    """从 Redis PSUBSCRIBE snapshot:* 读消息, 根据 codes_ref["codes"] 过滤转发.

    codes_ref 传引用式 dict, 便于 _ws_receiver 动态修改订阅集合.
    """
    pubsub = redis_client.pubsub()
    try:
        # redis>=5 支持 psubscribe 带 pattern
        try:
            await pubsub.psubscribe(f"{CHANNEL_PREFIX}*")
        except TypeError:
            pubsub.psubscribe(f"{CHANNEL_PREFIX}*")
        loop = asyncio.get_event_loop()
        while True:
            # 兼容同步/异步 redis 客户端, 都走 executor 非阻塞
            msg = await loop.run_in_executor(
                None,
                lambda: pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0),
            )
            if msg is None:
                # 让出 event loop, 顺便让 sender 有机会被取消
                await asyncio.sleep(0)
                continue
            ch = msg.get("channel")
            if isinstance(ch, bytes):
                ch = ch.decode("utf-8", errors="ignore")
            ts_code = code_from_channel(ch or "")
            if not ts_code:
                continue
            subs = codes_ref.get("codes") or set()
            if subs and ts_code not in subs:
                continue
            data = msg.get("data")
            if isinstance(data, bytes):
                data = data.decode("utf-8", errors="ignore")
            if not data:
                continue
            await ws.send_text(data)
    finally:
        try:
            await pubsub.punsubscribe()
        except Exception:  # noqa: BLE001
            try:
                pubsub.punsubscribe()
            except Exception:  # noqa: BLE001
                pass
        try:
            await pubsub.close()
        except Exception:  # noqa: BLE001
            try:
                pubsub.close()
            except Exception:  # noqa: BLE001
                pass


async def _ws_receiver(ws: WebSocket, codes_ref: dict):
    """接收客户端消息, 支持 {\"op\":\"set_codes\",\"codes\":[...]} 动态订阅变更."""
    while True:
        raw = await ws.receive_text()
        try:
            msg = json.loads(raw)
        except Exception:  # noqa: BLE001
            continue
        if not isinstance(msg, dict):
            continue
        if msg.get("op") == "set_codes":
            codes_ref["codes"] = _parse_codes(",".join(msg.get("codes") or []))
            await ws.send_text(json.dumps({
                "op": "ack", "codes": sorted(codes_ref["codes"]),
            }))


def _default_redis_client():
    import redis
    return redis.Redis.from_url("redis://localhost:6379/0", decode_responses=True)


@router.websocket("/snapshot")
async def ws_snapshot(ws: WebSocket):
    """WebSocket endpoint. Redis 客户端优先从 app.state.redis 取."""
    app = ws.app
    redis_client = getattr(app.state, "redis", None) if app else None
    if redis_client is None:
        redis_client = _default_redis_client()

    codes = _parse_codes(ws.query_params.get("codes"))
    codes_ref = {"codes": codes}

    await ws.accept()
    await ws.send_text(json.dumps({"op": "hello", "codes": sorted(codes)}))

    pump_task = asyncio.create_task(_pump_redis_to_ws(ws, codes_ref, redis_client))
    recv_task = asyncio.create_task(_ws_receiver(ws, codes_ref))

    try:
        done, pending = await asyncio.wait(
            {pump_task, recv_task}, return_when=asyncio.FIRST_EXCEPTION,
        )
        for t in pending:
            t.cancel()
        # propagate 第一个异常供日志
        for t in done:
            if t.cancelled():
                continue
            exc = t.exception()
            if exc and not isinstance(exc, WebSocketDisconnect):
                logger.warning(f"[ws_snapshot] task ended: {exc!r}")
    except WebSocketDisconnect:
        pass
    finally:
        for t in (pump_task, recv_task):
            if not t.done():
                t.cancel()
        try:
            await ws.close()
        except Exception:  # noqa: BLE001
            pass
