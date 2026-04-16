# 龙虎榜量化交易系统 v2 - 重构计划

> 基于多轮评审优化版本
> 评审日期: 2026-04-16

---

## 〇、评审采纳说明

> 二次评审修订日期: 2026-04-16

### 已采纳的改进项 ✅

| 评审问题 | 采纳的改进 |
|----------|------------|
| 开发阶段划分不合理（Phase 依赖倒置） | 重新划分：Phase 1 基础设施 → Phase 2 数据层 → Phase 3 API → Phase 4 调度 → Phase 5 前端 |
| 任务粒度不足以执行 | 所有任务拆分到 1-3 天可完成粒度，添加预估工期和交付物 |
| 关键依赖路径未识别 | 补充风险评估表、关键路径、里程碑定义 |
| 评审项与开发阶段未对齐 | 补充评审项落地对照表，标注每项在哪个 Phase 实现 |
| 熔断器参数矛盾 | 统一引用 DATA_SOURCE.md 定义，不在 PLAN 中重复 |
| 目录结构缺项 | 补充 middleware/、完整 tasks/ 列表、audit.py 等 |
| 缺少技术 POC 验证计划 | Phase 1 增加 POC 环节，验证关键技术选型 |
| 缺少验收标准 | 每个 Phase 补充量化验收标准 |
| 文档引用缺失 | 补充文档创建时机表，明确 P0 文档在对应 Phase 开始前完成 |

### 不采纳的建议及原因 ❌

| 评审建议 | 不采纳原因 |
|----------|------------|
| 团队分工建议 | 当前为个人项目，无需团队分工 |
| 复杂的甘特图 | 单人开发，简单时间线足够 |
| 引入 RedBeat 替代 Celery Beat | ARCHITECTURE.md 评审已决定不采用，Docker restart: always 已足够 |

---

## 一、项目背景

### 1.1 现有系统问题

```
┌─────────────────────────────────────────────────────┐
│ 1. data_fetcher.py 职责混乱 - 数据获取+业务逻辑耦合  │
│ 2. 降级逻辑散落各处 - 每个函数各自实现retry         │
│ 3. 数据源优先级硬编码 - 无法运行时动态调整           │
│ 4. 无统一的健康检查 - 不知道哪个数据源当前可用       │
│ 5. 缓存策略不一致 - 有些12s有些进程级别             │
│ 6. Streamlit 单体架构 - 前后端耦合，难以扩展         │
│ 7. 无 WebSocket 实时推送 - 盘中监控体验差           │
└─────────────────────────────────────────────────────┘
```

### 1.2 重构目标

- **技术升级**: Vue3 + FastAPI + PostgreSQL + Redis + Celery
- **架构解耦**: 前端/后端/数据层完全分离
- **可维护性**: 清晰的分层架构，模块可插拔
- **可扩展性**: 支持多策略、多机器人并行
- **稳定性**: 数据源降级链 + 熔断机制

---

## 二、设计决策

### 2.1 保留的内容

| 模块 | 说明 | 处理方式 |
|------|------|----------|
| `/api/` | 万卿 LLM 代理服务 | **保留**，仅本地执行，不在远程部署 |
| 核心业务逻辑 | LHB 选股 + 机器人交易 | **迁移**，重写实现 |

### 2.2 新建的内容

| 模块 | 技术栈 | 说明 |
|------|--------|------|
| 后端 | FastAPI + SQLAlchemy | REST API + WebSocket |
| 前端 | Vue3 + TypeScript + Vite | SPA 单页应用 |
| 数据库 | PostgreSQL + TimescaleDB | 业务数据 + 时序行情 |
| 缓存 | Redis | 热点缓存 + Celery Broker |
| 任务队列 | Celery Beat + Celery Workers | 统一调度，所有定时任务走 Celery |
| 数据源 | TuShare + AKShare + Sina | 降级链 + 熔断器 |

### 2.3 部署策略

- **本地开发**: Docker Compose（PostgreSQL + Redis + Backend + Frontend）
- **远程部署**: Docker Compose 直连服务器 PostgreSQL

> **注意**: 万卿代理 (`/api/`) 仅在本地开发环境运行，不会在远程服务器部署。

---

## 三、技术架构

