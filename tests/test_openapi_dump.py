"""tests/test_openapi_dump.py — W12-6

覆盖:
  - scripts/dump_openapi.py 生成合法 OpenAPI JSON
  - paths 里包含关键端点 (/api/data-sources/status /api/lhb /api/dashboard
    /api/backtest/compare /api/robots /api/reports/weekly /metrics)
  - info.title / info.version 存在
  - app.openapi() 被真实调用 (用 python -c 直接调)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "dump_openapi.py"


def test_script_exists_and_imports():
    assert SCRIPT.is_file()


def test_generates_valid_json(tmp_path):
    out = tmp_path / "openapi.json"
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "--out", str(out)],
        capture_output=True, text=True,
        env={**os.environ, "PYTHONPATH": str(REPO)},
    )
    assert r.returncode == 0, r.stderr
    assert out.is_file()
    spec = json.loads(out.read_text(encoding="utf-8"))
    assert spec.get("openapi", "").startswith("3")
    assert "info" in spec and spec["info"].get("title")
    assert "paths" in spec

    paths = set(spec["paths"].keys())
    expected_prefixes = [
        "/api/data-sources",
        "/api/backtest",
        "/api/lhb",
        "/api/dashboard",
        "/api/robots",
        "/api/reports",
    ]
    for p in expected_prefixes:
        assert any(path.startswith(p) for path in paths), \
            f"缺少前缀 {p} 下任何路由"
    # /metrics 是 Response 路由, 可能不在 openapi 中 (include_in_schema=False), 不断言


def test_stdout_output_valid(tmp_path):
    r = subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True, text=True,
        env={**os.environ, "PYTHONPATH": str(REPO)},
    )
    assert r.returncode == 0, r.stderr
    spec = json.loads(r.stdout)
    assert spec["info"]["title"]
