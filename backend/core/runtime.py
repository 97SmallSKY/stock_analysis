"""W12-7 运行时 wire: 把 W5-W11 的各子系统在 FastAPI 启动时组装起来.

组件:
    RedisCache            /api/dashboard etc. 缓存 (W11-2)
    SnapshotPublisher     盘中快照 -> Redis pubsub (W8-4)
    IntradaySnapshotScheduler   盘中 10s 抓快照 + zt_pool + 清订阅 (W8-3)
    WeeklyReportScheduler APScheduler 周一 09:00 生成周报 (W10-2)
    RobotRegistry         app.state.robot_registry 初始化空 dict (W9/W12-1)

开关 (env):
    RUNTIME_ENABLE_INTRADAY=1     default 0 (盘中调度)
    RUNTIME_ENABLE_WEEKLY=1       default 0 (周报调度)
    RUNTIME_ENABLE_REDIS=1        default 0 (Redis 缓存 + pubsub)
    REDIS_URL=redis://localhost:6379/0

所有组件都通过 set_default_cache / app.state.xxx 供路由读取,
任一组件失败都 logger.warning, 不阻塞 FastAPI 启动.
"""
from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from fastapi import FastAPI

logger = logging.getLogger(__name__)


def _env_on(name: str) -> bool:
    return os.getenv(name, "").strip() in ("1", "true", "True", "yes")


def _default_redis_url() -> str:
    return os.getenv("REDIS_URL", "redis://localhost:6379/0")


# ------------------------------------------------------------------
# Redis (cache + pubsub)
# ------------------------------------------------------------------


def setup_redis(app: "FastAPI", *, url: Optional[str] = None) -> bool:
    """尝试接 Redis. 成功时:
      - app.state.redis         同步 redis 客户端 (给 /ws/snapshot 用)
      - app.state.cache         RedisCache 实例
      - set_default_cache(cache) 注入全局, @cached 自动命中
      - app.state.snapshot_publisher    SnapshotPublisher 实例
    返回 True=成功.
    """
    try:
        import redis
    except ImportError:
        logger.warning("[runtime] redis 未安装, 跳过")
        return False
    url = url or _default_redis_url()
    try:
        client = redis.Redis.from_url(url, decode_responses=True, socket_timeout=3)
        client.ping()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[runtime] Redis ping 失败 ({url}): {e}; 跳过缓存/pubsub")
        return False

    app.state.redis = client

    try:
        from backend.cache import RedisCache, set_default_cache
        cache = RedisCache(client)
        app.state.cache = cache
        set_default_cache(cache)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[runtime] RedisCache 初始化失败: {e}")

    try:
        from backend.domain.realtime.pubsub import SnapshotPublisher
        app.state.snapshot_publisher = SnapshotPublisher(redis_client=client)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[runtime] SnapshotPublisher 初始化失败: {e}")

    logger.info(f"[runtime] Redis 就绪: {url}")
    return True


# ------------------------------------------------------------------
# Intraday scheduler (W8-3)
# ------------------------------------------------------------------


