"""W12-2 Prometheus 指标

统一指标注册 + FastAPI middleware + /metrics endpoint.

导出指标 (所有指标都可 Grafana 直接渲染):
  http_requests_total{method,path,status}          Counter
  http_request_duration_seconds{method,path}       Histogram (默认 buckets)
  data_fetch_duration_seconds{adapter,category}    Histogram
  data_fetch_total{adapter,category,ok}            Counter
  adapter_cb_state{adapter}                        Gauge  0=closed/1=half_open/2=open
  robot_tick_total{robot_id,action}                Counter  (BUY/SELL/skipped)
  cache_ops_total{op,hit}                          Counter  op=get/set/del; hit=1/0
  snapshot_insert_rows_total                       Counter

使用:
    from backend.core.metrics import (
        setup_metrics, observe_fetch,
        adapter_cb_state, robot_tick_total,
        cache_ops_total, snapshot_insert_rows_total,
    )
    setup_metrics(app)            # 注册 middleware + /metrics

测试点: 所有公共 API 输入都是 str 标签 + float, 便于单测直接断言.
"""
from __future__ import annotations

import time
from typing import TYPE_CHECKING, Optional

from prometheus_client import (
    CONTENT_TYPE_LATEST, CollectorRegistry,
    Counter, Gauge, Histogram, generate_latest,
)

if TYPE_CHECKING:
    from fastapi import FastAPI

# 使用私有 registry, 避免 pytest 重复 import 重复注册报错
REGISTRY = CollectorRegistry()

# -----------------------------------------------------------------
# 指标定义
# -----------------------------------------------------------------

http_requests_total = Counter(
    "http_requests_total", "HTTP 请求数",
    labelnames=("method", "path", "status"),
    registry=REGISTRY,
)
http_request_duration_seconds = Histogram(
    "http_request_duration_seconds", "HTTP 请求耗时(秒)",
    labelnames=("method", "path"),
    registry=REGISTRY,
)

data_fetch_total = Counter(
    "data_fetch_total", "Router fetch 调用数",
    labelnames=("adapter", "category", "ok"),
    registry=REGISTRY,
)
data_fetch_duration_seconds = Histogram(
    "data_fetch_duration_seconds", "Router fetch 耗时(秒)",
    labelnames=("adapter", "category"),
    registry=REGISTRY,
)

adapter_cb_state = Gauge(
    "adapter_cb_state", "适配器熔断状态 0=closed 1=half_open 2=open",
    labelnames=("adapter",),
    registry=REGISTRY,
)

robot_tick_total = Counter(
    "robot_tick_total", "机器人 tick 决策次数",
    labelnames=("robot_id", "action"),     # action = BUY / SELL / skipped
    registry=REGISTRY,
)

cache_ops_total = Counter(
    "cache_ops_total", "缓存操作数",
    labelnames=("op", "hit"),               # op = get/set/del; hit = 1/0 (set/del 固定 0)
    registry=REGISTRY,
)

snapshot_insert_rows_total = Counter(
    "snapshot_insert_rows_total",
    "market_snapshots 批量入库行数",
    registry=REGISTRY,
)


# -----------------------------------------------------------------
# 工具: observe_fetch(适配器侧可直接调)
# -----------------------------------------------------------------


def observe_fetch(
    adapter: str, category: str, duration_sec: float, ok: bool,
) -> None:
    data_fetch_total.labels(adapter=adapter, category=category,
                            ok="1" if ok else "0").inc()
    try:
        data_fetch_duration_seconds.labels(
            adapter=adapter, category=category,
        ).observe(float(duration_sec))
    except Exception:  # noqa: BLE001
        pass


_CB_MAP = {"closed": 0.0, "half_open": 0.5, "half-open": 0.5, "open": 2.0}


def set_cb_state(adapter: str, state: str) -> None:
    v = _CB_MAP.get((state or "").lower(), 0.0)
    adapter_cb_state.labels(adapter=adapter).set(v)


# -----------------------------------------------------------------
# FastAPI middleware + /metrics
# -----------------------------------------------------------------


def setup_metrics(app: "FastAPI") -> None:
    """注册 HTTP 中间件 + /metrics 端点. 幂等."""
    if getattr(app.state, "_metrics_installed", False):
        return
    app.state._metrics_installed = True

    from fastapi import Response

    @app.middleware("http")
    async def _metrics_mw(request, call_next):
        t0 = time.perf_counter()
        # 避免 /metrics 调用自身影响标签基数: 对 /metrics 不记
        path = request.url.path
        if path == "/metrics":
            return await call_next(request)
        try:
            response = await call_next(request)
            status = response.status_code
        except Exception:
            status = 500
            raise
        finally:
            elapsed = time.perf_counter() - t0
            # 降低基数: 把 /api/xx/{id} 类动态段简化成 pattern 名
            route = getattr(
                request.scope.get("route"), "path", path,
            ) if request.scope.get("route") else path
            http_requests_total.labels(
                method=request.method, path=route, status=str(status),
            ).inc()
            http_request_duration_seconds.labels(
                method=request.method, path=route,
            ).observe(elapsed)
        return response

    @app.get("/metrics", include_in_schema=False)
    def metrics():
        data = generate_latest(REGISTRY)
        return Response(content=data, media_type=CONTENT_TYPE_LATEST)
