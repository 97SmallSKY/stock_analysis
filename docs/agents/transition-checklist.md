# [auto:stock-analysis] 状态转换权威 Checklist

> 本文件是 **所有 4 个 agent 的唯一转换依据**。任何 state 流转前必须逐条对照本表。如本表与角色 CLAUDE.local.md 冲突，以本表为准。

## Label 约定（6 个，PM 首轮 bootstrap 创建）

| Label | 含义 | 谁打 |
|---|---|---|
| `auto:stock-analysis` | 项目开关。agent 只处理带此 label 的 issue | **人工**(你新建 issue 时打) |
| `type:feature` | 新特性 → Dev 写 `docs/CHANGELOG.md` | PM |
| `type:bug` | bug 修复 → Dev 写 `docs/CHANGELOG.md` | PM |
| `type:chore` | 重构/文档/基建/监控 → Dev 写 `docs/DEVLOG.md` | PM |
| `type:watch` | Watch 巡检类,永久 `In Progress`,不走 T3/T4 流转;**Auditor M8 豁免** | Ops |
| `blocked:need-human` | agent 卡死升级人工 | agent 自动 |

**不引入** `prio:*` / `area:*` / `role:*` / `status:*`(重复 Linear 原生字段)。

> **M8 豁免注脚**(STO-54):`type:watch` 的 issue 按设计永久持有 `In Progress`(非卡死),Auditor 计算 M8(`blocked:need-human` > 24h)时必须排除此类;Watch issue 也不应同时挂 `blocked:need-human`,除非真的遇到技术阻塞。

## 5 个语义 state 槽

PM 首轮 `list_issue_statuses` 后把 Linear 现有 states 映射进来：

| 槽 | 映射示例 | 当前 owner |
|---|---|---|
| Intake | Backlog | 人工 → PM |
| Ready for Dev | Todo | PM → Dev |
| In Progress | In Progress | Dev |
| Ready to Deploy | In Review / 或新建 | Dev → Ops |
| Done | Done | Ops |

## 转换门槛（全部满足才能流转）

### T1 · Intake → Ready for Dev（PM 执行）

- [ ] issue 带 `auto:stock-analysis` label
- [ ] AC 三段式完整：`Given ... / When ... / Then ...`
- [ ] ≥ 1 条验收 checkpoint（如 "curl /api/xxx 返回 200 + 字段 y 存在"）
- [ ] `type:feature` / `type:bug` / `type:chore` 三选一已打
- [ ] **无** `blocked:need-human` label
- [ ] assignee = Dev（即本人 user id，靠 state 区分角色）

### T2 · Ready for Dev → In Progress（Dev 执行）

- [ ] Dev 当前无其他 In Progress issue（**并发 = 1**）
- [ ] 选中条在 Ready for Dev 里 priority 最高、createdAt 最早（并列取最早）
- [ ] Dev 已 `git fetch origin new-branch` + `git checkout -B work/STO-<id> origin/new-branch`
- [ ] 在 issue 下留 comment `@Dev 开工 · branch=work/STO-<id> · HEAD=<sha>`

### T3 · In Progress → Ready to Deploy（Dev 执行）

- [ ] `python3 -m pytest` exit 0（全绿）
- [ ] 覆盖率报告末行 `TOTAL` 百分比 ≥ 75%
- [ ] 若改动 API 边界：按主仓库 CLAUDE.md §11.1–11.3 验证（前端调用方 grep + curl 3 种边界）
- [ ] CHANGELOG.md 或 DEVLOG.md 已更新（按 `type:*` 分流）
- [ ] `docs/PROGRESS.md` 「近期变更日志」章节已追加
- [ ] `git rebase origin/new-branch` 无冲突
- [ ] `git push origin HEAD:new-branch` 成功（fast-forward）
- [ ] issue 下 comment：`@Ops ready · commit=<sha> · 覆盖率=<N>% · 改动:<3 行摘要>`
- [ ] **调 Linear API 改 state 到 In Review**（对应 Ready to Deploy 槽，必做，不能只发 comment）
      `save_issue(id='STO-<id>', state='In Review')` → `get_issue` 读回验证 status == 'In Review'
      缺此步会自锁：下一 tick 扫 In Progress 仍是本 issue，重复处理，永远拉不到 Todo 里新任务（STO-63）

