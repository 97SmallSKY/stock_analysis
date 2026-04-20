#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ai-digest.py — Linear 多角色挂机系统的每日巡检日报

用法:
    ./scripts/ai-digest.py                 # 产出昨日+今日的 digest
    ./scripts/ai-digest.py --dry-run        # 只打印,不写文件不建 issue
    ./scripts/ai-digest.py --hours 48       # 只扫最近 48 小时

需要 .env 里:
    LINEAR_API_KEY=lin_api_xxxxx
    LINEAR_TEAM_ID=a6a9d55b-9937-...
    LINEAR_PROJECT_ID=5c09b8da-a3fc-...
    AI_DIGEST_AUTO_ESCALATE=true
    AI_DIGEST_OUTPUT_DIR=docs/ops/digest

详见 docs/ops/ai-digest-manual.md
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

try:
    import requests
except ImportError:
    sys.exit("请先 pip install requests")

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    # 没装 python-dotenv 也能跑(需手动 export)
    pass


# ============================================================
# 配置
# ============================================================
REPO_ROOT = Path(__file__).resolve().parent.parent
LINEAR_API_URL = "https://api.linear.app/graphql"
LINEAR_API_KEY = os.environ.get("LINEAR_API_KEY", "").strip()
TEAM_ID = os.environ.get("LINEAR_TEAM_ID", "a6a9d55b-9937-4e65-83bd-ac09d20a18e4").strip()
PROJECT_ID = os.environ.get("LINEAR_PROJECT_ID", "5c09b8da-a3fc-45be-b866-5dc94cf01523").strip()
AUTO_ESCALATE = os.environ.get("AI_DIGEST_AUTO_ESCALATE", "true").lower() == "true"
OUTPUT_DIR = REPO_ROOT / os.environ.get("AI_DIGEST_OUTPUT_DIR", "docs/ops/digest")

# 角色标签名 → 视图用
ROLE_LABELS = {"@pm", "@dev", "@test", "@ops"}
TEST_PASSED_LABEL = "test-passed"
URGENT_LABEL = "Urgent"
BLOCKED_LABEL = "Blocked"

# 死锁阈值(小时)—— 一个 issue 挂多个角色标签超过此时长就告警
DEADLOCK_HOURS_THRESHOLD = 24
# 孤儿 issue 阈值(小时)
ORPHAN_HOURS_THRESHOLD = 24


# ============================================================
# 数据模型
# ============================================================
@dataclass
class Anomaly:
    """一条异常记录"""
    category: str   # 'deadlock' / 'rule-violation' / 'cleanliness' / 'health' / 'quality'
    severity: str   # 'info' / 'warn' / 'critical'
    title: str
    detail: str
    issue_id: Optional[str] = None  # 相关 Linear issue ID(如 STO-6)
    evidence: dict = field(default_factory=dict)

    def should_escalate(self) -> bool:
        return self.severity == "critical"


@dataclass
class DigestReport:
    date: str
    hours_scanned: int
    total_issues: int
    state_counts: dict
    role_counts: dict
    anomalies: list[Anomaly]

    def critical_count(self) -> int:
        return sum(1 for a in self.anomalies if a.severity == "critical")

    def warn_count(self) -> int:
        return sum(1 for a in self.anomalies if a.severity == "warn")


# ============================================================
# Linear GraphQL client
# ============================================================
def linear_query(query: str, variables: dict | None = None) -> dict:
    """调 Linear GraphQL API"""
    if not LINEAR_API_KEY:
        sys.exit("❌ 环境变量 LINEAR_API_KEY 未设置,请编辑 .env")

    headers = {
        "Authorization": LINEAR_API_KEY,
        "Content-Type": "application/json",
    }
    payload = {"query": query, "variables": variables or {}}
    resp = requests.post(LINEAR_API_URL, json=payload, headers=headers, timeout=30)
    resp.raise_for_status()
    body = resp.json()
    if "errors" in body:
        raise RuntimeError(f"Linear API error: {body['errors']}")
    return body["data"]


FETCH_ISSUES_QUERY = """
query DigestIssues($projectId: ID!, $after: String) {
  issues(
    filter: { project: { id: { eq: $projectId } } }
    first: 100
    after: $after
  ) {
    pageInfo { hasNextPage endCursor }
    nodes {
      id
      identifier
      title
      state { name type }
      priority
      createdAt
      updatedAt
      completedAt
      labels { nodes { id name } }
      comments(last: 5) { nodes { body createdAt } }
    }
  }
}
"""


