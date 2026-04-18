"""/api/reports/* — W10-2 周报接口

端点:
    GET  /api/reports/weekly            最近一期周报 (默认 end=today-1, days=7)
    POST /api/reports/weekly/generate   立即生成一份周报并返回 (不写 DB, 供前端点击按钮触发)

周报数据来自 backend.domain.reports.weekly.build_weekly_report,
默认用 backend.db.session.pg_conn 作为连接工厂. 测试可在 app.state
上注入 report_conn_factory 字段替代.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Query, Request

from backend.domain.reports.weekly import build_weekly_report

logger = logging.getLogger(__name__)
router = APIRouter()


def _conn_factory(request: Request):
    f = getattr(request.app.state, "report_conn_factory", None)
    if f is not None:
        return f
    from backend.db.session import pg_conn
    return pg_conn


@router.get("/weekly")
def weekly(
    request: Request,
    end: Optional[date] = Query(None, description="区间结束日 (含)"),
    days: int = Query(7, ge=1, le=90),
):
    rep = build_weekly_report(_conn_factory(request), end=end, days=days)
    return rep.to_dict()


@router.post("/weekly/generate")
def generate_weekly(request: Request, days: int = Query(7, ge=1, le=90)):
    rep = build_weekly_report(_conn_factory(request), end=date.today(), days=days)
    return rep.to_dict()
