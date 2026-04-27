# [auto:stock-analysis] 状态转换权威 Checklist

> 本文件是 **所有 4 个 agent 的唯一转换依据**。任何 state 流转前必须逐条对照本表。如本表与角色 CLAUDE.local.md 冲突，以本表为准。

## Label 约定（10 个，PM 首轮 bootstrap 创建）

### 核心 6 个

| Label | 含义 | 谁打 |
|---|---|---|
| `auto:stock-analysis` | 项目开关，agent 只处理带此 label 的 issue | **人工**（你新建 issue 时打）|
| `type:feature` | 新特性 → Dev 写 `docs/CHANGELOG.md` | PM |
| `type:bug` | bug 修复 → Dev 写 `docs/CHANGELOG.md` | PM |
| `type:chore` | 重构/文档/基建/监控 → Dev 写 `docs/DEVLOG.md` | PM |
| `type:watch` | Watch 巡检类，永久 `In Progress`，不走 T3/T4 流转；**Auditor M1/M8 豁免** | Ops |
| `blocked:need-human` | agent 卡死升级人工（红色紧急，火灾）| agent 自动 |

### 扩展 4 个（2026-04-25 新增）

| Label | 含义 | 谁打 |
|---|---|---|
| `needs:human-decision` | 正常流程等用户拍板的非紧急决策（粉色）| PM（6 场景）|
| `domain:lhb` | 涉及龙虎榜业务域，便于按域切片统计 | PM |
| `verified:human` | 人工已验证通过，与自动化 verify 区分 | **人工** |
| `type:data-bug` | 数据质量 bug，默认路由 sa-ops | PM |

**不引入** `prio:*` / `area:*` / `role:*` / `status:*`。

## 6 个语义 state 槽（2026-04-25 新增 Ready to Verify）

PM 首轮 `list_issue_statuses` 后把 Linear 现有 states 映射进来：

| 槽 | 映射示例 | 当前 owner |
|---|---|---|
| Intake | Backlog | 人工 → PM |
| Ready for Dev | Todo | PM → Dev |
| In Progress | In Progress | Dev |
| **Ready to Verify** | **（新建或复用已有 state）** | **Dev → Ops** |
| Ready to Deploy | In Review | Ops（验证通过后）→ Ops（部署）|
| Done | Done | Ops |

> **Ready to Verify** 若 Linear 无对应 state，PM bootstrap 时创建，命名建议 `In Verification`。

## 转换门槛（全部满足才能流转）

### T1 · Intake → Ready for Dev（PM 执行）

- [ ] issue 带 `auto:stock-analysis` label
- [ ] AC 三段式完整：`Given ... / When ... / Then ...`
- [ ] ≥ 1 条验收 checkpoint（如 `curl /api/xxx 返回 200 + 字段 y 存在`）
- [ ] `type:feature` / `type:bug` / `type:chore` 三选一已打
- [ ] **无** `blocked:need-human` label
- [ ] assignee = Dev user id
- [ ] PM 已在 comment 写 `/plan` 子任务拆解（≤ 5 条，C 型 comment）

### T2 · Ready for Dev → In Progress（Dev 执行）

- [ ] Dev 当前无其他 In Progress issue（**并发 = 1**）
- [ ] 选中条在 Ready for Dev 里 priority 最高、createdAt 最早
- [ ] Dev 已 `git fetch origin new-branch` + `git checkout -B work/STO-<id> origin/new-branch`
- [ ] 在 issue 下留 comment `@Dev 开工 · branch=work/STO-<id> · HEAD=<sha>`

### T3 · In Progress → Ready to Verify（Dev 执行）

- [ ] `python3 -m pytest` exit 0（全绿）
- [ ] 覆盖率报告末行 `TOTAL` 百分比 ≥ 75%
- [ ] 若改动 API 边界：按主仓库 CLAUDE.md §11.1–11.3 验证
- [ ] CHANGELOG.md 或 DEVLOG.md 已更新（按 `type:*` 分流）
- [ ] `docs/PROGRESS.md`「近期变更日志」章节已追加
- [ ] `git rebase origin/new-branch` 无冲突
- [ ] `git push origin HEAD:new-branch` 成功（fast-forward）
- [ ] issue 下留 `@Ops ready` comment，**必须含 `验证:` 段**（URL 必须用 jd 地址，不得写 localhost）
- [ ] **调 Linear API 改 state 到 Ready to Verify**（不是 In Review）
      `save_issue(id='STO-<id>', state='<Ready to Verify 的 Linear state 名>')` → 读回验证

### T3.5 · Ops 部署前 · FF 托底（Ops 执行，在 T3.6 之后）

Ops 部署前确保 issue 的 commit 是 `origin/new-branch` 的祖先：

- [ ] `git fetch origin` + 从 issue 最新 `@Ops ready` comment 拿 `commit=<sha>`
- [ ] `git merge-base --is-ancestor <sha> origin/new-branch` 判断
  - 是祖先 → 直接进 T4
  - 非祖先 → 执行 rebase 托底：
    - (a) `git checkout <feature-branch> && git reset --hard origin/<feature-branch>`
    - (b) `git rebase origin/new-branch`
    - (c) 无冲突 → `git push origin HEAD:new-branch`，记录新 sha，comment `@Dev auto-rebased · old=<sha> new=<new-sha>`
    - (d) 有冲突 → `git rebase --abort`，T6 打回 Dev
