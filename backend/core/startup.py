"""
进程启动初始化

职责:
1. 清除环境代理 —— 本项目所有出站都是国内源 (AKShare/Sina/EastMoney)，
   jd 服务器 shell 默认注入的 SG 代理会导致国内接口异常/超时，必须清掉。
2. 统一日志配置。

在 FastAPI / Celery / APScheduler / 任意入口脚本的最开头 `import` 即可。
"""

import logging
import os
import sys


def clear_outbound_proxy_env() -> None:
    """清掉 HTTP(S)_PROXY，所有适配器都应直连国内源。"""
    removed = []
    for k in (
        "HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
        "ALL_PROXY", "all_proxy",
    ):
        if os.environ.pop(k, None) is not None:
            removed.append(k)
    os.environ["NO_PROXY"] = "*"
    os.environ["no_proxy"] = "*"
    if removed:
        logging.getLogger(__name__).info(
            f"[startup] 已清除代理环境变量: {removed}"
        )


def configure_logging(level: str = "INFO") -> None:
    """统一日志格式。"""
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
        force=True,
    )


def bootstrap(log_level: str = None) -> None:
    """入口脚本调用一次即可完成启动前初始化。"""
    clear_outbound_proxy_env()
    configure_logging(log_level or os.getenv("LOG_LEVEL", "INFO"))
