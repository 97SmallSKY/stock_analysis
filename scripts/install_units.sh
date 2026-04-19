#!/usr/bin/env bash
# scripts/install_units.sh — 安装/更新 systemd unit(jd 服务器)
#
# 幂等:重跑会先 stop 再覆盖再 daemon-reload + restart。
# 同时处理迁移:如果机器上还残留已废弃的 stock_analysis_worker / scheduler,
# 会 disable + rm 它们。
#
# 用法:
#   sudo bash scripts/install_units.sh
#   DRY_RUN=1 bash scripts/install_units.sh    # 只打印,不执行

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
UNIT_SRC_DIR="${REPO_ROOT}/deploy/systemd"
UNIT_DST_DIR="/etc/systemd/system"
DRY_RUN="${DRY_RUN:-0}"

# 当前活跃的 unit 列表
#   - stock_analysis_api.service        常驻: uvicorn + APScheduler + WS
#   - stock_analysis_smoke.service      oneshot: 集成冒烟(由 timer 触发)
#   - stock_analysis_smoke.timer        每天 16:30 触发 smoke
#   - stock_analysis_daily.service      oneshot: 盘后数据更新(由 timer 触发)
#   - stock_analysis_daily.timer        工作日 18:40 触发 daily
ACTIVE_UNITS=(
    "stock_analysis_api.service"
    "stock_analysis_smoke.service"
    "stock_analysis_smoke.timer"
    "stock_analysis_daily.service"
    "stock_analysis_daily.timer"
)
# oneshot 类 unit,只 enable 不 start(由 timer 或手动拉起)
ONESHOT_UNITS=(
    "stock_analysis_smoke.service"
    "stock_analysis_daily.service"
)
# 已废弃的 unit —— 安装时顺便清理
LEGACY_UNITS=("stock_analysis_worker.service" "stock_analysis_scheduler.service")

log() { printf '[install_units] %s\n' "$*" >&2; }
run() {
    if [[ "${DRY_RUN}" == "1" ]]; then
        printf '+ %s\n' "$*" >&2
    else
        eval "$@"
    fi
}

if [[ ! -d "${UNIT_SRC_DIR}" ]]; then
    log "ERROR: 源目录不存在: ${UNIT_SRC_DIR}"
    exit 2
fi

for u in "${ACTIVE_UNITS[@]}"; do
    if [[ ! -f "${UNIT_SRC_DIR}/${u}" ]]; then
        log "ERROR: 缺少 unit 文件: ${UNIT_SRC_DIR}/${u}"
        exit 3
    fi
done

# 需要 root(除非 DRY_RUN)
if [[ "${DRY_RUN}" != "1" && "${EUID}" -ne 0 ]]; then
    log "ERROR: 本脚本需要 root(用 sudo 跑)"
    exit 4
fi

# ---- 1. 清理已废弃 unit ----
for u in "${LEGACY_UNITS[@]}"; do
    if systemctl list-unit-files 2>/dev/null | grep -q "^${u}"; then
        log "发现废弃 unit: ${u},执行 disable + rm"
        run "systemctl disable --now '${u}' || true"
        run "rm -f '${UNIT_DST_DIR}/${u}'"
    fi
done

# ---- 2. 覆盖活跃 unit ----
for u in "${ACTIVE_UNITS[@]}"; do
    log "安装 ${u}"
    run "cp '${UNIT_SRC_DIR}/${u}' '${UNIT_DST_DIR}/${u}'"
done

# ---- 3. reload + enable + (re)start ----
# 规则:
#   *.service(非 oneshot)     : enable + restart/start
#   *.timer                    : enable + start(幂等)
#   oneshot *.service          : 只 enable,不 start(由 timer 或手动拉起)
run "systemctl daemon-reload"
for u in "${ACTIVE_UNITS[@]}"; do
    run "systemctl enable '${u}'"
    # oneshot 类 service 只 enable,不 start(由 timer 触发)
    skip_start=0
    for o in "${ONESHOT_UNITS[@]}"; do
        if [[ "${u}" == "${o}" ]]; then skip_start=1; break; fi
    done
    if [[ ${skip_start} -eq 1 ]]; then
        log "${u} 是 oneshot,跳过 start(由 timer 触发)"
        continue
    fi
    if [[ "${DRY_RUN}" != "1" ]] && systemctl is-active --quiet "${u}"; then
        run "systemctl restart '${u}'"
    else
        run "systemctl start '${u}' || true"
    fi
done

log "OK: systemd units 安装完成"
if [[ "${DRY_RUN}" != "1" ]]; then
    for u in "${ACTIVE_UNITS[@]}"; do
        systemctl --no-pager --lines=0 status "${u}" || true
    done
fi