### 3.1 架构图

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              Vue3 前端 (:3000)                               │
│   Vite + TypeScript + Ant Design Vue + Pinia + Vue Router                   │
│   LightweightCharts (K线) + ECharts (收益曲线) + AG-Grid (表格)              │
└───────────────────────────────┬─────────────────────────────────────────────┘
                                │ REST API + WebSocket
                                │
┌───────────────────────────────▼─────────────────────────────────────────────┐
│                         FastAPI 后端 (:8000)                                  │
│   REST API + WebSocket                                                     │
│                                                                              │
│   ┌──────────────────────────────────────────────────────────────────────┐  │
│   │  Services 层                                                          │  │
│   │  ├─ scorer.py           评分引擎 (技术40%+资金35%+历史25%)             │  │
│   │  ├─ robot_service.py    机器人 CRUD + 生命周期                        │  │
│   │  ├─ trade_service.py    交易执行 (Phase A/B) + 结算                   │  │
│   │  ├─ monitor_service.py   盘中监控 (止盈/止损/连板)                     │  │
│   │  └─ risk_service.py     AHS 计算 + 熔断检查                           │  │
│   └──────────────────────────────────────────────────────────────────────┘  │
│                                                                              │
│   ┌──────────────────────────────────────────────────────────────────────┐  │
│   │  Data Provider 降级链 (按数据类型分别配置)                             │  │
│   │  ├─ DAILY_KLINE:     TuShare → AKShare → Sina                       │  │
│   │  ├─ LHB_DETAIL:       AKShare → Sina → 东方财富                     │  │
│   │  └─ REALTIME_QUOTE:   AKShare → Sina → 缓存                         │  │
│   └──────────────────────────────────────────────────────────────────────┘  │
└───────────────────────────┬─────────────────────────────────────────────────┘
                            │
        ┌───────────────────┼───────────────────┐
        ▼                   ▼                   ▼
