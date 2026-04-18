#!/usr/bin/env bash
# scripts/integration_smoke.sh — 线上集成冒烟
#
# 目标:在 jd 每天调度一次,确保关键路径活着 —— 不替代 pytest,只是
# "把外部依赖串一遍,失败就告警"。
#
# 检查项:
#   1. healthcheck.sh 全绿
#   2. /api/data-sources/status 返回 200 且 adapters 非空
#   3. 通过 backend.core.startup.bootstrap() 调一次 AKShare 现货,验证代理已绕过
#   4. PG 上 t_lhb_daily 最近 7 天有数据(快速一致性检查)
#
# 退出码:
#   0 全绿
#   1 任意硬检查失败
#   2 环境/依赖问题(如 .venv 缺失,跳过当天)

set -uo pipefail

cd "$(dirname "$0")/.."
REPO_ROOT="$(pwd)"

log() { printf '[smoke %s] %s\n' "$(date +%H:%M:%S)" "$*" >&2; }

FAIL=0

# ---- 0. 必备 venv ----
if [[ ! -x ".venv/bin/python" ]]; then
    log "WARN: .venv 不存在,跳过本次冒烟(请先跑 scripts/deploy.sh)"
    exit 2
fi

# 清代理 + 加载 .env
unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy ALL_PROXY all_proxy
export NO_PROXY="*"
if [[ -f .env ]]; then
    set -a
    # shellcheck disable=SC1091
    . ./.env 2>/dev/null || true
    set +a
fi

# ---- 1. healthcheck ----
log "==> healthcheck.sh"
if ./scripts/healthcheck.sh; then
    log "healthcheck OK"
else
    log "healthcheck FAIL"
    FAIL=$((FAIL + 1))
fi

# ---- 2. data-sources/status ----
log "==> /api/data-sources/status"
if curl -fsS http://localhost:8000/api/data-sources/status -o /tmp/_ds.json; then
    if grep -q '"adapters"' /tmp/_ds.json; then
        log "adapters 非空"
    else
        log "adapters 缺失"
        FAIL=$((FAIL + 1))
    fi
else
    log "接口不可达"
    FAIL=$((FAIL + 1))
fi

# ---- 3. AKShare 实地一发 ----
log "==> AKShare 现货抽样"
if .venv/bin/python - <<'PY'
import sys
try:
    from backend.core.startup import bootstrap
    bootstrap()
    import akshare as ak
    df = ak.stock_zh_a_spot_em()
    if df is None or len(df) < 100:
        print(f'rows={0 if df is None else len(df)} too few', file=sys.stderr)
        sys.exit(1)
    print(f'akshare ok, rows={len(df)}')
except Exception as e:
    print(f'akshare FAIL: {e}', file=sys.stderr)
    sys.exit(2)
PY
then
    log "AKShare OK"
else
    log "AKShare FAIL"
    FAIL=$((FAIL + 1))
fi

# ---- 4. PG 最近 7 天龙虎榜 ----
log "==> PG t_lhb_daily 最近 7 天"
if .venv/bin/python - <<'PY'
import os, sys
try:
    import psycopg2
    conn = psycopg2.connect(
        host=os.environ.get('PG_HOST', 'localhost'),
        port=int(os.environ.get('PG_PORT', '5432')),
        dbname=os.environ['POSTGRES_DB'],
        user=os.environ['POSTGRES_USER'],
        password=os.environ['POSTGRES_PASSWORD'],
        connect_timeout=5,
    )
    cur = conn.cursor()
    cur.execute(
        "select count(*) from t_lhb_daily "
        "where trade_date >= current_date - interval '7 days'"
    )
    n = cur.fetchone()[0]
    print(f'lhb 7d rows={n}')
    conn.close()
    if n == 0:
        sys.exit(3)
except Exception as e:
    print(f'PG check FAIL: {e}', file=sys.stderr)
    sys.exit(2)
PY
then
    log "PG OK"
else
    log "PG/数据 FAIL(可能近 7 天非交易日,但最好人工确认)"
    FAIL=$((FAIL + 1))
fi

if [[ ${FAIL} -eq 0 ]]; then
    log "==> ALL GREEN"
    exit 0
else
    log "==> ${FAIL} hard check(s) failed"
    exit 1
fi
