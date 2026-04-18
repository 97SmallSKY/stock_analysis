"""/api/data-sources/* API 测试 (用 TestClient + mock manager, 不依赖真实 DB/外网)。"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.data_sources import router as ds_router
from backend.data_gateway.category import AdapterName, DataCategory


# --------------------------------------------------------------------
# 测试用 app: 只挂 data-sources 路由, 绕开 main.py 的 bootstrap
# --------------------------------------------------------------------


def _make_app_with_mock_manager():
    app = FastAPI()
    app.include_router(ds_router, prefix="/api/data-sources")

    # Mock manager + router + adapters
    mgr = MagicMock()
    router = MagicMock()

    # 4 个 mock adapter
    def _make_ad(name_enum, initialized=True, cb_state="CLOSED"):
        ad = MagicMock()
        ad.name = name_enum
        ad.initialized = initialized
        ad.circuit_breaker.get_status.return_value = {
            "state": cb_state,
            "failure_count": 0,
        }
        ad.initialize.return_value = True
        return ad

    router._adapters = {
        AdapterName.AKSHARE: _make_ad(AdapterName.AKSHARE),
        AdapterName.SINA:    _make_ad(AdapterName.SINA),
        AdapterName.TUSHARE: _make_ad(
            AdapterName.TUSHARE, initialized=False
        ),
    }
    router._priority_chains = {
        DataCategory.DAILY_KLINE: [
            AdapterName.AKSHARE, AdapterName.SINA
        ],
    }
    mgr._router = router
    app.state.manager = mgr
    app.state.health_checker = None  # 测试里不需要健康快照
    return app, mgr


@pytest.fixture
def client_and_mgr():
    app, mgr = _make_app_with_mock_manager()
    return TestClient(app), mgr


# --------------------------------------------------------------------
# GET /status
# --------------------------------------------------------------------


class TestStatus:
    def test_status_returns_all_adapters(self, client_and_mgr):
        client, _ = client_and_mgr
        r = client.get("/api/data-sources/status")
        assert r.status_code == 200
        body = r.json()
        names = [a["name"] for a in body["adapters"]]
        assert set(names) == {"akshare", "sina", "tushare"}
        # 优先级链
        assert body["priority_chains"]["DAILY_KLINE"] == [
            "akshare", "sina",
        ]

    def test_status_reports_cb_state(self, client_and_mgr):
        client, mgr = client_and_mgr
        mgr._router._adapters[
            AdapterName.AKSHARE
        ].circuit_breaker.get_status.return_value = {
            "state": "OPEN", "failure_count": 5,
        }
        r = client.get("/api/data-sources/status")
        ak = next(a for a in r.json()["adapters"] if a["name"] == "akshare")
        assert ak["circuit"]["state"] == "OPEN"
        assert ak["circuit"]["failure_count"] == 5

    def test_status_without_manager_returns_500(self):
        app = FastAPI()
        app.include_router(ds_router, prefix="/api/data-sources")
        client = TestClient(app)
        r = client.get("/api/data-sources/status")
        assert r.status_code == 500


# --------------------------------------------------------------------
# POST /priority
# --------------------------------------------------------------------


class TestPriority:
    def test_set_valid_chain(self, client_and_mgr):
        client, mgr = client_and_mgr
        r = client.post(
            "/api/data-sources/priority",
            json={"category": "DAILY_KLINE",
                  "chain": ["sina", "akshare"]},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert body["chain"] == ["sina", "akshare"]
        mgr._router.set_priority_chain.assert_called_once()

    def test_unknown_category_400(self, client_and_mgr):
        client, _ = client_and_mgr
        r = client.post(
            "/api/data-sources/priority",
            json={"category": "NOPE", "chain": ["akshare"]},
        )
        assert r.status_code == 400

    def test_unknown_adapter_400(self, client_and_mgr):
        client, _ = client_and_mgr
        r = client.post(
            "/api/data-sources/priority",
            json={"category": "DAILY_KLINE",
                  "chain": ["wtf_adapter"]},
        )
        assert r.status_code == 400

    def test_body_missing_field_422(self, client_and_mgr):
        client, _ = client_and_mgr
        r = client.post(
            "/api/data-sources/priority",
            json={"category": "DAILY_KLINE"},  # 缺 chain
        )
        assert r.status_code == 422  # pydantic validation


# --------------------------------------------------------------------
# POST /adapter/{name}/retry
# --------------------------------------------------------------------


class TestRetry:
    def test_retry_calls_initialize(self, client_and_mgr):
        client, mgr = client_and_mgr
        ad = mgr._router._adapters[AdapterName.TUSHARE]
        ad.initialized = False
        ad.initialize.return_value = True
        ad.initialize.side_effect = lambda: setattr(ad, "initialized", True) or True
        r = client.post("/api/data-sources/adapter/tushare/retry")
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert body["name"] == "tushare"

    def test_retry_unknown_400(self, client_and_mgr):
        client, _ = client_and_mgr
        r = client.post("/api/data-sources/adapter/wtf/retry")
        assert r.status_code == 400

    def test_retry_unregistered_404(self, client_and_mgr):
        client, mgr = client_and_mgr
        # 拿掉 crawler
        assert AdapterName.CRAWLER not in mgr._router._adapters
        r = client.post("/api/data-sources/adapter/crawler/retry")
        assert r.status_code == 404


# --------------------------------------------------------------------
# POST /adapter/{name}/circuit/{action}
# --------------------------------------------------------------------


class TestCircuit:
    def test_close_calls_force_close(self, client_and_mgr):
        client, mgr = client_and_mgr
        r = client.post("/api/data-sources/adapter/akshare/circuit/close")
        assert r.status_code == 200
        assert r.json()["action"] == "close"
        mgr._router._adapters[
            AdapterName.AKSHARE
        ].circuit_breaker.force_close.assert_called_once()

    def test_open_calls_force_open(self, client_and_mgr):
        client, mgr = client_and_mgr
        r = client.post("/api/data-sources/adapter/akshare/circuit/open")
        assert r.status_code == 200
        assert r.json()["action"] == "open"
        mgr._router._adapters[
            AdapterName.AKSHARE
        ].circuit_breaker.force_open.assert_called_once()

    def test_invalid_action_400(self, client_and_mgr):
        client, _ = client_and_mgr
        r = client.post("/api/data-sources/adapter/akshare/circuit/boom")
        assert r.status_code == 400

    def test_unknown_adapter_400(self, client_and_mgr):
        client, _ = client_and_mgr
        r = client.post("/api/data-sources/adapter/wtf/circuit/close")
        assert r.status_code == 400
