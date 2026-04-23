# Linear 协作规约 · 角色路由总纲

> **本目录是 AI 协作的第一优先级规则**。任何接入本工作区的 AI(Claude / Cursor / Copilot / 其他)在开始工作前**必读本文**,然后按身份跳到对应角色文档。
>
> 与 [7x24_agent.md](../7x24_agent.md) 互补:
> - `7x24_agent.md` = **时间驱动**的运维巡检循环(每 20 分钟看日志)
> - 本目录 = **issue 驱动**的角色闭环协作(有明确任务时怎么办)

---

## 0. 工作区速查表(所有角色共享)

| 项 | 值 |
|---|---|
| **Workspace** | `xiaotian-dev` → https://linear.app/xiaotian-dev |
| **Team** | `Personal Dev`(前缀 `STO-`) |
| **当前项目** | 龙虎榜量化交易分析系统(ID `5c09b8da-a3fc-45be-b866-5dc94cf01523`) |
| **GitHub** | https://github.com/97SmallSKY/stock_analysis |
| **主开发分支** | `new-branch`(**重写版**,当前唯一在演进的) |
| **历史分支** | `main`(旧版代码,**与 new-branch 完全不同,不再合并**) |
| **部署服务器** | jd (117.72.184.8),仅 `new-branch` 被部署 |

⚠️ **分支铁律**:`main` 与 `new-branch` 是**两套独立代码**,禁止跨分支 merge / rebase / cherry-pick。AI 默认工作在 `new-branch`。

⚠️ **多项目提醒**:`Personal Dev` team 下**同时存在多个 project**(当前 2 个:龙虎榜量化交易分析系统 / wqbridge),它们**共用同一个 STO 编号池**,仅靠 issue ID 无法识别归属。本仓库的工作**严格限定在 `龙虎榜量化交易分析系统` 项目**(ID 见 L17 速查表),详见 §3.9。

---

## 1. 四角色与文档路由

> 每个角色文档都是**自包含**的(含 DoD / 标签 / 模板 / ID 速查),读完一份就能独立工作。

| 角色 | 文档 | 核心职责 | 典型触发 |
|---|---|---|---|
| 📋 **产品经理(PM)** | [pm.md](./pm.md) | 把模糊需求转化成规范 issue | 用户自然语言描述需求 |
| 🛠️ **开发(Dev)** + 代码审查 | [dev.md](./dev.md) | 读 issue → 写代码 → 推 PR | 「做 STO-X」「修 bug」 |
| 🧪 **测试(Test)** + 验收 | [test.md](./test.md) | 写测试 + 验收 PR | 「给 X 模块补测试」「验收 STO-X」 |
| 🚀 **运维(Ops)** + 文档 | [ops.md](./ops.md) | 部署、巡检、事故处理、ADR | 「部署上线」「线上报警」 |

---

## 2. 流水线视图(issue 在角色间流转)

```
         ┌─────────┐       ┌─────────┐       ┌─────────┐       ┌─────────┐
 用户  → │   PM    │   →   │   Dev   │   →   │  Test   │   →   │   Ops   │  →  Done
         └─────────┘       └─────────┘       └─────────┘       └─────────┘
             ↓                 ↓                 ↓                 ↓
        创建 issue         写代码 / PR       验收 DoD        部署 + 冒烟 + 巡检
        Backlog          In Progress       In Review          Done
```

### 每一棒的交接物(上游必须交、下游必须验)

| 交接 | 交接物 | 验收方 |
|---|---|---|
| PM → Dev | issue 含:背景 / 任务范围 checklist / 来源 / 验收标准 | Dev 通读后若不清晰,直接回退到 PM(`save_comment` 列疑问) |
| Dev → Test | PR(标题 `[STO-X]` + 描述 `Closes STO-X`)+ 本地 pytest/vitest 已绿 | Test 按 DoD 审,不过打回 In Progress |
| Test → Ops | PR 已批准 + issue 验收项全勾 | Ops 合并后才部署,部署前二次确认 DoD |
| Ops → Done | 部署指纹评论(commit SHA / 时间 / 冒烟结果 / 进程 PID) | **三方共识**后(见 §5)转 Done |

---

## 3. 强约束(所有角色通用,不可绕过)

