"""backend/core/startup 行为单测。

重点验证:默认不清代理(P0-4 变更);仅在 CLEAR_PROXY_ON_STARTUP=1 时清。
"""

import os

import pytest

from backend.core import startup


@pytest.fixture
def clean_env(monkeypatch):
    """确保测试前后环境变量干净可控。"""
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
              "ALL_PROXY", "all_proxy", "NO_PROXY", "no_proxy",
              "CLEAR_PROXY_ON_STARTUP"):
        monkeypatch.delenv(k, raising=False)
    yield


def test_bootstrap_default_preserves_proxy(clean_env, monkeypatch):
    """默认行为:不清代理,环境值原样保留。"""
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:10809")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:10809")

    startup.bootstrap()

    assert os.environ.get("HTTP_PROXY") == "http://127.0.0.1:10809"
    assert os.environ.get("HTTPS_PROXY") == "http://127.0.0.1:10809"
    # 默认也不应强制 NO_PROXY=*
    assert os.environ.get("NO_PROXY", "") != "*"


def test_bootstrap_clears_when_flag_on(clean_env, monkeypatch):
    """CLEAR_PROXY_ON_STARTUP=1 时,代理被清空且 NO_PROXY=*。"""
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:10809")
    monkeypatch.setenv("ALL_PROXY", "socks5://127.0.0.1:10808")
    monkeypatch.setenv("CLEAR_PROXY_ON_STARTUP", "1")

    startup.bootstrap()

    assert "HTTP_PROXY" not in os.environ
    assert "ALL_PROXY" not in os.environ
    assert os.environ["NO_PROXY"] == "*"


def test_clear_outbound_proxy_env_direct(clean_env, monkeypatch):
    """直接调 clear_outbound_proxy_env 仍保留为公开 API。"""
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:10809")
    startup.clear_outbound_proxy_env()
    assert "http_proxy" not in os.environ
    assert os.environ["no_proxy"] == "*"
