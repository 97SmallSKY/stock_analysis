"""tests/test_deploy_frontend_script.py

验证 scripts/deploy_frontend.sh 的:
  - bash 语法 (bash -n)
  - shebang + set -euo pipefail
  - DRY_RUN=1 模式正常打印, 不执行 npm / rsync 等真实命令
  - 对 FRONTEND_DIR 不存在时 exit 2

不依赖真实 node / rsync / nginx.
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


def test_script_exists_and_executable():
    assert SCRIPT.is_file()
    # 可读, 并以 shebang 开头
    head = SCRIPT.read_text(encoding="utf-8").splitlines()[0]
    assert head.startswith("#!"), f"缺 shebang: {head!r}"
    assert "bash" in head


def test_script_has_strict_mode():
    # 必须 set -euo pipefail 否则早期失败不会中断
    body = SCRIPT.read_text(encoding="utf-8")
    assert "set -euo pipefail" in body


def test_bash_syntax_ok():
    r = subprocess.run(
        ["bash", "-n", str(SCRIPT)], capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stderr


def test_missing_frontend_dir_exits_2(tmp_path):
    env = os.environ.copy()
    env["FRONTEND_DIR"] = str(tmp_path / "nope")
    env["DIST_TARGET"] = str(tmp_path / "dist_target")
    env["NGINX_RELOAD"] = "0"
    env["DRY_RUN"] = "1"
    r = subprocess.run(
        ["bash", str(SCRIPT)], capture_output=True, text=True, env=env,
    )
    assert r.returncode == 2, r.stderr
    assert "前端目录不存在" in r.stderr


def test_dry_run_prints_expected_commands(tmp_path):
    # 构造一个最小 frontend 目录让脚本过第一道检查
    fe = tmp_path / "frontend"
    (fe / "node_modules").mkdir(parents=True)  # 跳过 npm ci 分支
    env = os.environ.copy()
    env["FRONTEND_DIR"] = str(fe)
    env["DIST_TARGET"] = str(tmp_path / "live")
    env["NGINX_RELOAD"] = "1"
    env["DRY_RUN"] = "1"
    r = subprocess.run(
        ["bash", str(SCRIPT)], capture_output=True, text=True, env=env,
    )
    assert r.returncode == 0, r.stderr
    out = r.stderr  # log 走 stderr
    assert "npm run build" in out
    assert "rsync" in out
    # DRY_RUN 下 nginx 段仍应被打印 (前提: 本机有 nginx; 没有 nginx 时跳过, 两种都可接受)
    assert "OK: 前端已发布" in out