### 3.1 单一事实源优先级
1. **ADR**(`docs/ADR/`)— 最高,冲突以此为准
2. **PROGRESS.md**(`docs/PROGRESS.md`)— 真实进度
3. **CLAUDE.md** — 项目说明书
4. 其他文档 / 代码

**规则**:改**现有模块**前必读 PROGRESS + CLAUDE + 相关 ADR;**新模块**可直接动手。

### 3.2 三位一体 Git 关联
每个 issue 在三处留指纹:
1. Commit message 前缀 `[STO-X] <动宾短句>`
2. PR 标题 `[STO-X] <完整描述>`
3. PR 描述正文含 `Closes STO-X`

### 3.3 状态自动推进
AI 自己改状态,不等用户:

| 事件 | Linear 状态 |
|---|---|
| 开始读 issue + 动手 | `Backlog/Todo` → `In Progress` |
| 推出 PR | `In Progress` → `In Review` |
| 三方共识通过 + 部署 smoke 通过 | `In Review` → `Done` |
| 发现无法推进(等外部/决策) | 任意 → 保持原状态 + 打 `Blocked` + 评论阻塞原因 |

### 3.4 完成的 DoD(硬门槛,5 条)
1. ✅ `pytest -q` 全绿,覆盖率不降(基线 ≥ 75%)
2. ✅ 前端 `npx vue-tsc --noEmit` 0 error + `npx vitest run` 全绿
3. ✅ issue 描述里「验收」勾选项全部打勾
4. ✅ **服务器部署后运行无报错**(jd 冒烟通过)
5. ✅ 若是**长任务**(采集 / 评分 / 回测 / 机器人):必须配套创建 **Watch 巡检 issue**(见 ops.md)

### 3.5 事故即建 issue(零延迟)
发现线上异常立刻建 Bug + Urgent,优先级 1,先止损再复盘。

### 3.6 分支铁律(主干)
- `main` = 旧版代码,**冻结**,任何角色都不得 checkout / merge / cherry-pick
- `new-branch` = **发布分支**,Ops 唯一部署来源
- 所有新开发**必须开独立分支**(见 §3.7)

### 3.7 🌿 分支隔离铁律(并发安全 · v3 新增)

> **背景**:多 Agent 并发时,若 Dev / Test / Ops / PM 都在 `new-branch` 上操作,会出现:Test 测的代码和 Ops 部署的不一致 / PM 扫到的「漏洞」其实是 Dev 半成品 / 两个 Dev 互相覆盖。
> **解决**:**每个 issue 一个独立分支**,各角色各自锁定工作对象。

#### 🌳 分支拓扑

```
main (旧代码,冻结)

new-branch (发布分支,Ops 部署来源)
  ↑ Ops JIT merge(部署前合入,确认无问题)
  │
  ├── feat/STO-6-api-auth         ← Dev 开发功能
  ├── fix/STO-31-northbei-stocks  ← Dev 修 bug
  ├── refactor/STO-35-modal       ← Dev 重构
  ├── docs/STO-40-claude-md       ← Dev 写文档
  └── hotfix/STO-XX-prod-down     ← Ops 紧急修复
```

#### 🔒 4 条铁律(按角色)

1. **Dev 开工必新建分支** —— 从最新 `new-branch` 切出 `<type>/STO-X-<slug>`,**不在 new-branch 上直接改**
2. **Test 在 Dev 分支上测** —— `git checkout <type>/STO-X-<slug>`,**不在 new-branch 上测**
3. **Ops JIT merge 部署** —— 部署前最后一刻 `git merge --ff-only origin/<type>/STO-X-<slug>` 到 new-branch,失败即拒
4. **PM 只读 new-branch** —— 扫代码 / 建 issue 前 `git checkout new-branch`,**禁止** checkout 其他分支

### 3.8 🌳 Worktree 物理隔离(v3.2,并发安全前提)

> **背景**:4 个 AI Agent 同时挂机,若共用一个工作区,`git checkout` 会互相践踏(Dev 在写半成品时,Test 把工作区切到另一分支 → Dev 下轮回来写错分支)。

**架构**:每个角色 Agent 在**独立的 git worktree** 里工作,共享同一个 `.git` 但工作区文件物理隔离。

