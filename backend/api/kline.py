"""/api/kline/* — K 线只读查询 (W7 StockDrillModal 的数据源)

端点:
    GET /api/kline/daily?ts_code=&from=&to=        —— 日K (kline_daily 直查)
    GET /api/kline/weekly?ts_code=&from=&to=       —— 周K (从 daily 动态聚合)
    GET /api/kline/minute?ts_code=&trade_date=     —— 分钟 K (目前无数据源)

返回统一结构:
    {
      "ts_code": "000001.SZ",
      "freq": "daily" | "weekly" | "minute",
      "rows": [{"date": "2026-01-05", "open": x, "high": x, "low": x, "close": x, "vol": x}, ...],
      "empty_reason": "no_source_table" | "out_of_range" | null,
    }

设计约束:
    - ts_code 必填, 格式 '000001.SZ' / '600000.SH';大小写归一
    - from/to YYYYMMDD, 可选 (默认最近 60 日 daily / 52 周 weekly)
    - 无数据时 rows=[] + empty_reason 说明,前端按空态渲染
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from backend.db.session import pg_conn

logger = logging.getLogger(__name__)
router = APIRouter()


def _parse_ymd(s: Optional[str], field: str) -> Optional[date]:
    if not s:
        return None
    t = s.replace("-", "").strip()
    if len(t) != 8 or not t.isdigit():
        raise HTTPException(400, f"{field} 格式错误: {s}, 期望 YYYYMMDD")
    try:
        return datetime.strptime(t, "%Y%m%d").date()
    except ValueError:
        raise HTTPException(400, f"{field} 非法日期: {s}")


def _norm_code(ts_code: str) -> str:
    c = (ts_code or "").strip().upper()
    if not c or "." not in c:
        raise HTTPException(400, f"ts_code 格式错误: {ts_code}, 期望 000001.SZ")
    return c


@router.get("/daily")
def kline_daily(
    ts_code: str = Query(..., description="如 000001.SZ"),
    date_from: Optional[str] = Query(None, alias="from", description="YYYYMMDD"),
    date_to: Optional[str] = Query(None, alias="to", description="YYYYMMDD"),
    # P19.13 (2026-04-20): 上限从 1000 → 5000,支持前端传 [from, to] 跨年区间。
    # 1Y ~= 250 交易日, 5000 够 ~20 年,响应最大 ~200KB,不会压垮。
    limit: int = Query(250, ge=1, le=5000),
):
    code = _norm_code(ts_code)
    d_from = _parse_ymd(date_from, "from")
    d_to = _parse_ymd(date_to, "to")
    if d_from is None and d_to is None:
        d_to = date.today()
        d_from = d_to - timedelta(days=90)

    where = ["ts_code = %s"]
    params: list = [code]
    if d_from:
        where.append("trade_date >= %s")
        params.append(d_from)
    if d_to:
        where.append("trade_date <= %s")
        params.append(d_to)

    sql = f"""
        SELECT trade_date, open, high, low, close, vol
        FROM kline_daily
        WHERE {' AND '.join(where)}
        ORDER BY trade_date ASC
        LIMIT %s
    """
    try:
        with pg_conn() as conn, conn.cursor() as cur:
            cur.execute(sql, params + [limit])
            rows_raw = cur.fetchall()
    except Exception as e:
        logger.exception("[api/kline] daily failed")
        raise HTTPException(500, f"查询失败: {e}")

    rows = [
        {
            "date": r[0].isoformat() if r[0] else None,
            "open": float(r[1]) if r[1] is not None else None,
            "high": float(r[2]) if r[2] is not None else None,
            "low": float(r[3]) if r[3] is not None else None,
            "close": float(r[4]) if r[4] is not None else None,
            "vol": float(r[5]) if r[5] is not None else None,
        }
        for r in rows_raw
    ]
    empty_reason = None if rows else "out_of_range"
    return {"ts_code": code, "freq": "daily", "rows": rows, "empty_reason": empty_reason}


@router.get("/weekly")
def kline_weekly(
    ts_code: str = Query(..., description="如 000001.SZ"),
    date_from: Optional[str] = Query(None, alias="from"),
    date_to: Optional[str] = Query(None, alias="to"),
    # P19.13: 上限从 520 → 2000,1Y ~= 52 周, 2000 够 ~40 年。
    limit: int = Query(104, ge=1, le=2000),
):
    """周K: 从 kline_daily 按 ISO 周(date_trunc('week'))聚合.
    - open = 周内第一天 open
    - close = 周内最后一天 close
    - high = max(high)
    - low = min(low)
    - vol = sum(vol)
    """
    code = _norm_code(ts_code)
    d_from = _parse_ymd(date_from, "from")
    d_to = _parse_ymd(date_to, "to")
    if d_from is None and d_to is None:
        d_to = date.today()
        d_from = d_to - timedelta(days=365)

    where = ["ts_code = %s"]
    params: list = [code]
    if d_from:
        where.append("trade_date >= %s")
        params.append(d_from)
    if d_to:
        where.append("trade_date <= %s")
        params.append(d_to)

    # 用 DISTINCT ON + 窗口函数取每周首/末 open/close
    sql = f"""
        WITH d AS (
            SELECT trade_date, open, high, low, close, vol,
                   date_trunc('week', trade_date)::date AS wk
            FROM kline_daily
            WHERE {' AND '.join(where)}
        )
        SELECT wk AS date,
               (array_agg(open ORDER BY trade_date ASC))[1] AS open,
               MAX(high) AS high,
               MIN(low) AS low,
               (array_agg(close ORDER BY trade_date DESC))[1] AS close,
               SUM(vol) AS vol
        FROM d
        GROUP BY wk
        ORDER BY wk ASC
        LIMIT %s
    """
    try:
        with pg_conn() as conn, conn.cursor() as cur:
            cur.execute(sql, params + [limit])
            rows_raw = cur.fetchall()
    except Exception as e:
        logger.exception("[api/kline] weekly failed")
        raise HTTPException(500, f"查询失败: {e}")

    rows = [
        {
            "date": r[0].isoformat() if r[0] else None,
            "open": float(r[1]) if r[1] is not None else None,
            "high": float(r[2]) if r[2] is not None else None,
            "low": float(r[3]) if r[3] is not None else None,
            "close": float(r[4]) if r[4] is not None else None,
            "vol": float(r[5]) if r[5] is not None else None,
        }
        for r in rows_raw
    ]
    empty_reason = None if rows else "out_of_range"
    return {"ts_code": code, "freq": "weekly", "rows": rows, "empty_reason": empty_reason}


@router.get("/minute")
def kline_minute(
    ts_code: str = Query(...),
    trade_date: str = Query(..., description="YYYYMMDD"),
):
    """分钟K: 当前未接入任何分钟数据源, 统一返回 empty_reason='no_source_table'.

    前端按空态渲染 (Empty + '该数据源未接入, 待补'). 后端预留接口契约,
    以便后续接入 Tushare stk_mins / AkShare stock_zh_a_minute 时不改前端.
    """
    code = _norm_code(ts_code)
    d = _parse_ymd(trade_date, "trade_date")
    return {
        "ts_code": code,
        "freq": "minute",
        "trade_date": d.isoformat() if d else None,
        "rows": [],
        "empty_reason": "no_source_table",
    }


@router.get("/daterange")
def kline_daterange(
    ts_code: str = Query(..., description="如 000001.SZ"),
    freq: str = Query("daily", pattern="^(daily|weekly|minute)$"),
):
    """P19.1 (2026-04-20): 返回某股票在某频率下的可用日期范围。

    前端 K 线组件依赖此接口动态设置日期选择器的 min/max,
    避免硬编码 2026-01-20 等静态日期。

    返回:
      {"ts_code": "...", "freq": "daily", "min": "2020-01-02", "max": "2026-04-17", "count": 1520}
      或 min/max/count 全 null (股票无 K 线数据 / freq 无源)
    """
    code = _norm_code(ts_code)
    # 当前仅 daily 有真实数据源。weekly 从 daily 聚合,范围 = daily 范围。
    # minute 暂无数据源,直接返回空。
    if freq == "minute":
        return {
            "ts_code": code, "freq": freq,
            "min": None, "max": None, "count": 0,
            "empty_reason": "no_source_table",
        }

    with pg_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT MIN(trade_date), MAX(trade_date), COUNT(*)
               FROM kline_daily WHERE ts_code = %s""",
            (code,),
        )
        mn, mx, cnt = cur.fetchone()

    return {
        "ts_code": code,
        "freq": freq,
        "min": mn.isoformat() if mn else None,
        "max": mx.isoformat() if mx else None,
        "count": int(cnt or 0),
        "empty_reason": None if cnt else "no_data",
    }
