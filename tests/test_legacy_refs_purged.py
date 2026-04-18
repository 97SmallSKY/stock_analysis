"""tests/test_legacy_refs_purged.py — P5.2a

回归检测:确保活代码(scripts/ + deploy/ + requirements.txt + 关键 systemd unit)
里不再包含已废弃的历史引用:

  - /home/xiaotian       部署路径(现改为 /root)
  - stock_analysis_worker / stock_analysis_scheduler   已合并到 api unit
  - celery                      已不装(调度走进程内 APScheduler)

历史方案文档(docs/重写方案_v2.md 等)不在扫描范围内 —— 那是方案原稿,
架构变更已在 docs/adr 和 docs/progress 里追踪,原稿保留作为上下文。
"""
from __future__ import annotations

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def _strip_shell_comments(text: str) -> str:
    """去掉 shell / systemd 注释行,只返回可执行代码。

    - `#` 开头的整行注释去掉
    - 其他行保留(包括行尾注释,因为可能混在 `cmd # xxx` 里;
      我们只需足够精度判断活代码)
    """
    out: list[str] = []
    for raw in text.splitlines():
        if raw.lstrip().startswith("#"):
            continue
        out.append(raw)
    return "\n".join(out)

# 必须干净的活代码区域
LIVE_FILES: list[Path] = [
    REPO / "requirements.txt",
    REPO / "scripts" / "deploy.sh",
    REPO / "scripts" / "deploy_frontend.sh",
    REPO / "scripts" / "restart.sh",
    REPO / "scripts" / "healthcheck.sh",
    REPO / "scripts" / "backup_pg.sh",
    REPO / "deploy" / "systemd" / "stock_analysis_api.service",
]


@pytest.mark.parametrize("path", LIVE_FILES, ids=lambda p: p.relative_to(REPO).as_posix())
def test_no_home_xiaotian(path: Path) -> None:
    """文件里(包括注释)都不应再硬编码 /home/xiaotian。"""
    assert path.is_file(), f"expected file missing: {path}"
    text = path.read_text(encoding="utf-8")
    assert "/home/xiaotian" not in text, (
        f"{path.relative_to(REPO)} 仍包含 /home/xiaotian,"
        f"jd 部署路径已改为 /root/stock_analysis"
    )


@pytest.mark.parametrize("path", LIVE_FILES, ids=lambda p: p.relative_to(REPO).as_posix())
def test_no_legacy_units(path: Path) -> None:
    """活代码(不含注释)里不能再引用已废弃的 worker/scheduler unit。

    允许注释里提及它们以解释"为什么被废弃"。
    """
    assert path.is_file(), f"expected file missing: {path}"
    code = _strip_shell_comments(path.read_text(encoding="utf-8"))
    for bad in ("stock_analysis_worker", "stock_analysis_scheduler"):
        assert bad not in code, (
            f"{path.relative_to(REPO)} 活代码仍引用已废弃 unit {bad};"
            f"P5.2 起只保留 stock_analysis_api"
        )


def test_requirements_no_celery() -> None:
    req = REPO / "requirements.txt"
    text = req.read_text(encoding="utf-8")
    # 允许注释里解释"不用 celery",但不能有形如 'celery>=x' 这种依赖行
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        assert not stripped.lower().startswith("celery"), (
            f"requirements.txt 仍声明 celery 依赖: {line!r};"
            f"本项目不使用 Celery(APScheduler 嵌入进程)"
        )


def test_systemd_unit_has_new_paths() -> None:
    unit = REPO / "deploy" / "systemd" / "stock_analysis_api.service"
    text = unit.read_text(encoding="utf-8")
    assert "WorkingDirectory=/root/stock_analysis" in text
    assert "EnvironmentFile=/root/stock_analysis/.env" in text
    assert "/root/stock_analysis/.venv/bin/uvicorn" in text
    # workers=1(APScheduler/WS 多 worker 会重复启动)
    assert "--workers 1" in text
    # 显式清代理 + 开启内嵌调度
    assert 'Environment="HTTP_PROXY="' in text
    assert 'RUNTIME_ENABLE_INTRADAY=1' in text