```
~/Develop/personal/stock_analysis           ← 主仓库(交互式开发留给你自己)
~/Develop/personal/stock_analysis-pm        ← PM agent worktree,始终 agent/pm(跟踪 new-branch)
~/Develop/personal/stock_analysis-dev       ← Dev agent worktree,在此开 feat/STO-X-xxx
~/Develop/personal/stock_analysis-test      ← Test agent worktree,checkout Dev 推的分支
~/Develop/personal/stock_analysis-ops       ← Ops agent worktree,JIT merge 用
```

**初始化**(一次性):
```bash
./scripts/setup-agent-worktrees.sh
```

**启动**(每次挂机):
```bash
./scripts/start-ai-agents.sh    # 自动 cd 到各自 worktree
```

**保障效果**:
- Dev 写一半未提交 → Test 在自己 worktree 里 checkout → **Dev 工作区毫发无损**
- 每个 worktree 有独立的 `.pytest_cache` / `node_modules`(可软链共享)
- 共享 `.git` 对象 → Dev `git push` 后,Test `git fetch` 立刻可见

**Agent 铁律**(每个 Agent 启动时在自己 worktree 里):
- ✅ 只在自己 worktree 内操作,不 `cd` 到其他目录
- ✅ PM agent 始终在 `agent/pm` 跟踪分支(周期 `git pull` 同步 new-branch)
- ✅ Dev agent 在自己 worktree `git checkout -b feat/STO-X-...`
- ❌ 禁止跨 worktree 操作(写别的 agent 目录里的文件)

#### 📐 分支命名规范

```
<type>/STO-<id>-<slug>
```

- `<type>`:`feat` / `fix` / `refactor` / `docs` / `test` / `hotfix`(和 Linear 类型标签对应)
- `<id>`:Linear issue 编号,如 `6`、`31`
- `<slug>`:标题的英文/拼音 kebab-case,不超过 30 字符;特殊字符用 `-` 代替;中文可用拼音首字母

**示例**:
```
feat/STO-6-api-auth
fix/STO-31-northbei-stocks
refactor/STO-35-lhbpnl-modal
docs/STO-40-claude-md
```

**生命周期**:合并到 new-branch + 部署成功 → **立即删除**(本地 `git branch -d` + 远程 `git push --delete`)

#### 🎯 5 阶段工作流(从 issue 到部署)

```
① Dev 开工      git checkout -b <type>/STO-X-<slug>
                编码 + 提交(每个 commit 带 [STO-X])
                本地 pytest/vue-tsc/vitest/smoke 全绿
                ↓
② Dev 提 PR     git push origin <type>/STO-X-<slug>
                GitHub 开 PR: base=new-branch, head=<分支>
                Linear: 转 In Review,标签 -@dev +@test +@ops
                ↓
③ Test 验收    在 Dev 分支上 checkout + 测(不碰 new-branch)
                测试用例写在 issue 评论(§3.10 test.md)
                5 项测试过 → 打 test-passed 标签,-@test 保留 @ops
                ↓
④ Ops JIT 合并  ssh jd + git checkout new-branch && git pull
                git merge --ff-only origin/<type>/STO-X-<slug>
                ├─ 成功 → make deploy + smoke + 5min 观察
                │       → 成功:git push origin new-branch,删分支,转 Done + 指纹评论
                │       → 失败:git reset --hard HEAD~1 回滚,移除 test-passed,打回 @dev
                └─ FF 失败(new-branch 已前进)→ 拒绝合并,评论 @dev 请 rebase
                ↓
⑤ 删分支 + Done ops 删除 Dev 的分支(本地+远程)+ Linear 转 Done
```

#### 🧯 并发冲突解决

| 冲突场景 | 解决 |
|---|---|
| Dev A 和 Dev B 改同一文件 | 谁先合并谁赢。后合的在阶段 ④ `--ff-only` 失败 → rebase 自己分支 + 重新提 PR |
| Test 在 Dev 分支上测时 Dev 又推了新 commit | Test `git pull` 对应分支重新测;或评论要求 Dev **冻结分支**直到验收 |
| Ops 部署一半另一个 Ops 也想部署 | **按本工作区设定只有一个 Ops 实例**,无需加锁。Git `--ff-only` 是天然锁 |
| PM 扫代码发现「新 bug」,但其实 Dev 正在 STO-Y 里修 | **PM 建 issue 前必 `list_issues(state=In Progress)` 查重** |

#### ⚠️ 对本 issue 前的历史 new-branch 直接开发 

