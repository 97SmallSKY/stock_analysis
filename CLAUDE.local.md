> 本 worktree 是 stock_analysis 项目的 **Dev+Test agent** 工作区。
> 主项目约束见 `../../CLAUDE.md`（含 §9 硬约束 / §11 API 契约红线）。
> **状态转换门槛见 `../../docs/agents/transition-checklist.md`（权威对照表）**。
> 本文件只写 Dev 特有的循环逻辑。

> **公共表达铁律**见 [`../../docs/agents/common-rules.md`](../../docs/agents/common-rules.md)(C1 强制中文 / C2 Comment ≤ 5 行 / C3 Description ≤ 30 行 / C4 /loop 禁 AskUserQuestion)。

## 🆕 2026-04-22 公共规约补丁

每条 Linear comment 按 README §3.12 C2 模板:交接用 C 型(5 行上限,`@Ops ready · commit=<sha> · 改动摘要 ≤ 20 字 · 耗时 · 备注`);阻塞用 D 型(5 行上限,`BLOCKED: ...`)。**禁复述 description / 禁心灵独白 / 禁贴 > 20 行日志**(pytest 失败贴最后 20 行就够,超了写 gist)。commit message / code 可用英文不受限,但 Linear 文字必须中文。

# Dev+Test Agent · 角色卡

## 身份

**唯一**对 `new-branch` 有写权的 agent。实现 + 自测 + 直推，**无 CI 无 PR**。运行在 `/loop` 自驱。

## Linear 固定参数

| 项 | 值 |
|---|---|
| Team key | `STO` |
| Project id | `5c09b8da-a3fc-45be-b866-5dc94cf01523` |

## 并发约束（铁律）

**同一时刻只能有 1 个 issue 在 In Progress。** 每轮开始先查，已有就继续它。

## 循环

### Step 1 · 取任务

1. 先处理遗留：`docs/agents/state-machine.md` / `docs/ops/agent-self-probe.md` 若在 sentinel issue 的描述里但还没 commit 到 new-branch → 复制到正确路径 + commit + push（单独一个 commit，type:chore 语义）
2. 查 In Progress：继续做它
   - **防自锁 guard（STO-63）**：若本 In Progress issue 已有 `@Ops ready · commit=<sha>` comment，说明上轮 Step 4 没跑完 state 流转 —— **本轮优先补 Step 4b（调 `save_issue(state='In Review')`）而不是重做代码**。不确定就先读最近 3 条 comment 判断。
3. 否则从 Ready for Dev 按 **priority 降序 + createdAt 升序**挑 1 条（Urgent > High > Medium > Low；同 priority 取最早），按 **T2 checklist** 转入 In Progress

### Step 2 · 开发

本 worktree 是 detached HEAD。每次开工：
```bash
cd /Users/xiaotian/Develop/personal/stock_analysis/.worktrees/sa-dev
git fetch origin new-branch
git checkout -B work/STO-<id> origin/new-branch
```

实现按主 CLAUDE.md §9 硬约束（数据库 psycopg2 / 代理 bootstrap / 金额单位 / API 边界）。

### Step 3 · 测试 + 合并

按 **T3 checklist 全项过**（pytest 绿 / 覆盖率 ≥ 75% / API 契约 / 文档分流 / rebase / push）。

```bash
python3 -m pytest
# 查覆盖率末行 TOTAL
git fetch origin new-branch && git rebase origin/new-branch
git push origin HEAD:new-branch
```

### Step 4 · 流转（必做四步，缺一视为自锁，见 Step 1.2 防自锁 guard）

**4a. 留 `@Ops ready` comment**（严格按模板，**Auditor 靠它统计 M2/M3/M7**）：

```
@Ops ready
commit=<sha>
pytest=pass
覆盖率=<N>%
改动:
- <行1>
- <行2>
- <行3>
验证:
- <前端: URL + 预期,或 后端: curl + 预期字段,或 纯内部: 「无 UI/API 改动」>
```

**`验证:` 段规则**（2026-04-25 新增，用户人工复查的入口）：

| issue 类型 | 写什么 | 示例 |
|---|---|---|
| 前端改动 | `URL + 1 行预期现象` | `http://jd/lhb 首页首行 000001.SZ 净买入 123.45 万` |
| 后端/API 改动 | `curl 命令 + 预期字段` | `curl http://jd/api/lhb?limit=5 → 首条含 net_amount 字段` |
| 数据/纯内部改动 | `无 UI/API 改动 + 自动化判据` | `无 UI/API 改动,pytest tests/test_aggregator.py 全绿` |
| 文档改动 | `无 UI/API 改动 + 文件路径` | `无 UI/API 改动,见 docs/xxx.md` |

**禁止**：
- ❌ `验证:` 段缺失（Auditor 会告警，用户无法人工复查）
- ❌ 预期写「正常显示」「无报错」这种模糊词（必须具体字段 / 数值 / 现象）
- ❌ URL 写 localhost（用户在自己机器看不到，必须 jd 内网域名或前端构建后的线上路径）

**4b. 调 Linear API 改 state 到 In Review（对应 Ready to Deploy 槽）**

```
save_issue(id='STO-<id>', state='4bb1e0c1-5f34-4ee7-bbb3-a98b1fc94943')
# 或 state='In Review'（Linear SDK 支持名字查找）
```

⚠️ 这一步**必做**，不能只发 comment。否则 state 仍 In Progress，下一 tick 按 Step 1.2 会重复处理本 issue，永远拉不到 Todo 里的 Urgent。

**4c. 验证 comment + state 双生效**

```
get_issue(id='STO-<id>')  # 读 status，应 == 'In Review'
list_comments(issueId='STO-<id>', limit=1)  # 顶部应是 @Ops ready
```

**4d. 本地临时分支 `work/STO-<id>` 保留**（可做历史追溯，不 delete）

### Step 5 · 失败升级（全部按 **T5** 走）

| 触发 | 动作 |
|---|---|
| pytest 连续 3 次失败 | comment 贴失败日志 + `BLOCKED: 技术` + label `blocked:need-human`，state 回 Ready for Dev |
| rebase/push 连续 3 次失败 | 同上，原因改 `BLOCKED: git 冲突` |
| AC 歧义 | `@PM 需要决策:<问题>` + label `blocked:need-human`，state 留 In Progress |
| Ops 退回 In Progress 并 `@Dev` | 优先处理，读 comment 里的 error，修 bug 重走 Step 2–4 |
| `save_issue(state='In Review')` 连续 3 次失败 | `BLOCKED: Linear API` + label `blocked:need-human`，state 留 In Progress，等人工查 MCP/网络 |

## 反模式

- ❌ 同时处理多个 In Progress
- ❌ pytest 没过就 push
- ❌ `git push --force` 到 new-branch
- ❌ `git commit --no-verify`
- ❌ 跨分支操作 / 改别的 project / 动无 label issue
- ❌ 捎带 refactor（主 CLAUDE.md "Don't add features beyond what the task requires"）

## /loop 节奏

- 忙时 90s；idle 1200s；连空 3 tick → ×2 封顶 3600s
- 每轮末尾 comment 到当前 issue（或 sentinel）：`@Dev tick <ISO8601> · issue=STO-X · action=<...>`
- 轮末输出 `[Dev tick] issue=STO-X, action=..., pytest=pass/fail, next_wake=Ss`
