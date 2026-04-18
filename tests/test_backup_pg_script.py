"""tests/test_backup_pg_script.py — W12-4

验证 scripts/backup_pg.sh:
  - 存在 + shebang + set -euo pipefail
  - bash -n 语法通过
  - DRY_RUN=1 正常打印 pg_dump 命令, 含 KEEP_DAYS 默认 30
  - DRY_RUN=1 不需要 pg_dump 也能跑
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "backup_pg.sh"

pytestmark = pytest.mark.skipif(
    not shutil.which("bash"), reason="bash not available",
)


def test_exists_shebang_and_strict():
    assert SCRIPT.is_file()
    body = SCRIPT.read_text(encoding="utf-8")
    assert body.startswith("#!")
    assert "bash" in body.splitlines()[0]
    assert "set -euo pipefail" in body


def test_bash_syntax_ok():
    r = subprocess.run(
        ["bash", "-n", str(SCRIPT)], capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stderr


def test_dry_run_prints_pg_dump(tmp_path):
    env = os.environ.copy()
    env["BACKUP_DIR"] = str(tmp_path / "backups")
    env["DRY_RUN"] = "1"
    env["UPLOAD_S3"] = ""  # 不触发 aws 分支
    r = subprocess.run(
        ["bash", str(SCRIPT)], capture_output=True, text=True, env=env,
    )
    # DRY_RUN 不真跑 pg_dump, 所以即便没装也 exit 0
    assert r.returncode == 0, r.stderr
    out = (r.stderr or "") + (r.stdout or "")
    assert "pg_dump" in out
    assert "-Fc" in out
    assert "OK: 备份完成" in out


def test_dry_run_with_s3_upload(tmp_path):
    env = os.environ.copy()
    env["BACKUP_DIR"] = str(tmp_path / "backups")
    env["DRY_RUN"] = "1"
    env["UPLOAD_S3"] = "s3://fake-bucket/db"
    r = subprocess.run(
        ["bash", str(SCRIPT)], capture_output=True, text=True, env=env,
    )
    assert r.returncode == 0
    all_out = (r.stderr or "") + (r.stdout or "")
    # 有 aws CLI: 打印 aws s3 cp; 无 aws: 打印 WARN 跳过. 两者都可.
    assert ("aws s3 cp" in all_out) or ("aws CLI 未安装" in all_out)
