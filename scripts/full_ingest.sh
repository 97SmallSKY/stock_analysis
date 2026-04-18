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

# 外层超时保护 (2026-04-18 卡死事故后加)
# 配合 base_adapter._safe_request 的 per-call 硬超时, 形成两层护栏:
#   - 内层(30~60s): 单次 HTTP 调用卡死自动熔断+重试
#   - 外层(此处 timeout): 极端情况(如 Python GIL 被 native 死锁占用)
#     整条 CLI 进程被 SIGTERM, 脚本继续下一阶段
# --kill-after=60 给 SIGTERM 后 60s 机会清理, 到时 SIGKILL
TIMEOUT_SHORT="timeout --preserve-status --kill-after=60  1h"
TIMEOUT_LONG="timeout --preserve-status --kill-after=120 6h"

echo "[$(TS)] ============================================================"
echo "[$(TS)] 全量灌库开始"
echo "[$(TS)] ============================================================"

echo "[$(TS)] === 阶段1: stock-info 4 轮(累积行业覆盖) ==="
for i in 1 2 3 4; do
  echo "[$(TS)] -- stock-info 第 $i 轮 --"
  $TIMEOUT_SHORT $PY -m backend.cli ingest stock-info \
    || echo "[$(TS)] 第 $i 轮失败/超时,继续"
done

echo "[$(TS)] === 阶段2: kline 全市场 2024/01/01 - 2024/12/31 ==="
$TIMEOUT_LONG $PY -m backend.cli ingest kline --from 20240101 --to 20241231 --codes all \
  || echo "[$(TS)] kline 阶段失败/超时(exit=$?), 继续后续阶段"

echo "[$(TS)] === 阶段3: sentiment 全年(会触发 DB 反算,秒级完成) ==="
$TIMEOUT_SHORT $PY -m backend.cli ingest sentiment --from 20240101 --to 20241231 \
  || echo "[$(TS)] sentiment 阶段失败/超时, 继续"

echo "[$(TS)] === 阶段4: lhb 全年校对(幂等重跑) ==="
$TIMEOUT_LONG $PY -m backend.cli ingest lhb --from 20240101 --to 20241231 \
  || echo "[$(TS)] lhb 阶段失败/超时, 继续"

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
