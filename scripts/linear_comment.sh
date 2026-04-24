#!/usr/bin/env bash
# STO-78: 由 systemd OnSuccess/OnFailure hook 调用,往指定 Linear issue 留 comment
#
# 用法:  linear_comment.sh <issue-id> <body>
# 环境:  LINEAR_API_TOKEN  必须在调用方注入(/etc/default/stock_analysis 或 systemd drop-in)
#
# 失败语义:**永不抛错**,退出码恒为 0。hook 失败不能影响主业务(STO-78 AC)

set -u
issue_id="${1:-}"
body="${2:-}"

if [ -z "$issue_id" ] || [ -z "$body" ]; then
  echo "[linear_comment] usage: $0 <issue-id> <body>" >&2
  exit 0
fi

if [ -z "${LINEAR_API_TOKEN:-}" ]; then
  echo "[linear_comment] LINEAR_API_TOKEN 未设置,跳过" >&2
  exit 0
fi

# 转义 body 里的双引号和反斜杠(最小化 JSON 安全,内容由调用方控制)
escaped=$(printf '%s' "$body" | python3 -c 'import sys,json;print(json.dumps(sys.stdin.read()))')

# Linear GraphQL mutation
query=$(cat <<EOF
{"query":"mutation(\$issueId:String!,\$body:String!){commentCreate(input:{issueId:\$issueId,body:\$body}){success}}","variables":{"issueId":"$issue_id","body":$escaped}}
EOF
)

curl -sS --max-time 15 \
  -H "Authorization: $LINEAR_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d "$query" \
  https://api.linear.app/graphql \
  > /tmp/linear_comment_last.json 2>&1 || {
    echo "[linear_comment] curl 失败,忽略" >&2
    exit 0
  }

# 简单成功检测:返回含 \"success\":true
 if grep -q '"success":true' /tmp/linear_comment_last.json 2>/dev/null; then
  echo "[linear_comment] $issue_id ✅"
else
  echo "[linear_comment] $issue_id ⚠️ (response: $(head -c 200 /tmp/linear_comment_last.json))" >&2
fi
exit 0
