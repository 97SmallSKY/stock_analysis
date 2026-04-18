"""tests/test_p53_deploy_artifacts.py — P5.3

验证 P5.3 新增的部署产物契约:

  1. docker-compose.redis.yml 存在且声明合理
  2. deploy/systemd/stock_analysis_smoke.service + .timer 正确
  3. scripts/integration_smoke.sh 语法通过,失败时非 0,
     .venv 不存在时退出 2
  4. scripts/install_units.sh 的 ACTIVE_UNITS 已包含 smoke timer/service
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
COMPOSE = REPO / "docker-compose.redis.yml"
SMOKE_SERVICE = REPO / "deploy" / "systemd" / "stock_analysis_smoke.service"
SMOKE_TIMER = REPO / "deploy" / "systemd" / "stock_analysis_smoke.timer"
SMOKE_SCRIPT = REPO / "scripts" / "integration_smoke.sh"
INSTALL_SCRIPT = REPO / "scripts" / "install_units.sh"


# ---------- 1. redis compose ----------

def test_redis_compose_exists_and_sane():
    assert COMPOSE.is_file()
    text = COMPOSE.read_text(encoding="utf-8")
    assert "services:" in text
    # 只装 redis,不要 pgadmin 之类
    assert "redis:" in text
    assert "image: redis:" in text
    # 端口映射
    assert '"6379:6379"' in text
    # 有 healthcheck
    assert "redis-cli" in text and "ping" in text
    # 有 volume 持久化(cache 场景可选,但声明了就便于排查)
    assert "volumes:" in text
    # 内存上限声明(避免 cache 爆 server 内存)
    assert "maxmemory" in text


# ---------- 2. systemd smoke unit ----------

def test_smoke_service_is_oneshot_with_clean_proxy():
    assert SMOKE_SERVICE.is_file()
    text = SMOKE_SERVICE.read_text(encoding="utf-8")
    assert "Type=oneshot" in text
    assert "WorkingDirectory=/root/stock_analysis" in text
    assert "EnvironmentFile=/root/stock_analysis/.env" in text
    assert 'Environment="HTTP_PROXY="' in text
    assert 'Environment="NO_PROXY=*"' in text
    # ExecStart 指向 integration_smoke.sh
    assert "integration_smoke.sh" in text
    # 依赖 api 已启动
    assert "stock_analysis_api.service" in text


def test_smoke_timer_schedule():
    assert SMOKE_TIMER.is_file()
    text = SMOKE_TIMER.read_text(encoding="utf-8")
    assert "[Timer]" in text
    assert "OnCalendar" in text
    # 绑定到 smoke service
    assert "Unit=stock_analysis_smoke.service" in text
    # 持久化,以便服务器重启后补跑一次
    assert "Persistent=true" in text
    assert "[Install]" in text
    assert "WantedBy=timers.target" in text


# ---------- 3. integration_smoke.sh ----------

@pytest.mark.skipif(not shutil.which("bash"), reason="bash missing")
def test_smoke_script_syntax_ok():
    assert SMOKE_SCRIPT.is_file()
    text = SMOKE_SCRIPT.read_text(encoding="utf-8")
    assert text.startswith("#!")
    # 注意:脚本里用了 FAIL 累计,不能 set -e(会被单个 fail 中断),
    # 但必须 set -u -o pipefail
    assert "set -uo pipefail" in text or "set -euo pipefail" in text
    r = subprocess.run(
        ["bash", "-n", str(SMOKE_SCRIPT)], capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stderr


@pytest.mark.skipif(not shutil.which("bash"), reason="bash missing")
def test_smoke_script_exits_2_without_venv(tmp_path):
    # 在一个没有 .venv 的 cwd 里跑,应 exit 2 并打 WARN
    # 构造一个假仓库:scripts/integration_smoke.sh + scripts/healthcheck.sh 不需要存在
    fake_repo = tmp_path / "repo"
    scripts = fake_repo / "scripts"
    scripts.mkdir(parents=True)
    # 把真实脚本拷过来
    (scripts / "integration_smoke.sh").write_text(
        SMOKE_SCRIPT.read_text(encoding="utf-8"), encoding="utf-8",
    )
    env = os.environ.copy()
    env.pop("HTTP_PROXY", None)
    r = subprocess.run(
        ["bash", str(scripts / "integration_smoke.sh")],
        cwd=str(fake_repo),
        capture_output=True, text=True, env=env,
    )
    assert r.returncode == 2, (r.returncode, r.stderr)
    assert ".venv" in r.stderr


# ---------- 4. install_units.sh 扩展 ----------

def test_install_units_includes_smoke():
    text = INSTALL_SCRIPT.read_text(encoding="utf-8")
    assert "stock_analysis_smoke.service" in text
    assert "stock_analysis_smoke.timer" in text
    # oneshot 不 start
    assert "oneshot,跳过 start" in text