┌───────────────┐  ┌───────────────┐  ┌─────────────────────────┐
│   PostgreSQL  │  │     Redis     │  │    Celery Workers        │
│   (业务数据)   │  │   (缓存/锁)   │  │  (异步任务/盘中监控)    │
└───────────────┘  └───────────────┘  └─────────────────────────┘
```

### 3.2 调度架构

```
┌─────────────────────────────────────────────────────────────────┐
│                    Celery Beat (独立进程)                       │
│                                                                 │
│   ├─ cron: Phase A (18:50)       → queue: trading             │
│   ├─ cron: Phase B (09:35)       → queue: trading             │
│   ├─ cron: 盘中监控 (10s)        → queue: monitor (高优先级)  │
│   ├─ cron: 净值快照 (15:05)      → queue: daily               │
│   ├─ cron: 数据同步 (17:00)      → queue: daily               │
│   └─ cron: AHS 计算 (20:00)      → queue: daily               │
│                                                                 │
│   ※ 所有调度任务通过 Redis 持久化，进程重启不丢失               │
└─────────────────────────────────────────────────────────────────┘
```

**改进点**:
1. **Celery Beat 独立部署**: 不嵌入 FastAPI 进程
2. **独立监控队列**: 盘中监控使用独立队列，避免被慢任务阻塞
3. **分布式锁**: 所有关键任务加 Redis 锁，防止多 Worker 重复执行
4. **执行超时**: 盘中监控单次执行超时 8 秒强制结束

### 3.3 数据源降级链

> 详见 [DATA_SOURCE.md - 熔断器配置章节](./DATA_SOURCE.md#七熔断器配置)

摘要：
- 触发方式：连续失败次数（非比率，避免小样本误触发）
- 按数据类型区分恢复超时参数
- 具体参数以 DATA_SOURCE.md 为准

---

## 四、目录结构

```
stock_analysis/                          # Monorepo 根目录
├── api/                                 # ★ 保留：万卿代理（仅本地）
│   ├── server.py
│   └── README.md
│
├── backend/                             # ★ 新增：FastAPI 后端
│   ├── pyproject.toml                   # Python 依赖 (hatchling)
│   ├── Dockerfile
│   ├── app/
│   │   ├── main.py                     # FastAPI 入口
│   │   ├── config.py                   # Pydantic Settings
│   │   ├── deps.py                     # 依赖注入
│   │   ├── models/                     # SQLAlchemy ORM
│   │   │   ├── base.py                 # 基类 + Mixin
│   │   │   ├── market.py               # 龙虎榜 + K线 + 快照 + 情绪 + 股票信息
│   │   │   ├── robot.py                # 机器人 + 交易 + 执行 + 持仓 + 资金 + 净值 + AHS
│   │   │   ├── strategy.py             # 策略 + 参数
│   │   │   ├── audit.py                # 审计日志 + 交易事件 + 选股日志
│   │   │   └── system.py               # 应用配置 + 交易日历
│   │   ├── schemas/                    # Pydantic 模型
│   │   │   ├── common.py               # 分页请求/响应、排序参数、通用响应
│   │   │   ├── market.py               # 市场数据
│   │   │   ├── robot.py                # 创建/更新/列表/详情
│   │   │   └── trade.py                # 交易列表/手动买卖/平仓
│   │   ├── api/                        # 路由层
│   │   │   ├── v1/
│   │   │   │   ├── router.py           # 路由汇总
│   │   │   │   ├── market.py           # /market 端点
│   │   │   │   ├── robots.py           # /robots 端点
│   │   │   │   ├── strategies.py       # /strategies 端点
│   │   │   │   ├── backtest.py         # /backtest 端点
│   │   │   │   ├── selection.py        # /selection 端点
│   │   │   │   ├── dashboard.py        # /dashboard 端点
│   │   │   │   └── system.py           # /system 端点
│   │   │   └── ws/
│   │   │       └── connection.py       # WebSocket 管理
│   │   ├── services/                   # 业务逻辑层
│   │   │   ├── scorer.py               # 评分引擎
│   │   │   ├── robot_service.py        # 机器人服务
│   │   │   ├── trade_service.py        # 交易服务
│   │   │   ├── monitor_service.py      # 监控服务
│   │   │   ├── risk_service.py         # 风控服务
│   │   │   ├── selection_service.py    # 选股服务
│   │   │   ├── llm_selector.py         # LLM 选股增强（可选）
│   │   │   └── notification.py         # 飞书通知
│   │   ├── data_providers/             # 数据源降级链
│   │   │   ├── base.py                 # 抽象基类
│   │   │   ├── tushare.py              # TuShare 实现
│   │   │   ├── akshare.py              # AKShare 实现
│   │   │   ├── sina.py                 # Sina 实现
│   │   │   ├── eastmoney.py            # 东方财富实现（可选）
│   │   │   ├── manager.py              # 降级链管理器
│   │   │   ├── circuit_breaker.py      # 熔断器
│   │   │   ├── cache_manager.py        # 缓存管理
│   │   │   ├── rate_limiter.py         # 限流器
│   │   │   └── validator.py            # 数据校验器
│   │   ├── tasks/                      # Celery 任务
│   │   │   ├── celery_app.py           # Celery 实例 + 队列配置
│   │   │   ├── trading_task.py        # Phase A/B + 买卖执行
│   │   │   ├── monitor_task.py         # 盘中监控
│   │   │   ├── settlement_task.py      # 日结算 + 净值快照 + AHS 计算
│   │   │   ├── sync_task.py            # 数据同步（龙虎榜/K线/日历）
│   │   │   ├── backtest_task.py        # 回测
│   │   │   ├── health_task.py          # 健康检查 + 资金对账
│   │   │   └── notification_task.py    # 飞书通知
│   │   ├── strategies/                 # 策略插件
│   │   │   ├── base.py                 # 抽象基类
│   │   │   ├── lhb_multifactor.py      # 龙虎榜多因子
│   │   │   └── registry.py             # 策略注册表
│   │   ├── middleware/                 # 中间件
│   │   │   ├── request_id.py           # 请求ID注入
│   │   │   ├── error_handler.py        # 全局异常处理
│   │   │   ├── access_log.py           # 访问日志
│   │   │   └── rate_limit.py           # 限流（预留）
│   │   ├── core/                       # 核心工具
│   │   │   ├── config.py               # Pydantic Settings
│   │   │   ├── constants.py            # 业务常量（状态枚举等）
│   │   │   ├── events.py               # 应用生命周期事件
│   │   │   ├── exceptions.py           # 自定义异常体系
│   │   │   ├── logging.py              # 日志配置
│   │   │   ├── redis_client.py         # Redis 客户端
│   │   │   └── security.py             # 安全配置（预留）
│   │   └── db/                         # 数据库连接
│   │       ├── session.py              # Session 工厂
│   │       ├── async_session.py        # 异步会话（FastAPI）
│   │       └── sync_session.py         # 同步会话（Celery）
│   └── tests/                          # 单元测试
│       ├── conftest.py
│       ├── test_scorer.py
│       ├── test_trade_service.py
│       └── test_data_providers.py
│
├── frontend/                            # ★ 新增：Vue3 前端
│   ├── Dockerfile
│   ├── package.json
│   ├── vite.config.ts
│   └── src/
│       ├── api/                     # API 调用封装
│       ├── stores/                   # Pinia 状态
│       ├── views/                   # 页面组件
│       ├── components/              # 可复用组件
│       └── composables/              # 组合式函数
│
├── scripts/                             # 运维脚本
│   ├── init_db.sql                  # 数据库初始化
│   ├── migrate_data.py              # 数据迁移
│   └── seed_data.py                 # 测试数据
│
├── docs/                               # 开发文档
│   └── lhb-quant-v2/
│       ├── README.md
│       ├── PLAN.md
│       ├── PROGRESS.md
│       ├── ARCHITECTURE.md
│       ├── DATABASE.md
│       ├── API.md
│       ├── DEPLOYMENT.md
│       ├── DATA_SOURCE.md
│       ├── STRATEGY.md              # P0
│       ├── RISK_CONTROL.md          # P0
│       └── TRADING_FLOW.md          # P0
│
├── docker-compose.yml                   # Docker Compose 配置
├── Makefile                             # 常用命令
├── .env.example                        # 环境变量模板
└── README.md
```

---

## 五、数据库设计

> 详见 [DATABASE.md](./DATABASE.md)

---

## 六、开发阶段

### Phase 1: 基础设施（预估 2 周）

**目标**: 项目骨架可运行，数据库可用，关键技术验证通过

**前置依赖**: 无

**任务列表**:

| 任务 | 预估 | 前置 | 交付物 |
|------|------|------|--------|
| Monorepo 目录结构 + Git 初始化 | 1天 | 无 | 目录结构完成 |
| Docker Compose 配置（PG + Redis + Backend） | 2天 | 目录结构 | docker compose up 成功 |
| .env.example + Makefile | 0.5天 | 目录结构 | 配置模板可用 |
| 数据库 DDL 执行脚本 | 2天 | Docker配置 | 所有表创建成功 |
| FastAPI 项目初始化（main + config + health） | 1天 | Docker配置 | /health 返回 200 |
| SQLAlchemy Session 管理配置 | 1天 | FastAPI初始化 | deps.py 可注入 Session |

**技术 POC（可并行，3-5天）**:

| POC 项 | 验证方法 | 通过标准 |
|--------|----------|----------|
| Celery Beat 10秒精度 | 编写测试任务，统计100次间隔 | 95%在8-12秒范围 |
| TimescaleDB 压缩效果 | 导入1个月K线数据，比较压缩前后 | 体积<原始30% |
| AKShare 龙虎榜接口稳定性 | 连续7天调用，记录成功率 | 成功率>95% |
| WebSocket 并发表现 | 模拟50连接，持续推送10分钟 | 内存增长<100MB |
| Redis 分布式锁可靠性 | 模拟锁过期场景 | 0次重复执行 |

**验收标准**:
1. Docker 容器全部 running（PG/Redis/Backend）
2. 数据库 21 张表全部创建成功
3. TimescaleDB 超表可查询
4. /health 返回数据库和 Redis 连接状态
5. POC 5 项全部通过或有明确备选方案

**里程碑**: M1 - 基础设施就绪

**风险**:
- TimescaleDB 配置踩坑 → 先用标准 PG，特性延后启用
- Celery 精度不足 → 预留独立 asyncio 进程方案

---

### Phase 2: 数据层（预估 2-3 周）

**目标**: 数据源可用，降级链可切换，DB 中有历史数据

**前置依赖**: Phase 1 完成

**任务列表**:

| 任务 | 预估 | 前置 | 交付物 |
|------|------|------|--------|
| 数据源抽象基类（base.py） | 1天 | Phase 1 | Provider 基类 |
| TuShare 适配器 | 2天 | 基类 | tushare.py |
| AKShare 适配器 | 2天 | 基类 | akshare.py |
| Sina 适配器 | 1天 | 基类 | sina.py |
| 东方财富适配器（可选） | 1天 | 基类 | eastmoney.py |
| 降级链管理器（manager.py） | 2天 | 所有适配器 | 自动切换逻辑 |
| 熔断器（circuit_breaker.py） | 1天 | 管理器 | 熔断逻辑 |
| 限流器（rate_limiter.py） | 1天 | 管理器 | 令牌桶实现 |
| 数据校验器（validator.py） | 1天 | 管理器 | 完整校验规则 |
| Redis 缓存管理（cache_manager.py） | 1天 | 管理器 | 缓存读写 |
| 数据规范化实现 | 1天 | 所有适配器 | 统一格式转换 |
| 交易日历导入脚本 | 0.5天 | Phase 1 | trade_calendar 表填充 |
| 历史龙虎榜数据回填（近3个月） | 2天 | 数据源可用 | lhb_stock_daily 表 |
| 历史 K 线数据回填（近3个月） | 2天 | 数据源可用 | kline_daily 表 |
| 健康检查接口（/system/data-health） | 1天 | 所有组件 | 数据完整性报告 |

**验收标准**:
1. 所有数据源可通过切换配置选择
2. 熔断器在连续失败后正确触发
3. 限流器在请求过快时正确等待
4. 数据校验器能正确识别异常数据
5. DB 中有近 3 个月完整历史数据

**里程碑**: M2 - 数据层就绪

**风险**:
| 风险 | 概率 | 影响 | 缓解策略 |
|------|------|------|----------|
| AKShare 接口变更 | 中 | 高 | 提前测试所有接口，编写集成测试 |
| 数据量超预期 | 低 | 中 | 先回填 1 个月，验证性能后扩展 |

---

### Phase 3: 核心 API + 业务服务（预估 3-4 周）

**目标**: 后端功能完整，所有 API 可通过 Swagger 测试

**前置依赖**: Phase 2 完成

**任务列表**:

#### 3.1 ORM 模型（共 3-4 天）

| 任务 | 预估 |
|------|------|
| base.py: Base类 + TimestampMixin + SoftDeleteMixin | 0.5天 |
| robot.py: Robot + RobotTrade + TradeExecution + RobotPosition | 1天 |
| robot.py: CashLedger + DailyNav + WeeklySummary + AhsHistory | 1天 |
| market.py: LhbStockDaily + LhbSeat + LhbTnPrice | 0.5天 |
| market.py: KlineDaily + MarketSnapshot + MarketSentiment + StockInfo | 0.5天 |
| audit.py: AuditLog + TradeEvent + SelectionLog | 0.5天 |
| system.py: AppConfig + TradeCalendar | 0.5天 |

#### 3.2 Pydantic Schema（共 2 天）

| 任务 | 预估 |
|------|------|
| common.py: 分页请求/响应、排序参数 | 0.5天 |
| robot.py: 创建/更新/列表/详情 Schema | 0.5天 |
| trade.py: 交易列表/手动买卖/平仓 Schema | 0.5天 |
| market.py: 龙虎榜/K线/情绪 Schema | 0.5天 |

#### 3.3 中间件与异常处理（共 1.5 天）

| 任务 | 预估 |
|------|------|
| request_id.py: 请求ID注入 | 0.5天 |
| error_handler.py: 全局异常处理 | 0.5天 |
| access_log.py: 访问日志 | 0.5天 |

#### 3.4 市场数据 API（共 3-4 天）

| 任务 | 预估 |
|------|------|
| GET /market/lhb/daily | 1天 |
| GET /market/lhb/seats | 0.5天 |
| GET /market/lhb/tn-prices | 0.5天 |
| GET /market/kline/daily | 0.5天 |
| GET /market/sentiment | 0.5天 |

#### 3.5 机器人 API（共 3-4 天）

| 任务 | 预估 |
|------|------|
| GET/POST /robots | 1天 |
| GET/PUT/DELETE /robots/{id} | 1天 |
| GET /robots/{id}/positions | 0.5天 |
| GET /robots/{id}/trades | 0.5天 |
| GET /robots/{id}/nav | 0.5天 |
| POST /robots/batch | 0.5天 |

#### 3.6 交易 API（共 2 天）

| 任务 | 预估 |
|------|------|
| POST /robots/{id}/trades/manual-buy | 0.5天 |
| POST /robots/{id}/trades/{tid}/manual-sell | 0.5天 |
| POST /robots/{id}/actions/close-all | 0.5天 |
| WebSocket /ws/realtime | 0.5天 |

#### 3.7 其他 API（共 3 天）

| 任务 | 预估 |
|------|------|
| GET/PUT /strategies/{name}/params | 0.5天 |
| GET /selection/daily | 0.5天 |
| GET /aggregations/robots-summary | 0.5天 |
| GET /aggregations/today-trades | 0.5天 |
| GET /system/config | 0.5天 |
| GET /system/data-health | 0.5天 |

**验收标准**:
1. Swagger 文档可用，所有端点有描述
2. 所有 API 可通过 curl 测试
3. 输入校验严格，返回正确的 HTTP 状态码
4. WebSocket 可连接并接收推送
5. 异常处理器正确返回 errors 数组

**里程碑**: M3 - API 层就绪

---

### Phase 4: 任务调度 + 交易引擎（预估 3-4 周）

**目标**: 自动化交易闭环，模拟盘可运行完整交易日

**前置依赖**: Phase 3 完成

**任务列表**:

| 任务 | 预估 | 前置 | 交付物 |
|------|------|------|--------|
| Celery 应用配置（celery_app.py） | 1天 | Phase 3 | 队列配置完成 |
| Phase A 选股任务（trading_task.py） | 2天 | Celery配置 | 评分 + 选股 + 持久化 |
| Phase B 买入确认任务（trading_task.py） | 2天 | Phase A任务 | 前置检查 + 执行买入 |
| 盘中监控任务（monitor_task.py） | 3天 | Phase B任务 | 止盈/止损/连板检查 |
| 日结算任务（settlement_task.py） | 2天 | 监控任务 | 净值快照 + 卖出确认 |
| AHS 风控计算任务（settlement_task.py） | 1天 | 结算任务 | AHS 评分持久化 |
| 飞书通知任务（notification_task.py） | 1天 | 所有任务 | 告警推送 |
| 健康检查任务（health_task.py） | 1天 | 结算任务 | 资金对账报告 |

**验收标准**:
1. 模拟盘完整运行一个交易日
2. 所有任务按时间触发
3. Phase A 选股结果正确持久化
4. Phase B 正确执行买入和跳过
5. 止盈/止损正确触发
6. 飞书通知正确发送

**里程碑**: M4 - 交易闭环就绪

**风险**:
| 风险 | 概率 | 影响 | 缓解策略 |
|------|------|------|----------|
| Celery 精度不足 | 低 | 高 | Phase 1 已验证，备选方案已预留 |
| 并发交易重复执行 | 中 | 高 | 幂等键 + SELECT FOR UPDATE |

---

### Phase 5: 前端 + 集成测试（预估 3-4 周）

**目标**: 完整可用的系统，模拟盘稳定运行

**前置依赖**: Phase 4 完成

**任务列表**:

| 任务 | 预估 | 前置 | 交付物 |
|------|------|------|--------|
| Vue3 项目初始化 | 1天 | Phase 4 | Vite + TS 配置 |
| 路由与状态管理配置 | 1天 | 项目初始化 | Pinia + Router |
| Dashboard 页面 | 3天 | 路由配置 | 收益曲线 + 持仓 |
| 机器人管理页面 | 2天 | Dashboard | CRUD + 配置 |
| 龙虎榜信号页面 | 2天 | Dashboard | 列表 + 详情 |
| 实时监控页面 | 3天 | WebSocket API | 实时盈亏推送 |
| 系统管理页面 | 1天 | API 完成 | 配置 + 日志 |
| 集成测试 | 3天 | 所有页面 | E2E 测试 |
| 模拟盘验证（5+交易日） | 5天 | 集成测试 | 稳定性报告 |

**验收标准**:
1. 前后端联调完成
2. 所有页面功能正常
3. WebSocket 推送无断开
4. 模拟盘稳定运行 5+ 交易日
5. 无 P0/P1 缺陷

**里程碑**: M5 - 系统可用

---

## 七、核心模块设计

### 7.1 数据源降级链

> 详见 [DATA_SOURCE.md - 降级策略章节](./DATA_SOURCE.md#六降级策略)

### 7.2 评分引擎

| 维度 | 权重 | 评分因子 |
|------|------|----------|
| 技术面 | 40% | 涨跌幅、换手率、龙虎榜次数、席位集中度 |
| 资金面 | 35% | 净买入额、机构买入占比、游资类型 |
| 历史面 | 25% | T+1/T+2 胜率、历史最大涨幅 |

### 7.3 交易执行

```
Phase A (18:50) → 选股 + 模拟买入 → 持久化到 DB
    ↓ 跨夜检查
