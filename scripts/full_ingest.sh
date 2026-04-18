#!/usr/bin/env bash
# 全量灌库链式脚本(在 jd 上后台跑)
# 用法: nohup bash scripts/full_ingest.sh > logs/full_ingest.log 2>&1 &
set -u
cd "$(dirname "$0")/.."

# 关键: jd 上全局设置了代理 127.0.0.1:10809 / socks5 10808
# 但代理进程未运行, 所有 http 请求会 ProxyError
# 这里强制 unset, 让所有 ingest 直连 (akshare 走的都是国内站)
unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy

PY=.venv/bin/python
TS() { date '+%F %T'; }

echo "[$(TS)] ============================================================"
echo "[$(TS)] 全量灌库开始"
echo "[$(TS)] ============================================================"

echo "[$(TS)] === 阶段1: stock-info 4 轮(累积行业覆盖) ==="
for i in 1 2 3 4; do
  echo "[$(TS)] -- stock-info 第 $i 轮 --"
  $PY -m backend.cli ingest stock-info || echo "[$(TS)] 第 $i 轮失败,继续"
done

echo "[$(TS)] === 阶段2: kline 全市场 2024/01/01 - 2024/12/31 ==="
$PY -m backend.cli ingest kline --from 20240101 --to 20241231 --codes all

echo "[$(TS)] === 阶段3: sentiment 全年(会触发 DB 反算,秒级完成) ==="
$PY -m backend.cli ingest sentiment --from 20240101 --to 20241231

echo "[$(TS)] === 阶段4: lhb 全年校对(幂等重跑) ==="
$PY -m backend.cli ingest lhb --from 20240101 --to 20241231

echo "[$(TS)] === 数据库统计 ==="
$PY - <<'PYEND'
from backend.db.session import pg_conn
with pg_conn() as conn, conn.cursor() as cur:
    for t in ("stock_info", "kline_daily", "lhb_stock_daily",
              "market_sentiment", "trade_calendar"):
        cur.execute(f"SELECT COUNT(*) FROM {t}")
        n = cur.fetchone()[0]
        print(f"  {t:20s} = {n}")
    cur.execute("SELECT COUNT(industry) FROM stock_info")
    print("  stock_info.industry not null =", cur.fetchone()[0])
PYEND

echo "[$(TS)] ============================================================"
echo "[$(TS)] 全量灌库结束"
echo "[$(TS)] ============================================================"
