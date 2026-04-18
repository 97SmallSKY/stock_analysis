"""FastAPI 应用包。

子路由:
- data_sources: /api/data-sources/* 管理适配器
- (后续 W4+) lhb / score / robots / backtest ...

入口: backend.api.main:app, 用 uvicorn 启动:
    uvicorn backend.api.main:app --host 0.0.0.0 --port 8000
"""
