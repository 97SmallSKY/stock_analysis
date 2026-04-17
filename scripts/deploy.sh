#!/usr/bin/env bash
# deploy.sh — 在 jd 服务器上执行。由本地通过 ssh 触发：
#   ssh jd "cd ~/stock_analysis && git pull && ./scripts/deploy.sh"
#
# 失败任何一步都整体退出，不破坏旧进程（systemctl restart 先起新再杀旧）。
set -euo pipefail

cd "$(dirname "$0")/.."
PROJECT_ROOT="$(pwd)"
echo "==> Deploying from $PROJECT_ROOT"

# ---------------------------------------------------------
# 1. 清除代理（双保险；实际运行时 backend/core/startup.py 也会清一次）
# ---------------------------------------------------------
unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy ALL_PROXY all_proxy
export NO_PROXY="*"

# ---------------------------------------------------------
# 2. Python 依赖
# ---------------------------------------------------------
if [[ ! -d .venv ]]; then
    echo "==> Creating venv"
    python3.11 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
pip install -q -U pip
if [[ -f pyproject.toml ]]; then
    pip install -q -e .
elif [[ -f requirements.txt ]]; then
    pip install -q -r requirements.txt
fi

# ---------------------------------------------------------
# 3. 前端构建（W7 之后）
# ---------------------------------------------------------
if [[ -d frontend && -f frontend/package.json ]]; then
    echo "==> Building frontend"
    (cd frontend && pnpm install --frozen-lockfile && pnpm build)
fi

# ---------------------------------------------------------
# 4. 数据库 migration
# ---------------------------------------------------------
if [[ -f alembic.ini ]]; then
    echo "==> Running Alembic migrations"
    alembic upgrade head
fi

# ---------------------------------------------------------
# 5. 重启 systemd 服务
# ---------------------------------------------------------
services=(
    stock_analysis_api.service
    stock_analysis_worker.service
    stock_analysis_scheduler.service
)
for svc in "${services[@]}"; do
    if systemctl list-unit-files | grep -q "^${svc}"; then
        echo "==> Restarting $svc"
        sudo systemctl restart "$svc"
    else
        echo "==> Skip $svc (not installed yet)"
    fi
done

# reload nginx（若装了）
if systemctl is-active --quiet nginx; then
    echo "==> Reloading nginx"
    sudo nginx -t && sudo systemctl reload nginx
fi

# ---------------------------------------------------------
# 6. Healthcheck
# ---------------------------------------------------------
if [[ -x scripts/healthcheck.sh ]]; then
    echo "==> Running healthcheck"
    ./scripts/healthcheck.sh
fi

echo "==> Deploy complete."