以前(v1/v2 时期)Dev 直接在 new-branch 上改 + push 是允许的。**从本规则生效起**:

- 现有已完成的 commit 保留,不追溯
- 新开工的 issue 必须按上述流程
- 存量 @dev issue 需迁到规范分支名(见迁移手册)

### 3.9 🔒 多项目隔离铁律(v3.2,关键安全约束)

> **背景**:`Personal Dev` team 下已有多个 project(龙虎榜量化 / wqbridge / 未来可能更多),它们**共用 STO 编号池**(STO-14 可能是 wqbridge 的,STO-43 是龙虎榜的,混编不连续)。如果 AI 不带 `project` 参数扫 issue,或按 ID 盲操作,就会**跨项目串台**(读错需求 / 改错代码库 / 建错子任务)。

#### 🎯 本仓库(stock_analysis)的项目锁定

```
仓库路径:   ~/Develop/personal/stock_analysis
Project:     龙虎榜量化交易分析系统
Project ID:  <PROJECT_ID>    # 见 §0 速查表(L17)
```

**本仓库下运行的所有 AI,工作范围严格限定此 project**。

#### 🔒 4 条强制规则

1. **`list_issues` 必传 `project` 参数**
 ```python
 list_issues(project=PROJECT_ID, state="Backlog", ...)  # ✅
 list_issues(state="Backlog", ...)                     # ❌ 会拉到 wqbridge 的
 ```

2. **`save_issue` 新建时必传 `project` 参数**
 ```python
 save_issue(title="...", project=PROJECT_ID, ...)      # ✅
 save_issue(title="...", team=TEAM_ID, ...)            # ❌ 不够,team 下有多 project
 ```

3. **按 ID 操作前必先 `get_issue` 验证 `projectId`**
 ```python
 info = get_issue(id="STO-X")
 if info.projectId != PROJECT_ID:   # 见 §0 速查表(L17)
     save_comment(id="STO-X", body="⚠️ 此 issue 属于其他 project,本仓库 AI 拒绝操作")
     return  # 跳过,处理下一条
 # 通过验证后才能 save_issue / save_comment
 ```

4. **发现跨项目 issue 立即停手**,不改状态、不提 PR、不 checkout,仅留一条说明评论后跳过。

#### 🧪 快速自检(接手新会话时跑一次)

```python
# 扫本项目 In Progress,应该只看到 STO 编号中属于龙虎榜的
issues = list_issues(project=PROJECT_ID, state="In Progress")
for i in issues:
    assert i.projectId == PROJECT_ID   # 见 §0 速查表(L17)
```

---

### 3.10 🤫 零打扰铁律(v3.2,挂机模式自决)

> **背景**:7×24 挂机下用户不在线,任何 `AskUserQuestion` 都会**阻塞整个轮询循环**(Claude Code 等用户响应,后续 ScheduleWakeup 全部失效)。

#### 🚫 强制规则

1. **禁止调用 `AskUserQuestion`**(所有角色、所有场景)
2. **L4 决策**(花钱 / 不可逆 / 外部依赖)→ 改为:
   - 标题加 🔴 前缀
   - 打 `Urgent` + `@pm` 标签
   - 评论列清:决策点 + 至少 2 个选项 + 各自代价 + 默认推荐
   - **跳过此 issue,继续处理队列下一条**
3. **跨角色授权场景**(§4.6)→ 按 §4.2 权限表执行,权限表外的**默认拒绝**,不问用户
4. **仲裁僵持**(§5)→ 按 §5「常见冲突默认判决」表自动执行,不问用户
5. **唯一例外**:用户**主动在对话里追问时**,AI 可以反问澄清;但轮询循环里**绝不主动问**

#### 🔍 用户怎么看到等决策的 issue?

- 筛选器:`label = @pm AND label = Urgent`
- 或标题搜索 `🔴`
- 所有 L4 issue 都通过此视图汇聚,用户批量处理

---

### 3.11 🎯 决策边界 & 升级标识(v2)

