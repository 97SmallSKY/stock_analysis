#!/usr/bin/env bash
# 灌库状态速览(在 jd 上跑)
# 用法: ssh jd 'bash ~/stock_analysis/scripts/ingest_status.sh'
set -u
cd "$(dirname "$0")/.."

echo '============ 1. 进程 ============'
ps -ef | grep -E "full_ingest|backend.cli ingest" | grep -v grep || echo '  (none)'

echo
echo '============ 2. 阶段里程碑 ============'
if [ -f logs/full_ingest.log ]; then
  grep -E "=== 阶段|完成|失败.*只|失败.*天|行业覆盖|增量模式|进度" logs/full_ingest.log | tail -15
else
  echo '  (no log)'
fi

echo
echo '============ 3. 最近 5 条异常(非进度条) ============'
if [ -f logs/full_ingest.log ]; then
  grep -E "ERROR|失败:|触发限流|熔断|RemoteDisconnected" logs/full_ingest.log \
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