### T3.5 · Ops 部署前 · FF 托底（Ops 执行，2026-04-22 新增）

Ops 部署前必须确保 issue 的 commit 是 `origin/new-branch` 的祖先，否则 `git pull` 带不上。Dev 交付的 feature 分支常因 new-branch 前进而落后，此时由 Ops 自动 rebase 托底：

- [ ] `git fetch origin` + 从 issue 最新 `@Ops ready` comment 拿 `commit=<sha>` 和分支名
- [ ] `git merge-base --is-ancestor <sha> origin/new-branch` 判断
  - 是祖先 → 直接进 T4
  - 非祖先 → 执行下面的 rebase 托底
- [ ] **Rebase 托底流程（非祖先时）**：
  - (a) `git checkout <feature-branch> && git reset --hard origin/<feature-branch>`（对齐远端）
  - (b) `git rebase origin/new-branch`
  - (c) 无冲突 → `git push origin HEAD:new-branch`（FF push 到 new-branch，绕过 feature 分支），记录新 sha 进 T4；在 issue comment `@Dev auto-rebased · old=<sha> new=<new-sha> · 无冲突`
  - (d) 有冲突 → `git rebase --abort`，T6 打回 Dev：comment `@Dev need rebase · 冲突文件: <list>`，本 issue 本轮不进 T4
- [ ] **禁止** `git push -f` 到 `<feature-branch>`（Dev 可能还在上面开新 commit），rebase 结果只 FF 到 new-branch
- [ ] 同一 issue 连续 3 次 rebase 失败（冲突）→ 按 T5 升级 `blocked:need-human`

### T4 · Ready to Deploy → Done（Ops 执行）

- [ ] `ssh jd "cd ~/stock_analysis && git pull && ./scripts/deploy.sh"` exit 0
- [ ] 部署完成后等待 20 秒让服务稳定
- [ ] `journalctl -u <service_name> --since '3 min ago'` 中**无** `error_markers`（见 `docs/ops/agent-self-probe.md`）
- [ ] 日志中**出现** `startup_marker`（见 self-probe.md）
- [ ] `curl -fsS -m 5 <healthcheck_url>` 返回 200，且 body 含 `ok` / `healthy` 之一
- [ ] issue 下 comment：`@PM deploy ok · commit=<sha> · healthcheck=<body 摘要>`

### T5 · 任意 → blocked:need-human（任意 agent 执行）

触发条件（**任一满足即升级**）：

- 同一动作连续失败 3 次（push / pytest / deploy / rebase）
- 需求歧义 / AC 内部冲突 / ADR 未覆盖
- git 状态异常（冲突、未知分支、detached 跑偏）
- Linear 外部操作导致 state 不一致（例如 issue 被人工直接改到 Done，但 new-branch 没对应 commit）

动作：
- [ ] 加 label `blocked:need-human`
- [ ] comment 首行 `BLOCKED: <一句话原因>`，附失败日志 / 冲突文件 / 诊断
- [ ] state **不前移**，保持当前
- [ ] 本轮循环结束，降频到 1800s 等人工介入

### T6 · Done → Intake（Ops 判定部署失败后的回退）

- [ ] Ops 部署失败时：state → **In Progress**（不是 Intake）
- [ ] comment `@Dev deploy FAILED · <deploy_stdout_tail 30 行> · <journalctl error 片段>`
- [ ] **不动 label**（type:* 保留）
- [ ] **不 revert commit**（Dev 下轮看到后决定怎么修）

## 禁止事项（全局）

- ❌ 跨分支操作 `main` ↔ `new-branch`
- ❌ `git push --force` / `--force-with-lease` 到 `new-branch`
- ❌ `git commit --no-verify`
- ❌ 自动 `git revert`
- ❌ 改别的 project 的 issue
- ❌ 动没有 `auto:stock-analysis` label 的 issue
- ❌ 绕过本文件里的 checklist（"我判断应该可以跳过" = 立刻 blocked:need-human）

## 变更

本文件由 **Auditor agent** 每月月初 review 一次，发现过时规则 → 创 `[AUDIT]` issue 给 PM 流转。
