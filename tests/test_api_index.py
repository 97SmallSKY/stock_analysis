"""STO-87 /api/index/snapshot 重试 + cache + 降级测试。

覆盖 4 个 case:
- 一次成功:正常返回,写 cache
- 首次失败 + 重试成功:返回数据
- 双次失败 + cache 命中:返回缓存值 + warning
- 双次失败 + cache miss:返回 items=[] + warning(200,不再 502)
"""
from __future__ import annotations

from unittest.mock import patch

import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api import index as index_api
from backend.cache import RedisCache, set_default_cache


class _FakeRedis:
    """内存 dict 模拟 redis 客户端,够覆盖 RedisCache.get/set。"""

    def __init__(self):
        self._d: dict[str, str] = {}

    def get(self, k):
        return self._d.get(k)

    def set(self, k, v, ex=None):
        self._d[k] = v
        return True

    def delete(self, *keys):
        n = 0
        for k in keys:
            if k in self._d:
                del self._d[k]
                n += 1
        return n


@pytest.fixture
def client_with_cache():
    """把 fake redis 注入 default cache,加速重试不真睡 1s。"""
    cache = RedisCache(_FakeRedis(), prefix="test:")
    set_default_cache(cache)
    app = FastAPI()
    app.include_router(index_api.router, prefix="/api/index")
    with patch.object(index_api, "_RETRY_DELAY", 0.0):
        yield TestClient(app), cache
    set_default_cache(None)


def _sample_df():
    """构造 stock_zh_index_spot_em 返回值的 mock(中文列名 + 四大指数)。"""
    return pd.DataFrame([
        {"代码": "sh000001", "名称": "上证指数", "最新价": 3000.0, "涨跌额": 5.0, "涨跌幅": 0.17},
        {"代码": "sz399001", "名称": "深证成指", "最新价": 9000.0, "涨跌额": -10.0, "涨跌幅": -0.11},
        {"代码": "sz399006", "名称": "创业板指", "最新价": 1800.0, "涨跌额": 2.0, "涨跌幅": 0.11},
        {"代码": "sh000300", "名称": "沪深300", "最新价": 3500.0, "涨跌额": 1.0, "涨跌幅": 0.03},
        {"代码": "sh000016", "名称": "上证50", "最新价": 2500.0, "涨跌额": 0.0, "涨跌幅": 0.0},
    ])


def test_snapshot_success_writes_cache(client_with_cache):
    """一次成功:200,4 条 item,cache 写入。"""
    client, cache = client_with_cache
    with patch.object(index_api, "_run_with_timeout", return_value=_sample_df()):
        r = client.get("/api/index/snapshot")
    assert r.status_code == 200
    body = r.json()
    assert "warning" not in body
    codes = [it["code"] for it in body["items"]]
    assert codes == ["sh000001", "sz399001", "sz399006", "sh000300"]
    # cache 应已写入
    cached = cache.get(index_api._CACHE_KEY)
    assert cached is not None
    assert len(cached["items"]) == 4


def test_snapshot_retry_succeeds_on_second_attempt(client_with_cache):
    """首次抛 ConnectionError,第 2 次返回 df → 200。"""
    client, _ = client_with_cache
    calls = {"n": 0}

    def flaky(_fn, timeout):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ConnectionError("reset by peer")
        return _sample_df()

    with patch.object(index_api, "_run_with_timeout", side_effect=flaky):
        r = client.get("/api/index/snapshot")
    assert r.status_code == 200
    assert calls["n"] == 2
    assert len(r.json()["items"]) == 4


def test_snapshot_falls_back_to_cache_on_total_failure(client_with_cache):
    """双次失败但 cache 有值 → 200 + warning + 缓存数据。"""
    client, cache = client_with_cache
    cache.set(index_api._CACHE_KEY, {"items": [{"code": "sh000001", "name": "上证指数",
                                                  "latest": 2999.0, "change": 0.0, "pct": 0.0}]},
              ttl=30)
    with patch.object(index_api, "_run_with_timeout", side_effect=ConnectionError("down")):
        r = client.get("/api/index/snapshot")
    assert r.status_code == 200
    body = r.json()
    assert "warning" in body
    assert "远端失败" in body["warning"]
    assert len(body["items"]) == 1
    assert body["items"][0]["latest"] == 2999.0


def test_snapshot_returns_empty_with_warning_when_cache_miss(client_with_cache):
    """双次失败 + cache miss → 200 + items=[] + warning(不再 502)。"""
    client, _ = client_with_cache
    with patch.object(index_api, "_run_with_timeout", side_effect=ConnectionError("down")):
        r = client.get("/api/index/snapshot")
    assert r.status_code == 200
    body = r.json()
    assert body["items"] == []
    assert "warning" in body
    assert "不可用" in body["warning"]
