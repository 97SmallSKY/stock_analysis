#!/usr/bin/env bash
# start-ai-agents.sh — 一键启动 4 个 AI 角色挂机会话(本地 macOS,worktree 隔离版)
#
# 用法:
#   ./scripts/start-ai-agents.sh           # 启动全部 4 个
#   ./scripts/start-ai-agents.sh pm dev    # 只启动指定角色
#
# 工作机制:
#   - 每个角色 cd 到自己的独立 worktree(物理隔离,防 git checkout 互相践踏)
#   - 用 osascript 调 Terminal.app 开 4 个新窗口
#   - 每个窗口启动 claude + 贴入角色开场白 + /loop
#   - caffeinate -i 防止 macOS 睡眠
#
# 前置:首次使用必须先执行一次 ./scripts/setup-agent-worktrees.sh
# 详见 docs/ops/ai-agents-local-loop.md

set -euo pipefail

MAIN_REPO="$(cd "$(dirname "$0")/.." && pwd)"
PARENT="$(dirname "$MAIN_REPO")"
REPO_NAME="$(basename "$MAIN_REPO")"
ROLES=(pm dev test ops)

# 每角色对应的 worktree 路径(与 setup-agent-worktrees.sh 保持一致)
get_role_dir() {
    echo "$PARENT/${REPO_NAME}-$1"
}

# bash 3.2 兼容(macOS 默认),不用 ${var^^}
upper() { echo "$1" | tr '[:lower:]' '[:upper:]'; }

# 解析参数:指定角色或全部
if [ $# -gt 0 ]; then
    ROLES=("$@")
fi

echo "📦 主仓库: $MAIN_REPO"
echo "🎭 将启动的角色: ${ROLES[*]}"
echo ""

# 前置检查:worktree 是否已初始化
MISSING_WT=()
for role in "${ROLES[@]}"; do
    wt_dir="$(get_role_dir "$role")"
    if [ ! -d "$wt_dir" ]; then
        MISSING_WT+=("$role -> $wt_dir")
    fi
done

if [ ${#MISSING_WT[@]} -gt 0 ]; then
    echo "❌ 以下角色的 worktree 尚未初始化:"
    printf '   %s\n' "${MISSING_WT[@]}"
    echo ""
    echo "👉 请先执行:./scripts/setup-agent-worktrees.sh"
    exit 1
fi

# 防止 macOS 睡眠
if ! pgrep -f "caffeinate -i" > /dev/null; then
    echo "☕ 启动 caffeinate 防睡眠..."
    nohup caffeinate -i > /dev/null 2>&1 &
    echo $! > /tmp/ai-agents-caffeinate.pid
fi

# 为每个角色生成开场白 prompt(v3.2:项目锁定 + 榨干队列 + 零打扰)
generate_prompt() {
    local role=$1
    local role_upper
    role_upper="$(upper "$role")"
    # Ops 保留 20 分钟硬周期,其他角色榨干队列
    local cadence_note
    if [ "$role" = "ops" ]; then
        cadence_note="你是 Ops,保留 20 分钟硬周期(监控优先于效率)。每轮最多 1 次部署。"
    else
        cadence_note="你的循环模型:榨干队列再休眠。队列非空就连续做(一条完整走完再挑下一条,不分轮、不限数量),队列空才 ScheduleWakeup(1200)。"
    fi

    cat <<EOF
请先读 @docs/prompts/linear/README.md 和 @docs/prompts/linear/${role}.md 和 @CLAUDE.md

进入 ${role_upper} 角色,严格遵守以下铁律:

🎯 项目锁定(README §3.9)
  project = 5c09b8da-a3fc-45be-b866-5dc94cf01523  (龙虎榜量化交易分析系统)
  - 所有 list_issues / save_issue(新建)必带 project 参数
  - 按 ID 操作前必 get_issue 验证 projectId,不等则拒绝并留评论
  - 本 team(Personal Dev)下有多个 project,STO 编号混编,不靠 ID 识别归属

🤫 零打扰(README §3.10)
  - 禁止使用 AskUserQuestion(挂机模式下会阻塞整个轮询)
  - L4 决策(钱 / 不可逆 / 外部依赖)→ 标题加 🔴 + 打 Urgent + @pm + 评论列清选项和默认推荐 → 跳过此 issue 继续下一条
  - 仲裁僵持按 README §5 默认判决自动执行

⏰ 循环节奏
  ${cadence_note}

🌳 工作区隔离
  你已经在自己的独立 worktree 里(\$(pwd)),不会与其他 agent 冲突。
  不要 cd 到别的目录。

📝 每轮结束打印日志:[\$(date +%H:%M)] ${role_upper} cycle: <动作摘要>

现在开始第一轮。按 ${role}.md §0 的循环模型工作。完成后 /loop 自动循环。
EOF
}

# 启动单个角色会话
start_agent() {
    local role=$1
    local role_upper
    role_upper="$(upper "$role")"
    local wt_dir
    wt_dir="$(get_role_dir "$role")"
    local prompt
    prompt=$(generate_prompt "$role")
    # 把 prompt 暂存到临时文件,避免 AppleScript 转义地狱
    local prompt_file="/tmp/ai-agent-${role}-prompt.txt"
    printf '%s\n' "$prompt" > "$prompt_file"

    echo "🚀 启动 ${role_upper} 角色会话 → $wt_dir"

    osascript <<EOF
tell application "Terminal"
    activate
    set newTab to do script "cd $wt_dir && echo '===== AI Agent: ${role_upper} =====' && echo '工作区: $wt_dir (独立 worktree,不与其他 agent 冲突)' && echo '提示词已写入 $prompt_file' && echo '手动粘贴:cat $prompt_file | pbcopy  然后在 claude 里 Cmd+V' && claude"
    set custom title of newTab to "AI-${role_upper}"
end tell
EOF

    sleep 2  # 等 Terminal 窗口起来
}

# 主循环
for role in "${ROLES[@]}"; do
    if [[ ! " pm dev test ops " =~ " $role " ]]; then
        echo "⚠️  未知角色: $role,跳过"
        continue
    fi
    start_agent "$role"
done

echo ""
echo "✅ 全部 ${#ROLES[@]} 个角色会话已启动"
echo ""
echo "📌 下一步(每个新打开的终端窗口):"
echo "   1. 等 claude 启动完"
echo "   2. cat /tmp/ai-agent-<role>-prompt.txt | pbcopy"
echo "   3. 在 claude 里 Cmd+V 粘贴开场白并回车"
echo "   4. claude 会执行第一轮,然后自动 /loop"
echo ""
echo "🛑 停止全部:./scripts/stop-ai-agents.sh"