def fetch_all_issues() -> list[dict]:
    """拉取项目下全部 issue(含 labels / 最近 5 条 comment)"""
    issues: list[dict] = []
    after = None
    while True:
        data = linear_query(FETCH_ISSUES_QUERY, {"projectId": PROJECT_ID, "after": after})
        page = data["issues"]
        issues.extend(page["nodes"])
        if not page["pageInfo"]["hasNextPage"]:
            break
        after = page["pageInfo"]["endCursor"]
    return issues


CREATE_ISSUE_MUTATION = """
mutation CreateDigestAlert($title: String!, $description: String!, $teamId: String!, $projectId: String!, $labelIds: [String!]!, $priority: Int!) {
  issueCreate(input: {
    title: $title,
    description: $description,
    teamId: $teamId,
    projectId: $projectId,
    labelIds: $labelIds,
    priority: $priority
  }) {
    success
    issue { id identifier url }
  }
}
"""


def fetch_label_map() -> dict[str, str]:
    """返回 {name: id} 用于建 issue 时挂标签"""
    query = """
    query($teamId: String!) {
      team(id: $teamId) { labels(first: 50) { nodes { id name } } }
    }
    """
    data = linear_query(query, {"teamId": TEAM_ID})
    return {n["name"]: n["id"] for n in data["team"]["labels"]["nodes"]}


def create_escalation_issue(anomaly: Anomaly, label_map: dict[str, str]) -> Optional[str]:
    """为 critical 异常建 🔴 issue"""
    title = f"🔴 [AI-Digest] {anomaly.title}"
    description = f"""## 🤖 自动发现 · AI Digest 告警

> 由 `scripts/ai-digest.py` 在 {datetime.now().isoformat(timespec='seconds')} 自动建立。

## 类别

`{anomaly.category}` · severity=`{anomaly.severity}`

## 详情

{anomaly.detail}

## 证据

```json
{json.dumps(anomaly.evidence, indent=2, ensure_ascii=False)}
```

## 相关 issue

{f'- {anomaly.issue_id}' if anomaly.issue_id else '(无)'}

## 下一步

@pm 请判断这是真异常还是误报:
- 真异常 → 按 L4 决策(可能需要改规约 / 撤销 Dev 操作 / 补流程)
- 误报 → 移除 🔴 + `Urgent`,评论说明原因,用于改进 digest 脚本
"""
    label_ids = [
        label_map.get("Bug", ""),
        label_map.get("Urgent", ""),
        label_map.get("@pm", ""),
    ]
    label_ids = [x for x in label_ids if x]

    data = linear_query(CREATE_ISSUE_MUTATION, {
        "title": title,
        "description": description,
        "teamId": TEAM_ID,
        "projectId": PROJECT_ID,
        "labelIds": label_ids,
        "priority": 1,  # Urgent
    })
    if data["issueCreate"]["success"]:
        return data["issueCreate"]["issue"]["identifier"]
    return None


# ============================================================
# 异常检测逻辑(5 大类)
# ============================================================
def parse_iso(dt_str: str) -> datetime:
    """Linear 返回的是 2026-04-20T08:21:23.552Z 形式"""
    return datetime.fromisoformat(dt_str.replace("Z", "+00:00"))


def hours_ago(dt_str: str) -> float:
    """距现在多少小时"""
    now = datetime.now(timezone.utc)
    return (now - parse_iso(dt_str)).total_seconds() / 3600


def issue_label_names(issue: dict) -> list[str]:
    return [l["name"] for l in issue["labels"]["nodes"]]


def issue_role_labels(issue: dict) -> list[str]:
    return [l for l in issue_label_names(issue) if l in ROLE_LABELS]


