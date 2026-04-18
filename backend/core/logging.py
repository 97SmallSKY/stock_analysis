"""W11-3 日志脱敏 + loguru 接入

统一入口:
    from backend.core.logging import setup_logging, redact
    setup_logging(level="INFO")

特性:
  1. redact(msg): 屏蔽 token / api_key / cookie / authorization / password /
                  access_token / refresh_token / secret 等字段, 按常见 KV/JSON
                  形态检测替换为 "***".
     独立纯函数, 单元可测.
  2. setup_logging:
     - 同时装 stdlib logging 和 loguru: 把 stdlib record 桥接到 loguru sink,
       让既存 logging.getLogger(...) 代码自动享受脱敏 + 彩色输出 + 结构化.
     - 所有消息经 `_patch_record` 脱敏后再交给 sink.
     - 未装 loguru 时自动降级到 stdlib logging.basicConfig.

约束:
  - 绝不修改原 message 对象的引用内容 (str 不可变); 只替换最终字符串.
"""
from __future__ import annotations

import logging
import re
import sys
from typing import Optional

# 需要脱敏的字段 (大小写不敏感)
SENSITIVE_KEYS = (
    "token", "access_token", "refresh_token",
    "api_key", "apikey",
    "authorization", "cookie", "set-cookie",
    "password", "passwd", "pwd",
    "secret", "client_secret",
    "session", "session_id",
    "x-api-key",
)

_PLACEHOLDER = "***"

# KV 形式 (name=VALUE, name: VALUE, "name":"VALUE") 匹配 VALUE 并屏蔽
# VALUE 结束符: 空白 / 逗号 / 分号 / 引号 / 换行 / >
_KV_PATTERNS = [
    # key=val 或 key: val (非引号) — key 前可跟 `?`/`&`/空白/开头; val 以常见分隔符结束
    re.compile(
        rf"(?i)(?P<pre>(?:[?&;,\s]|^))(?P<key>{'|'.join(SENSITIVE_KEYS)})(?P<sep>\s*[:=]\s*)(?P<val>[^\s,;&\"'\r\n]+)"
    ),
    # "key":"val" 或 'key':'val' (JSON)
    re.compile(
        rf"(?i)(?P<q1>[\"'])(?P<key>{'|'.join(SENSITIVE_KEYS)})(?P<q2>[\"'])\s*:\s*(?P<q3>[\"'])(?P<val>[^\"']*)(?P<q4>[\"'])"
    ),
    # Authorization: Bearer xxx / Basic xxx
    re.compile(
        r"(?i)(?P<key>authorization)(?P<sep>\s*[:=]\s*)(?P<scheme>Bearer|Basic|Token)\s+(?P<val>[A-Za-z0-9._\-+/=]+)"
    ),
]


def redact(msg: str) -> str:
    """脱敏 msg 中的敏感字段.

    兼容 URL query / headers / JSON / key=val 各种形态.
    空串或非 str 原样返回.
    """
    if not msg or not isinstance(msg, str):
        return msg
    out = msg
    # 1. Authorization scheme (Bearer xxx)
    out = _KV_PATTERNS[2].sub(
        lambda m: f"{m.group('key')}{m.group('sep')}{m.group('scheme')} {_PLACEHOLDER}",
        out,
    )
    # 2. JSON "key": "val"
    out = _KV_PATTERNS[1].sub(
        lambda m: (
            f"{m.group('q1')}{m.group('key')}{m.group('q2')}:"
            f"{m.group('q3')}{_PLACEHOLDER}{m.group('q4')}"
        ),
        out,
    )
    # 3. key=val / key: val
    out = _KV_PATTERNS[0].sub(
        lambda m: f"{m.group('pre')}{m.group('key')}{m.group('sep')}{_PLACEHOLDER}",
        out,
    )
    return out


# --------------------------------------------------------------------
# loguru setup (可选)
# --------------------------------------------------------------------


class InterceptHandler(logging.Handler):
    """把 stdlib logging 转发到 loguru, 顺便脱敏."""

    def emit(self, record: logging.LogRecord):
        try:
            from loguru import logger
        except Exception:  # noqa: BLE001
            return
        level = record.levelname
        # 找到调用点 (跳过 logging + 本 handler)
        frame, depth = logging.currentframe(), 2
        while frame and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1
        msg = record.getMessage()
        logger.opt(depth=depth, exception=record.exc_info).log(level, redact(msg))


def setup_logging(level: str = "INFO") -> None:
    """装 loguru 并把 stdlib logging 桥接过去.

    幂等: 重复调用不会重复装 sink.
    未装 loguru 时降级 logging.basicConfig.
    """
    try:
        from loguru import logger
    except Exception:  # noqa: BLE001
        logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s %(message)s")
        return

    # stdlib root -> InterceptHandler
    root = logging.getLogger()
    if not any(isinstance(h, InterceptHandler) for h in root.handlers):
        root.handlers = [InterceptHandler()]
    root.setLevel(level)

    # 覆盖 loguru 默认 sink: 走 stderr, patcher 对每条记录脱敏
    logger.remove()
    logger.add(
        sys.stderr, level=level,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> "
            "<level>{level: <8}</level> "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
            "<level>{message}</level>"
        ),
    )
    logger.configure(patcher=_patch_record)


def _patch_record(record: dict) -> None:
    msg = record.get("message")
    if isinstance(msg, str):
        record["message"] = redact(msg)
