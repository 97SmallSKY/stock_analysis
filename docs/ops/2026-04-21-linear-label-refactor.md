# Linear 标签体系重构 v2(2026-04-21)

> 执行者:PM agent(worktree `sa-pm`)· 用户决策:全部替换(破坏性)

## 变更摘要

把 Linear `Personal Dev` team 下原有 22 个混合用途 label(角色路由 + 状态 + 变更类型)收敛为 5 个语义清晰的新 label。

### 新 label 体系(5 个)

| Label | 颜色 | 作用 | 谁加 |
|---|---|---|---|
| `auto:stock-analysis` | 🟢 `#27AE60` | 项目开关:agent 只处理带此 label 的 issue | 人工(新建 issue 时) |
| `type:feature` | 🔵 `#2F80ED` | 变更类型 → Dev 写 `CHANGELOG.md` | PM(AC 打磨时定) |
| `type:bug` | 🔴 `#EB5757` | 变更类型 → Dev 写 `CHANGELOG.md` | PM |
| `type:chore` | ⚪ `#95A5A6` | 重构/文档/基建 → Dev 写 `DEVLOG.md` | PM |
| `blocked:need-human` | 🟡 `#F2C94C` | agent 卡住需要人工介入 | agent 自动加 |

### 被移除的 22 个旧 label

- 角色路由:`@pm` `@dev` `@test` `@ops` `@reviewer` `@observer` `@fixer` `@guardian`
- 状态信号:`test-passed` `needs-user-decision` `needs-clarification` `待观察` `Blocked` `Urgent`
- 变更分类(混乱的多套):`Bug` `Feature` `Improvement` `Task` `Docs` `Refactor` `Test`

## 影响范围

### ✅ 已通过 Linear API 完成

- 新建 5 个 label
- **29 条龙虎榜 issue 全部 relabel**
  - `type:bug` × 4(STO-31 / 32 / 33 / 50)
  - `type:feature` × 1(STO-6)
  - `type:chore` × 24(其余)
  - `blocked:need-human` × 4(STO-38 / 39 / 42 / 44,Watch/通告类)
  - 全部额外打上 `auto:stock-analysis`
  - 所有旧 label 从这些 issue 上移除

### ⚠️ 需人工收尾

Linear MCP 不提供 `deleteIssueLabel` 工具。残留 22 个旧 label(本项目下已无 issue 引用)需用户在 Linear Web UI 手动删除:Settings → Labels → 逐个 delete。

> 注意:这些是 team 级 label,若 `Personal Dev` team 下其他 project 仍在用,删除前先确认。

### ⚠️ 文档规约联动(未做,需下次循环处理)

本次只改了 Linear 端数据,**未改仓库内文档**。以下文档仍按旧角色 label 规约编写,下次 PM/Dev/Ops loop 启动前需同步重写:

- `docs/prompts/linear/README.md`(协作总纲 v3.x)
- `docs/prompts/linear/{pm,dev,test,ops}.md`(各角色闭环)
- `docs/prompts/7x24_agent.md`(运维巡检手册)
- `.worktrees/sa-pm/CLAUDE.local.md` 等各 worktree 的 agent bootstrap

原角色路由(`@pm`/`@dev`/`@ops`/`@test` 等 label)需改为 **state + assignee + comment @mention** 驱动。

## 部署红线

**本次无代码改动,不需要 `ssh jd` 部署。** 该文档仅为变更记录,运行时行为不变。

## 时间线

- 2026-04-21 01:30 用户提出方案
- 2026-04-21 01:35 PM agent 确认破坏性替换 + 全量回填
- 2026-04-21 01:40 新建 5 个 label
- 2026-04-21 01:45 完成 29 条 issue 全量 relabel(通过 subagent 并行执行)
- 2026-04-21 01:55 文档 commit + tag `linear-labels-v2`

## Git Tag

- `linear-labels-v2` — 本次改造的可追溯锚点
