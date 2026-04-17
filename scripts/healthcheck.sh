#!/usr/bin/env bash
# healthcheck.sh — 部署后自检
set -uo pipefail

FAIL=0

check() {
    local name="$1"; shift
    if "$@" >/dev/null 2>&1; then
        echo "  ✓ $name"
    else
        echo "  ✗ $name"
        FAIL=$((FAIL + 1))
    fi
}

echo "==> Health check"

# 1. systemd 服务
for svc in stock_analysis_api stock_analysis_worker stock_analysis_scheduler; do
    if systemctl list-unit-files | grep -q "^${svc}.service"; then
        check "$svc active" systemctl is-active --quiet "$svc"
    fi
done

# 2. PostgreSQL
check "postgres accepting connections" pg_isready -h localhost -p 5432

# 3. Redis
check "redis ping" redis-cli -h localhost ping

# 4. API /healthz
if systemctl list-unit-files | grep -q "^stock_analysis_api.service"; then
    check "API /healthz" curl -fsS http://localhost:8000/healthz
fi

# 5. 代理已清理
if [[ -n "${HTTP_PROXY:-}" ]] || [[ -n "${HTTPS_PROXY:-}" ]]; then
    echo "  ✗ HTTP_PROXY/HTTPS_PROXY 未清理"
    FAIL=$((FAIL + 1))
else
    echo "  ✓ proxy env cleared"
fi

if [[ $FAIL -eq 0 ]]; then
    echo "==> All checks passed"
    exit 0
else
    echo "==> $FAIL check(s) failed"
    exit 1
fi
