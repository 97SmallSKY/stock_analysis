#!/usr/bin/env bash
# 全量灌库链式脚本(在 jd 上后台跑)
# 用法: nohup bash scripts/full_ingest.sh > logs/full_ingest.log 2>&1 &
set -u
cd "$(dirname "$0")/.."

# 代理处理: 本脚本**不再在此 unset 代理**, 改由 jd 的 shell 初始化文件配置
# NO_PROXY/no_proxy 来覆盖所有 akshare 会访问的国内域名.
# 具体域名列表见 docs/EXTERNAL_SOURCES_ISSUES.md 第 7 节
# 如仍出现 ProxyError, 检查 ~/.bashrc / ~/.profile 里的 no_proxy 是否包含目标域名

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
