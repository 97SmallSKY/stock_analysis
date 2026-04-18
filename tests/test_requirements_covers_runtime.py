"""tests/test_requirements_covers_runtime.py

防止 W12 / W13 之后再出现的"代码里 import 了但 requirements.txt 没写"
导致生产部署失败的问题(P5.3 远程部署时 prometheus_client 未声明踩到过)。

覆盖范围:列出 backend/ 运行时必须的第三方包,检查每个都在
requirements.txt 里声明。这不是完美的静态分析,只是显式白名单 —— 改
依赖时请同步本文件。
"""
from __future__ import annotations

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
REQ = REPO / "requirements.txt"


# 运行时强依赖(启动或请求路径上必然 import)。
# 用 pip 名规范(小写 + 短横线),我们只检查是否作为依赖行前缀出现。
RUNTIME_PINS = [
    "fastapi",
    "uvicorn",
    "pydantic",
    "pydantic-settings",
    "psycopg2-binary",
    "redis",
    "apscheduler",
    "tushare",
    "akshare",
    "requests",
    "lxml",
    "beautifulsoup4",
    "pandas",
    "numpy",
    "python-dotenv",
    "loguru",
    "httpx",
    "prometheus-client",   # W12-2 /metrics
]

# 显式**不应**出现的依赖(P5 决策)
FORBIDDEN = ["celery", "sqlalchemy", "alembic"]


def _req_lines() -> list[str]:
    text = REQ.read_text(encoding="utf-8")
    out = []
    for raw in text.splitlines():
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        out.append(s)
    return out


@pytest.mark.parametrize("pkg", RUNTIME_PINS, ids=lambda p: p)
def test_runtime_pin_declared(pkg: str) -> None:
    lines = _req_lines()
    # 每行形如:name>=x,<y 或 name[extra]>=x
    # 用大小写不敏感 + 允许 pip 名下划线/短横线互换
    norm = pkg.lower().replace("_", "-")
    found = False
    for line in lines:
        head = line.split("[", 1)[0]
        head = head.split(">", 1)[0].split("=", 1)[0].split("<", 1)[0].strip()
        head = head.lower().replace("_", "-")
        if head == norm:
            found = True
            break
    assert found, (
        f"requirements.txt 缺少 runtime 依赖: {pkg}(RUNTIME_PINS 白名单里有)"
    )


@pytest.mark.parametrize("pkg", FORBIDDEN, ids=lambda p: p)
def test_forbidden_not_declared(pkg: str) -> None:
    lines = _req_lines()
    for line in lines:
        head = line.split("[", 1)[0].split(">", 1)[0].split("=", 1)[0].split("<", 1)[0].strip()
        assert head.lower() != pkg.lower(), (
            f"requirements.txt 声明了已废弃依赖: {pkg}(行: {line!r})"
        )
