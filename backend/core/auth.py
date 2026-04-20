"""API 鉴权 (STO-6)。

设计目标
--------
- 简单:Header ``X-API-Key``,与 ``settings.auth.api_keys`` 精确比对。
- 可关闭:``AUTH_ENABLED=false`` 或未配置任何 key 时,``require_api_key`` 直接放行
  (便于本地开发 / 向后兼容当前 jd 部署)。
- 不存密:只校验等值;key 本身存在环境变量,不进 DB、不进日志。
- 常量时间比对 (``hmac.compare_digest``) 防时序侧信道。

用法
----
    from fastapi import APIRouter, Depends
    from backend.core.auth import require_api_key

    router = APIRouter()

    @router.post("/do-thing", dependencies=[Depends(require_api_key)])
    def do_thing(): ...

返回状态码
----------
- 401 Unauthorized:缺少 ``X-API-Key`` Header
- 403 Forbidden   :提供了 key 但不匹配任何已配置 key

两者区分方便日志审计:401 = 匿名访问,403 = 尝试攻击。
"""
from __future__ import annotations

import hmac
import logging
from typing import Optional

from fastapi import Header, HTTPException, status

from backend.config import get_settings

logger = logging.getLogger(__name__)

API_KEY_HEADER = "X-API-Key"


def _matches_any(provided: str, candidates: list[str]) -> bool:
    """常量时间比对,遍历所有候选 key。

    即使 candidates 为空也返回 False(不等价于放行,由上层 effective_enabled 判断)。
    """
    provided_b = provided.encode("utf-8")
    for c in candidates:
        if hmac.compare_digest(provided_b, c.encode("utf-8")):
            return True
    return False


async def require_api_key(
    x_api_key: Optional[str] = Header(default=None, alias=API_KEY_HEADER),
) -> Optional[str]:
    """FastAPI Dependency:校验 ``X-API-Key`` Header。

    Returns
    -------
    匹配到的 key 字符串(便于端点按 key 打审计标签);未启用鉴权时返回 ``None``。

    Raises
    ------
    HTTPException(401) : 未提供 Header(仅当鉴权启用时)
    HTTPException(403) : Header 存在但 key 不匹配
    """
    settings = get_settings()
    auth = settings.auth

    if not auth.effective_enabled:
        # 禁用 / 未配置 key:放行,但若用户给了 Header 依然不校验
        # (保持对外行为一致:开启/关闭切换不产生误伤)
        return None

    if not x_api_key:
        logger.info("[auth] 401 missing X-API-Key header")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing API key. Provide X-API-Key header.",
            headers={"WWW-Authenticate": "ApiKey"},
        )

    if not _matches_any(x_api_key, auth.api_keys):
        # 不记录 key 本身,只记前缀 + 长度,防日志泄密
        prefix = x_api_key[:4] if len(x_api_key) >= 4 else "***"
        logger.warning(
            "[auth] 403 invalid X-API-Key (prefix=%s len=%d)",
            prefix, len(x_api_key),
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid API key.",
        )

    return x_api_key