# ---- 1. 死锁检测 ----
def detect_deadlocks(issues: list[dict]) -> list[Anomaly]:
    out = []
    for i in issues:
        state_name = i["state"]["name"]
        if state_name in {"Done", "Canceled", "Duplicate"}:
            continue
        roles = issue_role_labels(i)
        if len(roles) < 2:
            continue
        hrs = hours_ago(i["updatedAt"])
        if hrs < DEADLOCK_HOURS_THRESHOLD:
            continue
        out.append(Anomaly(
            category="deadlock",
            severity="critical" if hrs >= 48 else "warn",
            title=f"{i['identifier']} 挂多角色标签 {hrs:.0f}h 未动",
            detail=(f"Issue **{i['identifier']}** 挂 `{', '.join(roles)}` 已 {hrs:.1f} 小时未更新。"
                    "疑似双方互相等待(死锁)。"),
            issue_id=i["identifier"],
            evidence={"labels": roles, "hours_idle": round(hrs, 1), "state": state_name},
        ))
    return out


# ---- 2. 规则违反 + 越权 ----
def detect_rule_violations(issues: list[dict]) -> list[Anomaly]:
    out = []
    for i in issues:
        labels = issue_label_names(i)
        state = i["state"]["name"]
        ident = i["identifier"]

        # 违规 1:Done 但没 test-passed(跳过 Test 签字)
        if state == "Done" and TEST_PASSED_LABEL not in labels:
            # 例外:Docs 类、Watch 类、规约通告本身可能无需 test-passed
            is_exception = any(l in labels for l in ["Docs", "Blocked"]) or i["title"].startswith(("[Watch]", "📢", "🔴 [AI-Digest]"))
            if not is_exception:
                out.append(Anomaly(
                    category="rule-violation",
                    severity="critical",
                    title=f"{ident} 已转 Done 但无 test-passed 标签(越权)",
                    detail=f"按 v3 规约,只有 Ops 能转 Done 且前置必须有 test-passed。该 issue 违反此规则。",
                    issue_id=ident,
                    evidence={"state": state, "labels": labels},
                ))

        # 违规 2:Done 还挂 @role 标签(部署指纹清洁度)
        if state == "Done":
            leftover = [l for l in labels if l in ROLE_LABELS]
            if leftover:
                out.append(Anomaly(
                    category="cleanliness",
                    severity="warn",
                    title=f"{ident} 已 Done 但还挂 {', '.join(leftover)}",
                    detail="按 ops.md §7,转 Done 时必须清空所有角色标签。",
                    issue_id=ident,
                    evidence={"leftover_role_labels": leftover},
                ))

        # 违规 3:有 test-passed 但评论里没有 pytest 数字(低质量 Test 签字)
        if TEST_PASSED_LABEL in labels and state in {"In Review", "Done"}:
            all_text = " ".join(c["body"] for c in i["comments"]["nodes"])
            has_pytest = bool(re.search(r"pytest.*passed|\bpassed\b.*\bfailed\b|\d+\s*passed", all_text, re.I))
            has_coverage = bool(re.search(r"coverage.*\d+\.?\d*%|\d+\.?\d*%.*cov", all_text, re.I))
            if not (has_pytest or has_coverage):
                out.append(Anomaly(
                    category="quality",
                    severity="warn",
                    title=f"{ident} 打了 test-passed 但评论无 pytest / 覆盖率数字",
                    detail="test.md §2 Step 4 要求签字评论必须含数字证据(pytest passed / coverage %)。",
                    issue_id=ident,
                    evidence={"labels": labels, "state": state},
                ))
    return out


# ---- 3. 标签清洁度 + 孤儿 issue ----
def detect_cleanliness(issues: list[dict]) -> list[Anomaly]:
    out = []
    for i in issues:
        labels = issue_label_names(i)
        state = i["state"]["name"]
        ident = i["identifier"]

        # 孤儿 issue:Backlog + 没有任何角色标签 + 超过 24h
        if state == "Backlog" and not issue_role_labels(i):
            hrs = hours_ago(i["createdAt"])
            if hrs >= ORPHAN_HOURS_THRESHOLD:
                out.append(Anomaly(
                    category="cleanliness",
                    severity="warn",
                    title=f"{ident} 孤儿 issue 超过 {ORPHAN_HOURS_THRESHOLD}h 无角色标签",
                    detail="按 pm.md §2.1,PM 应在巡检中捕获孤儿 issue 并分流。",
                    issue_id=ident,
                    evidence={"created_hours_ago": round(hrs, 1), "labels": labels},
                ))

        # Watch issue 被错误转 Done
        if state == "Done" and i["title"].startswith("[Watch]"):
            out.append(Anomaly(
                category="rule-violation",
                severity="critical",
                title=f"{ident} Watch issue 被错误转 Done(应常驻 In Progress)",
                detail="ops.md §5 规定 Watch issue 永远 In Progress 不转 Done。",
                issue_id=ident,
                evidence={"state": state, "title": i["title"]},
            ))
    return out


