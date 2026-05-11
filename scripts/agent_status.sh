#!/usr/bin/env bash
# agent_status.sh — 一键扫 4 个 agent 最近一次 tick 时间戳
#
# 用法:  ./scripts/agent_status.sh
# 环境:  LINEAR_API_TOKEN  必须(/etc/default/stock_analysis 或 shell env)
#
# 扫描规则:
#   近 24h 内,project = 龙虎榜量化交易分析系统 的所有 comment,
#   正则 @(PM|Dev|Ops|Auditor) tick <ISO8601>,取每个角色最新一条

set -u

PROJECT_ID="5c09b8da-a3fc-45be-b866-5dc94cf01523"

if [ -z "${LINEAR_API_TOKEN:-}" ]; then
  echo "❌ LINEAR_API_TOKEN 未设置" >&2
  echo "   export LINEAR_API_TOKEN=lin_api_xxx" >&2
  exit 2
fi

SINCE=$(python3 -c "from datetime import datetime,timedelta,timezone; print((datetime.now(timezone.utc)-timedelta(hours=24)).isoformat())")

# 拉近 24h 本 project 所有 issue 的 comment(GraphQL 分页,简单起见只取前 250 条 issue × 最新 10 条 comment)
read -r -d '' QUERY <<'EOF' || true
query($projectId:String!,$since:DateTimeOrDuration){
  issues(filter:{project:{id:{eq:$projectId}},updatedAt:{gt:$since}},first:250){
    nodes{
      identifier
      comments(first:10,orderBy:createdAt){
        nodes{ body createdAt }
      }
    }
  }
}
EOF

PAYLOAD=$(python3 -c "
import json,sys
q=sys.stdin.read()
print(json.dumps({'query':q,'variables':{'projectId':'$PROJECT_ID','since':'$SINCE'}}))
" <<<"$QUERY")

RESP=$(curl -sS --max-time 30 \
  -H "Authorization: $LINEAR_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d "$PAYLOAD" \
  https://api.linear.app/graphql)

if ! echo "$RESP" | grep -q '"data"'; then
  echo "❌ Linear API 返回异常:" >&2
  echo "$RESP" | head -c 500 >&2
  echo >&2
  exit 1
fi

# Python 解析:找每个角色最新 `@<Role> tick <ISO>`
python3 - "$RESP" <<'PY'
import json,sys,re
from datetime import datetime,timezone

data=json.loads(sys.argv[1])
nodes=data.get('data',{}).get('issues',{}).get('nodes',[])

latest={}  # role -> (iso, issue_identifier)
pat=re.compile(r'@(PM|Dev|Ops|Auditor)\s+tick\s+([0-9T:\-+.Z]+)')

for issue in nodes:
    iid=issue['identifier']
    for c in issue.get('comments',{}).get('nodes',[]):
        body=c.get('body','')
        for m in pat.finditer(body):
            role,iso=m.group(1),m.group(2)
            if role not in latest or iso>latest[role][0]:
                latest[role]=(iso,iid)

now=datetime.now(timezone.utc)
print(f"🕐 {now.strftime('%Y-%m-%d %H:%M:%S %Z')}\n")
print(f"{'Agent':<10} {'Last tick':<26} {'Ago':<12} {'Issue':<10} Status")
print('-'*70)

roles=['PM','Dev','Ops','Auditor']
for role in roles:
    if role not in latest:
        print(f"{role:<10} {'(none in 24h)':<26} {'-':<12} {'-':<10} ⚠️  无 tick")
        continue
    iso,iid=latest[role]
    try:
        t=datetime.fromisoformat(iso.replace('Z','+00:00'))
        mins=int((now-t).total_seconds()/60)
        ago=f"{mins}m ago" if mins<60 else f"{mins//60}h{mins%60}m ago"
        if mins<15: status='✅ 活跃'
        elif mins<60: status='✅ 正常'
        elif mins<120: status='⚠️  慢'
        else: status='🔴 可能停了'
    except Exception:
        ago='?'; status='?'
    print(f"{role:<10} {iso:<26} {ago:<12} {iid:<10} {status}")
PY
