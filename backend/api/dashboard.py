"""/api/dashboard — 首屏聚合接口 (W7)

目的: 一次查询返回首屏所需的全部轻量数据, 前端不用 4~5 次 HTTP 串行等待.

聚合内容:
    - sentiment: market_sentiment 最新 1 行 (今日/最近交易日)
    - top_lhb: 今日龙虎榜按 score 降序 TOP N
    - top_gainers: (占位, W8 实时快照上线后填充)
    - adapter_health: 各 adapter 最近健康探针
    - circuit_state: 各 adapter 熔断状态
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Query, Request

from backend.cache import cached
from backend.db.session import pg_conn

logger = logging.getLogger(__name__)
router = APIRouter()


# 列对齐说明:生产 PG 上 market_sentiment 实际存在的列不是 v2 方案里假设
# 的 total_stocks/up_count/down_count,而是 limit_up_count/limit_down_count
# 等离散情绪因子 + sentiment_score/sentiment_level 等聚合字段。
# lhb_stock_daily 的金额列命名为 l_buy/l_sell/l_net(而不是
# buy_amount/sell_amount/net_amount)。查询里用别名保持对外 API 稳定。
#
# "今日"语义:按最近一个有数据的 trade_date(周末/长假自动回退到周五等)。


@cached(lambda: "dashboard:sentiment:latest", ttl=15)
def _latest_sentiment() -> Optional[dict]:
    try:
        with pg_conn() as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT trade_date,
                          limit_up_count, limit_down_count,
                          first_limit_up, cont_limit_up, max_cont_board,
                          up_down_ratio, avg_pct_change, median_pct_change,
                          strong_stock_count, weak_stock_count,
                          lhb_stock_count, lhb_total_net, lhb_avg_score,
                          sentiment_score, sentiment_level, sentiment_change
                   FROM market_sentiment
                   ORDER BY trade_date DESC LIMIT 1"""
            )
            row = cur.fetchone()
            if not row:
                return None
            cols = [c.name for c in cur.description]
            out = dict(zip(cols, row))
            if out.get("trade_date") is not None:
                out["trade_date"] = out["trade_date"].isoformat()
            # 兼容旧前端:up/down/total 字段保留映射(None 占位)
            out.setdefault("total_stocks", None)
            out.setdefault("up_count", None)
            out.setdefault("down_count", None)
            return out
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[dashboard] latest_sentiment failed: {e}")
        return None


@cached(lambda top_n: f"dashboard:top_lhb:{top_n}", ttl=15)
def _top_lhb(top_n: int) -> list[dict]:
    try:
        with pg_conn() as conn, conn.cursor() as cur:
            # P10.1 JOIN stock_info 补股票简称 name
            cur.execute(
                """SELECT l.ts_code, si.name AS name, l.trade_date, l.reason,
                          l.l_net AS net_amount, l.l_buy AS buy_amount,
                          l.l_sell AS sell_amount, l.score
                   FROM lhb_stock_daily l
                   LEFT JOIN stock_info si ON si.ts_code = l.ts_code
                   WHERE l.trade_date = (SELECT MAX(trade_date) FROM lhb_stock_daily)
                   ORDER BY l.score DESC NULLS LAST, l.l_net DESC NULLS LAST
                   LIMIT %s""",
                (top_n,),
            )
            cols = [c.name for c in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        for r in rows:
            if r.get("trade_date") is not None:
                r["trade_date"] = r["trade_date"].isoformat()
        return rows
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[dashboard] top_lhb failed: {e}")
        return []


def _adapter_overview(request: Request) -> list[dict]:
    mgr = getattr(request.app.state, "manager", None)
    if mgr is None:
        return []
    hc = getattr(request.app.state, "health_checker", None)
    snap = hc.get_last_snapshot() if hc else {}
    out = []
    for name, ad in mgr._router._adapters.items():
        cb = ad.circuit_breaker.get_status()
        h = snap.get(name.value)
        out.append({
            "name": name.value,
            "initialized": ad.initialized,
            "circuit_state": cb.get("state"),
            "last_health_ok": h.is_healthy if h else None,
            "last_latency_ms": h.latency_ms if h else None,
        })
    return out


@router.get("")
def dashboard(
    request: Request,
    top_n: int = Query(10, ge=1, le=50),
):
    """首屏聚合. 单次调用返回四块数据."""
    return {
        "sentiment": _latest_sentiment(),
        "top_lhb": _top_lhb(top_n),
        "adapters": _adapter_overview(request),
    }
