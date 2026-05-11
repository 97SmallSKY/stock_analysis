#!/usr/bin/env bash
# cleanup_artifacts.sh — 清理 UI 验证 artifact 目录
#
# 策略:
#   成功 artifact 保留 7 天
#   失败 artifact 保留 30 天
#
# 目录结构:
#   ~/stock_analysis_artifacts/ui-verify/{issue_id}/{commit_sha_short}/
#   ~/stock_analysis_artifacts/ui-verify/{issue_id}/{commit_sha_short}/result.txt  (内容: PASS 或 FAIL)
#
# 用法:
#   ./scripts/cleanup_artifacts.sh [--dry-run]
set -euo pipefail

ARTIFACT_DIR="${HOME}/stock_analysis_artifacts/ui-verify"
SUCCESS_DAYS=7
FAIL_DAYS=30
DRY_RUN=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run) DRY_RUN=true; shift ;;
        *) echo "Unknown option: $1" >&2; exit 1 ;;
    esac
done

if [[ ! -d "$ARTIFACT_DIR" ]]; then
    echo "==> artifact 目录不存在，跳过清理: $ARTIFACT_DIR"
    exit 0
fi

DELETED=0
KEPT=0

# 遍历 {issue_id}/{commit_sha_short}/ 目录
while IFS= read -r -d '' artifact_path; do
    [[ -d "$artifact_path" ]] || continue

    # 判断是成功还是失败
    result_file="${artifact_path}/result.txt"
    if [[ -f "$result_file" ]] && grep -q '^PASS' "$result_file" 2>/dev/null; then
        KEEP_DAYS=$SUCCESS_DAYS
        TYPE="PASS"
    else
        KEEP_DAYS=$FAIL_DAYS
        TYPE="FAIL/UNKNOWN"
    fi

    # 检查目录的最后修改时间
    if [[ "$(uname)" == "Darwin" ]]; then
        MTIME=$(stat -f %m "$artifact_path" 2>/dev/null || echo 0)
    else
        MTIME=$(stat -c %Y "$artifact_path" 2>/dev/null || echo 0)
    fi
    NOW=$(date +%s)
    AGE_DAYS=$(( (NOW - MTIME) / 86400 ))

    if [[ $AGE_DAYS -gt $KEEP_DAYS ]]; then
        if [[ "$DRY_RUN" == "true" ]]; then
            echo "  [dry-run] 将删除: $artifact_path (${TYPE}, ${AGE_DAYS}天, 阈值${KEEP_DAYS}天)"
        else
            rm -rf "$artifact_path"
            echo "  删除: $artifact_path (${TYPE}, ${AGE_DAYS}天)"
        fi
        (( DELETED++ )) || true
    else
        (( KEPT++ )) || true
    fi
done < <(find "$ARTIFACT_DIR" -mindepth 2 -maxdepth 2 -type d -print0 2>/dev/null)

echo "==> cleanup_artifacts 完成: 删除 ${DELETED} 个，保留 ${KEPT} 个"
if [[ "$DRY_RUN" == "true" ]]; then
    echo "    (dry-run 模式，未实际删除)"
fi
