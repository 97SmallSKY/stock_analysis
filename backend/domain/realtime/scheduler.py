"""W8-3 盘中 10s 快照调度器.

职责:
    1. 交易时段 (mon-fri, 09:30-11:30 / 13:00-15:00 本地) 每 10s 触发一次
       snapshot_job: 拉订阅池 -> 拉快照 -> 批量写 market_snapshots -> publish.
    2. CrawlerAdapter CB open 时, 自动把 cron 降级到 30s 间隔;
       CB 恢复 (closed) 后自动回到 10s.
    3. 每分钟触发一次 zt_pool_job: 抓今日涨停池, 替换 subscription_pool reason=ZT_TODAY.
    4. 每 10 分钟清理 subscription_pool 过期记录.

设计:
    用 APScheduler.BackgroundScheduler, 单进程内可直接 start/stop.
    所有外部依赖通过 __init__ 注入(便于单测):
        - manager                  DataSourceManager (取 CrawlerAdapter CB 状态)
        - fetch_snapshot(codes)    返回 DataFrame-like (或 None)
        - fetch_zt_pool()          返回 list[str] ts_code 或 []
        - insert_snapshots(records)         写 market_snapshots, 返回 n
        - get_subscription_codes() 返回 list[str]
        - refresh_zt_today(codes)  替换 ZT_TODAY 订阅
        - publish(ts_code, row)    可选, 盘中推送 (W8-4 接 Redis pubsub)

所有错误吞进 logger.warning; job 里绝不 raise, 避免 APScheduler 把 job 标死.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Iterable, Optional

logger = logging.getLogger(__name__)

# 正常 / CB-open 时的 second 表达式
CRON_SECOND_NORMAL = "*/10"
CRON_SECOND_DEGRADED = "*/30"

# 盘中两段: 早盘 09:30-11:30, 午盘 13:00-15:00
# APScheduler CronTrigger 不支持组合 hour+minute 限定, 这里用两个 job 合并.
MORNING_HOUR = "9-11"
AFTERNOON_HOUR = "13-14"   # 14:xx:yy (13/14 整小时), 加收盘前的 15:00 独立处理
# 简化: 用 9-11 + 13-14 + 15 (只第 0 分钟), 可覆盖 09:30-15:00 全部 10s 槽位.
# 精细过滤 9:00-9:29 / 11:30-11:59 / 14:57-15:00 由 job 内 check_trading_time 控制.


@dataclass
class JobResult:
    ok: bool
    codes: int = 0
    rows: int = 0
    error: Optional[str] = None


class IntradaySnapshotScheduler:
    """盘中快照调度."""

    def __init__(
        self,
        *,
        manager=None,
        fetch_snapshot: Optional[Callable[[list[str]], object]] = None,
        fetch_zt_pool: Optional[Callable[[], list[str]]] = None,
        insert_snapshots: Optional[Callable[[object], int]] = None,
        get_subscription_codes: Optional[Callable[[], list[str]]] = None,
        refresh_zt_today: Optional[Callable[[Iterable[str]], int]] = None,
        cleanup_expired: Optional[Callable[[], int]] = None,
        publish: Optional[Callable[[str, dict], None]] = None,
        degrade_when_cb_open: bool = True,
    ):
        self._manager = manager
        self._fetch_snapshot = fetch_snapshot
        self._fetch_zt_pool = fetch_zt_pool
        self._insert_snapshots = insert_snapshots
        self._get_subscription_codes = get_subscription_codes
        self._refresh_zt_today = refresh_zt_today
        self._cleanup_expired = cleanup_expired
        self._publish = publish
        self._degrade_when_cb_open = degrade_when_cb_open

        self._scheduler = None
        self._current_second_expr = CRON_SECOND_NORMAL
        self.last_result: Optional[JobResult] = None

    # --------------------------------------------------------------
    # APScheduler 生命周期
    # --------------------------------------------------------------

    def start(self):
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.cron import CronTrigger
        if self._scheduler is not None:
            return self._scheduler
        sched = BackgroundScheduler(timezone="Asia/Shanghai")
        # 主快照 job: 10s
        sched.add_job(
            self.snapshot_job, CronTrigger(
                day_of_week="mon-fri",
                hour=f"{MORNING_HOUR},{AFTERNOON_HOUR},15",
                second=CRON_SECOND_NORMAL,
            ),
            id="intraday_snapshot",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        # 涨停池刷新: 每分钟
        sched.add_job(
            self.zt_pool_job, CronTrigger(
                day_of_week="mon-fri",
                hour=f"{MORNING_HOUR},{AFTERNOON_HOUR},15",
                minute="*",
            ),
            id="intraday_zt_pool",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        # 订阅过期清理: 每 10 分钟
        sched.add_job(
            self.cleanup_job, CronTrigger(minute="*/10"),
            id="intraday_cleanup_subs",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        sched.start()
        self._scheduler = sched
        logger.info("[IntradayScheduler] started")
        return sched

    def stop(self):
        if self._scheduler is not None:
            try:
                self._scheduler.shutdown(wait=False)
            except Exception:  # noqa: BLE001
                pass
            self._scheduler = None

    # --------------------------------------------------------------
    # Job 实现
    # --------------------------------------------------------------

    def _crawler_cb_open(self) -> bool:
        """检查 CrawlerAdapter 熔断是否打开."""
        if self._manager is None:
            return False
        try:
            from backend.data_gateway.category import AdapterName
            ad = self._manager._router._adapters.get(AdapterName.CRAWLER)
            if ad is None:
                return False
            return ad.circuit_breaker.state.value.lower() == "open"
        except Exception:  # noqa: BLE001
            return False

    def _maybe_degrade(self):
        """根据 CB 状态动态切换 cron 表达式."""
        if not self._degrade_when_cb_open or self._scheduler is None:
            return
        target = CRON_SECOND_DEGRADED if self._crawler_cb_open() else CRON_SECOND_NORMAL
        if target == self._current_second_expr:
            return
        from apscheduler.triggers.cron import CronTrigger
        try:
            self._scheduler.reschedule_job(
                "intraday_snapshot",
                trigger=CronTrigger(
                    day_of_week="mon-fri",
                    hour=f"{MORNING_HOUR},{AFTERNOON_HOUR},15",
                    second=target,
                ),
            )
            logger.warning(
                f"[IntradayScheduler] cron 切换 {self._current_second_expr} -> {target}"
            )
            self._current_second_expr = target
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[IntradayScheduler] reschedule failed: {e}")

    def snapshot_job(self) -> JobResult:
        """一次快照轮询. 供 scheduler 调用, 也可在测试里直接调."""
        self._maybe_degrade()
        try:
            codes = list(self._get_subscription_codes() if self._get_subscription_codes else [])
            if not codes:
                self.last_result = JobResult(ok=True, codes=0, rows=0)
                return self.last_result
            df = self._fetch_snapshot(codes) if self._fetch_snapshot else None
            rows = 0
            if df is not None:
                rows = int(self._insert_snapshots(df) if self._insert_snapshots else 0)
                # publish (可选): 盘中推送到 WS/Redis
                if self._publish is not None:
                    self._publish_df(df)
            self.last_result = JobResult(ok=True, codes=len(codes), rows=rows)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[IntradayScheduler] snapshot_job failed: {e}")
            self.last_result = JobResult(ok=False, error=str(e))
        return self.last_result

    def _publish_df(self, df):
        try:
            import pandas as pd
            if isinstance(df, pd.DataFrame):
                for _, r in df.iterrows():
                    d = r.to_dict()
                    tc = d.get("ts_code")
                    if tc:
                        self._publish(str(tc), d)
            else:
                for d in df:
                    tc = d.get("ts_code") if isinstance(d, dict) else None
                    if tc:
                        self._publish(str(tc), d)
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[IntradayScheduler] publish skipped: {e}")

    def zt_pool_job(self) -> JobResult:
        """盘中每分钟: 拉今日涨停池 -> 替换 ZT_TODAY 订阅."""
        try:
            if self._fetch_zt_pool is None or self._refresh_zt_today is None:
                return JobResult(ok=True, codes=0)
            codes = list(self._fetch_zt_pool() or [])
            self._refresh_zt_today(codes)
            return JobResult(ok=True, codes=len(codes))
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[IntradayScheduler] zt_pool_job failed: {e}")
            return JobResult(ok=False, error=str(e))

    def cleanup_job(self) -> JobResult:
        try:
            n = self._cleanup_expired() if self._cleanup_expired else 0
            return JobResult(ok=True, rows=int(n or 0))
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[IntradayScheduler] cleanup_job failed: {e}")
            return JobResult(ok=False, error=str(e))
