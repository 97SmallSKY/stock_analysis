#!/usr/bin/env bash
# deploy.sh — 在 jd 服务器上执行。由本地通过 ssh 触发:
#   ssh jd "cd ~/stock_analysis && git pull && ./scripts/deploy.sh"
#
# 设计原则:
# 1. 失败任何一步都整体退出,不破坏旧进程(systemctl restart 先起新再杀旧)。
# 2. Python 自适应:优先 3.11 → 3.10 → python3,按 jd 实际情况选可用版本。
# 3. 不装 Celery:调度统一走 APScheduler(嵌在 FastAPI 进程内,env 开关控制)。
# 4. Redis 通过 docker-compose.redis.yml 单独启动(可选)。
# 5. 不自动跑 DB DDL:schema 变更通过 db/init/*.sql 手动执行(ADR-0001)。
set -euo pipefail

cd "$(dirname "$0")/.."
PROJECT_ROOT="$(pwd)"
echo "==> Deploying from $PROJECT_ROOT"

# ---------------------------------------------------------
# 1. 清除代理(双保险;运行时 backend/core/startup.py 也会清一次)
# ---------------------------------------------------------
unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy ALL_PROXY all_proxy
export NO_PROXY="*"

# ---------------------------------------------------------
# 2. 选 Python 解释器(3.11 > 3.10 > 3)
# ---------------------------------------------------------
pick_python() {
    for cand in python3.11 python3.10 python3; do
        if command -v "$cand" >/dev/null 2>&1; then
            # 确保 >= 3.10
            if "$cand" -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)' 2>/dev/null; then
                echo "$cand"
                return 0
            fi
        fi
    done
    # pyenv 的 shims 路径兜底(jd 现状:/root/.pyenv/shims/python3.10)
    for cand in /root/.pyenv/shims/python3.11 /root/.pyenv/shims/python3.10; do
        if [[ -x "$cand" ]]; then
            echo "$cand"
            return 0
        fi
    done
    return 1
}

PYTHON="$(pick_python)" || {
    echo "ERROR: 找不到 Python >= 3.10(尝试过 python3.11 / python3.10 / python3 / pyenv shims)" >&2
    exit 1
}
echo "==> Using Python: $PYTHON ($($PYTHON --version 2>&1))"

# ---------------------------------------------------------
# 3. venv 复用或创建
# ---------------------------------------------------------
if [[ ! -d .venv ]]; then
    echo "==> Creating venv with $PYTHON"
    "$PYTHON" -m venv .venv
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
# 4. 前端构建(硬依赖:失败则整体部署失败)
# ---------------------------------------------------------
if [[ -d frontend && -f frontend/package.json ]]; then
    echo "==> Building frontend (hard requirement)"
    if command -v pnpm >/dev/null 2>&1; then
        FE_INSTALL="pnpm install --frozen-lockfile"
        FE_BUILD="pnpm build"
    elif command -v npm >/dev/null 2>&1; then
        # jd 默认环境:node v22 + npm 10.x,无 pnpm
        # 注:npm 10 的 ci 对 lock-file peer 检查比 11 更严格,如果跨版本 lock
        # 不完全对齐(常见于 vitest 4 子依赖要求 esbuild 0.28 但顶层 esbuild
        # 是 0.21)会硬失败。这里给个 fallback:ci 失败时退回 install --no-save
        # (不污染仓库 lock,只本地 resolve 一次)。
        FE_INSTALL="npm ci || (echo '[deploy] npm ci 失败,fallback 到 npm install --no-save' >&2; npm install --no-save --no-audit --no-fund)"
        FE_BUILD="npm run build"
    else
        echo "ERROR: 未找到 pnpm 或 npm,无法构建前端" >&2
        exit 1
    fi
    (
        cd frontend
        echo "  -> $FE_INSTALL"
        # 用 eval 让 ||、子 shell 等 shell 操作符生效(FE_INSTALL 里有 fallback 链)
        eval "$FE_INSTALL"
        echo "  -> $FE_BUILD"
        eval "$FE_BUILD"
    )
    # 验证产物
    if [[ ! -d frontend/dist ]] || [[ -z "$(ls -A frontend/dist 2>/dev/null)" ]]; then
        echo "ERROR: 前端构建完成但 frontend/dist 为空" >&2
        exit 1
    fi
    echo "==> Frontend built: $(find frontend/dist -type f | wc -l) files"
fi

# ---------------------------------------------------------
# 5. 数据库 schema(手动,不自动执行)
# ---------------------------------------------------------
# 本项目不使用 Alembic(docs/ADR/0001-drop-sqlalchemy-and-alembic.md)。
# Schema 变更需手动应用:
#   psql -h "$PG_HOST" -U "$POSTGRES_USER" -d "$POSTGRES_DB" -f db/init/NN_xxx.sql

# ---------------------------------------------------------
# 6. Redis(docker-compose,可选)
# ---------------------------------------------------------
# 仅用于 backend/cache/redis_cache.py;broker 配置保留但不启用 Celery。
if [[ -f docker-compose.redis.yml ]]; then
    if command -v docker >/dev/null 2>&1; then
        # 只在没跑时启动,避免每次部署重启影响应用缓存
        if ! docker compose -f docker-compose.redis.yml ps --status running 2>/dev/null | grep -q redis; then
            echo "==> Starting Redis container"
            docker compose -f docker-compose.redis.yml up -d
        else
            echo "==> Redis container already running"
        fi
    else
        echo "==> docker 未安装, 跳过 Redis 启动(若启用 @cached 必须手动起 Redis)"
    fi
fi

# ---------------------------------------------------------
# 7. 重启 systemd 服务
# ---------------------------------------------------------
# 注意:backend/tasks/ 和 backend/scheduler/ 都不存在(历史设计残留)。
# 本项目只有 API 一个常驻服务,所有 APScheduler 都嵌在 FastAPI 进程内。
services=(
    stock_analysis_api.service
)
for svc in "${services[@]}"; do
    if systemctl list-unit-files 2>/dev/null | grep -q "^${svc}"; then
        echo "==> Restarting $svc"
        sudo systemctl restart "$svc"
    else
        echo "==> Skip $svc (未安装, 请先跑 ./scripts/install_units.sh)"
    fi
done

# P5.1b:移除 nginx reload。FastAPI 自己 serve frontend/dist,一端口通吃,
# 降低部署复杂度(详见 docs/DEPLOYMENT.md)。

# ---------------------------------------------------------
# 8. Healthcheck
# ---------------------------------------------------------
if [[ -x scripts/healthcheck.sh ]]; then
    echo "==> Running healthcheck"
    ./scripts/healthcheck.sh
fi

echo "==> Deploy complete."
