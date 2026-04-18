#!/usr/bin/env bash
# HTTP 冒烟:检查关键只读端点是否 200。
# 用法: bash smoke_http.sh [port]
set -u
PORT="${1:-8000}"
BASE="http://127.0.0.1:${PORT}"
PATHS=(
  /api/dashboard
  "/api/lhb?limit=1"
  /api/reports/weekly
)
fail=0
for p in "${PATHS[@]}"; do
  code=$(curl -s -o /dev/null -w '%{http_code}' "${BASE}${p}")
  printf 'GET %-30s -> %s\n' "$p" "$code"
  [ "$code" = "200" ] || fail=1
done
exit $fail
