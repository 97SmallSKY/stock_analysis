#!/usr/bin/env bash
# scripts/deploy_frontend.sh — 仅构建前端,不再 rsync 到外部目录,不再 reload nginx
#
# 自 P5.1b 起,FastAPI 进程直接 serve <repo>/frontend/dist,因此前端
# "部署" = 在仓库内 build 出 dist。无需独立 nginx 站点,无需同步目录。
#
# 用法:
#   bash scripts/deploy_frontend.sh                     # 本机构建(默认)
#   FRONTEND_DIR=/path bash scripts/deploy_frontend.sh   # 自定义工程目录
#   DRY_RUN=1 bash scripts/deploy_frontend.sh            # 只打印命令不执行
#
# 环境变量:
#   FRONTEND_DIR  前端工程目录 (默认 <repo>/frontend)
#   DRY_RUN       1=只打印命令不执行 (默认 0)
#
# 步骤:
#   1. cd frontend && (pnpm install --frozen-lockfile | npm ci)
#   2. (pnpm build | npm run build) → 产出 frontend/dist
#   3. 校验 frontend/dist/index.html 存在
#
# 注:不会触碰 nginx,不会重启 systemd。如果只改了前端,运行结束后用
#     curl -fsS http://localhost:8000/ 验证即可(FastAPI SPA fallback)。

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

FRONTEND_DIR="${FRONTEND_DIR:-${REPO_ROOT}/frontend}"
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

# ---- 选包管理器 ----
if command -v pnpm >/dev/null 2>&1; then
    INSTALL_CMD="pnpm install --frozen-lockfile"
    BUILD_CMD="pnpm build"
elif command -v npm >/dev/null 2>&1; then
    INSTALL_CMD="npm ci --no-audit --no-fund"
    BUILD_CMD="npm run build"
else
    log "ERROR: 未找到 pnpm 或 npm"
    exit 3
fi

log "install: ${INSTALL_CMD}"
run "${INSTALL_CMD}"

log "build: ${BUILD_CMD}"
run "${BUILD_CMD}"

if [[ "${DRY_RUN}" != "1" ]]; then
    if [[ ! -f dist/index.html ]]; then
        log "ERROR: 构建产物缺失 dist/index.html"
        exit 4
    fi
    if [[ -z "$(ls -A dist 2>/dev/null)" ]]; then
        log "ERROR: frontend/dist 为空"
        exit 5
    fi
    FILE_COUNT="$(find dist -type f | wc -l | tr -d ' ')"
    log "OK: 构建完成 -> ${FRONTEND_DIR}/dist (${FILE_COUNT} files)"
    log "提示: FastAPI 已就地 serve 此目录;验证:curl http://localhost:8000/"
fi
