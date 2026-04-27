# [auto:stock-analysis] 公共表达铁律

> 本文件是 **4 个 agent（PM / Dev / Ops / Auditor）的共享表达规约**。
> 每份 `.worktrees/sa-*/CLAUDE.local.md` 顶部都应引用本文。
> 与 [transition-checklist.md](./transition-checklist.md) 互补：
> - checklist 管 **状态转换门槛**（T1-T6）
> - 本文管 **Linear 文字输出的格式**（comment / description / 语言）

## C1 · 🈶 强制中文

- 所有 **Linear description / comment / title** 必须以中文为主体
- **可用英文**：代码片段 / commit SHA / URL / API 路径 / pytest 输出 / journalctl 日志 / 技术术语（pytest、OAuth、schema、rebase 等）/ STO-ID / ADR 编号
- **判断标准**：「只懂中文的人能否看懂这条 comment 的结论和下一步？」看不懂 = 违规
- **Auditor M9 扫描**：description 字段中文字符占比 < 50% → minor 告警

## C2 · 📏 Comment 格式（一条 comment 只做一件事）

按类型选模板，**超长即违规**：

| 类型 | 行数上限 | 模板 |
|---|---|---|
| **A 心跳** | 1 行 | `@<Role> tick <ISO8601> · <关键状态摘要>`（例：`@Ops tick 2026-04-25T14:20:00Z · verify_queue=2 · last_verify=STO-42:PASS`）|
| **B 状态** | 3 行 | 结论 / 证据（commit 或 grep 结果 1 行）/ 下一步 + 预估耗时 |
| **C 交接** | 5 行 | `@<Next> <action>` / commit=<sha> / 改动摘要 ≤ 20 字 / 耗时 / 备注 |
| **D 告警** | 5 行 | `BLOCKED: <原因>` / 日志 1 行 / 诊断 / 待决 / `@<谁>` |

### 通用禁令

- ❌ 复述 issue description（下游已读过）
- ❌ 贴 > 20 行日志（超了贴 gist 外链或 `/tmp/xxx.log` 路径）
- ❌ 心灵独白：「我思考了一下」「综合考虑」「值得注意的是」
- ❌ 一条 comment 既心跳又交接又告警（拆成 2 条）

## C3 · 📐 Description 格式（新建 issue）

**总长 ≤ 30 行**，结构固定：

```
## 背景（≤ 5 行）
<为什么要做，从哪来的>

## AC（Given/When/Then，≤ 10 行）
- Given ...
- When ...
- Then ...

## 参考（可选，≤ 5 行）
- <链接 / STO-XX / commit>
```

超了 = 拆子 issue 或改用 `docs/designs/` 长文 + description 贴链接。

## C4 · 🚫 /loop 挂机禁用 AskUserQuestion

所有 4 个 agent 在 `/loop` 自驱时，**禁止任何 `AskUserQuestion`**（无人值守，没人会回答）。卡住时：

- PM / Dev / Ops → 按 [transition-checklist.md T5](./transition-checklist.md) 加 `blocked:need-human` label + comment 首行 `BLOCKED: <原因>` + 降频到 1800s
- Auditor → 建 `[AUDIT] severe` issue

## C5 · 🔗 验证段规范（Dev → Ops 交接的关键字段）

Dev 在 `@Ops ready` comment 里必须含 `验证:` 段，Ops Playwright MCP 和用户人工复查都依赖它：

| issue 类型 | 要求 | 禁止 |
|---|---|---|
| 前端改动 | jd 地址 URL + 1 行预期现象 | localhost 地址 |
| 后端/API 改动 | curl 命令（jd 地址）+ 预期字段 | 模糊词「正常显示」|
| 数据/纯内部 | `无 UI/API 改动` + 自动化判据 | 省略 `验证:` 段 |
| 文档改动 | `无 UI/API 改动` + 文件路径 | — |

缺 `验证:` 段 → Auditor M9 告警 + Ops 无法执行 T3.6 UI 验证。

## 变更

| 日期 | 改动 | 触发 |
|---|---|---|
| 2026-04-22 | 初版，C1-C4 | 用户反馈：comment 太长 + 必须中文 + 询问优先工具 |
| 2026-04-24 | 删 E 型 / C3 去掉 Verify Steps | 删 Verify agent，Ops 接手 smoke |
| 2026-04-25 | agent 数量更新为 4 / 新增 C5 验证段规范 / A 型心跳含状态摘要 | sa-data 并入 sa-ops，Playwright MCP 接管 UI 验证 |