> 详细规则见 [pm.md §3.9-3.11](./pm.md#39--pm-决策边界v2--谁拍板)。本节是全局 AI 都应知道的规则。

#### 决策分层

| 层级 | 例子 | 谁拍板 |
|---|---|---|
| **L1** 技术实现 | 选什么库、如何 mock | @dev 自己 |
| **L2** 产品小事 | CTA 跳哪 / 空态文案 | **PM 自己** + 评论留痕 |
| **L3** 产品方向(可逆) | 暂时关掉某功能 | **PM 选免费可逆方案** + 评论注明升级路径 |
| **L4** 钱 / 不可逆 / 外部依赖 | 花钱买服务 / 删表 | **升级用户**(🔴 标识) |

#### 核心规则:钱类 PM 不主动花钱

**PM 永远选免费方案**。若只有花钱方案 → 先看有没有降级可逆替代(方案 C 类):

- 降级可逆 → PM 选降级,评论留升级路径
- 降级不可逆 → 归为 L4,升级给用户

#### 🔴 升级标识(L4 专用)

需用户决策的 issue **统一标识**:

1. **标题加 🔴 前缀**(视觉最强)
2. **加 `Urgent` 标签**(优先级置顶)
3. **保留 `@pm` 标签**(表示球在用户手里)
4. **评论里列清选项 + 默认推荐**,然后跳过此 issue 继续下一条(**禁止 `AskUserQuestion`**,见 §3.10)

**查看所有等用户决策的 issue**:Linear filter `label = @pm AND label = Urgent` 或搜标题含 `🔴`。

#### ⚠️ 🔴 不是万能紧急标识

- 线上事故 Bug → 用 `Urgent` 标签,**不加 🔴**(Ops 自己处理)
- 用户反馈紧急 → 同上
- **🔴 专属于「PM 拍不了板」的场景**,避免滥用

#### PM 留痕模板(L2/L3 必填)

```markdown
🧭 **PM 决策(按 v2 规则)**
- **结论**:<一句话>
- **理由**:<为什么,1-3 条>
- **备选**:<其他方案及劣势,1-3 条>

### 当前角色标签调整
- 移除 `@pm` → 加 `@<role>`
```

---

## 4. 🔄 状态流转矩阵(严格边界,越权即拒)

> 本节是**所有角色共享的强制规则**。每个角色文档 §3.x 会再展开自己那部分的细节,但核心权限表以此节为准。

### 4.1 状态生命周期图

```
                                 ┌──────────────┐
                                 │  Canceled    │(仅 PM / 用户)
                                 └──▲───────────┘
                                    │
                                    │
┌─────────┐     ┌──────┐     ┌────────────┐     ┌───────────┐     ┌──────┐
│ Backlog │ ──→ │ Todo │ ──→ │In Progress │ ──→ │ In Review │ ──→ │ Done │
└─────────┘     └──────┘     └────────────┘     └───────────┘     └──────┘
     │                              ▲                 │
     │                              │  打回           │
     │                              └─────────────────┘
     │                               (Test / Ops)
     │
     ↓
┌───────────┐
│ Duplicate │(仅 PM / 用户)
└───────────┘
```

### 4.2 角色 × 状态 操作权限表

| 状态转换 | PM | Dev | Test | Ops |
|---|:---:|:---:|:---:|:---:|
| 创建 issue → `Backlog` | ✅ 常规 | ✅ 事故即建 Bug+Urgent | ✅ 补测/回归 | ✅ 事故即建 Bug+Urgent |
| `Backlog` → `Todo`(纳入本周) | ✅ | ✅ | ❌ | ❌ |
| `Backlog` / `Todo` → `In Progress`(开工) | ❌ | ✅ | ✅ 主动补测时 | ✅ 事故响应时 |
| `In Progress` → `In Review`(提 PR) | ❌ | ✅ | ✅ 补测 PR | ✅ 修复 PR |
| `In Review` → `In Progress`(**打回**) | ❌ | ❌ 自己不能打回自己 | ✅ DoD 不过 | ✅ 部署失败 |
| `In Review` → `Done` | ❌ | ❌ | ❌ | **✅ 唯一签字人** |
| 任意 → `Canceled` | ✅ | ❌ | ❌ | ❌ |
| 任意 → `Duplicate` | ✅ | ❌ | ❌ | ❌ |
| 打 `Blocked` 标签(**不改状态**) | ✅ | ✅ | ✅ | ✅ |

### 4.3 三条核心铁律

1. **只有 Ops 能转 `Done`** —— DoD 第 4 条「jd 部署冒烟」是硬门槛,未经 Ops 验证不可转 Done。Dev 和 Test 签字到 `In Review` 为止。
2. **只有 PM 能转 `Canceled` / `Duplicate`** —— 这两个终态代表「需求决策」,不是技术判断。Dev/Test/Ops 想取消某 issue → `save_comment` 给 PM,让 PM 决定。
3. **Dev 不能自己把自己从 `In Review` 打回 `In Progress`** —— 需由 Test 或 Ops 打回,避免自欺欺人。真发现自己错了 → `save_comment` 说明 + @Test 让 Test 打回。

### 4.4 Todo 的定位(本周 workset)

- `Backlog` = **需求池**,PM 建完默认进这里,量可能很大
- `Todo` = **本周 workset**,PM/Dev 筛选出近期要做的
- **Dev 从 `Todo` 拉任务开工**,不从 `Backlog` 直接拉(除非紧急事故)
- 每周开始时,PM 或 Dev 主动把下周要做的 issue 从 `Backlog` 拖到 `Todo`

### 4.5 Blocked 标签的处理

**Blocked 是标签,不改变状态**(保持原 `Backlog` / `Todo` / `In Progress` / `In Review`):

```python
# 打 Blocked 标签 + 评论说明(任何角色都可以)
save_issue(id="STO-X", labels=[..., "82c6e303-5c10-4307-88fc-c9c8e7b7302c"])
save_comment(issueId="STO-X", body="""
🔶 Blocked
- 阻塞原因:<具体原因,如「等 TuShare 升级 2000 积分」>
- 预计解除:<日期 or 条件>
- 受影响工作:<我本来要做什么>
""")
```

**解除 Blocked**:同样方式移除标签 + 评论说明解除,然后恢复原定流程。

### 4.6 越权操作的处理(严格边界)

AI 发现自己要做的状态转换**不在自己权限内**时:

1. **不要偷偷转** —— 立刻停下
2. `save_comment` 说明情况,@ 对应角色:
 ```
 @Ops 请转 Done,我(Dev 角色)的 DoD 已签字,PR 已合并部署。
 ```
3. **无人响应** → 按 §3.10 零打扰铁律处理:保持 `Blocked` 标签 + 等待对应角色下轮处理,**不主动问用户**。
4. 用户在对话里**主动授权**跨角色操作时,评论必须写「**代理 X 角色操作,授权来源:用户对话 <timestamp>**」留痕。

### 4.7 状态转换时必做的附加动作

| 转换 | 附加动作 |
|---|---|
| 任意 → `In Progress` | 自认 assignee = 操作者 |
| `In Progress` → `In Review` | `save_comment` 给下一棒:`@test 请验收 / @ops 请部署` |
| `In Review` → `In Progress`(打回) | `save_comment` 列具体 blocker,引用 DoD 第几条 |
| `In Review` → `Done` | `save_comment` 部署指纹(Ops 签字) |
| 任意 → `Canceled` | `save_comment` 说明原因 |
| 任意 → `Duplicate` | `save_comment` 引用目标 issue ID,如 `Dup of STO-Y` |

### 4.8 🏷️ 角色交接标签机制(「球在谁手里」的可视化)

> 本工作区**一个人开发**,Linear 里只有一个真实账号。我们用**角色标签**表示「这个 issue 当前需要哪个角色处理」,而不是真的分配给不同用户。

#### 4 个角色标签(Personal Dev Team)

| 标签 | 颜色 | ID | 含义 |
|---|:---:|---|---|
| `@pm` | 🔵 蓝 | `1190ea9b-649e-4895-8feb-8c771d3cc76f` | 当前需要 PM 处理(需求澄清 / 验收标准 / 取消决策) |
| `@dev` | 🟠 橙 | `f725ab92-9754-4220-b05c-6d9961758e36` | 当前需要 Dev 处理(编码 / 修 bug / 代码审查) |
| `@test` | 🟣 紫 | `9036b957-3142-47d3-9959-4a62095c2636` | 当前需要 Test 处理(验收 DoD / 补单测 / 回归测试) |
| `@ops` | 🔴 红 | `68c6b943-5dee-479f-a1c6-2c7338153984` | 当前需要 Ops 处理(部署 / 巡检 / 事故响应) |
| `test-passed` | 🟢 青 | `c35a902b-0fcf-4a4f-a816-bda25a4dee0d` | Test 签字标记(5 项测试全过 / 含不适用声明)。**Ops 转 Done 前必须看到此标签**;用户可撤回(见 [test.md §3.11](./test.md#311--test-passed-标签生命周期)) |

#### 挂标签的铁律

1. **同一 issue 可挂多个角色标签**(并行处理 / 多方都在等):
 - Dev 提 PR 后挂 `@test` + `@ops`(两人都得看)
 - Test 批完挂 `@ops` 并去掉 `@test`(只等 Ops 了)
 - Watch 巡检 issue 常挂 `@ops`(长期轮值)

2. **每次状态转换时同步更新角色标签**:

| 状态转换 | 移除标签 | 添加标签 |
|---|---|---|
| 建 issue → `Backlog` | — | `@pm` 或 `@dev`(事故)或 `@test`(补测)或 `@ops`(事故/Watch) |
| `Backlog/Todo` → `In Progress`(Dev 开工) | `@pm` | `@dev` |
| `In Progress` → `In Review`(Dev 提 PR) | `@dev` | `@test` + `@ops` |
| `In Review` → `In Progress`(Test 打回) | `@test`, `@ops` | `@dev` |
| `In Review` → `In Progress`(Ops 部署失败) | `@ops` | `@dev` |
| `In Review` → `Done` | `@test`, `@ops` | —(Done 状态无角色) |
| 任意 → `Canceled` | 所有 | —(已终结) |

3. **疑问交接**(核心:有疑问时「加别人标签 + 评论 @ 别人」):

 AI 在 X 角色处理时发现「这个得 Y 角色来」→ **不改状态**,只:
 - `save_issue` 加 `@y` 标签(保留 `@x` 不移除,表示我还在等 Y 的答复)
 - `save_comment` 写清疑问,文本里 `@y` 提醒

 例:Dev 发现 issue 验收标准不可量化:
 ```python
 # 保持 state=In Progress,只加 @pm 标签
 save_issue(id="STO-X", labels=[..., "@pm 标签 ID"]) # 保留 @dev + 加 @pm
 save_comment(issueId="STO-X", body="""
 @pm 此 issue 验收标准「更好看」不可量化,我无法判断何时算做完。
 请澄清具体指标(如:响应时间 < 200ms / 点击率 > X%)。
 澄清后请移除 @pm 标签,我继续 @dev。
 """)
 ```

4. **打 `Blocked` 标签时保留对应角色标签**,让人知道「谁在等什么」:
 ```
 @dev + Blocked + 评论「等 STO-Y 合并后才能继续」
 ```

#### 看板视图建议

在 Linear UI 里建 5 个 filter view(一次性操作,长期受益):

- 🔴 **等我决策(最重要)**:filter `label = @pm AND label = Urgent`(或搜标题含 `🔴`)
- **我的 PM 工作**:filter `label = @pm`
- **我的 Dev 工作**:filter `label = @dev`
- **我的 Test 工作**:filter `label = @test`
- **我的 Ops 工作**:filter `label = @ops`

切换角色时 = 切换 view,不用刷所有 issue。
🔴 view 是**最优先看的**,任何时候上面有 issue 就说明有 PM 拍不了的板,等你决策。

#### AI 操作的规则

- AI 始终以**你的主账号**执行 MCP 调用(createdBy 永远是你 xiaotian zhang)
- AI 不改 `assignee` 字段(保持是你或空)
- AI 只通过**角色标签**表示交接,不通过 `assignee` 表示交接
- 新 session 开场时,AI 读完角色 prompt,**只处理挂对应 `@<role>` 标签的 issue**

---

## 5. ⚖️ 冲突仲裁:三方共识才能转 Done

**任何 issue 转 Done 需要 Dev + Test + Ops 三方全部签字**:

| 角色 | 签字动作 | 通过标志 |
|---|---|---|
| **Dev** | PR 作者自评通过,本地测试全绿 | PR 描述里勾 ✅ 自测清单 |
| **Test** | 审 PR + 审 issue 验收项 + §3.10 5 项测试过 | 打 `test-passed` 标签 + 评论 `✅ Test 签字` |
| **Ops** | 部署到 jd + 冒烟 + 5 分钟观察无异常 | issue 评论 **部署指纹** |

### 否决规则
- **任何一方否决** → 打回 `In Progress`,评论说明原因
- **否决权无大小**:Dev 也能否决 Test 的验收意见(反过来一样),关键是对齐 DoD 而不是看谁职位高
- **僵持 2 轮以上**(A 说过 B 说不过 3 次以上) → 按下表「常见冲突的默认判决」自动执行,**不问用户**(见 §3.10)

### 常见冲突的默认判决(降低沟通成本)

| 冲突类型 | 默认判决 |
|---|---|
| Dev 说改好了 / Test 说验收项没勾完 | **Test 胜**,回到 In Progress 补齐 |
| Test 说单测够了 / Ops 说线上跑不通 | **Ops 胜**,DoD 第 4 条是硬门槛 |
| PM 说验收标准变了 / Dev 已做完 | **PM 胜**但需建新 issue,不追溯改旧 issue |
| 覆盖率下降 0.1% 但核心逻辑加了 | **Test 胜**(硬门槛),Dev 需补测或给出 waive 理由让 PM 裁决 |

---

## 6. 角色交接的互查清单(摘要)

每个角色文档里都有「我对别人的互查清单」详版,这里只列最关键的:

### PM → Dev 的交接,Dev 要验
- [ ] issue 标题是动宾短句?
- [ ] 有明确的「任务范围」checklist?
- [ ] 有可量化的「验收标准」?
- [ ] 涉及旧模块时有没有点明影响范围?
- [ ] 长任务有没有配套 Watch issue 模板?

### Dev → Test 的交接,Test 要验
- [ ] PR 标题 / commit / PR 描述三位一体关联?
- [ ] 单测覆盖 issue 任务范围每一项?
- [ ] 覆盖率未下降?
- [ ] 前端 vue-tsc 0 error?
- [ ] `print(` 调试残留清理了?

### Test → Ops 的交接,Ops 要验
- [ ] PR 是否已 Approve?
- [ ] issue 验收项是否全勾?
- [ ] 涉及 schema 变更时,迁移脚本在不在 PR?
- [ ] 涉及长任务时,Watch issue 模板是否就绪?

### Ops → 用户/PM 的交接,PM 要确认
- [ ] 部署指纹评论是否完整?
- [ ] 冒烟测试是否覆盖本次改动路径?
- [ ] 如果有用户可见的改动,`docs/CHANGELOG.md` 更新了吗?
- [ ] 如果有开发者关注的技术决策,`docs/DEVLOG.md` / ADR 更新了吗?

---

## 7. 新 Session 开场白(复制即用)

### 通用开场(不确定角色时用)
```
请先读 @docs/prompts/linear/README.md(Linear 协作总纲)
和 @CLAUDE.md(项目说明书)。

我要做的事:<任务描述>
请判断我应该用哪个角色,然后进入对应文档 @docs/prompts/linear/<role>.md。
```

### 明确角色时(推荐,对齐最快)
```
请先读 @docs/prompts/linear/<role>.md 和 @CLAUDE.md。
我现在扮演 <role>,当前任务:<描述>。

按该文档的闭环流程执行,遇到分歧时按 README §5 冲突仲裁规则处理,状态流转按 README §4。
```

其中 `<role>` 取自:`pm` / `dev` / `test` / `ops`。

---

## 8. 本目录维护规则

- 发现 AI 在某个角色上犯了规约未覆盖的错误 → 补充该角色文档的「反模式清单」
- 角色交接出现新冲突类型 → 补充本文 §5「常见冲突的默认判决」表
- 状态流转遇到新场景(如新增状态/新角色) → 更新 §4 状态矩阵
- 工作区结构变化(新标签 / 改 Team / 新项目) → 同步本文 §0 和各角色文档的 ID 速查小节
- 重大规则调整 → 在对应文档末尾记录变更日志

### 文件清单

| 文件 | 大纲 | 用途 |
|---|---|---|
| [README.md](./README.md)(本文) | 总纲 + 路由 + 仲裁 | 所有 AI 第一篇必读 |
| [pm.md](./pm.md) | 产品经理闭环 | 需求 → issue |
| [dev.md](./dev.md) | 开发+审查闭环 | issue → 代码 → PR |
| [test.md](./test.md) | 测试+验收闭环 | PR → 验收 → 批准 |
| [ops.md](./ops.md) | 运维+文档闭环 | 合并 → 部署 → 巡检 |

### 变更日志
- **2026-04-20** 初版拆分:从单文件 `linear_agent.md` 拆为 4 角色文档 + 总纲,引入三方共识仲裁
