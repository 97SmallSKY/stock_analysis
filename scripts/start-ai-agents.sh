#!/usr/bin/env bash
# start-ai-agents.sh — 一键启动 4 个 AI 角色挂机会话(本地 macOS)
#
# 用法:
#   ./scripts/start-ai-agents.sh           # 启动全部 4 个
#   ./scripts/start-ai-agents.sh pm dev    # 只启动指定角色
#
# 工作机制:
#   - 用 osascript 调 Terminal.app 开 4 个新窗口
#   - 每个窗口启动 claude + 贴入角色开场白 + /loop
#   - caffeinate -i 防止 macOS 睡眠
#
# 详见 docs/ops/ai-agents-local-loop.md

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ROLES=(pm dev test ops)

# 解析参数:指定角色或全部
if [ $# -gt 0 ]; then
    ROLES=("$@")
fi

echo "📦 Repo: $REPO_ROOT"
echo "🎭 将启动的角色: ${ROLES[*]}"
echo ""

# 防止 macOS 睡眠
if ! pgrep -f "caffeinate -i" > /dev/null; then
    echo "☕ 启动 caffeinate 防睡眠..."
    nohup caffeinate -i > /dev/null 2>&1 &
    echo $! > /tmp/ai-agents-caffeinate.pid
fi

# 为每个角色生成开场白 prompt
generate_prompt() {
    local role=$1
    cat <<EOF
请先读 @docs/prompts/linear/README.md 和 @docs/prompts/linear/${role}.md 和 @CLAUDE.md

进入 ${role^^} 角色,按以下规则每 10 分钟轮询一次:

1. 按 ${role}.md 的「主动巡检 / 循环闭环」筛选自己的队列
2. 队列非空时处理第一条(最多做完 1 条 issue 就停下)
3. 队列为空时 ScheduleWakeup(delaySeconds=600),进入下一轮
4. 遇到 L4 决策(钱 / 不可逆 / 外部依赖)加 🔴 + Urgent + 保留 @pm
5. 每轮开始和结束打印一行日志:\`[\$(date +%H:%M)] ${role^^} cycle: <动作摘要>\`

现在开始第一轮。完成后进入 /loop 自动循环。
EOF
}

# 启动单个角色会话
start_agent() {
    local role=$1
    local prompt
    prompt=$(generate_prompt "$role")
    # 把 prompt 暂存到临时文件,避免 AppleScript 转义地狱
    local prompt_file="/tmp/ai-agent-${role}-prompt.txt"
    printf '%s\n' "$prompt" > "$prompt_file"

    echo "🚀 启动 ${role^^} 角色会话..."

    osascript <<EOF
tell application "Terminal"
    activate
    set newTab to do script "cd $REPO_ROOT && echo '===== AI Agent: ${role^^} =====' && echo '提示词已写入 $prompt_file,下面会自动启动 claude 并贴入' && echo '手动粘贴方式:cat $prompt_file | pbcopy,然后在 claude 里 Cmd+V' && claude"
    set custom title of newTab to "AI-${role^^}"
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