- [ ] **禁止** `git push -f` 到 `<feature-branch>`
- [ ] 连续 3 次 rebase 失败 → T5：`blocked:need-human`

### T3.6 · Ready to Verify → Ready to Deploy（Ops 执行，UI 验证）

**这是 T3 到 T4 之间新增的必经门槛。**

- [ ] Ops 扫到 state=Ready to Verify 的 issue
- [ ] 读 Dev 的 `@Ops ready` comment 里的 `验证:` 段
- [ ] 调用 sub-agent `stock-ops-ui-verifier`（Playwright MCP）执行验证
- [ ] **PASS** → `save_issue(state='In Review')` 移 Ready to Deploy，comment 贴验证报告
- [ ] **SKIP**（含 `无 UI/API 改动`）→ `save_issue(state='In Review')` 直接放行，comment 注明跳过原因
- [ ] **FAIL** → `save_issue(state='In Progress')` 移回 In Progress，comment `@Dev UI验证失败 · <失败断言>，修复后重移 Ready to Verify`，**不进入 T4**
- [ ] **ERROR**（MCP 不可用 / 服务不可达）→ 打 `blocked:need-human`，人工介入

> 跳过 T3.6 直接移 Ready to Deploy = **违规**，Auditor M10 会告警。

### T4 · Ready to Deploy → Done（Ops 执行）

- [ ] `ssh jd "cd ~/stock_analysis && git pull && ./scripts/deploy.sh"` exit 0
- [ ] 部署完成后等待 20 秒让服务稳定
- [ ] `journalctl -u <service_name> --since '3 min ago'` 中**无** `error_markers`（见 agent-self-probe.md）
- [ ] 日志中**出现** `startup_marker`
- [ ] `curl -fsS -m 5 <healthcheck_url>` 返回 200，body 含 `ok` / `healthy`
- [ ] `scripts/smoke_http.sh` exit 0（HTTP 层 smoke：SPA index.html + JS bundle 探活）
- [ ] issue 下 comment：`@PM deploy ok · commit=<sha> · healthcheck=<body 摘要>`
- [ ] `save_issue(state='Done')`

### T5 · 任意 → blocked:need-human（任意 agent 执行）

触发条件（**任一满足即升级**）：

- 同一动作连续失败 3 次（push / pytest / deploy / rebase / UI 验证）
- 需求歧义 / AC 内部冲突 / ADR 未覆盖
- git 状态异常（冲突、未知分支、detached 跑偏）
- Linear 外部操作导致 state 不一致
- Playwright MCP 连续不可用

动作：
- [ ] 加 label `blocked:need-human`
- [ ] comment 首行 `BLOCKED: <一句话原因>`，附失败日志 / 冲突文件 / 诊断
- [ ] state **不前移**，保持当前
- [ ] 本轮循环结束，降频到 1800s 等人工介入

### T6 · Ready to Deploy → In Progress（Ops 部署失败后的回退）

- [ ] Ops 部署失败时：state → **In Progress**（不是 Intake，不是 Ready to Verify）
- [ ] comment `@Dev deploy FAILED · <deploy_stdout_tail 30 行> · <journalctl error 片段>`
- [ ] **不动 label**（type:* 保留）
- [ ] **不 revert commit**（Dev 下轮看到后决定怎么修）
- [ ] Dev 修复后重走 T3（pytest → push → Ready to Verify → T3.6 UI 验证 → Ready to Deploy）

## 完整流转图

```
人工建 issue（打 auto:stock-analysis）
         ↓
      Intake（Backlog）
         ↓ T1：PM /spec + /plan + 补 AC
   Ready for Dev（Todo）
         ↓ T2：Dev 开工
      In Progress
         ↓ T3：Dev pytest 全绿 + push + @Ops ready（含验证: 段）
   Ready to Verify          ← 2026-04-25 新增
         ↓ T3.6：Ops Playwright MCP 验证
         ├── PASS/SKIP → Ready to Deploy（In Review）
         └── FAIL → 回 In Progress（Dev 修复）
   Ready to Deploy（In Review）
         ↓ T3.5：Ops FF 托底（如需）
         ↓ T4：Ops 部署 + smoke
        Done
```

## 禁止事项（全局）

- ❌ 跨分支操作 `main` ↔ `new-branch`
- ❌ `git push --force` / `--force-with-lease` 到 `new-branch`
- ❌ `git commit --no-verify`
- ❌ 自动 `git revert`
- ❌ 改别的 project 的 issue
- ❌ 动没有 `auto:stock-analysis` label 的 issue
- ❌ 绕过本文件里的 checklist
- ❌ Dev 在 T3 把 state 直接移到 Ready to Deploy（必须先经 Ready to Verify）
- ❌ Ops 跳过 T3.6 直接把 Ready to Verify 移到 Done

## 变更

| 日期 | 改动 | 触发 |
|---|---|---|
| 2026-04-21 | T1-T5 初版 | 项目启动 |
| 2026-04-22 | T3.5 FF 托底 | Dev/Ops git 冲突问题 |
| 2026-04-25 | 新增 T3.6 / Ready to Verify 槽 / 扩展 4 个 label / M10 | sa-verify 并入 sa-ops，Playwright MCP 接管 UI 验证 |

本文件由 **Auditor agent** 每月月初 review 一次，发现过时规则 → 创 `[AUDIT]` issue 给 PM 流转。
