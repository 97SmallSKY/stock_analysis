"""tests/test_ws_snapshot.py — W8-4

覆盖:
  pubsub.SnapshotPublisher
    - publish 基本路径 (fakeredis): 收到消息 payload JSON 可解码
    - publish 对 datetime/Decimal/None/NaN 的序列化
    - publish 失败只 warning 返回 0
    - __call__ == publish, 便于做 scheduler 回调
    - channel_for / code_from_channel 工具

  ws_snapshot 端点 (fakeredis + TestClient WebSocket):
    - 连接上之后首条下发 {"op":"hello","codes":[...]} (与 query 一致)
    - Redis PUBLISH snapshot:<code>, 订阅集合内的 code 会被转发;
      不在集合内的不转发.
    - 客户端发 set_codes 后订阅集合即时变更, 并收到 ack.
"""
from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from backend.domain.realtime import pubsub as pub


# --------------------------------------------------------------------
# SnapshotPublisher
# --------------------------------------------------------------------


class TestChannelHelpers:
    def test_channel_for_and_code_from_channel(self):
        assert pub.channel_for("600000.sh") == "snapshot:600000.SH"
        assert pub.code_from_channel("snapshot:600000.SH") == "600000.SH"
        assert pub.code_from_channel("irrelevant") is None
        assert pub.code_from_channel("") is None


class TestSnapshotPublisher:
    def test_publish_basic_json_roundtrip(self):
        import fakeredis
        r = fakeredis.FakeStrictRedis(decode_responses=True)
        # 订阅者
        ps = r.pubsub()
        ps.subscribe("snapshot:A.SZ")
        # 丢掉 subscribe confirmation
        ps.get_message(timeout=0.1)

        p = pub.SnapshotPublisher(redis_client=r)
        row = {
            "ts_code": "A.SZ",
            "ts": datetime(2026, 4, 18, 10, 0, 5),
            "price": Decimal("10.25"),
            "pct_change": 1.5,
            "volume": 1234,
        }
        p.publish("A.SZ", row)

        # 从 fakeredis 读一条
        msg = None
        for _ in range(20):
            m = ps.get_message(ignore_subscribe_messages=True, timeout=0.1)
            if m:
                msg = m
                break
        assert msg is not None
        data = json.loads(msg["data"])
        assert data["ts_code"] == "A.SZ"
        assert data["price"] == 10.25
        assert data["ts"].startswith("2026-04-18T10:00:05")

    def test_publish_handles_nan_and_none(self):
        import numpy as np
        import fakeredis
        r = fakeredis.FakeStrictRedis(decode_responses=True)
        ps = r.pubsub()
        ps.subscribe("snapshot:B.SZ")
        ps.get_message(timeout=0.1)
        p = pub.SnapshotPublisher(redis_client=r)
        row = {"ts_code": "B.SZ", "price": np.nan, "volume": None}
        assert p.publish("B.SZ", row) >= 0
        msg = None
        for _ in range(20):
            m = ps.get_message(ignore_subscribe_messages=True, timeout=0.1)
            if m:
                msg = m
                break
        assert msg is not None
        d = json.loads(msg["data"])
        assert d["price"] is None
        assert d["volume"] is None

    def test_publish_failure_returns_zero(self):
        bad = MagicMock()
        bad.publish.side_effect = RuntimeError("redis down")
        p = pub.SnapshotPublisher(redis_client=bad)
        n = p.publish("A.SZ", {"ts_code": "A.SZ"})
        assert n == 0

    def test_callable_alias(self):
        called = []
        bad = MagicMock()
        bad.publish = lambda ch, data: called.append((ch, data)) or 3
        p = pub.SnapshotPublisher(redis_client=bad)
        assert p("A.SZ", {"x": 1}) == 3
        assert called and called[0][0] == "snapshot:A.SZ"


# --------------------------------------------------------------------
# WebSocket endpoint (fakeredis + TestClient)
# --------------------------------------------------------------------


def _make_app_with_redis(redis_client):
    """构造最小 FastAPI app 仅挂载 /ws/snapshot, 注入 fakeredis."""
    from fastapi import FastAPI
    from backend.api.ws_snapshot import router as ws_router
    app = FastAPI()
    app.state.redis = redis_client
    app.include_router(ws_router, prefix="/ws")
    return app


@pytest.fixture
def fake_redis():
    import fakeredis
    # fakeredis 同步客户端可被 FastAPI run_in_executor 调用
    return fakeredis.FakeStrictRedis(decode_responses=True)


def test_ws_hello_and_filter_by_codes(fake_redis):
    from fastapi.testclient import TestClient
    app = _make_app_with_redis(fake_redis)
    client = TestClient(app)
    with client.websocket_connect("/ws/snapshot?codes=A.SZ,B.SZ") as ws:
        hello = json.loads(ws.receive_text())
        assert hello["op"] == "hello"
        assert set(hello["codes"]) == {"A.SZ", "B.SZ"}

        # 在订阅集合内的
        fake_redis.publish("snapshot:A.SZ", json.dumps({"ts_code": "A.SZ", "price": 1}))
        # 不在集合内的 (不应转发)
        fake_redis.publish("snapshot:C.SH", json.dumps({"ts_code": "C.SH", "price": 9}))
        # 收 A.SZ
        msg = json.loads(ws.receive_text())
        assert msg["ts_code"] == "A.SZ"

        # 再 publish B.SZ, 应该也能收到
        fake_redis.publish("snapshot:B.SZ", json.dumps({"ts_code": "B.SZ", "price": 2}))
        msg2 = json.loads(ws.receive_text())
        assert msg2["ts_code"] == "B.SZ"


def test_ws_set_codes_dynamic_update(fake_redis):
    from fastapi.testclient import TestClient
    app = _make_app_with_redis(fake_redis)
    client = TestClient(app)
    with client.websocket_connect("/ws/snapshot?codes=A.SZ") as ws:
        _ = ws.receive_text()  # hello
        # 变更订阅
        ws.send_text(json.dumps({"op": "set_codes", "codes": ["X.SH"]}))
        ack = json.loads(ws.receive_text())
        assert ack["op"] == "ack"
        assert ack["codes"] == ["X.SH"]
        # 旧 code 不转发
        fake_redis.publish("snapshot:A.SZ", json.dumps({"ts_code": "A.SZ"}))
        # 新 code 转发
        fake_redis.publish("snapshot:X.SH", json.dumps({"ts_code": "X.SH"}))
        msg = json.loads(ws.receive_text())
        assert msg["ts_code"] == "X.SH"


def test_ws_empty_codes_receives_all(fake_redis):
    """不传 codes 表示 \"全部\" (空集合 -> 不过滤)."""
    from fastapi.testclient import TestClient
    app = _make_app_with_redis(fake_redis)
    client = TestClient(app)
    with client.websocket_connect("/ws/snapshot") as ws:
        _ = ws.receive_text()  # hello
        fake_redis.publish("snapshot:Z.SZ", json.dumps({"ts_code": "Z.SZ", "price": 7}))
        msg = json.loads(ws.receive_text())
        assert msg["ts_code"] == "Z.SZ"
