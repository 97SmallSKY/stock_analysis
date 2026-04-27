"""STO-9 告警中心

系统化告警落盘 + 日志。熔断触发 / 批任务失败 / 永久性采集错误统一走这里。

使用方式:
    from backend.core.alerts import send_alert, AlertLevel, AlertCategory
    send_alert(AlertLevel.CRITICAL, AlertCategory.NETWORK, source="akshare",
               message="熔断触发", details={"failure_count": 5})

设计:
- 写 DB 失败时不抛异常 (告警不能阻塞主流程), 降级到 logger
- 不做 webhook (留给 Ops 层用 cron 轮询 alert_log 做分发)
- 线程安全: 每次新开 pg_conn, 不共享 cursor
"""

from __future__ import annotations

import json
import logging
from enum import Enum
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


class AlertLevel(str, Enum):
    INFO = "INFO"
    WARN = "WARN"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class AlertCategory(str, Enum):
    NETWORK = "NETWORK"            # 网络层 / 熔断
    API = "API"                    # 上游 API 永久性错误 (限额/鉴权)
    BATCH_JOB = "BATCH_JOB"        # 日终批任务失败 (lhb / score)
    DATA_QUALITY = "DATA_QUALITY"  # 解析/落库异常


_ConnFactory = Callable[[], Any]


def send_alert(
    level: AlertLevel,
    category: AlertCategory,
    source: str,
    message: str,
    details: Optional[dict] = None,
    conn_factory: Optional[_ConnFactory] = None,
) -> bool:
    """发送告警: 写日志 + 落 alert_log 表。

    返回是否成功落库。写库失败不抛异常, 仅降级到日志。
    conn_factory 默认使用 backend.db.session.pg_conn, 测试时可注入 mock。
    """
    log_fn = {
        AlertLevel.INFO: logger.info,
        AlertLevel.WARN: logger.warning,
        AlertLevel.ERROR: logger.error,
        AlertLevel.CRITICAL: logger.critical,
    }[level]
    log_fn(
        f"[ALERT:{level.value}:{category.value}] source={source} {message}"
        + (f" details={details}" if details else "")
    )

    if conn_factory is None:
        try:
            from backend.db.session import pg_conn
            conn_factory = pg_conn
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[alerts] 无法加载 pg_conn, 跳过落库: {e}")
            return False

    details_json = json.dumps(details, ensure_ascii=False) if details else None
    try:
        with conn_factory() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO alert_log (level, category, source, message, details) "
                    "VALUES (%s, %s, %s, %s, %s::jsonb)",
                    (level.value, category.value, source, message, details_json),
                )
            conn.commit()
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[alerts] 落库失败 (仅记日志): {type(e).__name__}: {e}")
        return False
