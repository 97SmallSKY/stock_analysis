# Ops 子文档 · 角色交接详细动作

> 父文档:[../ops.md](../ops.md)

---

## 👉 Ops 主动做的交接(Ops → 别人)

### 闭环 A(部署)

| 场景 | 动作 |
|---|---|
| 部署成功,转 Done | 标签:**清空所有角色标签**(`@test` 和 `@ops` 都移除)。评论:部署指纹。**状态 → Done** |
| 部署失败打回 Dev | 标签:移除 `@test` + `@ops`,加 `@dev`。移除 `test-passed`。**状态**:`In Review` → `In Progress`。评论:日志证据 + 回滚记录 |
| Test 未批就部署(越权) | ❌ 不允许。先 `save_comment` @test 催验收,加 `@test` 标签 |

### 闭环 B(事故响应)

| 场景 | 动作 |
|---|---|
| 建事故 Bug+Urgent issue | 标签:`@ops`(我自己处理)+ `Bug` + `Urgent`。**状态直接 `In Progress`**(不走 Backlog) |
| 事故已回滚,修复中 | 标签:保留 `@ops`,状态 `In Progress`。评论:止损记录 |
| Hotfix PR 提好 | 标签:移除 `@ops`,加 `@test` + `@ops`(自循环,因为我既是作者也是部署者)。评论:`@test 请验收` |
| 事故复盘需 PM 参与决策 | 加 `@pm`,评论 @pm 说明决策点 |

### 闭环 C(Watch 巡检)

| 场景 | 动作 |
|---|---|
| 建 Watch issue | 标签:`@ops` + `Blocked` + `Task`。**状态直接 `In Progress` 常驻** |
| 日常巡检(正常) | 标签 / 状态不变。评论:`<日期> ✅ 指标正常` |
| 巡检发现异常 | 建子 Bug issue 关联此 Watch。本 Watch 下评论 `⚠️ 异常,已建 STO-Y` |

## 👈 别人交给 Ops 的情况

当 AI 切到 Ops 角色,**只处理挂 `@ops` 标签的 issue**。

| 来源 | 表现 | Ops 要做的 |
|---|---|---|
| Dev 提 PR 待部署 | 标签 `@test` + `@ops`,状态 `In Review`(Test 先验) | **等 Test 批再动**(看 Approve 评论 + `@test` 被移除),再按闭环 A 部署 |
| Test 批了 PR,等我部署 | 标签 `@ops`(`@test` 已移除),状态 `In Review` | 按闭环 A 部署 + 冒烟 + 转 Done |
| Dev 咨询部署风险 | 标签 `@dev` + `@ops`,评论 @ops | 给意见(需备份 DB / 需停 scheduler),移除 `@ops` 留 `@dev` |
| Test 需要 jd 环境跑集成测试 | 标签 `@test` + `@ops` | 协助开窗口跑,完了移除 `@ops` 留 `@test` |
| 7x24 巡检发现异常 | 我自己建新 issue `@ops` | 按闭环 B 处理 |

## 🔁 Ops 疑问交接

**规则**:保留 `@ops`,加要咨询的角色标签,评论 @,**不改状态**。

| 疑问类型 | 交给谁 | 示例 |
|---|---|---|
| 部署后行为异常,不确定是否 bug | `@dev` | `@dev 部署后 /api/X 返回 Y,不确定是否预期?` |
| 线上指标异常但不确定影响 | `@pm` | `@pm 日活跌 20%,不确定是否需要立刻回滚?请评估` |
| 冒烟失败但 Dev 说本地通过 | `@test` + `@dev` | `@test @dev 冒烟失败日志 <片段>,是否可复现?` |
| 长任务规律变化(耗时翻倍) | `@dev` | `@dev 每日灌库从 2h 变 4h,是否新加了什么?` |

## ⚠️ Ops 交接特别注意

1. **转 Done 时必须清空所有角色标签** —— Done 是终态,不需要「球在谁」
2. **收到挂 `@ops` 的 issue 时,先看 `@test` 是否也挂着** —— 如果 `@test` 还在说明 Test 没批,**不要提前部署**
3. **Watch issue 永远挂 `@ops`** —— 这是我的长期轮值,不交出
4. **事故 issue 建好立刻 In Progress 不走 Backlog** —— 零延迟
5. **打回 Dev 时必须加 `@dev` 标签 + 移除 `@ops`** —— 明确告诉 Dev「球在你那」
