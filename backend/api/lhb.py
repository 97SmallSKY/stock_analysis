"""/api/lhb/* — 龙虎榜只读查询接口 (W7 前端 AG Grid 的数据源)

端点:
    GET  /api/lhb                —— 主表分页查询
    GET  /api/lhb/seats          —— 席位明细
    GET  /api/lhb/stat/{stat_type}/{stat_key}   —— 汇总统计 (lhb_agg_stats)

所有查询走 pg_conn, 不经适配器, 是纯 DB 只读.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from backend.db.session import pg_conn

logger = logging.getLogger(__name__)
router = APIRouter()


def _to_iso(date_str: Optional[str]) -> Optional[str]:
    """YYYYMMDD → YYYY-MM-DD; 已是 ISO 或 None 原样返回."""
    if not date_str:
        return None
    s = date_str.replace("-", "")
    if len(s) != 8 or not s.isdigit():
        raise HTTPException(400, f"日期格式错误: {date_str}, 期望 YYYYMMDD")
    return f"{s[:4]}-{s[4:6]}-{s[6:]}"


@router.get("")
def list_lhb(
    date_from: Optional[str] = Query(None, description="YYYYMMDD"),
    date_to: Optional[str] = Query(None, description="YYYYMMDD"),
    ts_code: Optional[str] = Query(None),
    reason: Optional[str] = Query(None, description="上榜原因模糊匹配"),
    net_amount_min: Optional[float] = Query(None, description="最小净买入"),
    score_min: Optional[float] = Query(None, description="最小评分"),
    order_by: str = Query("trade_date", pattern="^(trade_date|net_amount|score)$"),
    order_desc: bool = Query(True),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    """分页查询龙虎榜主表 lhb_stock_daily."""
    where = ["1=1"]
    params: list = []
    if date_from:
        where.append("trade_date >= %s")
        params.append(_to_iso(date_from))
    if date_to:
        where.append("trade_date <= %s")
        params.append(_to_iso(date_to))
    if ts_code:
        where.append("ts_code = %s")
        params.append(ts_code)
    if reason:
        where.append("reason ILIKE %s")
        params.append(f"%{reason}%")
    if net_amount_min is not None:
        where.append("net_amount >= %s")
        params.append(net_amount_min)
    if score_min is not None:
        where.append("score >= %s")
        params.append(score_min)

    order_dir = "DESC" if order_desc else "ASC"
    sql = f"""
        SELECT ts_code, trade_date, reason,
               buy_amount, sell_amount, net_amount, turnover,
               score, score_detail
        FROM lhb_stock_daily
        WHERE {' AND '.join(where)}
        ORDER BY {order_by} {order_dir}, ts_code
        LIMIT %s OFFSET %s
    """
    count_sql = f"SELECT COUNT(*) FROM lhb_stock_daily WHERE {' AND '.join(where)}"
    try:
        with pg_conn() as conn, conn.cursor() as cur:
            cur.execute(count_sql, params)
            total = cur.fetchone()[0]
            cur.execute(sql, params + [limit, offset])
            cols = [c.name for c in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        # trade_date 转字符串
        for r in rows:
            if r.get("trade_date") is not None:
                r["trade_date"] = r["trade_date"].isoformat()
        return {"total": total, "limit": limit, "offset": offset, "rows": rows}
    except Exception as e:
        logger.exception("[api/lhb] list failed")
        raise HTTPException(500, f"查询失败: {e}")


@router.get("/seats")
def list_seats(
    trade_date: str = Query(..., description="YYYYMMDD"),
    ts_code: Optional[str] = Query(None),
    direction: Optional[str] = Query(None, pattern="^(BUY|SELL)$"),
    limit: int = Query(200, ge=1, le=2000),
):
    """席位明细 lhb_seats."""
    where = ["trade_date = %s"]
    params: list = [_to_iso(trade_date)]
    if ts_code:
        where.append("ts_code = %s")
        params.append(ts_code)
    if direction:
        where.append("direction = %s")
        params.append(direction)
    sql = f"""
        SELECT ts_code, trade_date, broker_name, direction,
               buy_amount, sell_amount, net_amount, rank_no
        FROM lhb_seats
        WHERE {' AND '.join(where)}
        ORDER BY net_amount DESC NULLS LAST
        LIMIT %s
    """
    try:
        with pg_conn() as conn, conn.cursor() as cur:
            cur.execute(sql, params + [limit])
            cols = [c.name for c in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        for r in rows:
            if r.get("trade_date") is not None:
                r["trade_date"] = r["trade_date"].isoformat()
        return {"rows": rows, "n": len(rows)}
    except Exception as e:
        logger.exception("[api/lhb] seats failed")
        raise HTTPException(500, f"查询失败: {e}")


@router.get("/stat/{stat_type}/{stat_key}")
def get_stat(stat_type: str, stat_key: str, stat_period: str = Query("all")):
    """聚合统计 lhb_agg_stats, 例: /stat/broker/国泰君安/all"""
    try:
        with pg_conn() as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT stat_type, stat_key, stat_period, payload, updated_at
                   FROM lhb_agg_stats
                   WHERE stat_type=%s AND stat_key=%s AND stat_period=%s""",
                (stat_type, stat_key, stat_period),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(404, "stat not found")
            cols = [c.name for c in cur.description]
            out = dict(zip(cols, row))
        if out.get("updated_at"):
            out["updated_at"] = out["updated_at"].isoformat()
        return out
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[api/lhb] stat failed")
        raise HTTPException(500, f"查询失败: {e}")
