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
            # P18.14 (2026-04-20): 加 WHERE up_down_ratio IS NOT NULL。
            # 否则盘中(今日 kline 未灌时)会取到只有 limit_up_count
            # 没有 up_down_ratio/avg_pct_change 的半成品记录,前端
            # Dashboard 大量字段显示 NULL/-。
            # 改为取「最新完整记录」— 通常是昨日收盘数据。
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
            td = out.get("trade_date")
            if td is not None:
                out["trade_date"] = td.isoformat()
            # P18.13 (2026-04-20): market_sentiment 表里**没有**
            # total_stocks/up_count/down_count 列(v2 设计稿里假设过,实际
            # 未建)。这里从 kline_daily 实时聚合这 3 个字段,填进响应。
            # 单次 COUNT 查询带 trade_date 索引,+2ms 级别开销可忽略。
            if td is not None:
                try:
                    # STO-126: 若 sentiment td 当日 kline 无数据（节假日等），
                    # 回退到 kline_daily 中最近有数据的交易日
                    cur.execute(
                        """SELECT MAX(trade_date) FROM kline_daily
                           WHERE trade_date <= %s AND pct_change IS NOT NULL""",
                        (td,),
                    )
                    kline_td_row = cur.fetchone()
                    kline_td = kline_td_row[0] if kline_td_row else None
                    if kline_td is not None:
                        cur.execute(
                            """SELECT COUNT(*) AS total,
                                      SUM(CASE WHEN pct_change > 0 THEN 1 ELSE 0 END) AS up_cnt,
                                      SUM(CASE WHEN pct_change < 0 THEN 1 ELSE 0 END) AS down_cnt
                               FROM kline_daily
                               WHERE trade_date = %s AND pct_change IS NOT NULL""",
                            (kline_td,),
                        )
                        agg = cur.fetchone()
                        if agg and agg[0]:
                            out["total_stocks"] = int(agg[0])
                            out["up_count"] = int(agg[1] or 0)
                            out["down_count"] = int(agg[2] or 0)
                        else:
                            out["total_stocks"] = None
                            out["up_count"] = None
                            out["down_count"] = None
                    else:
                        out["total_stocks"] = None
                        out["up_count"] = None
                        out["down_count"] = None
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"[dashboard] kline agg fallback failed: {e}")
                    out.setdefault("total_stocks", None)
                    out.setdefault("up_count", None)
                    out.setdefault("down_count", None)

                # P20.4 (2026-04-20): 从 lhb_stock_daily 实时聚合 3 个 LHB 字段。
                # market_sentiment 表有这 3 列但长期为 NULL,一直未跑回填脚本;
                # 改为 API 层实时算,避免再踩 schema-vs-API 错配事故(P18.13b)。
                try:
                    cur.execute(
                        """SELECT COUNT(*) AS cnt,
                                  SUM(l_net) AS total_net,
                                  AVG(score) FILTER (WHERE score IS NOT NULL) AS avg_score
                           FROM lhb_stock_daily
                           WHERE trade_date = %s""",
                        (td,),
                    )
                    lhb_agg = cur.fetchone()
                    if lhb_agg and lhb_agg[0]:
                        out["lhb_stock_count"] = int(lhb_agg[0])
                        out["lhb_total_net"] = float(lhb_agg[1]) if lhb_agg[1] is not None else None
                        out["lhb_avg_score"] = round(float(lhb_agg[2]), 2) if lhb_agg[2] is not None else None
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"[dashboard] lhb agg fallback failed: {e}")

                # P20.5 (2026-04-20): sentiment_change 实时计算(当日 - 前一个完整交易日)。
                # market_sentiment 表有该列但长期 NULL,表示情绪温度环比变化。
                # 若前一日缺数据或无变化则为 None / 0。
                if out.get("sentiment_change") is None:
                    try:
                        cur.execute(
                            """SELECT sentiment_score FROM market_sentiment
                               WHERE trade_date < %s AND up_down_ratio IS NOT NULL
                               ORDER BY trade_date DESC LIMIT 1""",
                            (td,),
                        )
                        prev = cur.fetchone()
                        curr_score = out.get("sentiment_score")
                        if prev and prev[0] is not None and curr_score is not None:
                            diff = float(curr_score) - float(prev[0])
                            out["sentiment_change"] = round(diff, 2)
                    except Exception as e:  # noqa: BLE001
                        logger.warning(f"[dashboard] sentiment_change calc failed: {e}")
            else:
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
