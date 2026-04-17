"""验证 BaseDataAdapter._is_retryable_error / _safe_request 的重试判定。

重点: 只对可重试错误重试,不可重试立即返回 None。
"""

from __future__ import annotations

import pandas as pd
import pytest

from backend.data_gateway.base_adapter import BaseDataAdapter
from backend.data_gateway.category import AdapterName


class _Dummy(BaseDataAdapter):
    """最小实现,仅用于测试基类方法。"""

    def __init__(self):
        super().__init__(name=AdapterName.AKSHARE, rate_limit=9999)
        # 直接标记可用,避免走 initialize()
        self._initialized = True

    def _do_initialize(self) -> bool:
        return True

    def health_check(self) -> bool:
        return True


@pytest.fixture
def adapter():
    a = _Dummy()
    # 确保熔断器通路
    a.circuit_breaker.force_close()
    return a


# ---------------------------------------------------------------------------
# _is_retryable_error
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("msg", [
    "当前系统繁忙，请稍后再试",
    "访问过于频繁",
    "Connection aborted",
    "RemoteDisconnected",
    "HTTP 429 Too Many Requests",
    "ReadTimeout",
    "ProxyError",
    "JSONDecodeError expecting value",
])
def test_retryable_true(msg):
    assert BaseDataAdapter._is_retryable_error(Exception(msg)) is True


@pytest.mark.parametrize("msg", [
    "invalid ts_code",
    "权限不足",
    "参数错误",
    "抱歉，您没有接口访问权限",
])
def test_retryable_false(msg):
    assert BaseDataAdapter._is_retryable_error(Exception(msg)) is False


def test_valueerror_cn_keyword_is_retryable():
    # AKShare 偶尔直接 raise ValueError("当前系统繁忙")
    assert BaseDataAdapter._is_retryable_error(ValueError("当前系统繁忙")) is True
    assert BaseDataAdapter._is_retryable_error(ValueError("ts_code 格式错误")) is False


# ---------------------------------------------------------------------------
# _safe_request —— 通过 monkeypatch 不用真等
# ---------------------------------------------------------------------------

def test_safe_request_success(adapter, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    df = pd.DataFrame([{"a": 1}])
    out = adapter._safe_request(lambda: df)
    assert out is df


def test_safe_request_retryable_then_success(adapter, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    calls = {"n": 0}

    def f():
        calls["n"] += 1
        if calls["n"] < 2:
            raise ConnectionError("Connection aborted")
        return pd.DataFrame([{"x": 1}])

    out = adapter._safe_request(f, max_retries=2)
    assert out is not None
    assert calls["n"] == 2  # 第 1 次失败,第 2 次成功


def test_safe_request_non_retryable_fails_fast(adapter, monkeypatch):
    """不可重试错误应在首次就失败,不再等待/重试。"""
    slept: list = []
    monkeypatch.setattr("time.sleep", lambda s: slept.append(s))
    calls = {"n": 0}

    def f():
        calls["n"] += 1
        raise ValueError("invalid parameter xx")

    out = adapter._safe_request(f, max_retries=3)
    assert out is None
    assert calls["n"] == 1  # 只调 1 次
    assert slept == []  # 从未 sleep


def test_safe_request_retryable_exhaust(adapter, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    calls = {"n": 0}

    def f():
        calls["n"] += 1
        raise ConnectionError("Connection aborted")

    out = adapter._safe_request(f, max_retries=2)
    assert out is None
    assert calls["n"] == 3  # 1 首次 + 2 重试
