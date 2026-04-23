"""龙虎榜入库 CLI 入口(薄壳)。

历史上本模块实现过完整的单日抓取 + 主表入库 + stock_info 骨架补齐 + 同日合并逻辑
(W2 版,只写主表)。W3 之后席位入库 + 领域 parser/ingestor 迁移到
`backend/domain/lhb/`,本模块退化为 CLI 薄壳 —— 仅负责:

  - 交易日历加载(`_load_trading_days`,sentiment 仍在复用)
  - 自然日回退(`_daterange`)
  - `run(start, end)` 将入库任务委托给 `backend.domain.lhb.daily_job.run_range`,
    与 `scripts/backfill_lhb.py` 行为完全一致(主表 + 席位 + 骨架 + 末次 aggregate)。

变更历史:
  - 2025-04-18:移除重复实现(COLMAP / _ensure_stock_info_skel / _dedup_same_key /
    _load_covered_days),统一走 domain 层。保留函数签名 `run(start, end, skip_existing=True)`
    以兼容 `python -m backend.cli ingest lhb` 调用。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from backend.db.session import pg_conn

logger = logging.getLogger(__name__)


def _daterange(start: str, end: str):
    """自然日迭代器(YYYYMMDD)。交易日历不可用时的回退路径。"""
    s = datetime.strptime(start, "%Y%m%d")
    e = datetime.strptime(end, "%Y%m%d")
    cur = s
    while cur <= e:
        yield cur.strftime("%Y%m%d")
        cur += timedelta(days=1)


def _load_trading_days(start: str, end: str) -> list[str]:
    """从 trade_calendar 读 (exchange=SSE, is_open=TRUE) 的交易日。

    sentiment 模块也在复用这个函数,保留以避免扩散副作用。
    """
    with pg_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT to_char(cal_date,'YYYYMMDD') FROM trade_calendar "
            "WHERE exchange='SSE' AND is_open=TRUE "
            "AND cal_date BETWEEN %s AND %s ORDER BY cal_date",
            (f"{start[:4]}-{start[4:6]}-{start[6:]}",
             f"{end[:4]}-{end[4:6]}-{end[6:]}"),
        )
        return [r[0] for r in cur.fetchall()]


def run(start: str, end: str, skip_existing: bool = True) -> dict:
    """抓取龙虎榜(区间),委托给 domain.lhb.daily_job.run_range。

    Args:
        start: YYYYMMDD
        end: YYYYMMDD
        skip_existing: 已灌过该 trade_date 的天会被跳过(断点续跑)。

    Returns:
        dict:{total_days, done_days, skipped, fail, main_rows, seat_rows}
    """
    # 延迟导入:避免模块导入期触发 data_gateway 的 heavy 初始化
    from backend.data_gateway.manager import DataSourceManager
    from backend.domain.lhb.daily_job import run_range

    # STO-72: 必须 initialize(),否则 get_lhb_detail 抛「未初始化」被 catch 成 warning,
    # 导致 daily_update 日志 OK 但 0 行入库(静默数据丢失,断档 4 天才被发现)
    manager = DataSourceManager()
    manager.initialize()
    trade_days = _load_trading_days(start, end) or list(_daterange(start, end))
    logger.info(
        f"[ingest.lhb] 待处理 {len(trade_days)} 天 [{start} -> {end}], "
        f"skip_existing={skip_existing}"
    )

    summary = run_range(
        pg_conn,
        manager,
        trade_days,
        fetch_seats=True,
        aggregate_at_end=True,
        skip_existing=skip_existing,
    )
    logger.info(f"[ingest.lhb] 完成: {summary}")
    return summary


def run_seats(
    start: str,
    end: str,
    skip_existing: bool = True,
    top_n: int | None = None,
    reverse: bool = False,
) -> dict:
    """仅回填 lhb_seats(主表已齐时用)。

    Args:
        skip_existing: True=跳过 lhb_seats 已出现过的日期
        top_n: 每日仅回填主表 l_net DESC 前 N 只的 seats(None=全量)
        reverse: True=从 end 往 start 倒序回填(便于从最新一日先拿到近期数据)
    """
    from backend.data_gateway.manager import DataSourceManager
    from backend.domain.lhb.daily_job import run_seats_only_range

    manager = DataSourceManager()
    manager.initialize()
    trade_days = _load_trading_days(start, end) or list(_daterange(start, end))
    if reverse:
        trade_days = list(reversed(trade_days))
    logger.info(
        f"[ingest.lhb-seats] 待处理 {len(trade_days)} 天 [{start} -> {end}], "
        f"skip_existing={skip_existing}, top_n={top_n}, reverse={reverse}"
    )

    summary = run_seats_only_range(
        pg_conn,
        manager,
        trade_days,
        skip_existing=skip_existing,
        top_n=top_n,
    )
    logger.info(f"[ingest.lhb-seats] 完成: {summary}")
    return summary
