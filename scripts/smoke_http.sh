#!/usr/bin/env bash
# HTTP 冒烟:检查关键只读端点是否 200。
# 用法: bash smoke_http.sh [port]
#
# P20.1 (2026-04-20): 覆盖 /api/changelog + /api/devlog(分拆后的特性/开发日志)
# P19.1/P19.12: 覆盖 /api/kline/daterange + /api/kline/daily (前端 K 线核心依赖)
set -u
PORT="${1:-8000}"
BASE="http://127.0.0.1:${PORT}"
PATHS=(
  /healthz
  /api/dashboard
  "/api/lhb?limit=1"
  /api/reports/weekly
  /api/changelog
  /api/devlog
  "/api/kline/daterange?ts_code=600519.SH"
  "/api/kline/daily?ts_code=600519.SH&limit=5"
)
fail=0
for p in "${PATHS[@]}"; do
  code=$(curl -s -o /dev/null -w '%{http_code}' "${BASE}${p}")
  printf 'GET %-50s -> %s\n' "$p" "$code"
  [ "$code" = "200" ] || fail=1
done
if [ $fail -eq 0 ]; then
  echo "All ${#PATHS[@]} endpoints OK"
else
  echo "FAIL: 有端点非 200"
fi
exit $fail
