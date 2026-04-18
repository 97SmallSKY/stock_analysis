"""W10-2 周报聚合

汇总过去 7 天 (或自定义区间) 三类指标:

1. 龙虎榜: 上榜股票数、原因 Top 5、净买入 Top 10
2. 机器人: 每个 robot 的累计盈亏 (cash_ledger + sim_trades)
3. 回测: 最近 7 天内完成的 backtest_tasks (按 annual_return 降序)

纯函数 build_weekly_report(conn_factory, start, end) -> WeeklyReport.
所有 SQL 参数化, psycopg2 游标; 每块查询独立 try/except, 单块失败不影响其它.
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class WeeklyReport:
    start_date: date
    end_date: date
    lhb_total: int = 0
    lhb_top_reasons: list[dict] = field(default_factory=list)
    lhb_top_net: list[dict] = field(default_factory=list)
    robot_summary: list[dict] = field(default_factory=list)
    backtest_top: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
            "lhb_total": self.lhb_total,
            "lhb_top_reasons": list(self.lhb_top_reasons),
            "lhb_top_net": list(self.lhb_top_net),
            "robot_summary": list(self.robot_summary),
            "backtest_top": list(self.backtest_top),
            "errors": list(self.errors),
        }


def _rows(cur) -> list[dict]:
    cols = [c.name for c in cur.description] if cur.description else []
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _lhb_section(conn, start: date, end: date, report: WeeklyReport) -> None:
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT COUNT(*) AS n
                   FROM lhb_stock_daily
                   WHERE trade_date BETWEEN %s AND %s""",
                (start, end),
            )
            row = cur.fetchone()
            report.lhb_total = int(row[0]) if row and row[0] else 0

            cur.execute(
                """SELECT reason, COUNT(*) AS cnt
                   FROM lhb_stock_daily
                   WHERE trade_date BETWEEN %s AND %s
                     AND reason IS NOT NULL AND reason <> ''
                   GROUP BY reason
                   ORDER BY cnt DESC
                   LIMIT 5""",
                (start, end),
            )
            report.lhb_top_reasons = _rows(cur)

            # 列对齐:实际列名 l_net,对外别名 net_amount
            cur.execute(
                """SELECT ts_code, trade_date, reason,
                          l_net AS net_amount, score
                   FROM lhb_stock_daily
                   WHERE trade_date BETWEEN %s AND %s
                   ORDER BY l_net DESC NULLS LAST
                   LIMIT 10""",
                (start, end),
            )
            rows = _rows(cur)
            for r in rows:
                if r.get("trade_date") is not None:
                    r["trade_date"] = r["trade_date"].isoformat()
            report.lhb_top_net = rows
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[weekly] lhb_section failed: {e}")
        report.errors.append(f"lhb_section: {e}")


def _robot_section(conn, start: date, end: date, report: WeeklyReport) -> None:
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT robot_id,
                          SUM(CASE WHEN entry_type = 'SELL' THEN amount ELSE 0 END) AS sells,
                          SUM(CASE WHEN entry_type = 'BUY' THEN amount ELSE 0 END) AS buys,
                          SUM(CASE WHEN entry_type = 'COMMISSION' THEN amount ELSE 0 END) AS fees,
                          COUNT(*) AS entries
                   FROM cash_ledger
                   WHERE biz_date BETWEEN %s AND %s
                   GROUP BY robot_id
                   ORDER BY robot_id""",
                (start, end),
            )
            rows = _rows(cur)
            for r in rows:
                sells = float(r.get("sells") or 0)
                buys = float(r.get("buys") or 0)
                fees = float(r.get("fees") or 0)
                r["net_pnl"] = round(sells - buys - fees, 2)
            report.robot_summary = rows
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[weekly] robot_section failed: {e}")
        report.errors.append(f"robot_section: {e}")


def _backtest_section(conn, start: date, end: date, report: WeeklyReport) -> None:
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT task_id, strategy_name,
                          annual_return, sharpe_ratio, max_drawdown, win_rate,
                          finished_at
                   FROM backtest_tasks
                   WHERE status = 'SUCCESS'
                     AND finished_at IS NOT NULL
                     AND finished_at::date BETWEEN %s AND %s
                   ORDER BY annual_return DESC NULLS LAST
                   LIMIT 10""",
                (start, end),
            )
            rows = _rows(cur)
            for r in rows:
                ft = r.get("finished_at")
                if ft is not None:
                    r["finished_at"] = ft.isoformat()
            report.backtest_top = rows
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[weekly] backtest_section failed: {e}")
        report.errors.append(f"backtest_section: {e}")


def build_weekly_report(
    conn_factory,
    *,
    end: Optional[date] = None,
    days: int = 7,
) -> WeeklyReport:
    """聚合上周数据. end 默认为 today; 区间 [end - days + 1, end].

    conn_factory 是上下文管理器 (默认 backend.db.session.pg_conn).
    """
    end = end or date.today()
    start = end - timedelta(days=days - 1)
    report = WeeklyReport(start_date=start, end_date=end)
    try:
        with conn_factory() as conn:
            _lhb_section(conn, start, end, report)
            _robot_section(conn, start, end, report)
            _backtest_section(conn, start, end, report)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[weekly] build failed: {e}")
        report.errors.append(f"build: {e}")
    return report
