# Linear 协作规约(已拆分)

> ⚠️ **本文件已拆分为 4 角色 + 总纲,保留在此仅为向后兼容旧链接。**
>
> 新入口:[docs/prompts/linear/README.md](./linear/README.md)

---

## 快速跳转

| 角色 | 文档 | 用途 |
|---|---|---|
| 🏠 **总纲** | [linear/README.md](./linear/README.md) | **新 session 第一篇必读**,含工作区速查 + 仲裁规则 + 路由 |
| 📋 产品经理 | [linear/pm.md](./linear/pm.md) | 需求 → issue |
| 🛠️ 开发 + 审查 | [linear/dev.md](./linear/dev.md) | issue → 代码 → PR |
| 🧪 测试 + 验收 | [linear/test.md](./linear/test.md) | PR → 验收 → 批准 |
| 🚀 运维 + 文档 | [linear/ops.md](./linear/ops.md) | 合并 → 部署 → 巡检 |

---

## 拆分原因(2026-04-20)

原单文件 `linear_agent.md` 约 450 行,涵盖 4 角色所有规则,问题:

- **单角色工作时噪音过多**:Dev 不关心 PM 模板,PM 不关心 Ops 部署流程
- **缺少交叉印证**:角色间交接、互查、冲突仲裁没有清晰规则
- **难以维护**:改一个角色的规则要翻整个文件

**新架构**:

- 每个角色文档**自包含**(含 DoD / 标签 / 模板 / ID 速查),独立可读
- 每个角色有**三重视角**:上游期望 + 我的工作 + 下游产出 + 互查清单
- **三方共识**仲裁机制:Dev + Test + Ops 都签字才转 Done
- 总纲 `README.md` 负责**路由**与**冲突仲裁**,不重复细节

---

## 与 7x24_agent.md 的关系

| 文档 | 驱动 | 用途 |
|---|---|---|
| [linear/*.md](./linear/) | **issue 驱动** | 有明确任务时,按角色闭环执行 |
| [7x24_agent.md](./7x24_agent.md) | **时间驱动** | 无任务时,每 20 分钟跑运维巡检 |

两者**互补不冲突**。Ops 角色在无 issue 时默认跑 7x24 巡检,有 issue 时切到 ops.md 闭环。
