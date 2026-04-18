"""W10-2 周报定时调度: 每周一 09:00 生成上周报告.

轻量 APScheduler 包装; 生成的报告通过回调 sink(report_dict) 落地 (DB/邮件/飞书).
默认 sink 只打 INFO 日志, 便于单测.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Callable, Optional

logger = logging.getLogger(__name__)


class WeeklyReportScheduler:
    def __init__(
        self,
        *,
        conn_factory,
        sink: Optional[Callable[[dict], None]] = None,
        build_fn: Optional[Callable] = None,
    ):
        self._conn_factory = conn_factory
        self._sink = sink or self._default_sink
        # 测试注入: 默认用真实 build_weekly_report
        if build_fn is None:
            from backend.domain.reports.weekly import build_weekly_report
            build_fn = build_weekly_report
        self._build = build_fn
        self._scheduler = None
        self.last_report: Optional[dict] = None

    @staticmethod
    def _default_sink(rep: dict) -> None:
        logger.info(f"[WeeklyReport] {rep.get('start_date')}~{rep.get('end_date')} "
                    f"lhb_total={rep.get('lhb_total')} "
                    f"robots={len(rep.get('robot_summary') or [])} "
                    f"backtests={len(rep.get('backtest_top') or [])}")

    def start(self):
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.cron import CronTrigger
        if self._scheduler is not None:
            return self._scheduler
        sched = BackgroundScheduler(timezone="Asia/Shanghai")
        # 每周一 09:00 生成上周报告 (end = 昨天=周日, 往前 7 天)
        sched.add_job(
            self.run_once, CronTrigger(day_of_week="mon", hour=9, minute=0),
            id="weekly_report",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        sched.start()
        self._scheduler = sched
        logger.info("[WeeklyReport] scheduler started (mon 09:00)")
        return sched

    def stop(self):
        if self._scheduler is not None:
            try:
                self._scheduler.shutdown(wait=False)
            except Exception:  # noqa: BLE001
                pass
            self._scheduler = None

    def run_once(self, *, end: Optional[date] = None, days: int = 7) -> dict:
        """立即生成并通过 sink 落地. 返回 dict."""
        end = end or date.today() - timedelta(days=1)
        try:
            rep = self._build(self._conn_factory, end=end, days=days)
            d = rep.to_dict() if hasattr(rep, "to_dict") else rep
            self.last_report = d
            try:
                self._sink(d)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[WeeklyReport] sink failed: {e}")
            return d
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[WeeklyReport] run failed: {e}")
            return {"error": str(e)}
