# 龙虎榜量化交易系统 v2

> 基于龙虎榜数据的量化选股与交易系统

---

## 📌 项目简介

本系统是基于 A 股龙虎榜数据的量化选股与交易系统，通过量化评分筛选高胜率标的，实现自动化的模拟交易/实盘交易。

**核心能力**:
1. **龙虎榜数据采集** - 多数据源降级链（TuShare → AKShare → Sina）
2. **智能选股** - 多因子评分（技术面 40% + 资金面 35% + 历史面 25%）
3. **自动交易** - Phase A/B 两阶段执行 + 盘中实时监控
4. **风险控制** - AHS 仓位管理 + 系统级硬性限制
5. **实时监控** - WebSocket 推送 + Dashboard 可视化

---

## 🏗️ 架构简图

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              Vue3 前端 (:3000)                             │
│                      实时行情 / 机器人管理 / 龙虎榜信号                      │
└───────────────────────────────┬─────────────────────────────────────────────┘
                                │ REST API + WebSocket
                                ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         FastAPI 后端 (:8000)                                │
│     Services: 评分引擎 | 交易执行 | 盘中监控 | AHS风控 | LLM选股            │
│     Scheduler: Celery Beat (Phase A/B/监控)                                 │
└───────────────────────────┬─────────────────────────────────────────────────┘
                            │
        ┌───────────────────┼───────────────────┐
        ▼                   ▼                   ▼
┌───────────────┐  ┌───────────────┐  ┌─────────────────────────┐
│   PostgreSQL  │  │     Redis     │  │    Celery Workers        │
│   (业务数据)   │  │   (缓存/锁)   │  │  (异步任务/盘中监控)    │
└───────────────┘  └───────────────┘  └─────────────────────────┘
```

---

## 🚀 快速启动

### 1. 克隆项目

```bash
cd /path/to/stock_analysis
```

### 2. 配置环境变量

```bash
cp .env.example .env
# 编辑 .env 配置数据库和 Redis 连接
```

### 3. 启动服务（Docker Compose）

```bash
docker compose up -d
```

### 4. 访问系统

- 前端: http://localhost:3000
- API 文档: http://localhost:8000/docs
- 飞书通知: 配置 `FEISHU_WEBHOOK_URL`

---

## 📚 文档体系

### 设计文档

| 文档 | 说明 | 优先级 |
|------|------|--------|
| [PLAN.md](./PLAN.md) | 重构计划与设计决策 | 必读 |
| [ARCHITECTURE.md](./ARCHITECTURE.md) | 技术架构设计 | 必读 |
| [DATABASE.md](./DATABASE.md) | 数据库 schema + DDL | 参考 |
| [API.md](./API.md) | API 接口规范 | 开发时 |
| [DEPLOYMENT.md](./DEPLOYMENT.md) | Docker 部署指南 | 部署时 |

### 核心设计文档

| 文档 | 说明 | 优先级 |
|------|------|--------|
| [STRATEGY.md](./STRATEGY.md) | 评分公式 + 因子定义 | **P0** |
| [RISK_CONTROL.md](./RISK_CONTROL.md) | AHS + 熔断 + 硬限制 | **P0** |
| [TRADING_FLOW.md](./TRADING_FLOW.md) | Phase A/B/监控/结算流程 | **P0** |
| [STATE_MACHINE.md](./STATE_MACHINE.md) | 状态转换定义 | **P1** |

### 测试与运维

| 文档 | 说明 | 优先级 |
|------|------|--------|
| [TESTING.md](./TESTING.md) | 测试策略与用例 | **P1** |
| [RUNBOOK.md](./RUNBOOK.md) | 运维手册 | **P2** |

### Agent 辅助开发

| 文档 | 说明 |
|------|------|
| [DEVELOPMENT_GUIDE.md](../DEVELOPMENT_GUIDE.md) | Agent 辅助开发工作流指南（人类开发者阅读） |
| [prompts/S01_DATA_LAYER.md](../prompts/S01_DATA_LAYER.md) | S01: 数据层开发 Prompt |
| [prompts/S02_STRATEGY_TRADING.md](../prompts/S02_STRATEGY_TRADING.md) | S02: 策略与交易引擎 Prompt |
| [prompts/S03_API_LAYER.md](../prompts/S03_API_LAYER.md) | S03: API 层开发 Prompt |
| [prompts/S04_FRONTEND.md](../prompts/S04_FRONTEND.md) | S04: 前端开发 Prompt |
| [prompts/S05_INFRASTRUCTURE.md](../prompts/S05_INFRASTRUCTURE.md) | S05: 基础设施 Prompt |
| [prompts/S06_TESTING.md](../prompts/S06_TESTING.md) | S06: 测试 Prompt |

### 评审报告

| 文档 | 说明 |
|------|------|
| [架构评审](./ARCHITECTURE_REVIEW.md) | P0-P3 架构问题 |
| [业务逻辑评审](./BUSINESS_LOGIC_REVIEW.md) | 业务逻辑问题 |
| [补充评审](./SUPPLEMENTARY_REVIEW.md) | 数学/A股/文档问题 |

---

## 📖 推荐阅读顺序

```
首次了解: README → ARCHITECTURE.md → STRATEGY.md → RISK_CONTROL.md
开始开发: PLAN.md → PROGRESS.md → API.md → DATABASE.md
架构决策: ARCHITECTURE_REVIEW.md → STRATEGY.md → TRADING_FLOW.md
运维部署: DEPLOYMENT.md → TRADING_FLOW.md
```

---

## 🔧 技术栈

| 层级 | 技术 | 版本 |
|------|------|------|
| 前端 | Vue3 + TypeScript + Vite | ^3.4 |
| 后端 | FastAPI + SQLAlchemy | ^0.110 |
| 数据库 | PostgreSQL + TimescaleDB | 15 |
| 缓存 | Redis | 7 |
| 任务队列 | Celery + Celery Beat | ^5.3 |
| 数据源 | AKShare + TuShare | 最新 |

---

## ⚠️ 已知限制

| 问题 | 严重度 | 计划解决 |
|------|--------|----------|
| Phase A/B 跨夜状态一致性 | P0 | Phase 5 |
| APScheduler 嵌入式风险 | P0 | Phase 5 |
| LLM 调用无费用控制 | P1 | 待评估 |
| 无回测与实盘对比机制 | P2 | Phase 5 |
| 策略过拟合未验证 | P1 | 上线后持续跟踪 |

---

## 📊 开发进度

| Phase | 内容 | 状态 |
|-------|------|------|
| Phase 1 | 项目结构与数据库设计 | 🔄 进行中 (25%) |
| Phase 2 | 核心 API 开发 | ⏳ 待开始 |
| Phase 3 | 前端 UI 开发 | ⏳ 待开始 |
| Phase 4 | 数据源接入 | ⏳ 待开始 |
| Phase 5 | 机器人交易 | ⏳ 待开始 |

详见 [PROGRESS.md](./PROGRESS.md)

---

## 📝 贡献指南

1. 遵循文档命名规范 (`*.md`)
2. 关键数值变更需同步相关文档
3. API 变更需更新 API.md + 代码注释
4. 提交前运行 `make lint`

---

## 🔗 相关链接

- [项目 Wiki](../../PROJECT_OVERVIEW.md)
- [架构文档](../../ARCHITECTURE.md)
- [技术栈](../../TECH_STACK_AND_PREFERENCES.md)
