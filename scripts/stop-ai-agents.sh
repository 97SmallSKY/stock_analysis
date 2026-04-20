#!/usr/bin/env bash
# stop-ai-agents.sh — 停止所有 AI 角色挂机会话

set -euo pipefail

echo "🛑 停止 AI 挂机会话..."

# 关闭 caffeinate
if [ -f /tmp/ai-agents-caffeinate.pid ]; then
    pid=$(cat /tmp/ai-agents-caffeinate.pid)
    if kill -0 "$pid" 2>/dev/null; then
        kill "$pid" || true
        echo "☕ caffeinate 已停(pid=$pid)"
    fi
    rm -f /tmp/ai-agents-caffeinate.pid
fi

# 关闭标题以 AI- 开头的 Terminal 窗口(通过 osascript)
osascript <<'EOF'
tell application "Terminal"
    set windowList to every window
    repeat with w in windowList
        try
            set winTitle to custom title of (selected tab of w)
            if winTitle starts with "AI-" then
                close w saving no
            end if
        end try
    end repeat
end tell
EOF

# 清理 prompt 临时文件
rm -f /tmp/ai-agent-*-prompt.txt

echo "✅ 挂机会话已停止"
echo ""
echo "📌 如需确认 claude 进程全部退出:"
echo "   ps -ef | grep -i claude | grep -v grep"
