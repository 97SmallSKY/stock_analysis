"""FastAPI 应用入口。

用法:
    uvicorn backend.api.main:app --reload --port 8000

P5.1b 起,本进程同时服务前端 SPA(frontend/dist),
省掉一层 nginx 反代;对外端口只有 8000。
"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

logger = logging.getLogger(__name__)


def _frontend_dist_dir() -> Path:
    """vite build 产物目录。相对仓库根定位,部署路径随之变化也能用。"""
    # backend/api/main.py -> 仓库根 = parents[2]
    return Path(__file__).resolve().parents[2] / "frontend" / "dist"


def _mount_frontend(app: FastAPI) -> None:
    """把 frontend/dist 挂成 SPA:

    - `/assets/*` 交给 StaticFiles 处理(vite 产出带 hash 的 JS/CSS)
    - `/` 返回 index.html
    - 任意前端路由(Vue Router history 模式)fallback 到 index.html

    若目录不存在(例如只部署后端时),只打一条 warning,不抛错。
    """
    dist = _frontend_dist_dir()
    if not dist.is_dir():
        logger.warning(
            f"[frontend] {dist} 不存在, 跳过 SPA 挂载。"
            f"部署时请先 `cd frontend && npm ci && npm run build`。"
        )
        return

    assets_dir = dist / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

    index_html = dist / "index.html"

    # 根路径
    @app.get("/", include_in_schema=False)
    def _index():
        return FileResponse(str(index_html))

    # SPA fallback:任何未命中 /api /ws /healthz /metrics /assets 的 GET 返回 index.html
    # 注意:FastAPI 路由匹配顺序,API 路由在本函数调用前已全部注册,所以这里放最后
    # 不会吃掉 API 请求。
    @app.get("/{path:path}", include_in_schema=False)
    def _spa_fallback(path: str):
        # 如果直链是已构建出的文件(例如 /favicon.ico),先尝试静态返回
        candidate = dist / path
        if candidate.is_file():
            return FileResponse(str(candidate))
        return FileResponse(str(index_html))

    logger.info(f"[frontend] SPA 已挂载: {dist}")


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

    # W12-2 Prometheus /metrics + HTTP 中间件
    from backend.core.metrics import setup_metrics
    setup_metrics(app)

    # 路由
    from backend.api.data_sources import router as ds_router
    app.include_router(ds_router, prefix="/api/data-sources",
                       tags=["data-sources"])
    from backend.api.backtest import router as bt_router
    app.include_router(bt_router, prefix="/api/backtest",
                       tags=["backtest"])
    from backend.api.lhb import router as lhb_router
    app.include_router(lhb_router, prefix="/api/lhb", tags=["lhb"])
    from backend.api.dashboard import router as dash_router
    app.include_router(dash_router, prefix="/api/dashboard", tags=["dashboard"])
    # W8-4 WebSocket /ws/snapshot — Redis pubsub 转发
    from backend.api.ws_snapshot import router as ws_router
    app.include_router(ws_router, prefix="/ws", tags=["ws"])
    # W9 盘中机器人控制 (registry 占位, live 引擎由 bootstrap 注入)
    if not hasattr(app.state, "robot_registry"):
        app.state.robot_registry = {}
    from backend.api.robots import router as robots_router
    app.include_router(robots_router, prefix="/api/robots", tags=["robots"])
    # W10-2 周报
    from backend.api.reports import router as reports_router
    app.include_router(reports_router, prefix="/api/reports", tags=["reports"])
    # P7.3 K 线只读(StockDrillModal 数据源)
    from backend.api.kline import router as kline_router
    app.include_router(kline_router, prefix="/api/kline", tags=["kline"])
    # P18.3 特性介绍(开发日志):docs/CHANGELOG.md → /api/changelog
    from backend.api.changelog import router as changelog_router
    app.include_router(changelog_router, prefix="/api/changelog", tags=["changelog"])

    # W12-7 运行时集成: scheduler + redis + robot_registry
    from backend.core.runtime import wire_runtime, shutdown_runtime
    wire_runtime(app)

    # P5.1b: 挂载前端 SPA(vite 产物)—— 一端口通吃,不再需要 nginx 反代。
    # 所有 /api/* 和 /ws/* 路由已在上面注册,下面 catch-all 挂静态目录。
    _mount_frontend(app)

    @app.on_event("shutdown")
    def _shutdown():
        hc.stop()
        audit.stop()
        shutdown_runtime(app)

    return app


app = create_app()
