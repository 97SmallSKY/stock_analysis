#!/bin/bash
# ============================================================
# 每日数据同步脚本
# ============================================================
#
# 用法:
#   ./scripts/daily_sync.sh              # 完整盘后同步
#   ./scripts/daily_sync.sh --opencli    # 仅 opencli 同步（龙虎榜+指数+北向）
#   ./scripts/daily_sync.sh --akshare    # 仅 AKShare 同步（全量11步）
#   ./scripts/daily_sync.sh --etl        # 仅 ETL 桥接（real→logical表）
#
# 数据源优先级:
#   1. AKShare (Python) — 主数据源，全量覆盖，Linux 原生支持
#   2. opencli (Node.js) — 补充来源，龙虎榜/指数/北向资金
#
# opencli Linux 安装:
#   npm install -g @jackwener/opencli
#   (需要 Node.js >=18，参考: https://www.npmjs.com/package/@jackwener/opencli)
#
# opencli macOS 安装:
#   brew install opencli
#   # 或: npm install -g @jackwener/opencli
#
# Crontab 推荐配置 (Linux 服务器):
#   # 盘后主同步 — 18:40
#   40 18 * * 1-5  /root/stock_analysis/scripts/daily_sync.sh >> /root/stock_analysis/logs/daily_sync.log 2>&1
#
#   # 开盘前补全 T+N — 09:05
#   5  9  * * 1-5  /root/stock_analysis/scripts/daily_sync.sh --akshare --steps 3,11 >> /root/stock_analysis/logs/daily_sync.log 2>&1
#
#   # 盘中情绪快照 — 14:55
#   55 14 * * 1-5  /root/stock_analysis/scripts/daily_sync.sh --sentiment >> /root/stock_analysis/logs/daily_sync.log 2>&1
# ============================================================

set -euo pipefail

# ── 路径 ──
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$ROOT_DIR"

LOG_DIR="$ROOT_DIR/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/daily_sync.log"

# ── Python ──
if [ -f "$ROOT_DIR/.venv/bin/python" ]; then
    PYTHON="$ROOT_DIR/.venv/bin/python"
elif [ -f "/root/miniconda3/bin/python" ]; then
    PYTHON="/root/miniconda3/bin/python"
elif command -v python3 &>/dev/null; then
    PYTHON="$(command -v python3)"
else
    echo "❌ 找不到 Python" | tee -a "$LOG_FILE"
    exit 1
fi

# ── 日志 ──
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"
}

# ── 参数解析 ──
MODE="full"
AKSHARE_EXTRA=""
while [ $# -gt 0 ]; do
    case "$1" in
        --opencli)  MODE="opencli" ;;
        --akshare)  MODE="akshare" ;;
        --etl)      MODE="etl" ;;
        --sentiment) MODE="sentiment" ;;
        --steps)    AKSHARE_EXTRA="--steps $2"; shift ;;
        *)          log "未知参数: $1"; exit 1 ;;
    esac
    shift
done

# ═══════════════════════════════════════════════════════════════
# Step 1: opencli 同步（龙虎榜 + 指数 + 北向资金）
# ═══════════════════════════════════════════════════════════════
run_opencli_sync() {
    log "━━━ opencli 数据同步 ━━━"

    # 检查 opencli 是否可用
    if ! command -v opencli &>/dev/null; then
        log "⚠️  opencli 未安装，跳过"
        log "   安装方法: npm install -g @jackwener/opencli"
        log "   或: brew install opencli (macOS)"
        return 0
    fi

    log "opencli 版本: $(opencli --version 2>/dev/null || echo 'unknown')"

    # 1. 龙虎榜明细
    log "  [1/3] 拉取龙虎榜数据..."
    if opencli eastmoney longhu --limit 100 2>/dev/null > /tmp/opencli_lhb.yaml; then
        count=$(grep -c 'code:' /tmp/opencli_lhb.yaml 2>/dev/null || echo "0")
        log "    ✅ 龙虎榜: ${count} 条"
    else
        log "    ❌ 龙虎榜拉取失败，请检查 opencli 版本或网络"
    fi

    # 2. 大盘指数
    log "  [2/3] 拉取大盘指数..."
    if opencli eastmoney index-board --group main 2>/dev/null > /tmp/opencli_index.json; then
        log "    ✅ 指数行情已获取"
    else
        log "    ⚠️  指数行情拉取失败"
    fi

    # 3. 北向资金
    log "  [3/3] 拉取北向资金..."
    if opencli eastmoney northbound 2>/dev/null > /tmp/opencli_northbound.json; then
        log "    ✅ 北向资金已获取"
    else
        log "    ⚠️  北向资金拉取失败"
    fi

    # 调用 Python 脚本写入 PG
    if [ -f "$ROOT_DIR/scripts/opencli_sync.py" ]; then
        log "  写入 PostgreSQL..."
        $PYTHON "$ROOT_DIR/scripts/opencli_sync.py" --mode daily 2>&1 | tail -5 | while IFS= read -r line; do
            log "    $line"
        done
        log "  ✅ opencli 同步完成"
    else
        log "  ⚠️  scripts/opencli_sync.py 不存在，跳过PG写入"
    fi
}