Phase B (09:35) → 前置检查(停牌/ST/偏离度) → 确认买入
    ↓
盘中监控 (10s) → 止盈/止损/连板检查 → 加锁执行卖出
    ↓
结算 (15:05) → 净值快照 + 卖出确认
```

**Phase B 前置检查**:
1. 停牌检测
2. ST 检测
3. 开盘价偏离度检查（>N% 则放弃）

### 7.4 AHS 风险控制

| AHS 区间 | 仓位 | 行为 |
|----------|------|------|
| 0-20 | 100% | 正常交易 |
| 21-40 | 85% | 适度谨慎 |
| 41-60 | 70% | 保守交易 |
| 61-80 | 40% | 警戒模式 |
| 81-100 | 停止开仓 | 只平不清 |

### 7.5 系统级硬性限制

> 独立于策略参数，不可被覆盖

| 限制项 | 阈值 | 说明 |
|--------|------|------|
| 单笔买入上限 | 总资金 20% | 防止单次重仓 |
| 单日交易总额上限 | 总资金 50% | 防止日内过度交易 |
| 累计亏损停机 | 20% | 自动停止所有开仓 |
| 单只持仓集中度 | 30% | 防止过度集中 |

### 7.6 LLM 选股降级策略

| 场景 | 处理方式 |
|------|----------|
| LLM 超时（>10秒） | 直接走规则降级，不等待 |
| LLM 返回格式错误 | schema 校验失败，走规则降级 |
| LLM 降级率 > 30%/周 | 触发告警通知 |
| 规则降级结果 | 同样经过评分和风控流程 |

### 7.7 机器人淘汰机制

| 规则 | 说明 |
|------|------|
| 最低观察期 | 20 个交易日，不满不参与淘汰 |
| 样本量置信度 | 交易次数少的评分加不确定性惩罚 |
| 淘汰前平仓 | 必须先平掉所有持仓 |
| 淘汰后恢复 | 支持手动恢复，重置观察期 |

### 7.8 模拟盘阶段

| 状态 | 说明 |
|------|------|
| paper_trading | 模拟盘，全流程运行但不实际下单 |
| 准入实盘标准 | 胜率 > 50%、最大回撤 < 10%、Sharpe > 1.0 |
| 最短运行期 | 20 个交易日 |
| 数据隔离 | 模拟盘与实盘数据完全隔离 |

---

## 八、评审优先级处理

### 8.1 评审项落地对照表

#### P0 评审项

| 评审项 | 落地阶段 | 对应任务 | 验收标准 |
|--------|----------|----------|----------|
| Celery Beat 独立部署 | Phase 1 | Docker Compose 配置 | beat 作为独立容器运行 |
| 盘中监控超时容错 | Phase 4 | monitor_task.py | 单次超时 8 秒自动终止 |
| Phase A/B 状态持久化 | Phase 4 | trading_task.py | 重启后能恢复执行 |
| 资金流水事务一致性 | Phase 3 | trade_service.py | 买入/卖出在单一事务内 |
| 系统级硬性限制 | Phase 4 | risk_service.py | 超限时拒绝并记录日志 |

#### P1 评审项

| 评审项 | 落地阶段 | 对应任务 | 验收标准 |
|--------|----------|----------|----------|
| 数据源按类型降级 | Phase 2 | manager.py | 每种数据类型独立配置 |
| 分布式锁防并发 | Phase 4 | trading_task.py | Redis 锁 + 幂等键 |
| WebSocket 协议完善 | Phase 3 | connection.py | 心跳 + 序列号 |
| 数据校验完整 | Phase 2 | validator.py | K线/龙虎榜/席位全覆盖 |
| LLM 降级策略 | Phase 4 | llm_selector.py | 超时 10 秒走规则降级 |

---

## 九、参考文档

### 9.1 设计文档

| 文档 | 说明 |
|------|------|
| [README](./README.md) | 文档总览 |
| [重构计划](./PLAN.md) | 本文档 |
| [开发进度](./PROGRESS.md) | 任务追踪 |
| [架构设计](./ARCHITECTURE.md) | 技术架构 |
| [数据库设计](./DATABASE.md) | 数据库 schema |
| [API 接口](./API.md) | 接口规范 |
| [部署指南](./DEPLOYMENT.md) | Docker 部署 |
| [数据源配置](./DATA_SOURCE.md) | 数据源配置 |

### 9.2 核心设计文档（P0）

| 文档 | 创建时机 | 前置依赖 | 说明 |
|------|----------|----------|------|
| [策略设计](./STRATEGY.md) | Phase 2 开始前 | DATABASE.md 定稿 | 评分公式/因子定义 |
| [风控体系](./RISK_CONTROL.md) | Phase 2 开始前 | STRATEGY.md | AHS/熔断/硬限制 |
| [交易流程](./TRADING_FLOW.md) | Phase 4 开始前 | API.md + STRATEGY.md | Phase A/B/监控/结算 |

### 9.3 辅助设计文档（P1）

| 文档 | 创建时机 | 说明 |
|------|----------|------|
| [状态机](./STATE_MACHINE.md) | Phase 3 ORM 开发时 | 交易状态转换定义 |
| [测试策略](./TESTING.md) | Phase 1 完成后 | 测试分层与用例 |

### 9.4 运维文档（P2）

| 文档 | 创建时机 | 说明 |
|------|----------|------|
| [运维手册](./RUNBOOK.md) | Phase 5 完成后 | 日常巡检与故障排查 |

### 9.5 阅读顺序

```
首次了解: README → ARCHITECTURE.md → DATABASE.md → API.md
开始开发: PLAN.md → PROGRESS.md → DEPLOYMENT.md
架构决策: ARCHITECTURE.md → STRATEGY.md → RISK_CONTROL.md
运维部署: DEPLOYMENT.md → TRADING_FLOW.md → RUNBOOK.md
```

---

## 十、风险评估与关键路径

### 10.1 技术风险评估

| 风险 | 概率 | 影响 | 阶段 | 缓解策略 |
|------|------|------|------|----------|
| AKShare 接口变更 | 中 | 高 | Phase 2 | 提前测试所有接口，编写集成测试 |
| TimescaleDB 配置踩坑 | 中 | 中 | Phase 1 | 先用标准 PG，特性延后启用 |
| Celery 监控精度不足 | 低 | 高 | Phase 4 | Phase 1 POC 已验证，备选独立进程方案 |
| WebSocket 联调问题 | 中 | 中 | Phase 5 | Phase 3 即搭建最小原型，前后端同步验证 |
| 数据量超预期 | 低 | 中 | Phase 2 | 先回填 1 个月数据，验证性能后扩展 |

### 10.2 关键路径

```
Phase 1 (基础设施)
    │
    ▼
Phase 2 (数据层)
    │
    ├──────────────────┐
    ▼                  ▼
Phase 3 (API)    Phase 4 (调度) ← 可并行后半段
    │                  │
    └──────────────────┘
              │
              ▼
       Phase 5 (前端)
```

### 10.3 里程碑

| 里程碑 | 目标日期 | 验收标准 |
|--------|----------|----------|
| M1 | Phase 1 完成 | DB 可用，服务可启动 |
| M2 | Phase 2 完成 | 有历史数据，数据源降级链可用 |
| M3 | Phase 3 完成 | API 可通过 Swagger 完整测试 |
| M4 | Phase 4 完成 | 模拟盘可运行完整交易日 |
| M5 | Phase 5 完成 | 全系统可用，模拟盘稳定运行 5 天+ |

---

*文档版本: 2.1*
*最后修订: 2026-04-16*
