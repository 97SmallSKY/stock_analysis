"""tests/test_deploy_script.py — P5.1

验证 scripts/deploy.sh 的结构约束,不真跑(会 sudo / 装依赖)。
契约:
  - shebang + set -euo pipefail
  - bash -n 语法通过
  - pick_python 函数存在且覆盖 3.11/3.10/python3 + pyenv shims 兜底
  - systemd services 只剩 api(删了 worker/scheduler)
  - 明确不使用 Alembic + 不使用 Celery
  - Redis 走 docker-compose 条件启动
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "deploy.sh"

pytestmark = pytest.mark.skipif(
    not shutil.which("bash"), reason="bash not available",
)


def _body() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_exists_shebang_strict():
    assert SCRIPT.is_file()
    body = _body()
    assert body.startswith("#!")
    assert "bash" in body.splitlines()[0]
    assert "set -euo pipefail" in body


def test_bash_syntax_ok():
    r = subprocess.run(
        ["bash", "-n", str(SCRIPT)], capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stderr


def test_picks_python_310_plus():
    body = _body()
    # 应该按优先级尝试 3.11 → 3.10 → python3
    assert "python3.11" in body
    assert "python3.10" in body
    assert "python3" in body
    # 必须有 >=3.10 的版本校验
    assert "version_info >= (3,10)" in body
    # pyenv shims 兜底(jd 环境)
    assert "/root/.pyenv/shims" in body


def test_no_celery_in_services():
    body = _body()
    # services 数组里不应该再有 worker(Celery)或 scheduler(独立进程)
    assert "stock_analysis_worker.service" not in body
    assert "stock_analysis_scheduler.service" not in body
    # 只保留 api
    assert "stock_analysis_api.service" in body


def test_no_alembic():
    body = _body()
    # 部署流程绝对不能再调 alembic(ADR-0001)
    assert "alembic upgrade" not in body.lower()
    assert "alembic" not in body.lower() or "不使用 alembic" in body.lower() or \
        "docs/adr/0001" in body.lower()


def test_redis_conditional_on_compose_file():
    body = _body()
    # Redis 通过 docker-compose 启动,且应当只在文件存在时触发
    assert "docker-compose.redis.yml" in body
    assert "docker compose" in body or "docker-compose" in body
    # 条件启动(只在文件存在时)
    assert '[[ -f docker-compose.redis.yml ]]' in body


def test_proxy_cleared_first():
    body = _body()
    # 代理清理必须在 Python 选型和依赖安装之前
    proxy_idx = body.find("unset HTTP_PROXY")
    py_idx = body.find("pick_python")
    # pip install 是代理污染风险最高的步骤,它必须在清代理之后
    pip_idx = body.find("pip install")
    assert 0 < proxy_idx < py_idx
    assert proxy_idx < pip_idx


def test_healthcheck_still_called_at_end():
    body = _body()
    assert "scripts/healthcheck.sh" in body
    # 必须在所有服务重启之后
    restart_idx = body.find("systemctl restart")
    hc_idx = body.find("scripts/healthcheck.sh")
    assert restart_idx < hc_idx


# ---------- P5.1b 前端硬依赖 ----------


def test_frontend_build_is_hard_requirement():
    body = _body()
    # 前端构建是硬依赖:pnpm 优先,退回 npm,两者都没有则 exit 1
    assert "npm ci" in body
    assert "npm run build" in body
    assert 'command -v pnpm' in body
    assert 'command -v npm' in body
    # 既无 pnpm 又无 npm 必须硬失败
    assert "未找到 pnpm 或 npm" in body
    # 产物校验必须存在
    assert "frontend/dist" in body
    assert "frontend/dist 为空" in body


def test_nginx_config_removed():
    """P5.1b 决策:FastAPI 直接 serve 前端 SPA,不再需要 nginx 站点配置。"""
    nginx_dir = Path(__file__).resolve().parents[1] / "deploy" / "nginx"
    assert not nginx_dir.exists(), (
        "deploy/nginx/ 应已被删除(简化部署:一端口通吃)"
    )


def test_deploy_has_no_nginx_reload():
    body = _body()
    assert "systemctl reload nginx" not in body
    assert "nginx -t" not in body


def test_healthcheck_covers_frontend_and_api():
    hc = Path(__file__).resolve().parents[1] / "scripts" / "healthcheck.sh"
    assert hc.is_file()
    text = hc.read_text(encoding="utf-8")
    # 不再检查 worker / scheduler
    assert "stock_analysis_worker" not in text
    assert "stock_analysis_scheduler" not in text
    # 检查 API + 前端 SPA 根
    assert "/healthz" in text
    assert "frontend/dist" in text
    assert "http://localhost:8000/" in text
    # PG 地址从 .env 读,不再硬编码 localhost
    assert '${PG_HOST' in text or 'PG_HOST:-localhost' in text
    assert "$PG_HOST" in text


# ---------- STO-33 sudo non-interactive ----------

def test_sudo_is_non_interactive():
    """STO-33: deploy.sh 所有 `sudo systemctl ...` 必须是 `sudo -n`。

    背景:当前 jd 部署用 root,sudo 是 no-op;但非 root 用户跑 deploy.sh
    时裸 sudo 会弹密码提示或 hang。`-n` 让它不交互式失败,给明确错误。

    防止未来被人改回裸 sudo 的回归锚点测试。
    """
    import re
    body = _body()
    lines = [
        ln.strip()
        for ln in body.splitlines()
        if re.search(r"\bsudo\b.*\bsystemctl\b", ln)
        and not ln.lstrip().startswith("#")
    ]
    assert lines, "deploy.sh 应该至少有一处 sudo systemctl(restart service)"
    for ln in lines:
        assert re.search(r"\bsudo\s+-n\s+systemctl\b", ln), (
            f"STO-33 红线:`sudo systemctl` 必须是 `sudo -n systemctl` "
            f"(non-interactive),但这行不是: {ln!r}"
        )
