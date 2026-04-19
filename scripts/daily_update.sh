#!/bin/bash
# ============================================================
# 每日盘后数据更新(A股交易日 18:40 由 systemd timer 触发)
# 步骤:
#   1. 龙虎榜主表:akshare 拉当日 lhb_stock_daily
#   2. 龙虎榜席位 top10:回填 lhb_seats
#   3. 市场情绪:market_sentiment 当日
#   4. 日K线:kline_daily 当日增量(全市场)
#   5. 评分:对当日新增龙虎榜行评分(--only-empty)
# 任一步骤失败不中断后续步骤,最终以是否有 ERROR 标记 exit code。
# ============================================================
set -u
cd "$(dirname "$0")/.." || exit 2

LOG_DIR="${LOG_DIR:-logs}"
mkdir -p "$LOG_DIR"

TODAY="${TODAY:-$(date +%Y%m%d)}"
START_TS=$(date +%s)
LOG_FILE="$LOG_DIR/daily_update.log"
ERR_COUNT=0

log() { printf '[%s] %s\n' "$(date '+%F %T')" "$*" | tee -a "$LOG_FILE"; }

run_step() {
  local name="$1"; shift
  log "==> BEGIN $name :: $*"
  if "$@" >>"$LOG_FILE" 2>&1; then
    log "<== OK $name"
  else
    local rc=$?
    log "<== ERROR $name (rc=$rc)"
    ERR_COUNT=$((ERR_COUNT+1))
  fi
}

log "################ daily_update TODAY=$TODAY ################"

# 激活项目 venv(若存在)
if [ -f .venv/bin/activate ]; then
  # shellcheck disable=SC1091
  . .venv/bin/activate
  log "venv activated: $(python -V 2>&1)"
fi

# 1) 龙虎榜主表(单日)
run_step lhb_daily python -m backend.cli ingest lhb --from "$TODAY" --to "$TODAY"

# 2) 席位 TOP10
run_step lhb_seats python -m backend.cli ingest lhb-seats --from "$TODAY" --to "$TODAY" --top 10

# 3) 市场情绪
run_step sentiment python -m backend.cli ingest sentiment --from "$TODAY" --to "$TODAY"

# 4) 日K线(全市场增量)
run_step kline python -m backend.cli ingest kline --from "$TODAY" --to "$TODAY" --codes all

# 5) 评分(仅空缺行)
run_step score python -m backend.cli score --from "$TODAY" --to "$TODAY" --only-empty

ELAPSED=$(( $(date +%s) - START_TS ))
log "################ daily_update END errors=$ERR_COUNT elapsed=${ELAPSED}s ################"

exit "$ERR_COUNT"
