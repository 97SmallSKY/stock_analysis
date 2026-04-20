# Dev 子文档 · 角色交接详细动作

> 父文档:[../dev.md](../dev.md)

---

## 👉 Dev 主动做的交接(Dev → 别人)

| 场景 | 动作 |
|---|---|
| 开工(Backlog → In Progress) | 标签:保留 `@dev`,移除 `@pm`。**Git**:`git checkout -b <type>/STO-X-<slug>`。评论:`@dev 开工,分支 <type>/STO-X-<slug>` |
| 提 PR(In Progress → In Review) | 标签:移除 `@dev`,加 `@test` + `@ops`。**Git**:`git push` + `gh pr create --base new-branch`。评论:dev/templates.md 提 PR 模板 |
| 发现 issue 不合规要退回 PM | 标签:保留 `@dev` + 加 `@pm`(**不改状态**)。评论按 dev/templates.md 退回模板 |
| 发现技术可行性问题需 PM 决策 | 标签:保留 `@dev` + 加 `@pm`。评论列方案 A/B/C 让 PM 选 |
| 被 Blocked | 标签:保留 `@dev` + 加 `Blocked`。评论写等什么 / 谁能解 |
| Test/Ops 打回,修完再提 | **Git**:原分支改 + push。状态 `In Progress` → `In Review`,加回 `@test` + `@ops` |
| Ops FF-only 失败让我 rebase | **Git**:`git rebase origin/new-branch && git push --force-with-lease`。评论 `@ops 已 rebase` |

## 👈 别人交给 Dev 的情况(别人 → Dev,我接手)

当 AI 切到 Dev 角色,**只处理挂 `@dev` 标签的 issue**。

| 来源 | 表现 | Dev 要做的 |
|---|---|---|
| PM 新建 issue 指派我 | 标签 `@dev`,状态 `Backlog` / `Todo` | 按 dev.md §2 流程开工,转 `In Progress` + 建分支 |
| PM 答复我的疑问 | 标签 `@pm` 被移除,只剩 `@dev`,评论有答复 | 继续 dev.md §2 流程 |
| Test 打回 PR | 标签 `@dev`,状态 `In Progress`,评论列 blocker | 按评论修复,再转 `In Review` 加回 `@test` + `@ops` |
| Ops 部署失败打回 | 同上 + Ops 的日志证据 | 分析日志,修复,重新走完整流程 |
| 别人请我 review PR | 标签 `@dev`,评论 @dev 请审 | 走 dev.md §5 审查流程,给 Approve 或 Request changes |

## 🔁 Dev 疑问交接(我有疑问时)

**规则**:保留 `@dev`(我还要继续做),加上要咨询的角色标签,评论 @ 他们,**不改状态**。

| 疑问类型 | 交给谁 | 示例 |
|---|---|---|
| 需求不清 / 验收不可量化 | `@pm` | `@pm 验收标准「更好看」不可量化,请细化` |
| 验收标准和实现方案冲突 | `@pm` | `@pm 要求 X,但实现 X 必然影响 Y,是否接受 trade-off?` |
| 测试策略怎么定 | `@test` | `@test 这段逻辑怎么测更合适?mock 到哪层?` |
| 部署风险预评估 | `@ops` | `@ops 此 PR 涉及 schema 变更,部署时要注意什么?` |

对方答复后,对方会**移除自己的标签**,剩 `@dev` 让我继续。

## ⚠️ Dev 交接特别注意

1. **提 PR 时必须加 `@test` + `@ops` 双标签** —— 两人都要看,少一个会漏交接
2. **收到 @pm 答复后,移除 `@pm` 标签** —— 否则看板永远显示「PM 欠答复」,污染视图
3. **不要同时挂 `@dev` + `@test` 然后自己验收** —— 三方共识规则,自己不能两栖
4. **v3**:开工必建独立分支,即使是小改动
5. **v3**:被 Ops 打回 rebase 时,用 `--force-with-lease`(不是 `--force`),保护 Test 已 checkout 的 commit
