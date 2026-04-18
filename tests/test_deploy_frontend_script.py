"""tests/test_deploy_frontend_script.py

验证 scripts/deploy_frontend.sh(P5.2a 简化后)的契约:
  - bash 语法 (bash -n)
  - shebang + set -euo pipefail
  - FRONTEND_DIR 不存在时 exit 2
  - DRY_RUN=1 打印 build 命令但不执行
  - 不再有 rsync / nginx / DIST_TARGET / xiaotian 残留

不依赖真实 node / pnpm。
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "deploy_frontend.sh"


def _bash_available() -> bool:
    return shutil.which("bash") is not None


pytestmark = pytest.mark.skipif(not _bash_available(), reason="bash not in PATH")


def _body() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_script_exists_and_executable():
    assert SCRIPT.is_file()
    head = _body().splitlines()[0]
    assert head.startswith("#!"), f"缺 shebang: {head!r}"
    assert "bash" in head


def test_script_has_strict_mode():
    assert "set -euo pipefail" in _body()


def test_bash_syntax_ok():
    r = subprocess.run(
        ["bash", "-n", str(SCRIPT)], capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stderr


def test_missing_frontend_dir_exits_2(tmp_path):
    env = os.environ.copy()
    env["FRONTEND_DIR"] = str(tmp_path / "nope")
    env["DRY_RUN"] = "1"
    r = subprocess.run(
        ["bash", str(SCRIPT)], capture_output=True, text=True, env=env,
    )
    assert r.returncode == 2, r.stderr
    assert "前端目录不存在" in r.stderr


def test_dry_run_prints_build_commands(tmp_path):
    # 构造一个最小 frontend 目录
    fe = tmp_path / "frontend"
    fe.mkdir()
    env = os.environ.copy()
    env["FRONTEND_DIR"] = str(fe)
    env["DRY_RUN"] = "1"
    r = subprocess.run(
        ["bash", str(SCRIPT)], capture_output=True, text=True, env=env,
    )
    assert r.returncode == 0, r.stderr
    out = r.stderr
    # 必须有 install + build 步骤的日志
    assert "install:" in out
    assert "build:" in out
    # 至少选到一个包管理器
    assert ("npm" in out) or ("pnpm" in out)


def _code_lines(text: str) -> list[str]:
    """去掉注释行和空行,只保留可执行 shell 语句。"""
    out: list[str] = []
    for raw in text.splitlines():
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        out.append(raw)
    return out


def test_no_legacy_rsync_or_nginx():
    code = "\n".join(_code_lines(_body()))
    # P5.2a: 前端由 FastAPI serve,不再 rsync 到外部目录,不再 reload nginx
    assert "rsync" not in code
    assert "nginx" not in code
    assert "DIST_TARGET" not in code
    # 不再有 /home/xiaotian 硬编码(注释和代码都不行)
    assert "/home/xiaotian" not in _body()


def test_fail_when_no_pm():
    # 当 PATH 里没有 pnpm 也没有 npm 时,脚本应 exit 3
    # 构造一个空 PATH(只保留必要 shell 工具)
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        # 软链接必要基础命令
        bin_dir = Path(td)
        for cmd in ("bash", "ls", "find", "dirname", "cd", "cat", "printf"):
            src = shutil.which(cmd)
            if src:
                try:
                    os.symlink(src, bin_dir / cmd)
                except FileExistsError:
                    pass
        fe = bin_dir / "frontend"
        fe.mkdir()
        env = {"PATH": str(bin_dir), "FRONTEND_DIR": str(fe), "DRY_RUN": "0"}
        r = subprocess.run(
            ["bash", str(SCRIPT)], capture_output=True, text=True, env=env,
        )
        assert r.returncode == 3, (r.returncode, r.stderr)
        assert "未找到 pnpm 或 npm" in r.stderr
