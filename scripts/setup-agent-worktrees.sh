#!/usr/bin/env bash
# setup-agent-worktrees.sh — 为 4 个 AI 角色创建独立 git worktree(一次性执行)
#
# 背景:
#   多个 Claude Code 会话共用同一工作区会产生 git checkout 互相践踏、
#   未提交代码丢失、pytest 缓存污染等数据竞争。用 worktree 物理隔离。
#
# 结果:
#   ~/Develop/personal/stock_analysis            (主仓库,保持 new-branch,仅供交互式开发)
#   ~/Develop/personal/stock_analysis-pm         (PM agent,只读 new-branch)
#   ~/Develop/personal/stock_analysis-dev        (Dev agent,开 feat/* 分支)
#   ~/Develop/personal/stock_analysis-test       (Test agent,checkout Dev 分支验收)
#   ~/Develop/personal/stock_analysis-ops        (Ops agent,JIT merge 部署)
#
#   四个目录共享同一个 .git(worktree 原生特性),磁盘仅多一份工作区文件。
#
# 用法:
#   ./scripts/setup-agent-worktrees.sh          # 创建全部 4 个
#   ./scripts/setup-agent-worktrees.sh --prune  # 先清理孤儿 worktree 再创建
#   ./scripts/setup-agent-worktrees.sh --list   # 列出所有 worktree
#

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PARENT="$(dirname "$REPO_ROOT")"
REPO_NAME="$(basename "$REPO_ROOT")"

ROLES=(pm dev test ops)
BASE_BRANCH="new-branch"

# 颜色
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

log()  { echo -e "${GREEN}[setup-worktrees]${NC} $*"; }
warn() { echo -e "${YELLOW}[warn]${NC} $*"; }
err()  { echo -e "${RED}[err]${NC} $*" >&2; }

# ---------- 参数解析 ----------
ACTION="create"
for arg in "$@"; do
    case "$arg" in
        --prune) ACTION="prune-then-create" ;;
        --list)  ACTION="list" ;;
        --help|-h)
            sed -n '2,25p' "$0"
            exit 0
            ;;
    esac
done

cd "$REPO_ROOT"

# ---------- 列出现有 worktree ----------
if [ "$ACTION" = "list" ]; then
    git worktree list
    exit 0
fi

# ---------- 预检 ----------
log "仓库根: $REPO_ROOT"
log "父目录: $PARENT"
log "将基于分支: $BASE_BRANCH"

# 确认 new-branch 存在
if ! git show-ref --verify --quiet "refs/heads/$BASE_BRANCH"; then
    err "本地没有 $BASE_BRANCH 分支,先 git fetch origin $BASE_BRANCH:$BASE_BRANCH"
    exit 1
fi

# 拉取最新
log "git fetch origin --prune ..."
git fetch origin --prune

# ---------- 清理孤儿 worktree(可选) ----------
if [ "$ACTION" = "prune-then-create" ]; then
    log "git worktree prune ..."
    git worktree prune -v
fi

# ---------- 为每个角色建 worktree ----------
for role in "${ROLES[@]}"; do
    WT_PATH="$PARENT/${REPO_NAME}-${role}"
    WT_BRANCH="agent/${role}"   # 每个 agent 有一个本地跟踪分支,起点 = new-branch

    log "─────── 角色: ${role^^} ───────"
    log "  worktree 路径: $WT_PATH"
    log "  跟踪分支:     $WT_BRANCH (起点 $BASE_BRANCH)"

    if [ -d "$WT_PATH" ]; then
        warn "  目录已存在,跳过创建(若需重建:git worktree remove $WT_PATH)"
        continue
    fi

    # 分支存在?
    if git show-ref --verify --quiet "refs/heads/$WT_BRANCH"; then
        log "  分支 $WT_BRANCH 已存在,直接 add"
        git worktree add "$WT_PATH" "$WT_BRANCH"
    else
        log "  创建新分支 $WT_BRANCH 并 add worktree"
        git worktree add -b "$WT_BRANCH" "$WT_PATH" "$BASE_BRANCH"
    fi

    log "  ✅ ${role^^} worktree 就绪"
done

# ---------- 结果 ----------
echo ""
log "所有 worktree:"
git worktree list

echo ""
log "✅ 初始化完成。"
echo ""
echo "📌 下一步:"
echo "  1. 重启 AI agent:  ./scripts/stop-ai-agents.sh && ./scripts/start-ai-agents.sh"
echo "  2. 新启动脚本会让每个角色 cd 到对应的 worktree"
echo "  3. 主仓库 $REPO_ROOT 留给你交互式开发使用"
echo ""
echo "⚠️ 注意事项:"
echo "  - 每个 worktree 有自己的 venv / node_modules(首次需要各自 pip/npm install)"
echo "    可以 ln -s 共享:ln -sf $REPO_ROOT/.venv $PARENT/${REPO_NAME}-dev/.venv"
echo "  - Dev agent 在自己的 worktree 里 git checkout -b feat/STO-X-... 新建分支"
echo "  - worktree 之间自动共享 .git 对象,一边 commit/push 另一边 fetch 立刻可见"
