"""日K线历史数据入库。

全市场灌库思路：
1. 从 stock_info 取所有 ts_code（如果库里还没有，调用者应先 ingest_stock_info）
2. 逐只调 manager.get_daily_kline(ts_code, start, end)
3. 批量 upsert 到 kline_daily
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
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
    """只取 A 股主体,排除可转债/基金/B股/港股/已退市/数据源不可用。

    过滤规则:
      - ``is_active = TRUE``(可转债/退市股在骨架补齐时已标 FALSE)
      - ``data_source_status != 'bse_unavailable'``(STO-31:北交所数据源止损)
      - symbol 前缀白名单:
         000/001/002/003  深市主板/中小板
         300/301           创业板
         600/601/603/605   沪市主板
         688               科创板
         920               北交所(保留前缀,将来方案 B/A 启用时直接解除
                                   ``data_source_status`` 即可恢复)
        其他前缀(1xx/2xx/5xx/9xx 等)多为债券/基金/B股,akshare 日K 不支持
    """
    allow_prefix = (
        "000", "001", "002", "003",
        "300", "301",
        "600", "601", "603", "605",
        "688",
        "920",
    )
    with pg_conn() as conn, conn.cursor() as cur:
        # STO-31: 过滤 bse_unavailable 标记的股票(北交所数据源不支持)
        # COALESCE 是为了兼容迁移前的旧记录(data_source_status 为 NULL)
        cur.execute(
            "SELECT ts_code, symbol FROM stock_info "
            "WHERE is_active = TRUE "
            "AND COALESCE(data_source_status, 'ok') != 'bse_unavailable'"
        )
        rows = cur.fetchall()
        cur.execute(
            "SELECT COUNT(*) FROM stock_info "
            "WHERE is_active = TRUE AND data_source_status = 'bse_unavailable'"
        )
        skipped_count = cur.fetchone()[0]
    if skipped_count:
        logger.info(
            "[ingest.kline] skip bse_unavailable stocks: count=%d (STO-31 止损;"
            "跟踪 Phase 2 方案 B 东财北交所路径)",
            skipped_count,
        )
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


def _iter_month_segments_reverse(start: str, end: str) -> list[tuple[str, str]]:
    """生成从 end 到 start 的月反向段: [(seg_start, seg_end), ...]

    每段起止都是 YYYYMMDD, seg_start <= seg_end. 最新的月份排最前,
    保证"从近到远"的采集语义: 任务被中断时, 近端数据已入库.

    例: start=20230101, end=20260418 →
        [('20260401','20260418'), ('20260301','20260331'), ...,
         ('20230101','20230131')]

    如该月无交易日 (春节整月?不存在), 仍会返回, 由下游 _filter_trading_days
    过滤掉. 不因空月中断.
    """
    s_dt = date(int(start[:4]), int(start[4:6]), int(start[6:]))
    e_dt = date(int(end[:4]), int(end[4:6]), int(end[6:]))
    if s_dt > e_dt:
        return []

    segs: list[tuple[str, str]] = []
    cur_end = e_dt
    while cur_end >= s_dt:
        # 本月 1 号
        month_first = cur_end.replace(day=1)
        seg_start = max(month_first, s_dt)
        segs.append((
            seg_start.strftime("%Y%m%d"),
            cur_end.strftime("%Y%m%d"),
        ))
        # 下一段: 上一个月的最后一天
        if month_first <= s_dt:
            break
        cur_end = month_first - timedelta(days=1)
    return segs


def _estimate_min_rows(
    seg_start: str, seg_end: str, fallback: int = 100
) -> int:
    """查 trade_calendar 得该段内 SSE 交易日数 × 0.9, 作为"已覆盖"
    判定阈值. trade_calendar 未灌时返回 fallback.

    月反向模式单段只 ~20 交易日, 必须动态估算, 否则 min_rows=100 会
    永远命中"未覆盖"反复重拉.
    """
    s_iso = f"{seg_start[:4]}-{seg_start[4:6]}-{seg_start[6:]}"
    e_iso = f"{seg_end[:4]}-{seg_end[4:6]}-{seg_end[6:]}"
    try:
        with pg_conn() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM trade_calendar "
                "WHERE exchange='SSE' AND is_open=TRUE "
                "AND cal_date BETWEEN %s AND %s",
                (s_iso, e_iso),
            )
            n = cur.fetchone()[0] or 0
        if n <= 0:
            return fallback
        # 0.9 留停牌余地, 最少 3 行避免过严
        return max(int(n * 0.9), 3)
    except Exception:  # noqa: BLE001
        return fallback


def _has_any_trading_day(seg_start: str, seg_end: str) -> bool:
    """查 trade_calendar 判断该段内是否至少有一个 SSE 交易日.
    trade_calendar 未灌时默认返回 True (走自然日, 不挡路)."""
    s_iso = f"{seg_start[:4]}-{seg_start[4:6]}-{seg_start[6:]}"
    e_iso = f"{seg_end[:4]}-{seg_end[4:6]}-{seg_end[6:]}"
    try:
        with pg_conn() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM trade_calendar "
                "WHERE exchange='SSE' AND is_open=TRUE "
                "AND cal_date BETWEEN %s AND %s LIMIT 1",
                (s_iso, e_iso),
            )
            return cur.fetchone() is not None
    except Exception:  # noqa: BLE001
        return True  # 表不存在/未灌, 不拦截


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


def _fetch_one(
    code: str, start: str, end: str
) -> tuple[str, Optional[list[dict]], Optional[str]]:
    """worker 线程内调用: 拉一只股票并做行规范化. 不访问 DB.

    返回 (code, rows_or_None, error_or_None).
    """
    mgr = get_manager()
    try:
        df = mgr.get_daily_kline(code, start, end)
        rows = df_to_rows(df, COLMAP)
        if not rows:
            return code, None, None
        for r in rows:
            r.setdefault("ts_code", code)
            r["trade_date"] = normalize_date(r.get("trade_date"))
        _normalize_pct(rows)
        # 批内去重 (同 ts_code+trade_date 只保留最后一条)
        dedup: dict = {}
        for r in rows:
            dedup[(r.get("ts_code"), r.get("trade_date"))] = r
        return code, list(dedup.values()), None
    except Exception as e:  # noqa: BLE001
        return code, None, str(e)


def run(
    start: str,
    end: str,
    codes: Optional[List[str]] = None,
    skip_existing: bool = True,
    min_rows: int = 100,
    workers: int = 4,
    range_mode: str = "month_reverse",
) -> int:
    """抓取日K线。

    Args:
        skip_existing: 增量幂等. True 时, 已有 [start,end] 内行数 >= min_rows
            的 ts_code 自动跳过。重启任务不会重复拉取已有数据。
        min_rows: 判断"已覆盖"的最小行数阈值。
        workers: 并发线程数 (2026-04-18 新增). 推荐 4, 新浪源实测 100+ RPS,
            配合 BaseDataAdapter._rate_lock 全局限流不会超限. workers=1 时
            退化为串行(便于排障).
        range_mode:
            - 'month_reverse' (默认): 按月反向切分 [start,end], 从最近月到
              最远月依次采集. 每段内走并发 fetch, 保证近端数据最先落盘.
              跳过 trade_calendar 里无交易日的月份.
            - 'single' / 其它: 整段 [start,end] 一次性采集 (旧行为).
    """
    # month_reverse 在最外层迭代, 每段再走 single 主体
    if range_mode == "month_reverse":
        segs = _iter_month_segments_reverse(start, end)
        if not segs:
            logger.warning(f"[ingest.kline] 空区间: {start}~{end}")
            return 0
        logger.info(
            f"[ingest.kline] 月反向模式: {len(segs)} 段, "
            f"首段={segs[0]}, 末段={segs[-1]}"
        )
        grand_total = 0
        for i, (seg_s, seg_e) in enumerate(segs, 1):
            if not _has_any_trading_day(seg_s, seg_e):
                logger.info(
                    f"[ingest.kline] 段 {i}/{len(segs)} {seg_s}~{seg_e} "
                    f"无交易日, 跳过"
                )
                continue
            logger.info(
                f"[ingest.kline] ===== 段 {i}/{len(segs)} {seg_s}~{seg_e} ====="
            )
            grand_total += _run_single_range(
                seg_s, seg_e, codes=codes,
                skip_existing=skip_existing, min_rows=min_rows,
                workers=workers,
            )
        logger.info(
            f"[ingest.kline] 月反向完成: 所有段累计 {grand_total} 行"
        )
        return grand_total

    return _run_single_range(
        start, end, codes=codes,
        skip_existing=skip_existing, min_rows=min_rows,
        workers=workers,
    )


def _run_single_range(
    start: str,
    end: str,
    codes: Optional[List[str]] = None,
    skip_existing: bool = True,
    min_rows: int = 100,
    workers: int = 4,
) -> int:
    """整段 [start,end] 的采集主体 (旧 run() 实现). 由 run() 的
    range_mode='month_reverse' 分段调度, 或直接走 single 模式.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    # 触发 manager 在主线程先初始化(避免 worker 竞态建 adapter)
    get_manager()

    if not codes or codes == ["all"]:
        codes = _load_all_ts_codes()
        if not codes:
            logger.error("[ingest.kline] stock_info 为空，请先 ingest stock-info")
            return 0

    original_n = len(codes)
    if skip_existing:
        # 动态估算 min_rows: 段内预期交易日数 × 0.9 (容忍停牌/上市短)
        # 单月段约 20 交易日, 年段约 240 交易日; 写死 100 对月段不友好.
        effective_min = _estimate_min_rows(start, end, fallback=min_rows)
        covered = _load_covered_codes(start, end, effective_min)
        if covered:
            codes = [c for c in codes if c not in covered]
            logger.info(
                f"[ingest.kline] 增量模式: {len(covered)} 只已覆盖 "
                f"(min_rows={effective_min}), 跳过 / 待拉 {len(codes)} 只 "
                f"(原 {original_n} 只)"
            )

    total = 0
    fail = 0
    n_codes = len(codes)
    logger.info(
        f"[ingest.kline] 开始: {n_codes} 只股票, {start}~{end}, workers={workers}"
    )

    # workers=1 时走串行路径, 避免线程池调度开销
    if workers <= 1:
        with pg_conn() as conn:
            for idx, code in enumerate(codes, 1):
                code, rows, err = _fetch_one(code, start, end)
                if err:
                    fail += 1
                    logger.warning(f"[ingest.kline] {code} 失败: {err}")
                    continue
                if not rows:
                    continue
                try:
                    n = upsert_kline_daily(conn, rows)
                    total += n
                except Exception as e:  # noqa: BLE001
                    fail += 1
                    try:
                        conn.rollback()
                    except Exception:
                        pass
                    logger.warning(f"[ingest.kline] {code} upsert 失败: {e}")
                if idx % 100 == 0:
                    conn.commit()
                    logger.info(
                        f"[ingest.kline] 进度 {idx}/{n_codes}  累计 {total} 行"
                    )
            conn.commit()
        logger.info(f"[ingest.kline] 完成(串行): 共 {total} 行, 失败 {fail} 只")
        return total

    # 并发路径: fetch 在线程池, upsert 串行在主线程
    # 原因: psycopg2 connection 非线程安全, 且同表 upsert 并发会拉高 lock;
    # 实测 fetch 是瓶颈(HTTP 0.5~2s), upsert 单行 <1ms, 完全跟得上.
    with pg_conn() as conn, ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(_fetch_one, c, start, end): c for c in codes}
        done_n = 0
        for fut in as_completed(futures):
            done_n += 1
            try:
                code, rows, err = fut.result()
            except Exception as e:  # noqa: BLE001
                fail += 1
                logger.warning(f"[ingest.kline] future 异常: {e}")
                continue
            if err:
                fail += 1
                logger.warning(f"[ingest.kline] {code} 失败: {err}")
            elif rows:
                try:
                    n = upsert_kline_daily(conn, rows)
                    total += n
                except Exception as e:  # noqa: BLE001
                    fail += 1
                    try:
                        conn.rollback()
                    except Exception:
                        pass
                    logger.warning(f"[ingest.kline] {code} upsert 失败: {e}")
            if done_n % 100 == 0:
                conn.commit()
                logger.info(
                    f"[ingest.kline] 进度 {done_n}/{n_codes}  累计 {total} 行  失败 {fail}"
                )
        conn.commit()

    logger.info(
        f"[ingest.kline] 完成(并发 workers={workers}): 共 {total} 行, 失败 {fail} 只"
    )
    return total
