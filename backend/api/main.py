"""FastAPI 应用入口。

用法:
    uvicorn backend.api.main:app --reload --port 8000
"""
from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    from backend.core.startup import bootstrap

    bootstrap()

    app = FastAPI(
        title="Stock Analysis API",
        version="0.3.0",  # W3 完成
        description="龙虎榜量化交易分析系统 - 数据网关 + 评分 + 策略",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 初始化数据源管理器(进程内单例)
    from backend.data_gateway.manager import DataSourceManager
    from backend.config import get_settings

    s = get_settings()
    DataSourceManager.reset_instance()
    mgr = DataSourceManager.get_instance()
    mgr.initialize({
        "tushare": {"token": s.ds.tushare_token},
    })
    app.state.manager = mgr

    # 启动 HealthChecker + FetchAudit(生产推荐打开)
    from backend.data_gateway.health_checker import HealthChecker
    from backend.data_gateway.fetch_audit import FetchAudit

    hc = HealthChecker(mgr, interval_sec=300)
    hc.start()
    app.state.health_checker = hc

    audit = FetchAudit.get_instance()
    audit.start()
    app.state.fetch_audit = audit

    # 健康检查
    @app.get("/healthz")
    def healthz():
        return {"status": "ok", "version": app.version}

    # 路由
    from backend.api.data_sources import router as ds_router
    app.include_router(ds_router, prefix="/api/data-sources",
                       tags=["data-sources"])

    @app.on_event("shutdown")
    def _shutdown():
        hc.stop()
        audit.stop()

    return app


app = create_app()