# ---- 4. 质量失控 ----
def detect_quality_issues(issues: list[dict]) -> list[Anomaly]:
    out = []

    # 统计最近 7 天的 Bug 数量
    now = datetime.now(timezone.utc)
    seven_days_ago = now - timedelta(days=7)
    recent_bugs = [
        i for i in issues
        if "Bug" in issue_label_names(i) and parse_iso(i["createdAt"]) > seven_days_ago
    ]
    if len(recent_bugs) >= 8:
        out.append(Anomaly(
            category="quality",
            severity="warn",
            title=f"Bug 数量激增:最近 7 天新增 {len(recent_bugs)} 条",
            detail="Bug 增速异常,可能质量失控。建议 PM 聚合分析 + 建 Improvement 父 issue 集中修。",
            evidence={"recent_bugs": [i["identifier"] for i in recent_bugs]},
        ))

    # 同一模块反复出 bug(按标题关键词粗略 cluster)
    module_bugs = defaultdict(list)
    for i in recent_bugs:
        title = i["title"]
        # 粗略提取:前两个中文/英文词
        words = re.findall(r"[a-zA-Z_]+|[\u4e00-\u9fff]+", title)[:2]
        if words:
            key = "_".join(words).lower()
            module_bugs[key].append(i["identifier"])

    for module, bug_ids in module_bugs.items():
        if len(bug_ids) >= 3:
            out.append(Anomaly(
                category="quality",
                severity="warn",
                title=f"模块 `{module}` 最近 7 天出 {len(bug_ids)} 个 bug",
                detail="同一模块反复出 bug,建议 Refactor 而不是继续 patch。",
                evidence={"module_key": module, "bug_ids": bug_ids},
            ))

    return out


# ---- 5. 系统健康(轻量版,不连真实进程)----
def detect_health_issues(issues: list[dict]) -> list[Anomaly]:
    out = []

    # 至少应有 2 个 Watch issue(STO-38 daily / STO-44 schema drift)In Progress
    watch_issues = [i for i in issues
                    if i["title"].startswith("[Watch]") and i["state"]["name"] == "In Progress"]
    if len(watch_issues) < 2:
        out.append(Anomaly(
            category="health",
            severity="warn",
            title=f"Watch issue 数量偏少:当前 {len(watch_issues)} 个(预期 ≥ 2)",
            detail="可能有 Watch 被误转 Done 或未建。检查 STO-38 / STO-44。",
            evidence={"current_watch_count": len(watch_issues)},
        ))

    # 长时间无任何活动(整个 project 超过 72h 无 issue 更新)
    if issues:
        latest = max(parse_iso(i["updatedAt"]) for i in issues)
        idle_hrs = (datetime.now(timezone.utc) - latest).total_seconds() / 3600
        if idle_hrs > 72:
            out.append(Anomaly(
                category="health",
                severity="warn",
                title=f"项目 {idle_hrs:.0f}h 无任何 issue 活动",
                detail="4 角色挂机可能全部停止,检查 AI session 是否存活。",
                evidence={"last_update_hours_ago": round(idle_hrs, 1)},
            ))

    return out


# ============================================================
# 汇总 + 生成日报
# ============================================================
def build_report(issues: list[dict], hours: int) -> DigestReport:
    anomalies: list[Anomaly] = []
    anomalies += detect_deadlocks(issues)
    anomalies += detect_rule_violations(issues)
    anomalies += detect_cleanliness(issues)
    anomalies += detect_quality_issues(issues)
    anomalies += detect_health_issues(issues)

    state_counts = Counter(i["state"]["name"] for i in issues)
    role_counts = Counter()
    for i in issues:
        for r in issue_role_labels(i):
            role_counts[r] += 1

    return DigestReport(
        date=datetime.now().strftime("%Y-%m-%d"),
        hours_scanned=hours,
        total_issues=len(issues),
        state_counts=dict(state_counts),
        role_counts=dict(role_counts),
        anomalies=anomalies,
    )


