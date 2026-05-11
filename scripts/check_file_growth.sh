#!/usr/bin/env bash
# check_file_growth.sh — M14 文件增长检测(供 Auditor agent 读取)
#
# 用法:
#   ./scripts/check_file_growth.sh [--since-sha <sha>] [--add-limit 250] [--total-limit 800]
#
# 输出格式(供 Auditor 机器解析):
#   STATUS: PASS | WARN
#   <文件路径>  +<新增行数>  total=<总行数>  [ADD_EXCEEDED | TOTAL_EXCEEDED]
#
# 退出码:
#   0 = 无超标文件
#   1 = 有超标文件
set -euo pipefail

ADD_LIMIT=250
TOTAL_LIMIT=800
SINCE_SHA=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --since-sha) SINCE_SHA="$2"; shift 2 ;;
        --add-limit) ADD_LIMIT="$2"; shift 2 ;;
        --total-limit) TOTAL_LIMIT="$2"; shift 2 ;;
        *) echo "Unknown option: $1" >&2; exit 1 ;;
    esac
done

cd "$(dirname "$0")/.."

# 确定对比基准
if [[ -z "$SINCE_SHA" ]]; then
    # 默认对比 origin/new-branch 的最近一次 merge base
    SINCE_SHA=$(git merge-base HEAD origin/new-branch 2>/dev/null || git rev-parse HEAD~1 2>/dev/null || echo "")
fi

if [[ -z "$SINCE_SHA" ]]; then
    echo "STATUS: SKIP (无法确定对比基准 SHA)"
    exit 0
fi

# 找出变更的 Python/TypeScript/Vue 文件(排除 docs/ tests/ migrations/)
CHANGED_FILES=$(git diff --name-only "$SINCE_SHA" HEAD 2>/dev/null \
    | grep -E '\.(py|ts|vue|js)$' \
    | grep -v '^docs/' \
    | grep -v '^tests/' \
    | grep -v 'db/init/' \
    || true)

if [[ -z "$CHANGED_FILES" ]]; then
    echo "STATUS: PASS"
    echo "(无 Python/TypeScript/Vue 文件变更)"
    exit 0
fi

WARN_FILES=()

while IFS= read -r file; do
    [[ -z "$file" ]] && continue
    [[ ! -f "$file" ]] && continue

    # 新增行数
    ADDED=$(git diff "$SINCE_SHA" HEAD -- "$file" 2>/dev/null \
        | grep '^+' | grep -v '^+++' | wc -l || echo 0)

    # 当前总行数
    TOTAL=$(wc -l < "$file" 2>/dev/null || echo 0)

    EXCEEDED=""
    if [[ $ADDED -gt $ADD_LIMIT ]]; then
        EXCEEDED="ADD_EXCEEDED"
    fi
    if [[ $TOTAL -gt $TOTAL_LIMIT ]]; then
        EXCEEDED="${EXCEEDED:+$EXCEEDED,}TOTAL_EXCEEDED"
    fi

    if [[ -n "$EXCEEDED" ]]; then
        WARN_FILES+=("$file  +${ADDED}行  total=${TOTAL}  [$EXCEEDED]")
    fi
done <<< "$CHANGED_FILES"

if [[ ${#WARN_FILES[@]} -eq 0 ]]; then
    echo "STATUS: PASS"
    echo "检查了 $(echo "$CHANGED_FILES" | wc -l) 个文件，无超标"
    exit 0
else
    echo "STATUS: WARN"
    echo "发现 ${#WARN_FILES[@]} 个文件超过增长阈值(新增>${ADD_LIMIT}行 或 总行数>${TOTAL_LIMIT}行):"
    for f in "${WARN_FILES[@]}"; do
        echo "  $f"
    done
    exit 1
fi
