#!/usr/bin/env bash
# scripts/deploy_frontend.sh — 前端构建并发布到 nginx 根目录
#
# 用法:
#   bash scripts/deploy_frontend.sh                     # 本机构建+发布(默认)
#   FRONTEND_DIR=/path DIST_TARGET=/srv/web ./x.sh      # 自定义路径
#   DRY_RUN=1 bash scripts/deploy_frontend.sh           # 只打印命令不执行
#
# 环境变量:
#   FRONTEND_DIR   前端工程目录 (默认 <repo>/frontend)
#   DIST_TARGET    部署目标目录 (默认 /home/xiaotian/stock_analysis/frontend/dist)
#   NGINX_RELOAD   1=reload nginx (默认 1); 0=只发布不 reload
#   BACKUP_KEEP    保留最近 N 份备份 (默认 3)
#
# 步骤:
#   1. cd frontend && npm ci (若 node_modules 缺失)
#   2. npm run build → 产出 frontend/dist
#   3. 备份 DIST_TARGET -> ${DIST_TARGET}.bak.<ts>, 保留最近 BACKUP_KEEP 份
#   4. rsync 增量同步到 DIST_TARGET
#   5. nginx -t && systemctl reload nginx

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

FRONTEND_DIR="${FRONTEND_DIR:-${REPO_ROOT}/frontend}"
DIST_TARGET="${DIST_TARGET:-/home/xiaotian/stock_analysis/frontend/dist}"
NGINX_RELOAD="${NGINX_RELOAD:-1}"
BACKUP_KEEP="${BACKUP_KEEP:-3}"
DRY_RUN="${DRY_RUN:-0}"

log() { printf '[deploy_frontend] %s\n' "$*" >&2; }
run() {
    if [[ "${DRY_RUN}" == "1" ]]; then
        printf '+ %s\n' "$*" >&2
    else
        eval "$@"
    fi
}

if [[ ! -d "${FRONTEND_DIR}" ]]; then
    log "ERROR: 前端目录不存在: ${FRONTEND_DIR}"
    exit 2
fi

cd "${FRONTEND_DIR}"

# ---- 1. 安装依赖 ----
if [[ ! -d node_modules ]]; then
    log "node_modules 不存在, 执行 npm ci ..."
    run "npm ci --no-audit --no-fund"
fi

# ---- 2. 构建 ----
log "npm run build ..."
run "npm run build"

if [[ "${DRY_RUN}" != "1" && ! -f dist/index.html ]]; then
    log "ERROR: 构建产物缺失 dist/index.html"
    exit 3
fi

# ---- 3. 备份旧版本 ----
TARGET_PARENT="$(dirname "${DIST_TARGET}")"
TS="$(date +%Y%m%d-%H%M%S)"
if [[ -d "${DIST_TARGET}" ]]; then
    BAK="${DIST_TARGET}.bak.${TS}"
    log "备份 ${DIST_TARGET} -> ${BAK}"
    run "mv '${DIST_TARGET}' '${BAK}'"
else
    log "目标目录不存在, 首次部署"
    run "mkdir -p '${TARGET_PARENT}'"
fi

# ---- 4. 同步 ----
log "rsync dist/ -> ${DIST_TARGET}/"
run "mkdir -p '${DIST_TARGET}'"
run "rsync -a --delete '${FRONTEND_DIR}/dist/' '${DIST_TARGET}/'"

# 清理多余备份
if [[ "${DRY_RUN}" != "1" ]]; then
    OLD_BAKS=$(ls -1dt "${DIST_TARGET}.bak."* 2>/dev/null | tail -n +"$((BACKUP_KEEP + 1))" || true)
    if [[ -n "${OLD_BAKS}" ]]; then
        log "清理旧备份 (保留最近 ${BACKUP_KEEP} 份)"
        while IFS= read -r d; do
            rm -rf "$d"
        done <<< "${OLD_BAKS}"
    fi
fi

# ---- 5. reload nginx ----
if [[ "${NGINX_RELOAD}" == "1" ]]; then
    if command -v nginx >/dev/null 2>&1; then
        log "nginx -t ..."
        run "sudo nginx -t"
        log "systemctl reload nginx ..."
        run "sudo systemctl reload nginx"
    else
        log "WARN: 未找到 nginx, 跳过 reload"
    fi
fi

log "OK: 前端已发布 -> ${DIST_TARGET}"
