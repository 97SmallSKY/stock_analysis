"""日K线历史数据入库。

全市场灌库思路：
1. 从 stock_info 取所有 ts_code（如果库里还没有，调用者应先 ingest_stock_info）
2. 逐只调 manager.get_daily_kline(ts_code, start, end)
3. 批量 upsert 到 kline_daily
"""

from __future__ import annotations

import logging
from typing import List, Optional

from backend.db.session import pg_conn
from backend.db.writer import upsert_kline_daily
from backend.ingest.base import df_to_rows, get_manager, normalize_date

logger = logging.getLogger(__name__)

COLMAP = {
    "ts_code": "ts_code",
    "trade_date": "trade_date",
    "open": "open",
    "high": "high",
    "low": "low",
    "close": "close",
    "pre_close": "pre_close",
    "pct_chg": "pct_change",
    "pct_change": "pct_change",
    "vol": "vol",
    "amount": "amount",
    "turnover_rate": "turnover_rate",
    "turnover_rate_f": "turnover_rate_f",
    "volume_ratio": "volume_ratio",
    "pe_ttm": "pe_ttm",
    "pb": "pb",
    "total_mv": "total_mv",
    "circ_mv": "circ_mv",
}


def _load_all_ts_codes() -> List[str]:
    """只取 A 股主体,排除可转债/基金/B股/港股/已退市。

    过滤规则:
      - is_active = TRUE (主过滤: 可转债/退市股在骨架补齐时已标 is_active=FALSE)
      - symbol 前缀白名单:
         000/001/002/003  深市主板/中小板
         300/301           创业板
         600/601/603/605   沪市主板
         688               科创板
         920               北交所
        其他前缀(1xx/2xx/5xx/9xx 等)多为债券/基金/B股, akshare 日K 不支持
    """
    allow_prefix = (
        "000", "001", "002", "003",
        "300", "301",
        "600", "601", "603", "605",
        "688",
        "920",
    )
    with pg_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT ts_code, symbol FROM stock_info WHERE is_active = TRUE")
        rows = cur.fetchall()
    return [
        ts_code for ts_code, symbol in rows
        if symbol and symbol.startswith(allow_prefix)
    ]


def _normalize_pct(rows: list[dict]) -> None:
    """TuShare 的 pct_chg 是百分比（5.23 表示 5.23%），DB 存小数（0.0523）。"""
    for r in rows:
        v = r.get("pct_change")
        if v is not None:
            try:
                fv = float(v)
                if abs(fv) > 1:  # 看起来是百分比
                    r["pct_change"] = fv / 100.0
            except (TypeError, ValueError):
                r["pct_change"] = None
        tr = r.get("turnover_rate")
        if tr is not None:
            try:
                ftr = float(tr)
                if abs(ftr) > 1:
                    r["turnover_rate"] = ftr / 100.0
            except (TypeError, ValueError):
                r["turnover_rate"] = None


def _load_covered_codes(start: str, end: str, min_rows: int = 100) -> set[str]:
    """识别已覆盖[start,end]区间的 ts_code, 后续直接跳过。

    判断条件: kline_daily 在 [start, end] 内的行数 >= min_rows
    (默认 100 行对应 2024 全年 ~240 个交易日的 40%, 能避开只灌过
     几天的部分样本被误判为"已覆盖")

    停牌/新上市的股票行数天然就少, 所以不能用"行数 = 区间交易日数"判断
    """
    s_iso = f"{start[:4]}-{start[4:6]}-{start[6:]}"
    e_iso = f"{end[:4]}-{end[4:6]}-{end[6:]}"
    with pg_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT ts_code, COUNT(*)
            FROM kline_daily
            WHERE trade_date BETWEEN %s AND %s
            GROUP BY ts_code
            HAVING COUNT(*) >= %s
            """,
            (s_iso, e_iso, min_rows),
        )
        return {r[0] for r in cur.fetchall()}


def run(
    start: str,
    end: str,
    codes: Optional[List[str]] = None,
    skip_existing: bool = True,
    min_rows: int = 100,
) -> int:
    """抓取日K线。

    Args:
        skip_existing: 增量幂等. True 时, 已有 [start,end] 内行数 >= min_rows
            的 ts_code 自动跳过。重启任务不会重复拉取已有数据。
        min_rows: 判断"已覆盖"的最小行数阈值。
    """
    mgr = get_manager()

    if not codes or codes == ["all"]:
        codes = _load_all_ts_codes()
        if not codes:
            logger.error("[ingest.kline] stock_info 为空，请先 ingest stock-info")
            return 0

    original_n = len(codes)
    if skip_existing:
        covered = _load_covered_codes(start, end, min_rows)
        if covered:
            codes = [c for c in codes if c not in covered]
            logger.info(
                f"[ingest.kline] 增量模式: {len(covered)} 只已覆盖, "
                f"跳过 / 待拉 {len(codes)} 只 (原 {original_n} 只)"
            )

    total = 0
    fail = 0
    logger.info(f"[ingest.kline] 开始: {len(codes)} 只股票, {start}~{end}")

    with pg_conn() as conn:
        for idx, code in enumerate(codes, 1):
            try:
                df = mgr.get_daily_kline(code, start, end)
                rows = df_to_rows(df, COLMAP)
                if not rows:
                    continue
                for r in rows:
                    r.setdefault("ts_code", code)
                    r["trade_date"] = normalize_date(r.get("trade_date"))
                _normalize_pct(rows)
                # 批内去重 (同 ts_code+trade_date 只保留最后一条)
                dedup: dict = {}
                for r in rows:
                    dedup[(r.get("ts_code"), r.get("trade_date"))] = r
                rows = list(dedup.values())
                n = upsert_kline_daily(conn, rows)
                total += n
                if idx % 100 == 0:
                    conn.commit()
                    logger.info(f"[ingest.kline] 进度 {idx}/{len(codes)}  累计 {total} 行")
            except Exception as e:
                fail += 1
                try:
                    conn.rollback()
                except Exception:
                    pass
                logger.warning(f"[ingest.kline] {code} 失败: {e}")

        conn.commit()  # 末轮 flush

    logger.info(f"[ingest.kline] 完成: 共 {total} 行, 失败 {fail} 只")
    return total
