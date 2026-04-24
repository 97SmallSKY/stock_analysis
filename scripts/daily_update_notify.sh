#!/usr/bin/env bash
# STO-78: daily_update 结束后(OnSuccess 或 OnFailure)自动往 STO-38 留巡检 comment
# 被 deploy/systemd/stock_analysis_daily_notify.service 调用
#
# 环境:LINEAR_API_TOKEN 必须可用(由 EnvironmentFile=/etc/default/stock_analysis 注入)

set -u
cd "$(dirname "$0")/.." || exit 0  # hook 失败不抛

STO_38_ID="c3b519c6-1719-4afd-b678-4555a07fbb3e"
LOG_FILE="${LOG_DIR:-logs}/daily_update.log"
STATE="${1:-unknown}"   # success / failure,由 service 的 ExecStart 参数传入

# 从日志末尾抓 daily_update END 那行(含 errors / elapsed)
end_line=$(grep 'daily_update END' "$LOG_FILE" 2>/dev/null | tail -1)
last_tail=$(tail -5 "$LOG_FILE" 2>/dev/null | sed 's/\r//g')

if [ "$STATE" = "failure" ]; then
  icon="⚠️"
else
  icon="✅"
fi

today=$(date '+%Y-%m-%d %H:%M %Z')
body=$(cat <<EOF
$today $icon daily_update $STATE(systemd hook · STO-78)

\`\`\`
$end_line
\`\`\`

<details><summary>last 5 log lines</summary>

\`\`\`
$last_tail
\`\`\`

</details>
EOF
)

exec bash "$(dirname "$0")/linear_comment.sh" "$STO_38_ID" "$body"