def render_markdown(report: DigestReport) -> str:
    lines = []
    lines.append(f"# AI Digest · {report.date}")
    lines.append("")
    lines.append(f"> 扫描范围:最近 {report.hours_scanned}h · 生成时间:{datetime.now().isoformat(timespec='seconds')}")
    lines.append("")

    # 概览
    lines.append("## 📊 概览")
    lines.append("")
    lines.append(f"- 总 issue 数:{report.total_issues}")
    lines.append(f"- 严重异常(critical):{report.critical_count()}")
    lines.append(f"- 警告(warn):{report.warn_count()}")
    lines.append("")

    # 状态分布
    lines.append("### 状态分布")
    lines.append("")
    for state, n in sorted(report.state_counts.items(), key=lambda x: -x[1]):
        lines.append(f"- `{state}`:{n}")
    lines.append("")

    # 角色分布
    lines.append("### 角色队列")
    lines.append("")
    for role in ["@pm", "@dev", "@test", "@ops"]:
        lines.append(f"- `{role}`:{report.role_counts.get(role, 0)}")
    lines.append("")

    # 异常详情
    if not report.anomalies:
        lines.append("## ✅ 无异常")
        lines.append("")
        lines.append("系统运行正常。")
    else:
        lines.append("## 🚨 异常详情")
        lines.append("")
        by_sev = defaultdict(list)
        for a in report.anomalies:
            by_sev[a.severity].append(a)
        for sev in ["critical", "warn", "info"]:
            if not by_sev[sev]:
                continue
            lines.append(f"### {sev.upper()}({len(by_sev[sev])} 条)")
            lines.append("")
            for a in by_sev[sev]:
                emoji = "🔴" if sev == "critical" else "🟡" if sev == "warn" else "ℹ️"
                lines.append(f"#### {emoji} [{a.category}] {a.title}")
                lines.append("")
                lines.append(a.detail)
                if a.issue_id:
                    lines.append(f"\n**相关 issue**:{a.issue_id}")
                if a.evidence:
                    lines.append("")
                    lines.append("```json")
                    lines.append(json.dumps(a.evidence, indent=2, ensure_ascii=False))
                    lines.append("```")
                lines.append("")

    return "\n".join(lines)


# ============================================================
# 入口
# ============================================================
def main():
    ap = argparse.ArgumentParser(description="AI Digest — Linear 多角色挂机系统日报")
    ap.add_argument("--dry-run", action="store_true", help="只打印,不写文件不建 issue")
    ap.add_argument("--hours", type=int, default=24, help="扫描最近多少小时(默认 24)")
    ap.add_argument("--no-escalate", action="store_true", help="发现 critical 也不建 🔴 issue")
    args = ap.parse_args()

    print(f"🔍 AI Digest 开始 · 扫描最近 {args.hours}h")

    # 1. 拉 issue
    issues = fetch_all_issues()
    print(f"   拉取 {len(issues)} 条 issue")

    # 2. 分析
    report = build_report(issues, args.hours)
    md = render_markdown(report)

    # 3. 打印
    print()
    print("─" * 60)
    print(md)
    print("─" * 60)

    if args.dry_run:
        print("\n✅ --dry-run 完成,未写文件")
        return

    # 4. 写日报文件
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_file = OUTPUT_DIR / f"{report.date}.md"
    out_file.write_text(md, encoding="utf-8")
    print(f"\n📄 日报已写入:{out_file.relative_to(REPO_ROOT)}")

    # 5. 自动升级 critical 异常到 🔴 issue
    if AUTO_ESCALATE and not args.no_escalate:
        criticals = [a for a in report.anomalies if a.should_escalate()]
        if criticals:
            print(f"\n🔴 发现 {len(criticals)} 条 critical 异常,自动建 issue...")
            label_map = fetch_label_map()
            for a in criticals:
                try:
                    new_id = create_escalation_issue(a, label_map)
                    print(f"   - 已建 {new_id}:{a.title}")
                except Exception as e:
                    print(f"   - ❌ 建 issue 失败({a.title}):{e}")
        else:
            print("\n✅ 无 critical 异常,无需升级")

    print("\n✨ AI Digest 完成")


if __name__ == "__main__":
    main()
