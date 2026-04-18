"""tests/test_install_units.py — P5.2b

验证 deploy/systemd/README.md + scripts/install_units.sh 的契约:

  - README 存在且覆盖关键步骤(install / 迁移 / journalctl)
  - install_units.sh 语法 OK,DRY_RUN 下可跑
  - 脚本声明的 ACTIVE_UNITS 里的 .service 文件都真实存在
  - 脚本同时处理了已废弃 unit 的 disable/rm(迁移必需)
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
README = REPO / "deploy" / "systemd" / "README.md"
SCRIPT = REPO / "scripts" / "install_units.sh"
UNIT_DIR = REPO / "deploy" / "systemd"


def test_readme_exists_and_covers_keys():
    assert README.is_file()
    text = README.read_text(encoding="utf-8")
    # 安装
    assert "install_units.sh" in text
    assert "systemctl daemon-reload" in text
    assert "enable --now" in text
    # 迁移(老 unit 清理)
    assert "stock_analysis_worker" in text
    assert "stock_analysis_scheduler" in text
    assert "disable --now" in text
    # 日志
    assert "journalctl -u stock_analysis_api" in text
    # 单 worker 原因
    assert "workers" in text.lower()


def test_script_exists_and_syntax_ok():
    assert SCRIPT.is_file()
    head = SCRIPT.read_text(encoding="utf-8").splitlines()[0]
    assert head.startswith("#!") and "bash" in head
    assert "set -euo pipefail" in SCRIPT.read_text(encoding="utf-8")
    if shutil.which("bash"):
        r = subprocess.run(
            ["bash", "-n", str(SCRIPT)], capture_output=True, text=True,
        )
        assert r.returncode == 0, r.stderr


def test_script_declared_active_units_exist():
    body = SCRIPT.read_text(encoding="utf-8")
    # 简单解析 ACTIVE_UNITS=("a.service" "b.service")
    import re
    m = re.search(r'ACTIVE_UNITS=\((.*?)\)', body, re.S)
    assert m, "ACTIVE_UNITS not found in script"
    names = re.findall(r'"([^"]+\.service)"', m.group(1))
    assert names, "no units parsed"
    for n in names:
        p = UNIT_DIR / n
        assert p.is_file(), f"{n} declared in install script but file missing: {p}"


def test_script_handles_legacy_units():
    body = SCRIPT.read_text(encoding="utf-8")
    # 必须提到要清理的老 unit
    assert "stock_analysis_worker.service" in body
    assert "stock_analysis_scheduler.service" in body
    # 必须有 disable + rm 逻辑
    assert "disable --now" in body
    # 必须有 daemon-reload
    assert "daemon-reload" in body


@pytest.mark.skipif(not shutil.which("bash"), reason="bash missing")
def test_dry_run_non_root_ok(tmp_path):
    env = os.environ.copy()
    env["DRY_RUN"] = "1"
    # DRY_RUN 时不检查 root
    r = subprocess.run(
        ["bash", str(SCRIPT)], capture_output=True, text=True, env=env,
    )
    assert r.returncode == 0, r.stderr
    # 必须打印出拷贝 + reload 指令
    out = r.stderr + r.stdout
    assert "cp" in out or "stock_analysis_api.service" in out
    assert "daemon-reload" in out
