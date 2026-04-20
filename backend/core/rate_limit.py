"""基础限流 (STO-6)。

基于 ``slowapi`` 的简易封装:

- 单例 Limiter(进程内),按客户端 IP 粒度(``get_remote_address``)。
- 内存 backend —— 单 uvicorn 进程够用;未来多副本部署改 Redis backend:
    Limiter(key_func=..., storage_uri="redis://...")
- 豁免路径:``/healthz`` / ``/metrics`` / ``/assets/**`` / ``/ws/**`` /
  SPA fallback(非 ``/api`` 开头的 GET)
- 提供两个命名装饰器:
    * ``default_limit(func)``  —— 只读端点,用 ``settings.auth.rate_limit_default``
    * ``write_limit(func)``    —— 写端点,    用 ``settings.auth.rate_limit_write``
  调用时 **在函数签名上必须有 ``request: Request`` 参数**,slowapi 用它拿 IP。

异常处理
--------
slowapi 超限会抛 ``RateLimitExceeded``,FastAPI 把它映射成 429。
统一处理器见 ``install_rate_limit`` 内部。
"""
from __future__ import annotations

import logging
from typing import Callable

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

try:
    from slowapi import Limiter
    from slowapi.errors import RateLimitExceeded
    from slowapi.middleware import SlowAPIMiddleware
    from slowapi.util import get_remote_address
    from starlette.requests import Request as _StarletteRequest
    _SLOWAPI_AVAILABLE = True
except ImportError:  # pragma: no cover - slowapi 是硬依赖,缺失即启动失败
    _SLOWAPI_AVAILABLE = False
    Limiter = None  # type: ignore
    RateLimitExceeded = Exception  # type: ignore
    SlowAPIMiddleware = None  # type: ignore
    get_remote_address = None  # type: ignore

# slowapi ``exempt_when`` 回调是无参签名 ``() -> bool`` (0.1.9 行为)。
# 所以只能用来控制全局开关,不能按 path 做精细豁免。
# 路径级豁免的做法是: ``/healthz``、``/metrics``、SPA fallback 本身就不挂
# ``@default_limit`` / ``@write_limit`` 装饰器,不进入 Limiter 的计数逻辑。

from backend.config import get_settings

logger = logging.getLogger(__name__)

# 豁免路径前缀:这些路径永不限流
_EXEMPT_PREFIXES = (
    "/healthz",
    "/metrics",
    "/assets/",
    "/ws/",
)


def _exempt_when() -> bool:
    """slowapi ``exempt_when`` 钩子(无参签名):返回 True 则跳过限流。

    slowapi 0.1.9 以 ``self.exempt_when()`` 无参调用。无法从这里访问 request,
    所以路径级豁免通过「不在对应端点上用 @default_limit/@write_limit」实现
    (见 ``/healthz``、``/metrics``、SPA fallback 本身就不挂装饰器)。

    这里只负责全局开关:``RATE_LIMIT_ENABLED=false`` 时所有限流失效。
    """
    return not get_settings().auth.rate_limit_enabled


def _default_limit_value() -> str:
    return get_settings().auth.rate_limit_default


def _write_limit_value() -> str:
    return get_settings().auth.rate_limit_write


# 单例 Limiter。key_func 按 IP,默认无全局 limit(各端点按装饰器声明)。
# ``request_filter`` 是 slowapi 的全局过滤器,签名 ``(request) -> bool``,
# True 表示跳过(用于路径豁免 + 全局开关)。
if _SLOWAPI_AVAILABLE:
    limiter = Limiter(
        key_func=get_remote_address,
        default_limits=[],           # 不设全局默认,避免误伤
        # headers_enabled=False: 端点返回 dict 时 slowapi 会因为想改
        # 响应头而抛 "response must be Response" —— 保守关闭。
        # 客户端能通过 429 状态码判限流;需要 RateLimit 头可后续扩展。
        headers_enabled=False,
    )
else:
    limiter = None  # type: ignore


def default_limit(func: Callable) -> Callable:
    """只读端点限流装饰器。端点签名须含 ``request: Request``。

    全局开关由 ``_exempt_when`` 统一处理;``/healthz`` 等豁免路径本身
    不挂此装饰器即可。
    """
    if limiter is None:
        return func
    return limiter.limit(_default_limit_value, exempt_when=_exempt_when)(func)


def write_limit(func: Callable) -> Callable:
    """写端点限流装饰器(更严)。端点签名须含 ``request: Request``。"""
    if limiter is None:
        return func
    return limiter.limit(_write_limit_value, exempt_when=_exempt_when)(func)


async def _rate_limit_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    """统一 429 响应体。"""
    logger.info(
        "[rate_limit] 429 ip=%s path=%s limit=%s",
        get_remote_address(request) if get_remote_address else "?",
        request.url.path,
        getattr(exc, "detail", "rate limit exceeded"),
    )
    return JSONResponse(
        status_code=429,
        content={
            "detail": "Rate limit exceeded.",
            "limit": str(getattr(exc, "detail", "")),
        },
    )


def install_rate_limit(app: FastAPI) -> None:
    """在 FastAPI app 上装配 Limiter + 异常处理器 + 中间件。

    若 slowapi 未安装或 ``RATE_LIMIT_ENABLED=false`` 仍会装配中间件,
    但 ``_exempt_when`` 会让所有请求跳过 —— 好处是开关切换无须重启时改代码。
    """
    if not _SLOWAPI_AVAILABLE:
        logger.warning(
            "[rate_limit] slowapi 未安装,限流功能被跳过。"
            "pip install slowapi>=0.1.9 启用。"
        )
        return

    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_handler)
    app.add_middleware(SlowAPIMiddleware)
    logger.info(
        "[rate_limit] installed: default=%s write=%s enabled=%s",
        _default_limit_value(),
        _write_limit_value(),
        get_settings().auth.rate_limit_enabled,
    )