# ═══════════════════════════════════════════════════════════════
# Step 2: AKShare 全量同步（11步数据管线）
# ═══════════════════════════════════════════════════════════════
run_akshare_sync() {
    log "━━━ AKShare 数据同步 ━━━"

    if [ ! -f "$ROOT_DIR/scripts/daily_update.py" ]; then
        log "❌ scripts/daily_update.py 不存在"
        return 1
    fi

    log "开始 AKShare 数据更新..."
    $PYTHON "$ROOT_DIR/scripts/daily_update.py" $AKSHARE_EXTRA >> "$LOG_FILE" 2>&1
    local exit_code=$?

    if [ $exit_code -eq 0 ]; then
        log "✅ AKShare 同步完成"
    else
        log "❌ AKShare 同步失败 (exit=$exit_code)"
        return $exit_code
    fi
}

# ═══════════════════════════════════════════════════════════════
# Step 3: ETL 桥接（real → logical 表）
# ═══════════════════════════════════════════════════════════════
run_etl() {
    log "━━━ ETL 桥接 ━━━"

    if [ ! -f "$ROOT_DIR/scripts/etl_real_to_logical.py" ]; then
        log "⚠️  scripts/etl_real_to_logical.py 不存在，跳过"
        return 0
    fi

    $PYTHON "$ROOT_DIR/scripts/etl_real_to_logical.py" >> "$LOG_FILE" 2>&1
    local exit_code=$?

    if [ $exit_code -eq 0 ]; then
        log "✅ ETL 桥接完成 (real → logical)"
    else
        log "❌ ETL 桥接失败 (exit=$exit_code)"
        return $exit_code
    fi
}

# ═══════════════════════════════════════════════════════════════
# Step 4: 盘中情绪快照（轻量）
# ═══════════════════════════════════════════════════════════════
run_sentiment() {
    log "━━━ 盘中情绪快照 ━━━"
    $PYTHON "$ROOT_DIR/scripts/daily_update.py" --sentiment-only >> "$LOG_FILE" 2>&1
    log "✅ 情绪快照完成"
}

# ═══════════════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════════════
log "══════════════════════════════════════════════"
log "  每日数据同步开始 (mode=$MODE)"
log "  Python: $PYTHON"
log "══════════════════════════════════════════════"

case "$MODE" in
    full)
        # 先 AKShare 全量（主数据源）
        run_akshare_sync

        # 再 opencli 补充（如果可用）
        run_opencli_sync

        # 最后 ETL 桥接
        run_etl
        ;;
    opencli)
        run_opencli_sync
        # opencli 后也需要 ETL 让页面可见
        run_etl
        ;;
    akshare)
        run_akshare_sync
        run_etl
        ;;
    etl)
        run_etl
        ;;
    sentiment)
        run_sentiment
        ;;
esac

# ── 数据完整性检查 ──
log "━━━ 数据完整性扫描 ━━━"
$PYTHON "$ROOT_DIR/scripts/data_check.py" --scan 2>&1 | while IFS= read -r line; do
    log "  $line"
done

log "══════════════════════════════════════════════"
log "  每日数据同步结束 ($(date '+%Y-%m-%d %H:%M:%S'))"
log "══════════════════════════════════════════════"
