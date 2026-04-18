"""tests/test_metrics.py — W12-2

覆盖 backend.core.metrics:
  - /metrics 端点返回 Prometheus 文本, 含主要指标名
  - HTTP middleware: 请求后 http_requests_total / duration 有变化
  - observe_fetch: 写入 data_fetch_total + duration
  - set_cb_state: closed/half_open/open -> 0/0.5/2
  - robot_tick_total / cache_ops_total / snapshot_insert_rows_total 可递增
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from prometheus_client import generate_latest

import backend.core.metrics as M


def make_app():
    app = FastAPI()
    M.setup_metrics(app)
    # 幂等: 再装一次也不该报错
    M.setup_metrics(app)

    @app.get("/ping")
    def ping():
        return {"ok": True}

    @app.get("/boom")
    def boom():
        raise RuntimeError("boom")

    return app


def _read_metrics(client: TestClient) -> str:
    r = client.get("/metrics")
    assert r.status_code == 200
    assert "text/plain" in r.headers.get("content-type", "")
    return r.text


class TestMetricsEndpoint:
    def test_metrics_contains_core_names(self):
        app = make_app()
        client = TestClient(app)
        client.get("/ping")
        body = _read_metrics(client)
        for name in (
            "http_requests_total",
            "http_request_duration_seconds",
            "data_fetch_total",
            "data_fetch_duration_seconds",
            "adapter_cb_state",
            "robot_tick_total",
            "cache_ops_total",
            "snapshot_insert_rows_total",
        ):
            assert name in body

    def test_middleware_counts_requests(self):
        app = make_app()
        client = TestClient(app)
        before = _read_metrics(client)
        client.get("/ping")
        client.get("/ping")
        after = _read_metrics(client)
        # /ping 请求数增加
        def count_ping(body: str) -> int:
            total = 0
            for line in body.splitlines():
                if line.startswith("http_requests_total{") and 'path="/ping"' in line:
                    try:
                        total += int(float(line.split()[-1]))
                    except ValueError:
                        pass
            return total
        assert count_ping(after) - count_ping(before) >= 2

    def test_middleware_records_exception_status(self):
        app = make_app()
        client = TestClient(app, raise_server_exceptions=False)
        client.get("/boom")
        body = _read_metrics(client)
        assert 'status="500"' in body


class TestHelpers:
    def test_observe_fetch(self):
        # 清理前后对比
        app = make_app()
        client = TestClient(app)
        body0 = _read_metrics(client)
        M.observe_fetch("akshare", "DAILY_KLINE", 0.42, True)
        M.observe_fetch("akshare", "DAILY_KLINE", 1.2, False)
        body1 = _read_metrics(client)
        assert 'data_fetch_total{adapter="akshare",category="DAILY_KLINE",ok="1"}' in body1
        assert 'data_fetch_total{adapter="akshare",category="DAILY_KLINE",ok="0"}' in body1
        # ok=1 的计数应当比 body0 多
        assert body1 != body0

    def test_set_cb_state(self):
        app = make_app()
        client = TestClient(app)
        M.set_cb_state("tushare", "closed")
        M.set_cb_state("crawler", "open")
        M.set_cb_state("sina", "half_open")
        body = _read_metrics(client)
        assert 'adapter_cb_state{adapter="tushare"} 0.0' in body
        assert 'adapter_cb_state{adapter="crawler"} 2.0' in body
        assert 'adapter_cb_state{adapter="sina"} 0.5' in body

    def test_counters_increment(self):
        app = make_app()
        client = TestClient(app)
        M.robot_tick_total.labels(robot_id="1", action="BUY").inc()
        M.robot_tick_total.labels(robot_id="1", action="SELL").inc(2)
        M.cache_ops_total.labels(op="get", hit="1").inc()
        M.snapshot_insert_rows_total.inc(100)
        body = _read_metrics(client)
        assert 'robot_tick_total{action="BUY",robot_id="1"} 1.0' in body
        assert 'robot_tick_total{action="SELL",robot_id="1"} 2.0' in body
        assert 'cache_ops_total{hit="1",op="get"} 1.0' in body
        assert "snapshot_insert_rows_total 100.0" in body
