#!/usr/bin/env bash
# healthcheck.sh — 部署后自检
#
# 检查清单(P5.1b 同步精简):
#   1. stock_analysis_api 是 active(其他 unit 已废弃)
#   2. PostgreSQL 可达(从 .env 读 PG_HOST,远程 / 本机都支持)
#   3. Redis 可达(可选;本项目仅作 cache,失败只 warn)
#   4. API /healthz 200
#   5. 前端 SPA 根 / 200(SPA fallback 工作)
#   6. 进程内代理已清理
set -uo pipefail

cd "$(dirname "$0")/.."

FAIL=0
WARN=0
# 事故教训 (2026-04-20 #7): deploy.sh 刚 systemctl restart 完就跑 healthcheck,
# uvicorn/PG 连接还没稳,导致一次性探测失败。重试 3 次 × 1s 间隔足以覆盖启动窗口。
RETRY="${HEALTHCHECK_RETRY:-3}"
RETRY_INTERVAL="${HEALTHCHECK_RETRY_INTERVAL:-1}"

# _probe_with_retry <cmd...>: 成功一次就返回 0;全部失败返回最后一次退出码。
_probe_with_retry() {
    local i rc
    for ((i=1; i<=RETRY; i++)); do
        if "$@" >/dev/null 2>&1; then
            return 0
        fi
        rc=$?
        if [[ $i -lt $RETRY ]]; then
            sleep "$RETRY_INTERVAL"
        fi
    done
    return "${rc:-1}"
}

check() {
    local name="$1"; shift
    if _probe_with_retry "$@"; then
        echo "  ✓ $name"
    else
        echo "  ✗ $name (重试 ${RETRY} 次仍失败)"
        FAIL=$((FAIL + 1))
    fi
}

warn_check() {
    local name="$1"; shift
    if _probe_with_retry "$@"; then
        echo "  ✓ $name"
    else
        echo "  ⚠ $name (软失败,不阻断)"
        WARN=$((WARN + 1))
    fi
}

# 从 .env 读 PG/Redis 主机(没读到时退默认值)
PG_HOST="localhost"
PG_PORT="5432"
REDIS_HOST="localhost"
REDIS_PORT="6379"
if [[ -f .env ]]; then
    # shellcheck disable=SC1091
    set -a
    . ./.env 2>/dev/null || true
    set +a
    PG_HOST="${PG_HOST:-localhost}"
    PG_PORT="${POSTGRES_PORT:-5432}"
    REDIS_HOST="${REDIS_HOST:-localhost}"
    REDIS_PORT="${REDIS_PORT:-6379}"
fi

echo "==> Health check"

# 1. systemd 服务(只检 api,worker/scheduler 已被 P5.2 移除)
if systemctl list-unit-files 2>/dev/null | grep -q "^stock_analysis_api.service"; then
    check "stock_analysis_api active" systemctl is-active --quiet stock_analysis_api
else
    echo "  ⚠ stock_analysis_api 未安装(请跑 ./scripts/install_units.sh)"
    WARN=$((WARN + 1))
fi

# 2. PostgreSQL(用 .env 里的 PG_HOST,可能是远程地址)
if command -v pg_isready >/dev/null 2>&1; then
    check "postgres @${PG_HOST}:${PG_PORT}" pg_isready -h "$PG_HOST" -p "$PG_PORT"
else
    echo "  ⚠ pg_isready 未安装,跳过 PG 检查"
    WARN=$((WARN + 1))
fi

# 3. Redis(软失败 —— 本项目仅作 cache,缺它降级也能跑)
if command -v redis-cli >/dev/null 2>&1; then
    warn_check "redis @${REDIS_HOST}:${REDIS_PORT}" \
        redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" ping
else
    # 没装 redis-cli 时用 nc/curl 探一下端口
    if command -v nc >/dev/null 2>&1; then
        warn_check "redis @${REDIS_HOST}:${REDIS_PORT} (nc)" \
            nc -z -w2 "$REDIS_HOST" "$REDIS_PORT"
    else
        echo "  ⚠ redis-cli 与 nc 都未装,跳过 Redis 检查"
        WARN=$((WARN + 1))
    fi
fi

# 4. API /healthz
check "API /healthz" curl -fsS http://localhost:8000/healthz

# 5. 前端 SPA 根 /(P5.1b: 由 FastAPI 自己 serve frontend/dist)
if [[ -d frontend/dist ]]; then
    check "frontend SPA /" curl -fsS -o /dev/null http://localhost:8000/
else
    echo "  ⚠ frontend/dist 不存在,跳过 SPA 检查"
    WARN=$((WARN + 1))
fi

# 6. 代理已清理(进程级,不是 shell)
if [[ -n "${HTTP_PROXY:-}" ]] || [[ -n "${HTTPS_PROXY:-}" ]]; then
    echo "  ⚠ 当前 shell HTTP_PROXY/HTTPS_PROXY 未清理(systemd unit 内 env 可能仍正确)"
    WARN=$((WARN + 1))
else
    echo "  ✓ proxy env cleared"
fi

if [[ $FAIL -eq 0 ]]; then
    if [[ $WARN -eq 0 ]]; then
        echo "==> All checks passed"
    else
        echo "==> All hard checks passed (${WARN} soft warnings)"
    fi
    exit 0
else
    echo "==> $FAIL hard check(s) failed, $WARN soft warning(s)"
    exit 1
fi
