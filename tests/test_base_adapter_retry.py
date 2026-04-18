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


# ---------------------------------------------------------------------------
# 硬超时 (2026-04-18 卡死事故回归用例)
# ---------------------------------------------------------------------------
def test_safe_request_timeout_error_is_retryable(adapter, monkeypatch):
    """TimeoutError (硬超时抛出的异常) 必须被判为可重试, 走重试路径后
    返回 None, 不会永久挂起上层调用。

    这是 2026-04-18 事故的回归用例: 当时 akshare 底层 requests 对腾讯源
    TCP recv 永久阻塞, _safe_request 新增的线程级 hard timeout 会主动抛
    TimeoutError, 必须被重试关键字白名单(含 'timeout') 命中。
    """
    monkeypatch.setattr("time.sleep", lambda s: None)
    calls = {"n": 0}

    def f():
        calls["n"] += 1
        raise TimeoutError(
            "_safe_request hard timeout after 60s"
        )

    out = adapter._safe_request(f, max_retries=2)
    assert out is None
    assert calls["n"] == 3  # 1 首次 + 2 重试, 证明走了重试路径


def test_safe_request_returns_value_when_fast(adapter):
    """快速返回时硬超时线程机制不应拦截正常结果。"""
    def f():
        return pd.DataFrame({"a": [1]})

    out = adapter._safe_request(f)
    assert out is not None and len(out) == 1


# ---------------------------------------------------------------------------
# 并发限流 (2026-04-18 多线程采集前置用例)
# ---------------------------------------------------------------------------
def test_rate_limit_is_thread_safe(monkeypatch):
    """多个 worker 同时调 _check_rate_limit 时, 内部计数必须互斥,
    实测 _request_count 应等于调用次数, 不因竞态丢更新或超出 rate_limit."""
    from concurrent.futures import ThreadPoolExecutor

    a = _Dummy()
    a.rate_limit = 10_000  # 设得很大, 避免真触发 sleep
    a.circuit_breaker.force_close()

    # 让 sleep 变 no-op, 节省测试时间
    monkeypatch.setattr("time.sleep", lambda s: None)

    N = 200
    with ThreadPoolExecutor(max_workers=8) as ex:
        list(ex.map(lambda _: a._check_rate_limit(), range(N)))

    # 单次窗口内请求全部落同一分钟窗口, 计数必须等于 N
    assert a._request_count == N


def test_safe_request_hard_timeout_actually_kills_blocking_call(
    adapter, monkeypatch
):
    """集成验证: func 真实阻塞超过硬超时时, _safe_request 整体能在有限时间
    内返回 (而不是卡死测试进程)。

    为让测试快速结束, monkeypatch hard_timeout 相关常量 —— 但因该值在
    `_safe_request` 内是局部变量, 改为直接 monkeypatch adapter.timeout=1
    (hard_timeout = max(1*2, 30) = 30s), 这对 CI 来说偏长, 因此也
    patch time.sleep 把重试间隔归零. 整个调用预期在 ~30s 内结束.

    为了不让 CI 真等 30s, 这里我们用一个取巧: 把 threading 模块的
    `Thread.start` patch 成立即标记完成但不真跑 target, 并在 join 后
    直接让 is_alive() 返回 True 一次。
    """
    import threading as _th

    monkeypatch.setattr("time.sleep", lambda s: None)

    # 构造一个 "join 后仍 alive" 的假 Thread
    class FakeThread:
        def __init__(self, target, daemon=None):
            self._target = target

        def start(self):
            pass  # 不真跑, 模拟线程被卡住

        def join(self, timeout=None):
            pass  # 立即返回

        def is_alive(self):
            return True  # 总是 alive → 触发 TimeoutError

    monkeypatch.setattr(_th, "Thread", FakeThread)

    def f():
        raise AssertionError("不应被真正调用")

    out = adapter._safe_request(f, max_retries=1)
    assert out is None  # 超时两次后返回 None, 不挂起
