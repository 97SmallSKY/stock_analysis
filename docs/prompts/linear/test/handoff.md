# Test 子文档 · 角色交接详细动作

> 父文档:[../test.md](../test.md)

---

## 👉 Test 主动做的交接(Test → 别人)

### 闭环 B(验收 Dev PR)

| 场景 | 动作 |
|---|---|
| 验收通过(Approve) | 标签:移除 `@test`,保留 `@ops`,加 `test-passed`。评论 templates.md 签字模板。**状态保持 In Review**(不转 Done) |
| 验收打回(Request changes) | 标签:移除 `@test` + `@ops`,加 `@dev`。**状态**:`In Review` → `In Progress`。评论:列 blocker 引用 DoD |
| 文档不合格(只缺 PR 描述) | 标签:保持 `@test` + `@ops` + 加 `@dev`。**状态不变**(`In Review`)。评论 @dev 补 PR 描述 |

### 闭环 A(主动补测)

| 场景 | 动作 |
|---|---|
| 建补测 issue | 标签:`@test`(我自己 handle) |
| 开工 | 标签:保留 `@test`。状态 `In Progress`。Git:切 `test/STO-X-<slug>` 分支 |
| 补测 PR 提交 | 标签:移除 `@test`,加 `@dev` + `@ops`。评论:`@dev 请审 + @ops 待部署` |

## 👈 别人交给 Test 的情况

当 AI 切到 Test 角色,**只处理挂 `@test` 标签的 issue**。

| 来源 | 表现 | Test 要做的 |
|---|---|---|
| Dev 提 PR 请我验收 | 标签 `@test` + `@ops`,状态 `In Review` | 按闭环 B + checklist.md 验收 |
| Dev 咨询测试策略 | 标签 `@dev` + `@test` | 给建议,移除 `@test` 留 `@dev` |
| PM 仲裁后让我续验 | 标签 `@pm` 被移除,只剩 `@test` | 按新验收标准重新验 |
| 覆盖率监控报警 | 标签 `@test`,CI 建的 | 按闭环 A 补测 |

## 🔁 Test 疑问交接

**规则**:保留 `@test` 标签,加要咨询的角色标签,评论 @,**不改状态**。

| 疑问类型 | 交给谁 | 示例 |
|---|---|---|
| 验收标准不可量化 | `@pm` | `@pm 验收项「响应更快」无法测,请给数字阈值` |
| Dev 说「测不了」 | `@dev` | `@dev 这段为什么测不了?请论证或拆分` |
| 集成测试需真实环境 | `@ops` | `@ops 此测试需 jd,请协助` |
| 线上 bug 但 Dev 说「不是 bug」 | `@pm` | `@pm 请裁决是 Bug 还是 Feature` |

## ⚠️ Test 交接特别注意

1. **验收通过时必须移除 `@test` 标签** —— 保留会让看板显示「Test 还没验」
2. **文档不合格不动 `@test` 标签** —— 加 `@dev` 催补文档
3. **打回 In Progress 时必须加 `@dev` 标签** —— 否则 Dev 不知道球回到他手里
4. **验收评论必须含数字证据** —— pytest/覆盖率/vitest passed 数
5. **v3**:必须在 Dev 独立分支上测,不在 new-branch 上测
