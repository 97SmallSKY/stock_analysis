"""龙虎榜单日任务 (domain 层, 纯组合).

职责:
  给定 `trade_date` (YYYYMMDD), 调用 DataGatewayManager 拉当日
  龙虎榜明细 + 席位, parse → merge → attach seats → ingest,
  可选跟一把当日 aggregate.

设计要点:
- 不直接依赖 backend.data_gateway.manager 的 singleton, manager/conn
  都由调用方注入, 方便测试 (用 FakeManager / FakeConn).
- seats 拉取失败不阻断主表写入 (历史数据源容易 400, 告警即可).
- 返回 dict 指标(main_rows / seat_rows / skel_rows / aggregated), 供
  scheduler 写日志 / 回填脚本做进度汇总.
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any, Callable, Optional

from backend.domain.lhb.parser import (
    LhbRecord,
    merge_same_key,
    parse_lhb_dataframe,
    parse_seats_dataframe,
)
from backend.domain.lhb.ingestor import ingest_records

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------
# 工具
# --------------------------------------------------------------------


def _to_date(v: Any) -> Optional[date]:
    if v is None:
        return None
    if isinstance(v, date) and not isinstance(v, datetime):
        return v
    if isinstance(v, datetime):
        return v.date()
    s = str(v)
    if len(s) == 8 and s.isdigit():
        return date(int(s[:4]), int(s[4:6]), int(s[6:]))
    try:
        return datetime.fromisoformat(s).date()
    except Exception:
        return None


def attach_seats(
    records: list[LhbRecord],
    seats_by_key: dict[tuple, list],
) -> int:
    """按 (ts_code, trade_date) 把 parse_seats_dataframe 出来的席位挂到主表记录上.

    - seats_by_key 的 key 使用的是 DataFrame 原始 trade_date (可能是 str / date)
    - 为最大容错, 比较时双向 normalize 成 date 再匹配
    返回成功 attach 的记录数.
    """
    if not records or not seats_by_key:
        return 0
    # 预归一化 key 的 date 侧
    norm_map: dict[tuple, list] = {}
    for (ts, td), seats in seats_by_key.items():
        d = _to_date(td)
        norm_map[(ts, d)] = seats

    hit = 0
    for r in records:
        d = _to_date(r.trade_date)
        key = (r.ts_code, d)
        seats = norm_map.get(key)
        if seats:
            r.seats = list(seats)
            hit += 1
    return hit


# --------------------------------------------------------------------
# 主入口
# --------------------------------------------------------------------


def run_single_day(
    conn,
    manager,
    trade_date: str,
    *,
    fetch_seats: bool = True,
    aggregate: bool = False,
    aggregator: Optional[Callable] = None,
) -> dict:
    """执行单日龙虎榜入库.

    Args:
        conn: psycopg2 connection
        manager: 具备 get_lhb_detail / get_lhb_seats 的对象(DataGatewayManager 或测试 fake)
        trade_date: 'YYYYMMDD'
        fetch_seats: 是否抓席位(盘后凌晨对全量回填可能跳过以节省请求)
        aggregate: 完成后是否跑当日 aggregate
        aggregator: 注入 aggregator.aggregate_all, 便于测试

    Returns:
        {main_rows, seat_rows, skel_rows, aggregated}
    """
    result = {
        "trade_date": trade_date,
        "main_rows": 0,
        "seat_rows": 0,
        "skel_rows": 0,
        "seats_attached": 0,
        "aggregated": False,
    }

    # 1. 主表
    try:
        df = manager.get_lhb_detail(trade_date)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[lhb.daily_job] {trade_date} get_lhb_detail 失败: {e}")
        return result
    records = parse_lhb_dataframe(df)
    if not records:
        logger.info(f"[lhb.daily_job] {trade_date} 无龙虎榜记录")
        return result
    records = merge_same_key(records)

    # 2. 席位 (失败降级)
    if fetch_seats:
        try:
            seats_df = manager.get_lhb_seats(trade_date)
            seats_by_key = parse_seats_dataframe(seats_df)
            result["seats_attached"] = attach_seats(records, seats_by_key)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[lhb.daily_job] {trade_date} 席位抓取失败: {e}")

    # 3. 入库
    ingest_ret = ingest_records(conn, records)
    result["main_rows"] = ingest_ret["main_rows"]
    result["seat_rows"] = ingest_ret["seat_rows"]
    result["skel_rows"] = ingest_ret["skel_rows"]
    conn.commit()

    # 4. aggregate (可选)
    if aggregate:
        try:
            agg_fn = aggregator
            if agg_fn is None:
                from backend.domain.lhb.aggregator import aggregate_all as agg_fn  # type: ignore
            as_of = _to_date(trade_date)
            agg_fn(conn, as_of=as_of)
            result["aggregated"] = True
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[lhb.daily_job] {trade_date} aggregate 失败: {e}")

    logger.info(
        f"[lhb.daily_job] {trade_date} 完成: main={result['main_rows']} "
        f"seats={result['seat_rows']} skel={result['skel_rows']} "
        f"attached={result['seats_attached']} agg={result['aggregated']}"
    )
    return result


# --------------------------------------------------------------------
# 区间批量 (backfill 用)
# --------------------------------------------------------------------


def run_range(
    conn_factory: Callable,
    manager,
    trade_days: list[str],
    *,
    fetch_seats: bool = True,
    aggregate_at_end: bool = True,
    skip_existing: bool = True,
    aggregator: Optional[Callable] = None,
    progress_cb: Optional[Callable[[int, int, dict], None]] = None,
) -> dict:
    """按给定交易日列表顺序执行 run_single_day.

    Args:
        conn_factory: 无参 callable, 返回 psycopg2 conn (或 ctx manager). 为避免
                      超长事务, 每 N 天会 commit (run_single_day 内部已 commit 过).
        manager: DataGatewayManager
        trade_days: ['YYYYMMDD', ...]
        skip_existing: 是否跳过 lhb_stock_daily 已覆盖的日期
        aggregate_at_end: 所有日完成后统一 aggregate 一次(避免每天都算)
        progress_cb: (idx, total, single_result) -> None, 方便回填脚本打进度

    Returns:
        {total_days, done_days, skipped, fail, main_rows, seat_rows}
    """
    summary = {
        "total_days": len(trade_days),
        "done_days": 0,
        "skipped": 0,
        "fail": 0,
        "main_rows": 0,
        "seat_rows": 0,
    }

    # 预计算 covered 集合以跳过
    covered: set[str] = set()
    if skip_existing and trade_days:
        try:
            conn = _get_conn(conn_factory)
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT DISTINCT to_char(trade_date, 'YYYYMMDD') "
                        "FROM lhb_stock_daily WHERE trade_date BETWEEN %s AND %s",
                        (
                            f"{trade_days[0][:4]}-{trade_days[0][4:6]}-{trade_days[0][6:]}",
                            f"{trade_days[-1][:4]}-{trade_days[-1][4:6]}-{trade_days[-1][6:]}",
                        ),
                    )
                    covered = {r[0] for r in cur.fetchall()}
            finally:
                _maybe_close(conn)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[lhb.daily_job] 查 covered 失败, 不跳过: {e}")

    for idx, d in enumerate(trade_days, 1):
        if d in covered:
            summary["skipped"] += 1
            continue
        conn = _get_conn(conn_factory)
        try:
            ret = run_single_day(
                conn, manager, d,
                fetch_seats=fetch_seats,
                aggregate=False,
                aggregator=aggregator,
            )
            summary["main_rows"] += ret["main_rows"]
            summary["seat_rows"] += ret["seat_rows"]
            summary["done_days"] += 1
            if progress_cb:
                try:
                    progress_cb(idx, len(trade_days), ret)
                except Exception:  # noqa: BLE001
                    pass
        except Exception as e:  # noqa: BLE001
            summary["fail"] += 1
            logger.warning(f"[lhb.daily_job] {d} 失败: {e}")
            try:
                conn.rollback()
            except Exception:
                pass
        finally:
            _maybe_close(conn)

    # 统一 aggregate
    if aggregate_at_end and trade_days and summary["done_days"] > 0:
        conn = _get_conn(conn_factory)
        try:
            agg_fn = aggregator
            if agg_fn is None:
                from backend.domain.lhb.aggregator import aggregate_all as agg_fn  # type: ignore
            as_of = _to_date(trade_days[-1])
            agg_fn(conn, as_of=as_of)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[lhb.daily_job] 末段 aggregate 失败: {e}")
        finally:
            _maybe_close(conn)

    logger.info(f"[lhb.daily_job] 区间完成: {summary}")
    return summary


# --------------------------------------------------------------------
# conn factory 兼容: 允许 factory 返回 ctx manager 或 直接 conn
# --------------------------------------------------------------------


def _get_conn(factory: Callable):
    obj = factory()
    # 若是 context manager, 进入
    if hasattr(obj, "__enter__"):
        return obj.__enter__()
    return obj


def _maybe_close(conn) -> None:
    # 没法可靠区分是不是 ctx manager, 这里尽量 close
    try:
        close = getattr(conn, "close", None)
        if callable(close):
            close()
    except Exception:  # noqa: BLE001
        pass
