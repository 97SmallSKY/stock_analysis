#!/usr/bin/env bash
# scripts/backup_pg.sh — W12-4 PG 每日备份
#
# 用法:
#   bash scripts/backup_pg.sh                     # 默认备份到 ./backups
#   BACKUP_DIR=/srv/backups bash scripts/backup_pg.sh
#   DRY_RUN=1 bash scripts/backup_pg.sh           # 只打印
#
# 环境变量:
#   BACKUP_DIR       备份目录 (默认 <repo>/backups)
#   PG_HOST / PG_PORT / PG_USER / PG_DB / PGPASSWORD
#                    传给 pg_dump (默认本地 postgres/lhb)
#   KEEP_DAYS        保留 N 天 (默认 30)
#   UPLOAD_S3        s3://bucket/prefix, 非空时尝试 aws s3 cp
#   DRY_RUN=1        不执行, 只打印
#
# 步骤:
#   1. pg_dump -Fc -f <dir>/lhb-YYYYMMDD-HHMMSS.dump
#   2. (可选) aws s3 cp 到 UPLOAD_S3
#   3. 清理本地 >= KEEP_DAYS 天的旧 .dump

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

BACKUP_DIR="${BACKUP_DIR:-${REPO_ROOT}/backups}"
PG_HOST="${PG_HOST:-localhost}"
PG_PORT="${PG_PORT:-5432}"
PG_USER="${PG_USER:-postgres}"
PG_DB="${PG_DB:-lhb}"
KEEP_DAYS="${KEEP_DAYS:-30}"
UPLOAD_S3="${UPLOAD_S3:-}"
DRY_RUN="${DRY_RUN:-0}"

log() { printf '[backup_pg] %s\n' "$*" >&2; }
run() {
    if [[ "${DRY_RUN}" == "1" ]]; then
        printf '+ %s\n' "$*" >&2
    else
        eval "$@"
    fi
}

if [[ "${DRY_RUN}" != "1" ]] && ! command -v pg_dump >/dev/null 2>&1; then
    log "ERROR: pg_dump 未安装"
    exit 2
fi

mkdir -p "${BACKUP_DIR}"
TS="$(date +%Y%m%d-%H%M%S)"
FNAME="${BACKUP_DIR}/${PG_DB}-${TS}.dump"

log "pg_dump ${PG_DB}@${PG_HOST}:${PG_PORT} -> ${FNAME}"
run "pg_dump -h '${PG_HOST}' -p '${PG_PORT}' -U '${PG_USER}' -d '${PG_DB}' -Fc -Z 6 -f '${FNAME}'"

# 校验
if [[ "${DRY_RUN}" != "1" ]]; then
    SIZE=$(stat -c%s "${FNAME}" 2>/dev/null || stat -f%z "${FNAME}" 2>/dev/null || echo 0)
    log "备份大小 ${SIZE} bytes"
    if [[ "${SIZE}" -lt 1024 ]]; then
        log "ERROR: 备份过小 (<1KB), 可能失败; 保留文件以便排查"
        exit 3
    fi
fi

# 上传 S3 (可选)
if [[ -n "${UPLOAD_S3}" ]]; then
    if command -v aws >/dev/null 2>&1; then
        log "aws s3 cp -> ${UPLOAD_S3}"
        run "aws s3 cp '${FNAME}' '${UPLOAD_S3}/'"
    else
        log "WARN: aws CLI 未安装, 跳过上传"
    fi
fi

# 清理旧备份
if [[ "${DRY_RUN}" != "1" ]]; then
    DEL=$(find "${BACKUP_DIR}" -name "${PG_DB}-*.dump" -type f -mtime "+${KEEP_DAYS}" 2>/dev/null || true)
    if [[ -n "${DEL}" ]]; then
        echo "${DEL}" | while IFS= read -r f; do
            log "清理 $f"
            rm -f "$f"
        done
    fi
fi

log "OK: 备份完成 ${FNAME}"

# cron 示例 (每天 02:30):
#   30 2 * * *  /bin/bash /home/xiaotian/stock_analysis/scripts/backup_pg.sh >> /var/log/lhb_backup.log 2>&1
