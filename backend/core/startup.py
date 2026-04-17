"""
进程启动初始化

职责:
1. 统一日志配置
2. (可选)按环境变量决定是否清代理——默认"不清"

设计调整 (2026-04-18 / W2_PROGRESS.md P0-4):
  早期版本 bootstrap 默认清掉所有 HTTP(S)_PROXY 并设 NO_PROXY=*,
  理由是"本项目出站都是国内源"。但对比 main 分支后发现这条假设
  不完全成立:
    - jd 服务器配的 xray 代理在正常工作时,通过代理访问
      AKShare/新浪/东财接口反而 < 0.5s 且稳定;
    - 京东云 IP 直连东财若干 API 会被 RST(黑名单),代理才是出路;
    - 本地 macOS 环境本来就没代理,清不清都一样。
  因此默认行为改为**尊重现有环境**,只在显式要求时清代理:
    CLEAR_PROXY_ON_STARTUP=1  (默认 0)

在 FastAPI / Celery / APScheduler / 任意入口脚本的最开头 `import`
bootstrap 即可。
"""

import logging
import os
import sys


def clear_outbound_proxy_env() -> None:
    """清掉 HTTP(S)_PROXY 与 NO_PROXY=*(仅在显式请求时调用)。

    适用场景:确认代理出站已失效、且所有目标站都支持 jd 直连时使用。
    默认不调用。
    """
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


def _proxy_summary() -> str:
    keys = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
            "http_proxy", "https_proxy", "all_proxy", "NO_PROXY")
    vals = {k: os.environ.get(k) for k in keys if os.environ.get(k)}
    return "代理环境: " + (", ".join(f"{k}={v}" for k, v in vals.items()) if vals else "none")


def bootstrap(log_level: str = None) -> None:
    """入口脚本调用一次即可完成启动前初始化。

    行为:
      - 默认:只配日志,不动代理
      - `CLEAR_PROXY_ON_STARTUP=1`:额外清掉所有 HTTP(S)_PROXY 并
        设 NO_PROXY=*(兼容旧部署场景)
    """
    configure_logging(log_level or os.getenv("LOG_LEVEL", "INFO"))

    if os.getenv("CLEAR_PROXY_ON_STARTUP", "").strip() in ("1", "true", "True"):
        clear_outbound_proxy_env()

    logging.getLogger(__name__).info(f"[startup] {_proxy_summary()}")
