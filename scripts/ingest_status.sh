#!/usr/bin/env bash
# 灌库状态速览(在 jd 上跑)
# 用法: ssh jd 'bash ~/stock_analysis/scripts/ingest_status.sh'
set -u
cd "$(dirname "$0")/.."

echo '============ 1. 进程 ============'
ps -ef | grep -E "full_ingest|backend.cli ingest" | grep -v grep || echo '  (none)'

echo
echo '============ 1.5 日志心跳检查(自动定位最近活跃日志)============'
# 事故教训 (2026-04-18): 进程活着但日志长时间无增长 = 某个 HTTP 调用卡死.
# 事故教训 (2026-04-20): 仅看 full_ingest.log 会漏掉 p16_*.log 等分流日志, 误报卡死.
# 策略: 扫描 logs/*.log, 取 mtime 最新的那个作为"活跃日志", 同时列出前 3 个最近修改的.
mapfile -t RECENT_LOGS < <(
  find logs/ -maxdepth 1 -name '*.log' -type f -printf '%T@ %p\n' 2>/dev/null \
    | sort -rn | head -5 | awk '{print $2}'
)
if [ ${#RECENT_LOGS[@]} -gt 0 ]; then
  ACTIVE_LOG="${RECENT_LOGS[0]}"
  MTIME=$(stat -c %Y "$ACTIVE_LOG" 2>/dev/null || stat -f %m "$ACTIVE_LOG")
  NOW=$(date +%s)
  SILENT=$(( NOW - MTIME ))
  echo "  活跃日志: $ACTIVE_LOG"
  if [ $SILENT -gt 600 ]; then
    echo "  ⚠️  已静默 $((SILENT / 60)) 分钟 (>10 分钟), 疑似卡死"
    echo "     排查: ss -tnp | grep <pid>  /  lsof -nP -p <pid> | grep TCP"
    echo "     处置: kill -9 <pid> 让任务进入下一阶段"
  else
    echo "  ✓ ${SILENT}s 前刚更新 (<10 分钟)"
  fi
  echo
  echo '  最近修改的日志 Top5:'
  for f in "${RECENT_LOGS[@]}"; do
    m=$(stat -c %Y "$f" 2>/dev/null || stat -f %m "$f")
    age=$(( NOW - m ))
    sz=$(stat -c %s "$f" 2>/dev/null || stat -f %z "$f")
    printf '    %-45s  age=%5ds  size=%s\n' "$f" "$age" "$sz"
  done
else
  echo '  (no log files under logs/)'
  ACTIVE_LOG=""
fi

echo
echo '============ 2. 阶段里程碑(活跃日志)============'
if [ -n "$ACTIVE_LOG" ] && [ -f "$ACTIVE_LOG" ]; then
  grep -E "=== 阶段|完成|失败.*只|失败.*天|行业覆盖|增量模式|进度" "$ACTIVE_LOG" | tail -15
else
  echo '  (no active log)'
fi

echo
echo '============ 3. 最近 5 条异常(活跃日志,非进度条)============'
if [ -n "$ACTIVE_LOG" ] && [ -f "$ACTIVE_LOG" ]; then
  grep -E "ERROR|失败:|触发限流|熔断|RemoteDisconnected" "$ACTIVE_LOG" \
    | grep -v -E "%\||\[0m" \
    | tail -5 || echo '  (clean)'
fi

echo
echo '============ 4. DB 计数 ============'
.venv/bin/python - <<'PY'
from backend.db.session import pg_conn
with pg_conn() as conn, conn.cursor() as cur:
    for t in ("stock_info", "kline_daily", "lhb_stock_daily",
              "market_sentiment", "trade_calendar"):
        cur.execute(f"SELECT COUNT(*) FROM {t}")
        print(f"  {t:22s} = {cur.fetchone()[0]:>10}")
    cur.execute("SELECT COUNT(DISTINCT ts_code) FROM kline_daily")
    print(f"  kline uniq ts_code      = {cur.fetchone()[0]:>10}")
    cur.execute("SELECT COUNT(industry) FROM stock_info")
    n_industry = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM stock_info")
    total = cur.fetchone()[0]
    pct = n_industry / max(total, 1) * 100
    print(f"  industry 覆盖           = {n_industry}/{total} ({pct:.1f}%)")
PY

echo
echo '============ 5. 代码版本 ============'
git log --oneline -1

echo
echo '完成。如需看日志尾: ssh jd "tail -30 ~/stock_analysis/logs/full_ingest.log"'
