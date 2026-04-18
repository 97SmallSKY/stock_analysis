"""龙虎榜入库（lhb_stock_daily）。

席位明细 (lhb_seats) 依赖 stock_daily_id 外键，分两步：W3 再做，
当前 W2 只灌主表，保证能做评分。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from backend.db.session import pg_conn
from backend.db.writer import upsert_lhb_stock_daily
from backend.ingest.base import df_to_rows, get_manager, normalize_date
from psycopg2.extras import execute_values

logger = logging.getLogger(__name__)

COLMAP = {
    "ts_code": "ts_code",
    "trade_date": "trade_date",
    "name": "name",
    "close": "close_price",
    "close_price": "close_price",
    "pct_change": "pct_change",
    "pct_chg": "pct_change",
    "turnover_rate": "turnover_rate",
    "amount": "amount",
    "l_buy": "l_buy",
    "l_sell": "l_sell",
    "l_net": "l_net",
    "reason": "reason",
    "reason_type": "reason_type",
    "industry": "industry",
    "market_cap": "market_cap",
    "float_market_cap": "float_market_cap",
}


def _daterange(start: str, end: str):
    s = datetime.strptime(start, "%Y%m%d")
    e = datetime.strptime(end, "%Y%m%d")
    cur = s
    while cur <= e:
        yield cur.strftime("%Y%m%d")
        cur += timedelta(days=1)


def _load_trading_days(start: str, end: str) -> list[str]:
    with pg_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT to_char(cal_date,'YYYYMMDD') FROM trade_calendar "
            "WHERE exchange='SSE' AND is_open=TRUE "
            "AND cal_date BETWEEN %s AND %s ORDER BY cal_date",
            (f"{start[:4]}-{start[4:6]}-{start[6:]}",
             f"{end[:4]}-{end[4:6]}-{end[6:]}"),
        )
        return [r[0] for r in cur.fetchall()]


def _ensure_stock_info_skel(conn, rows: list[dict]) -> None:
    """龙虎榜会出现可转债 / 已退市标的, 可能不在 stock_info 中。
    外键约束要求先存在, 因此对缺失的 ts_code 先 upsert 一条骨架(symbol/name 必填)。
    is_active=False 标记为非普通股, 供下游过滤。
    """
    if not rows:
        return
    codes = {r.get("ts_code") for r in rows if r.get("ts_code")}
    if not codes:
        return
    with conn.cursor() as cur:
        cur.execute(
            "SELECT ts_code FROM stock_info WHERE ts_code = ANY(%s)",
            (list(codes),),
        )
        existed = {r[0] for r in cur.fetchall()}
    missing = codes - existed
    if not missing:
        return

    # 为缺失 code 拼骨架记录(从 rows 取同 code 的 name)
    name_by_code: dict[str, str] = {}
    for r in rows:
        c = r.get("ts_code")
        if c in missing and c not in name_by_code:
            name_by_code[c] = str(r.get("name") or c)[:32]

    skel_rows = []
    for c in missing:
        symbol = c.split(".")[0] if "." in c else c
        exch = c.split(".")[-1] if "." in c else None
        exchange = {"SH": "SSE", "SZ": "SZSE", "BJ": "BSE"}.get(exch)
        skel_rows.append((
            c, symbol, name_by_code.get(c, symbol),
            exchange, False,  # is_active=False (非普通股 / 已退市)
        ))
    with conn.cursor() as cur:
        execute_values(
            cur,
            "INSERT INTO stock_info (ts_code, symbol, name, exchange, is_active) "
            "VALUES %s ON CONFLICT (ts_code) DO NOTHING",
            skel_rows,
            page_size=500,
        )
    logger.info(f"[ingest.lhb] 自动补 stock_info 骨架 {len(skel_rows)} 条")


def _dedup_same_key(rows: list[dict]) -> list[dict]:
    """同一 (ts_code, trade_date) 在一天内可能出现多次 (多个上榜原因)。
    合并策略: 金额字段求和, reason 拼接, 其他字段取第一条。
    """
    from collections import OrderedDict

    merged: "OrderedDict[tuple, dict]" = OrderedDict()
    sum_cols = ("l_buy", "l_sell", "l_net")
    for r in rows:
        key = (r.get("ts_code"), r.get("trade_date"))
        if key not in merged:
            merged[key] = dict(r)
            continue
        base = merged[key]
        for c in sum_cols:
            v1 = base.get(c) or 0
            v2 = r.get(c) or 0
            try:
                base[c] = float(v1) + float(v2)
            except (TypeError, ValueError):
                pass
        # 合并 reason (去重)
        r1 = str(base.get("reason") or "").strip()
        r2 = str(r.get("reason") or "").strip()
        if r2 and r2 not in r1:
            base["reason"] = f"{r1};{r2}" if r1 else r2
    return list(merged.values())


def _load_covered_days(start: str, end: str) -> set[str]:
    """已有龙虎榜记录的交易日 (YYYYMMDD 字符串)"""
    s_iso = f"{start[:4]}-{start[4:6]}-{start[6:]}"
    e_iso = f"{end[:4]}-{end[4:6]}-{end[6:]}"
    with pg_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT to_char(trade_date, 'YYYYMMDD') "
            "FROM lhb_stock_daily WHERE trade_date BETWEEN %s AND %s",
            (s_iso, e_iso),
        )
        return {r[0] for r in cur.fetchall()}


def run(start: str, end: str, skip_existing: bool = True) -> int:
    """抓取龙虎榜。

    Args:
        skip_existing: 已灌过该 trade_date 的天会被跳过, 适合断点续跑。
    """
    mgr = get_manager()
    days = _load_trading_days(start, end) or list(_daterange(start, end))
    if skip_existing:
        covered = _load_covered_days(start, end)
        if covered:
            days = [d for d in days if d not in covered]
            logger.info(
                f"[ingest.lhb] 增量模式: {len(covered)} 天已覆盖, 待抓 {len(days)} 天"
            )
    logger.info(f"[ingest.lhb] {len(days)} 天待抓")

    total = 0
    fail = 0
    with pg_conn() as conn:
        for idx, d in enumerate(days, 1):
            try:
                df = mgr.get_lhb_detail(d)
                rows = df_to_rows(df, COLMAP)
                if not rows:
                    continue
                for r in rows:
                    r["trade_date"] = normalize_date(r.get("trade_date")) or f"{d[:4]}-{d[4:6]}-{d[6:]}"
                rows = _dedup_same_key(rows)  # 去重避免 ON CONFLICT 二次命中
                _ensure_stock_info_skel(conn, rows)  # 补齐外键
                n = upsert_lhb_stock_daily(conn, rows)
                total += n
                if idx % 20 == 0:
                    conn.commit()
                    logger.info(f"[ingest.lhb] 进度 {idx}/{len(days)}  累计 {total}")
            except Exception as e:
                fail += 1
                # 关键: rollback 避免污染后续 day 的 tx
                try:
                    conn.rollback()
                except Exception:
                    pass
                logger.warning(f"[ingest.lhb] {d} 失败: {e}")

        conn.commit()  # 最后一轮未达 20 天倍数的批次也要 flush

    logger.info(f"[ingest.lhb] 完成: {total} 行, 失败 {fail} 天")
    return total
