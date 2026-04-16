# Agent Prompt 索引

> 本目录包含龙虎榜量化交易系统 v2 各模块开发的详细 Agent Prompt。
> 每个 Prompt 独立完整，可直接复制到 CodeFlicker 会话作为 System Prompt 使用。

---

## 目录结构

```
docs/prompts/
├── README.md              # 本索引
├── S01_DATA_LAYER.md      # S01: 数据层开发
├── S02_STRATEGY_TRADING.md # S02: 策略与交易引擎
├── S03_API_LAYER.md       # S03: API 层开发
├── S04_FRONTEND.md        # S04: 前端开发
├── S05_INFRASTRUCTURE.md  # S05: 基础设施
└── S06_TESTING.md         # S06: 测试
```

---

## 会话概览

| 编号 | 模块 | 核心职责 | 依赖前置 |
|------|------|----------|----------|
| S01 | 数据层 | ORM 模型、Alembic 迁移、数据采集服务、降级链 | 无 |
| S02 | 策略+交易 | 评分引擎、选股服务、交易执行、风控、状态机 | S01 |
| S03 | API层 | FastAPI 路由、Pydantic Schema、WebSocket、中间件 | S01 |
| S04 | 前端 | Vue3 页面、组件、状态管理、API 集成 | S03 |
| S05 | 基础设施 | Docker Compose、CI/CD、Makefile、监控告警 | 无 |
| S06 | 测试 | 单元测试、集成测试、E2E 测试 | S01-S04 |

---

## 如何使用

### 方式一：完整复制

1. 打开目标会话对应的 Prompt 文件（如 `S01_DATA_LAYER.md`）
2. 全选复制完整内容
3. 粘贴到 CodeFlicker 新会话的 System Prompt
4. 开始开发

### 方式二：增量补充

在已有会话中追加：

```markdown
## 额外上下文

以下是你需要开发的子任务：

[TASK_DESCRIPTION]

参考设计文档：
- DATABASE.md
- API.md
```

---

## Prompt 结构规范

每个 Prompt 包含以下章节：

1. **Role & Boundary** — 角色定义和职责边界
2. **Tech Stack** — 技术栈说明
3. **Directory & File Scope** — 代码目录和文件范围
4. **Deliverables** — 交付物清单（含具体实现要求）
5. **Constraints & Rules** — 约束和规则
6. **Cross-Module Interfaces** — 跨模块接口约定
7. **Quality Checklist** — 质量检查清单（可勾选）
8. **Handoff Template** — 交接模板（Session 完成时填写）
9. **Related Documents** — 相关文档链接

---

## 设计文档索引

所有设计文档存放在 `docs/lhb-quant-v2/` 目录：

| 文档 | 优先级 | 说明 |
|------|--------|------|
| ARCHITECTURE.md | P0 | 技术架构 |
| DATABASE.md | P0 | 数据库设计 |
| API.md | P0 | API 接口设计 |
| STRATEGY.md | P0 | 策略设计 |
| RISK_CONTROL.md | P0 | 风控体系 |
| TRADING_FLOW.md | P0 | 交易流程 |
| STATE_MACHINE.md | P1 | 状态机定义 |
| DATA_SOURCE.md | P1 | 数据源配置 |
| TESTING.md | P1 | 测试策略 |
| DEPLOYMENT.md | P1 | 部署方案 |
| RUNBOOK.md | P2 | 运维手册 |