def setup_intraday_scheduler(app: "FastAPI") -> bool:
    """启动盘中 10s 快照 scheduler.

    依赖:
      app.state.manager                DataSourceManager (bootstrap 已装)
      app.state.snapshot_publisher     可选, 有则盘中推 Redis
    """
    try:
        from backend.domain.realtime.scheduler import IntradaySnapshotScheduler
        from backend.domain.realtime import snapshot_writer, subscription_pool
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[runtime] 加载 IntradayScheduler 失败: {e}")
        return False

    mgr = getattr(app.state, "manager", None)
    if mgr is None:
        logger.warning("[runtime] manager 未初始化, 跳过 IntradayScheduler")
        return False

    from backend.db.session import pg_conn

    def _fetch_snapshot(codes):
        try:
            ad = mgr._router._adapters.get(None)   # placeholder, 具体由 router 路由
            # 走 manager 的 Router (未直接暴露 get_intraday_snapshot, 用 adapter 兜底)
            from backend.data_gateway.category import AdapterName
            crawler = mgr._router._adapters.get(AdapterName.CRAWLER)
            if crawler is None:
                return None
            return crawler.get_intraday_snapshot(codes)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[runtime] fetch_snapshot failed: {e}")
            return None

    def _fetch_zt_pool():
        try:
            ak = mgr._router._adapters
            # 轻量: 暂不抓涨停池 (需要 AKShare stock_zt_pool_em), W13 补
            return []
        except Exception:  # noqa: BLE001
            return []

    def _insert_snapshots(df):
        try:
            return snapshot_writer.insert_snapshots(pg_conn, df)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[runtime] insert_snapshots failed: {e}")
            return 0

    def _get_subs():
        try:
            return subscription_pool.get_active_codes(pg_conn)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[runtime] get_active_codes failed: {e}")
            return []

    def _refresh_zt(codes):
        try:
            return subscription_pool.refresh_zt_today(pg_conn, codes)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[runtime] refresh_zt failed: {e}")
            return 0

    def _cleanup():
        try:
            return subscription_pool.cleanup_expired(pg_conn)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[runtime] cleanup failed: {e}")
            return 0

    publisher = getattr(app.state, "snapshot_publisher", None)

    sched = IntradaySnapshotScheduler(
        manager=mgr,
        fetch_snapshot=_fetch_snapshot,
        fetch_zt_pool=_fetch_zt_pool,
        insert_snapshots=_insert_snapshots,
        get_subscription_codes=_get_subs,
        refresh_zt_today=_refresh_zt,
        cleanup_expired=_cleanup,
        publish=publisher if publisher else None,
    )
    try:
        sched.start()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[runtime] IntradayScheduler.start 失败: {e}")
        return False
    app.state.intraday_scheduler = sched
    logger.info("[runtime] IntradayScheduler 已启动 (10s 盘中快照)")
    return True


# ------------------------------------------------------------------
# Weekly report scheduler (W10-2)
# ------------------------------------------------------------------


def setup_weekly_scheduler(app: "FastAPI") -> bool:
    try:
        from backend.domain.reports.scheduler import WeeklyReportScheduler
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[runtime] 加载 WeeklyReportScheduler 失败: {e}")
        return False
    from backend.db.session import pg_conn

    def _sink(report: dict):
        # 默认只记日志, 生产可换飞书 webhook
        logger.info(
            f"[WeeklyReport] {report.get('start_date')}~{report.get('end_date')} "
            f"lhb_total={report.get('lhb_total')} "
            f"robots={len(report.get('robot_summary') or [])} "
            f"backtests={len(report.get('backtest_top') or [])}"
        )

    sched = WeeklyReportScheduler(conn_factory=pg_conn, sink=_sink)
    try:
        sched.start()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[runtime] WeeklyReport.start 失败: {e}")
        return False
    app.state.weekly_scheduler = sched
    logger.info("[runtime] WeeklyReportScheduler 已启动 (mon 09:00)")
    return True


# ------------------------------------------------------------------
# Robot registry (W9)
# ------------------------------------------------------------------


def setup_robot_registry(app: "FastAPI") -> None:
    if not hasattr(app.state, "robot_registry"):
        app.state.robot_registry = {}
    logger.info("[runtime] robot_registry 就绪 (灰度默认 OFF, API /enable 开启)")


# ------------------------------------------------------------------
# 主入口
# ------------------------------------------------------------------


def wire_runtime(app: "FastAPI") -> dict:
    """统一入口: 根据 env 开关组装各子系统.

    返回一个 dict {component: bool}, 便于 /healthz 检查 / 日志打印.
    """
    status = {"redis": False, "intraday": False, "weekly": False, "robots": True}

    setup_robot_registry(app)

    if _env_on("RUNTIME_ENABLE_REDIS"):
        status["redis"] = setup_redis(app)

    if _env_on("RUNTIME_ENABLE_INTRADAY"):
        status["intraday"] = setup_intraday_scheduler(app)

    if _env_on("RUNTIME_ENABLE_WEEKLY"):
        status["weekly"] = setup_weekly_scheduler(app)

    app.state.runtime_status = status
    return status


def shutdown_runtime(app: "FastAPI") -> None:
    """反向关闭 scheduler. 幂等."""
    for attr in ("intraday_scheduler", "weekly_scheduler"):
        s = getattr(app.state, attr, None)
        if s is not None:
            try:
                s.stop()
            except Exception:  # noqa: BLE001
                pass
