# 龙虎榜量化交易系统 v2 - 架构设计

> 本文档定义系统的技术架构、组件选型、数据流和部署拓扑。

## 七、可靠性设计

### 7.1 定时任务故障恢复机制

| 任务 | 失败影响 | 恢复策略 | 最大容忍延迟 |
|------|----------|----------|-------------|
| Phase A (14:50) | 无选股标的 | 15:00 补偿检查 | 10分钟 |
| Phase B (09:15) | 无法建仓 | 09:25 补偿检查 | 10分钟 |
| 盘中监控 (每10秒) | 错过止损 | Worker 自恢复 + restart:always | 30秒 |
| 日结算 (15:30) | 净值未更新 | 次日凌晨补算 | 12小时 |
| 数据采集 (17:00) | 数据缺失 | 次日凌晨补采 | 12小时 |

#### Celery Beat 故障保护

```yaml
celery_beat:
  restart: always
  healthcheck:
    test: ["CMD-SHELL", "celery -A app.celery_app inspect scheduled"]
    interval: 30s
    timeout: 10s
    retries: 3
```

### 7.2 Redis 使用规范

#### 缓存策略

| 数据 | Key 格式 | TTL | 失效策略 | 降级方案 |
|------|----------|-----|----------|----------|
| 实时行情 | quote:{stock_code} | 12秒 | 自然过期 | 直接请求 AKShare |
| 机器人状态 | robot:{id}:status | 60秒 | Write-through | 查询数据库 |
| 当日选股结果 | selection:{date}:{robot_id} | 24小时 | 手动失效 | 查询数据库 |
| 幂等键 | idempotency:{key} | 24小时 | 自然过期 | - |

#### 分布式锁

| 锁 | Key 格式 | 超时 | 用途 |
|----|----------|------|------|
| 交易执行锁 | lock:trade:{robot_id} | 30秒 | 防止同一机器人并发交易 |
| 选股任务锁 | lock:selection:{robot_id}:{date} | 5分钟 | 防止重复选股 |
| 结算锁 | lock:settlement:{date} | 10分钟 | 防止重复结算 |

#### Redis 宕机降级

- 缓存读取失败 → 直接查询数据库（自动降级）
- 分布式锁获取失败 → 使用数据库行锁（SELECT FOR UPDATE）
- 所有 Redis 操作包裹 try-except，不阻塞核心交易流程

#### 一致性原则

- **行情缓存**：仅用于展示，交易决策容忍12秒延迟
- **交易状态**：以数据库为准，Redis 仅加速读取
- **写入顺序**：先写数据库，成功后写 Redis（Redis 写入失败不回滚 DB）

### 7.3 任务可靠性设计

#### 幂等性

| 任务 | 幂等键 | 重复执行行为 |
|------|--------|-------------|
| Phase A | (robot_id, trade_date) | 返回已有结果 |
| Phase B | (trade_id) | 跳过已完成的买入 |
| 卖出 | (trade_id, sell_type) | 跳过已完成的卖出 |

#### 事务边界

- 单笔交易的所有 DB 写入在同一个数据库事务中
- 不跨 Celery 任务做分布式事务
- 每个任务内部 all-or-nothing（全部成功或全部回滚）

#### 补偿检查

| 检查时间 | 检查内容 | 修复动作 |
|----------|----------|----------|
| 09:30 | Phase B 是否执行完毕 | 补执行未完成的买入 |
| 15:05 | 所有到期持仓是否已卖出 | 补执行到期卖出 |
| 15:30 | 资金账是否平衡 | 告警 + 人工介入 |
| 每日凌晨 | robot.current_cash 与 cash_ledger 对账 | 自动修正或告警 |

#### 死信队列

- 重试3次仍失败的任务进入死信队列
- 触发飞书告警，附带任务参数和错误信息
- 运维人员手动处理后标记完成

---

## 八、日志架构

### 8.1 日志分级与输出

| 级别 | 用途 | 输出目标 | 保留期 |
|------|------|----------|--------|
| ERROR | 异常、失败 | 文件 + 飞书通知 | 90天 |
| WARNING | 降级、超时、接近阈值 | 文件 | 30天 |
| INFO | 交易执行、任务完成、状态变更 | 文件 | 14天 |
| DEBUG | 评分细节、SQL、API 调用 | 文件（仅开发环境） | 3天 |

### 8.2 日志格式（JSON 结构化）

```json
{
  "timestamp": "2026-04-16T10:50:00.123+08:00",
  "level": "INFO",
  "module": "trade_executor",
  "robot_id": 1,
  "trade_id": 101,
  "action": "buy_executed",
  "stock_code": "000001",
  "price": 12.5,
  "shares": 1000,
  "request_id": "req_abc123",
  "duration_ms": 150
}
```

### 8.3 日志文件轮转

```
logs/
├── app.log              # 当前日志
├── app.2026-04-15.log   # 按天轮转
├── error.log            # 错误日志（独立文件）
├── trade.log            # 交易专用日志（保留更久）
└── celery/
    ├── worker.log
    └── beat.log
```

### 8.4 关键约束

- 交易相关日志必须包含 robot_id + trade_id
- API 请求日志必须包含 request_id
- 所有日志使用 UTC+8 时间戳
- 生产环境 INFO 级别，不输出 DEBUG
- 敏感数据（密码、token）禁止出现在日志中

---

## 九、备份与恢复策略

### 9.1 备份方案

| 数据 | 备份方式 | 频率 | 保留期 | 存储位置 |
|------|----------|------|--------|----------|
| PostgreSQL 全量 | pg_dump | 每日凌晨2:00 | 30天 | 本地 + 远程对象存储 |
| PostgreSQL WAL | 连续归档 | 实时 | 7天 | 本地 /backup/wal/ |
| Redis RDB | BGSAVE | 每小时 | 3天 | 本地 |
| 配置文件 | Git | 每次变更 | 永久 | Git 仓库 |
| 日志文件 | rsync | 每日 | 按日志保留期 | 远程存储 |

### 9.2 恢复 RTO/RPO 目标

| 场景 | RPO（数据丢失容忍） | RTO（恢复时间目标） |
|------|---------------------|---------------------|
| 数据库磁盘损坏 | < 1小时（WAL归档） | < 2小时 |
| 误删交易记录 | 0（审计日志可追溯） | < 30分钟 |
| 整机宕机 | < 24小时（每日备份） | < 4小时 |
| Redis 数据丢失 | 可接受（缓存可重建） | < 5分钟 |

### 9.3 备份验证

- 每周自动恢复备份到测试实例，验证数据完整性
- 备份完成后计算 checksum，写入备份日志
- 备份失败触发飞书告警

---

## 评审建议采纳说明

> 二次评审修订日期: 2026-04-16

### 已采纳的改进项 ✅

| 评审问题 | 采纳的改进 |
|----------|------------|
| Celery Beat 单点故障 | 补充定时任务故障恢复机制表（容忍延迟、恢复策略）；采用 Docker `restart: always` + 健康检查方案（单机部署场景足够） |
| 盘中监控时序可靠性 | 补充监控任务 SLA 定义和告警阈值；专用队列作为方案 |
| Redis 与数据库状态一致性未定义 | 补充 Redis 使用规范：缓存策略、分布式锁定义、宕机降级方案、一致性原则 |
| 交易执行流缺乏幂等性和补偿机制 | 补充任务可靠性设计：幂等性定义、事务边界、补偿检查、死信队列 |
| 数据源降级链缺乏具体设计 | 补充数据源降级链架构：降级策略表、熔断器参数、数据规范化 |
| Celery Worker 缺乏队列隔离 | 补充 Celery 队列隔离设计：队列定义、任务路由 |
| 前端架构缺乏描述 | 补充前端架构章节：目录结构、数据流 |
| 日志架构不完整 | 补充日志架构章节：分级策略、格式规范、轮转策略 |
| 异步/同步上下文共存未说明 | 补充数据库连接架构：双引擎设计、连接池规划 |
| 缺少灾难恢复与备份策略 | 补充备份与恢复策略：备份方案、RTO/RPO 目标 |
| 目录结构缺少通用模块 | 补充 core/、middleware/、schemas/、utils/ 目录说明 |
| 监控指标缺少业务维度 | 补充业务监控指标：交易、数据源、任务指标 |
| 安全设计过于简略 | 细化安全设计：网络层、应用层、数据层措施 |

### 不采纳的建议及原因 ❌

| 评审建议 | 不采纳原因 |
|----------|------------|
| 引入 RedBeat 替代 Celery Beat | 单机部署场景下 Docker `restart: always` + 健康检查已足够；RedBeat 增加 Redis 依赖和运维复杂度，不适合当前规模 |
| 独立 asyncio 监控进程 | 当前阶段不引入新组件；作为后续优化方向，暂用 Celery 专用队列方案 |
| WebSocket 重连与降级策略 | 属于前端实现细节，已在 API.md WebSocket 章节说明，架构文档不应重复 |
| 版本发布与灰度策略 | 属于运维文档范畴，不是架构设计内容 |
| 4C8G 资源优化方案 | 属于部署调优文档，不是架构设计内容 |

---

## 一、系统架构图

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              Vue3 前端 (:3000)                               │
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

## 二、技术栈

### 2.1 后端

| 组件 | 技术 | 版本 | 说明 |
|------|------|------|------|
| Web 框架 | FastAPI | 0.110+ | 高性能异步框架 |
| ORM | SQLAlchemy | 2.0+ | 异步支持 |
| 数据库 | PostgreSQL | 15 | ACID 事务 |
| 时序扩展 | TimescaleDB | 2.x | 自动分区 |
| 缓存 | Redis | 7.x | 实时行情缓存 |
| 任务队列 | Celery | 5.3+ | 异步任务 |
| 调度器 | Celery Beat | - | 定时任务 |
| 数据验证 | Pydantic | 2.x | 类型安全 |
| 日志 | loguru | - | 结构化日志 |

### 2.2 前端

| 组件 | 技术 | 版本 | 说明 |
|------|------|------|------|
| 框架 | Vue 3 | 3.4+ | Composition API |
| 构建 | Vite | 5.x | 快速构建 |
| UI 库 | Ant Design Vue | 4.x | 企业组件 |
| 状态管理 | Pinia | 2.x | 组合式 Store |
| HTTP | Axios | 1.x | 请求封装 |
| 图表 | ECharts | 5.x | 数据可视化 |
| K线 | LightweightCharts | 4.x | 金融图表 |
| 表格 | AG-Grid | 31.x | 高性能表格 |

### 2.3 基础设施

| 组件 | 技术 | 说明 |
|------|------|------|
| 容器 | Docker | 服务隔离 |
| 编排 | Docker Compose | 单机部署 |
| 监控 | Prometheus | 指标采集 |
| 可视化 | Grafana | 仪表盘 |
| 告警 | Alertmanager | 通知推送 |

---

## 三、核心模块

### 3.1 数据层

```
backend/app/
├── models/           # SQLAlchemy ORM 模型
│   ├── base.py       # Base 类、Mixin
│   ├── robot.py      # Robot, RobotTrade, TradeExecution
│   ├── market.py     # LhbStockDaily, LhbSeat, KlineDaily
│   ├── strategy.py   # Strategy, StrategyParam
│   └── audit.py      # AuditLog, TradeEvent
├── providers/        # 数据源适配器
│   ├── base.py       # 抽象基类
│   ├── tushare.py    # TuShare 实现
│   ├── akshare.py    # AKShare 实现
│   └── manager.py    # 降级链管理
├── core/             # 核心配置（新增）
│   ├── config.py     # Pydantic Settings（环境变量管理）
│   ├── constants.py  # 业务常量（交易状态、事件类型枚举）
│   ├── exceptions.py  # 自定义异常体系
│   └── events.py     # 应用生命周期事件（startup/shutdown）
├── middleware/       # 中间件（新增）
│   ├── request_id.py  # 请求 ID 注入
│   ├── error_handler.py  # 全局异常处理
│   ├── access_log.py  # 访问日志
│   └── rate_limit.py  # 限流（预留）
├── schemas/          # Pydantic 模型（新增，与 ORM 分离）
│   ├── robot.py      # 机器人请求/响应 Schema
│   ├── trade.py      # 交易请求/响应 Schema
│   ├── market.py     # 市场数据 Schema
│   └── common.py     # 分页、排序等通用 Schema
├── utils/            # 工具函数（新增）
│   ├── date_utils.py  # 交易日计算
│   ├── number_utils.py  # 金额/价格格式化
│   └── notification.py  # 飞书通知封装
└── db/
    ├── session.py    # 数据库会话
    ├── async_session.py  # 异步会话（FastAPI 专用）
    └── sync_session.py   # 同步会话（Celery 专用）
```

### 3.2 服务层

```
backend/app/services/
├── scoring_engine.py    # 多因子评分引擎
├── stock_selector.py    # 选股服务（Phase A/B）
├── trade_executor.py    # 交易执行服务
├── risk_control.py      # AHS 风控服务
├── state_machine.py     # 交易状态机
├── settlement.py        # 日结算服务
└── llm_selector.py      # LLM 选股增强（可选）
```

### 3.2.1 异常处理体系

```
# backend/app/core/exceptions.py

class AppException(Exception):
    """应用异常基类"""
    code: int = 5001
    http_status: int = 500
    message: str = "服务器内部错误"

# ── 业务异常 ──
class BusinessError(AppException):
    """业务逻辑错误"""
    code = 3001
    http_status = 422

class InsufficientFundsError(BusinessError):
    """资金不足"""
    message = "可用资金不足"

class MaxPositionExceededError(BusinessError):
    """超过最大持仓"""
    message = "已达到最大持仓数量"

class InvalidTradeStateError(BusinessError):
    """非法交易状态转换"""
    message = "当前交易状态不允许此操作"

# ── 资源异常 ──
class ResourceNotFoundError(AppException):
    """资源不存在"""
    code = 1002
    http_status = 404

class ResourceConflictError(AppException):
    """资源冲突"""
    code = 1003
    http_status = 409

# ── 外部服务异常 ──
class ProviderError(AppException):
    """数据源异常"""
    message = "数据源请求失败"

class AllProvidersFailedError(ProviderError):
    """所有数据源均不可用"""
    message = "所有数据源均不可用，请稍后重试"
```

全局异常处理器通过 middleware/error_handler.py 实现。

### 3.3 API 层

```
backend/app/api/
├── v1/
│   ├── router.py       # 路由汇总
│   ├── robots.py       # 机器人 CRUD
│   ├── trades.py       # 交易管理
│   ├── market.py       # 市场数据
│   ├── selection.py    # 选股中心
│   └── dashboard.py    # Dashboard 聚合
└── ws/
    └── connection.py   # WebSocket 管理
```

### 3.4 任务层

```
backend/app/tasks/
├── scheduler.py        # Celery Beat 配置
├── selection_tasks.py  # Phase A/B 选股任务
├── monitoring_tasks.py # 盘中监控（每10秒）
├── settlement_tasks.py # 日结算任务
└── data_sync_tasks.py # 数据同步任务
```

### 3.5 数据库连接架构

#### 双引擎设计

| 组件 | 驱动 | 连接池 | 用途 |
|------|------|--------|------|
| FastAPI | asyncpg | pool_size=5, max_overflow=3 | 异步请求处理 |
| Celery | psycopg2 | pool_size=3, max_overflow=2 | 同步任务执行 |

#### Session 生命周期

- **FastAPI**: 通过 Depends 注入 AsyncSession，请求结束自动关闭
- **Celery**: 任务开始创建 ScopedSession，任务结束关闭，异常时回滚

#### 连接池总量规划

```
PostgreSQL max_connections = 60（默认100，预留空间）

分配：
- FastAPI 异步池: (5 + 3) × 2 Worker = 16
- Celery Workers (2个): (3 + 2) × 2 = 10
- Celery Beat: 1
- 监控/管理工具: 5
- 预留: 28
────────────────────────────────
合计: ~30，预留空间充足
```

#### 代码约束

- `db/async_session.py` → 仅 FastAPI 使用
- `db/sync_session.py` → 仅 Celery 使用
- 禁止在 Celery 任务中导入 async session
- Service 层同时提供 async/sync 方法，或通过适配器桥接

---

## 四、数据流

### 4.1 实时行情流

```
AKShare API → Redis 缓存（12秒TTL）
                    ↓
            盘中监控任务（每10秒）
                    ↓
            WebSocket 推送给前端
                    ↓
            前端实时更新持仓盈亏
```

### 4.2 交易执行流

```
Phase A (14:50)
    ↓
评分引擎计算 → 选出候选（TOP N）
    ↓
写入 selection_log (phase='A')
    ↓
Phase B (09:15 次日)
    ↓
确认候选 → 风控检查 → 执行买入
    ↓
写入 robot_trades + trade_executions
    ↓
盘中监控 → 止盈/止损/到期 → 执行卖出
    ↓
更新 robot_trades.status = 'closed'
```

### 4.3 数据采集流

```
定时任务（收盘后）
    ↓
TuShare → AKShare → Sina（降级链）
    ↓
写入 lhb_stock_daily + lhb_seats
    ↓
计算 T+N 价格 → 写入 lhb_tn_prices
    ↓
更新统计指标
```

### 4.4 数据源降级链架构

#### 降级策略

| 数据类型 | 主数据源 | 降级链 | 策略 |
|----------|----------|--------|------|
| K线 | TuShare | AKShare → Baostock | 熔断器 |
| 龙虎榜 | AKShare | Sina → 东方财富 | 熔断器 |
| 实时行情 | AKShare | Sina | 快速切换 |

#### 熔断器参数

```yaml
circuit_breaker:
  failure_threshold: 3       # 连续3次失败触发熔断
  recovery_timeout: 300      # 熔断5分钟后尝试恢复
  half_open_max_calls: 1     # 半开状态最多1次探测
  request_timeout: 10        # 单次请求超时（秒）
```

#### 数据规范化

- 所有数据源返回统一的内部数据结构（DTO）
- 字段映射在各 Provider 内部完成
- 价格统一为3位小数，金额统一为元
- 缺失字段标记为 None，不使用默认值填充

#### 全部不可用兜底

| 数据类型 | 兜底策略 |
|----------|----------|
| 行情数据 | 使用最后一次有效缓存（标记 stale=true） |
| 龙虎榜数据 | 跳过当日采集，次日补采 |
| 告警 | 触发飞书告警，人工介入 |

---

## 五、前端架构

### 5.1 目录结构

```
frontend/src/
├── api/                    # API 请求层
│   ├── client.ts           # Axios 封装（拦截器、错误处理）
│   ├── robots.ts           # 机器人相关 API
│   ├── market.ts           # 市场数据 API
│   └── ws.ts               # WebSocket 客户端
├── stores/                 # Pinia 状态管理
│   ├── robotStore.ts       # 机器人状态
│   ├── marketStore.ts      # 市场数据
│   ├── wsStore.ts          # WebSocket 连接状态
│   └── appStore.ts         # 全局状态（主题、配置）
├── views/                  # 页面组件
│   ├── Dashboard/          # 看板页
│   ├── Robots/             # 机器人管理页
│   ├── Selection/          # 选股中心页
│   ├── Market/             # 市场数据页
│   ├── Backtest/           # 回测页
│   └── System/             # 系统管理页
├── components/             # 通用组件
│   ├── charts/             # 图表组件（K线、净值曲线）
│   ├── tables/             # 表格组件
│   └── common/             # 通用 UI 组件
├── composables/            # 组合式函数
│   ├── useWebSocket.ts     # WebSocket Hook
│   ├── usePolling.ts       # 轮询 Hook
│   └── useTradeFormat.ts   # 交易数据格式化
├── router/                 # 路由配置
└── utils/                  # 工具函数
```

### 5.2 数据流

```
API/WebSocket → Pinia Store → Vue 组件（响应式更新）
```

### 5.3 离线/弱网处理

- API 请求失败 → 显示最后一次成功数据 + 警告提示
- WebSocket 断开 → 自动重连（指数退避）+ 降级为轮询
- 关键操作（买入/卖出）→ 提交失败时本地暂存，恢复后重试

---

## 五、部署拓扑

### 5.1 开发环境

```yaml
services:
  postgres:
    image: timescale/timescaledb:2.13.1-pg15
    ports: ["5432:5432"]
    volumes: [pgdata:/var/lib/postgresql/data]

  redis:
    image: redis:7.2-alpine
    ports: ["6379:6379"]

  backend:
    build: ./backend
    ports: ["8000:8000"]
    volumes: [.:/app]
    environment:
      - DATABASE_URL=postgresql+asyncpg://...

  celery_worker:
    build: ./backend
    command: celery -A app.celery_app worker --loglevel=info
    depends_on: [redis, postgres]

  celery_beat:
    build: ./backend
    command: celery -A app.celery_app beat --loglevel=info
    depends_on: [redis]
```

### 5.2 生产环境

```yaml
# docker-compose.prod.yml
services:
  postgres:
    restart: always
    deploy:
      resources:
        limits: { memory: 2G }

  redis:
    command: redis-server --requirepass ${REDIS_PASSWORD}

  backend:
    restart: always
    environment:
      - APP_ENV=production
      - LOG_LEVEL=WARNING
```

### 5.3 Celery 队列隔离设计

#### 队列定义

| 队列名 | 优先级 | Worker 数 | 并发模式 | 用途 |
|--------|--------|-----------|----------|------|
| critical | P0 | 1 | prefork, concurrency=1 | 盘中监控、止损执行 |
| trading | P1 | 1 | prefork, concurrency=2 | Phase A/B、手动买卖 |
| default | P2 | 1 | prefork, concurrency=4 | 结算、通知、AHS计算 |
| batch | P3 | 1 | prefork, concurrency=2 | 数据同步、回测、统计 |

#### 任务路由

```python
task_routes = {
    'app.tasks.monitoring_tasks.*':     {'queue': 'critical'},
    'app.tasks.selection_tasks.*':      {'queue': 'trading'},
    'app.tasks.trade_tasks.*':          {'queue': 'trading'},
    'app.tasks.settlement_tasks.*':     {'queue': 'default'},
    'app.tasks.notification_tasks.*':   {'queue': 'default'},
    'app.tasks.data_sync_tasks.*':     {'queue': 'batch'},
    'app.tasks.backtest_tasks.*':      {'queue': 'batch'},
}
```

---

## 六、监控与告警

### 6.1 指标采集

| 指标 | 来源 | 用途 |
|------|------|------|
| API 响应时间 | Prometheus | 性能监控 |
| Celery 任务积压 | Redis | 队列健康 |
| 数据库连接数 | PostgreSQL exporter | 容量规划 |
| 内存使用率 | Node exporter | 资源告警 |

### 6.2 告警规则

| 告警 | 条件 | 级别 |
|------|------|------|
| BackendDown | 健康检查失败 > 1min | critical |
| HighErrorRate | 5xx > 5% | critical |
| CeleryQueueBacklog | 积压 > 1000 | warning |
| HighLatency | P99 > 2s | warning |

### 6.3 业务监控指标

#### 交易指标

| 指标名 | 类型 | 标签 | 说明 |
|--------|------|------|------|
| lhb_trades_total | Counter | robot_id, action, status | 交易总数 |
| lhb_trade_profit | Histogram | robot_id, sell_type | 单笔盈亏分布 |
| lhb_position_count | Gauge | robot_id | 当前持仓数量 |
| lhb_robot_nav | Gauge | robot_id | 当前净值 |
| lhb_robot_drawdown | Gauge | robot_id | 当前回撤 |
| lhb_daily_win_rate | Gauge | robot_id | 当日胜率 |

#### 数据源指标

| 指标名 | 类型 | 标签 | 说明 |
|--------|------|------|------|
| lhb_provider_requests_total | Counter | provider, data_type, status | 数据源请求总数 |
| lhb_provider_latency_seconds | Histogram | provider, data_type | 数据源延迟 |
| lhb_provider_circuit_state | Gauge | provider | 熔断器状态(0=关,1=开,2=半开) |
| lhb_data_freshness_seconds | Gauge | table_name | 数据新鲜度（距最新记录秒数） |

#### 任务指标

| 指标名 | 类型 | 标签 | 说明 |
|--------|------|------|------|
| lhb_task_duration_seconds | Histogram | task_name | 任务执行耗时 |
| lhb_task_failures_total | Counter | task_name | 任务失败总数 |
| lhb_monitor_interval_seconds | Histogram | - | 监控实际间隔 |
| lhb_selection_candidates | Gauge | robot_id, date | 当日候选股票数 |

#### 业务告警规则

| 告警 | 条件 | 级别 |
|------|------|------|
| MonitorDelayed | 监控间隔 > 30秒 | critical |
| PhaseAMissed | 15:00 仍无选股结果 | critical |
| DataStale | 龙虎榜数据延迟 > 26小时 | warning |
| AllProvidersFailed | 所有数据源熔断 | critical |
| CashMismatch | 资金对账不平 | critical |

---

## 十、安全设计

### 10.1 认证（预留）

- 当前单用户模式，无认证
- 后续可扩展 JWT + RBAC
- 说明未来 JWT + RBAC 的接入点：哪些接口需要认证，哪些公开（如 /health）

### 10.2 网络层安全

| 措施 | 具体实现 |
|------|----------|
| 端口绑定 | 所有服务仅绑定 127.0.0.1（当前 docker-compose 已做到） |
| 对外暴露 | 仅通过 Nginx/Caddy 反向代理暴露 |
| HTTPS | 强制 TLS 加密（即使内网部署，API Key 等仍需加密传输） |
| CORS | 白名单具体值（不能是 *） |

### 10.3 应用层安全

| 措施 | 具体实现 |
|------|----------|
| Rate Limiting | 按 IP/按接口限流（预留 middleware/rate_limit.py） |
| 请求体限制 | 限制请求体大小 |
| SQL 注入防护 | ORM 参数化查询，禁止拼接 SQL |
| 输入校验 | Pydantic 严格校验 |

### 10.4 数据层安全

| 措施 | 具体实现 |
|------|----------|
| 数据库权限 | 应用用户只有 SELECT/INSERT/UPDATE，无 DROP/TRUNCATE |
| 敏感配置 | 不进代码仓库，使用 .env 文件（权限 600） |
| 加密存储 | 敏感字段（如 API Key）使用 AES 加密 |
| 审计日志 | 完整记录所有关键操作 |

---

## 附录：章节索引

| 章节 | 内容 |
|------|------|
| 一、系统架构图 | 整体架构、组件关系 |
| 二、技术栈 | 后端、前端、基础设施选型 |
| 三、核心模块 | 数据层、服务层、API 层、任务层目录结构 |
| 四、数据流 | 实时行情流、交易执行流、数据采集流、数据源降级链 |
| 五、前端架构 | 目录结构、数据流、离线处理 |
| 六、部署拓扑 | 开发环境、生产环境、Celery 队列隔离 |
| 七、可靠性设计 | 定时任务恢复、Redis 规范、任务可靠性 |
| 八、日志架构 | 分级策略、格式规范、轮转策略 |
| 九、备份与恢复 | 备份方案、RTO/RPO 目标、验证机制 |
| 十、安全设计 | 网络、应用、数据层安全措施 |
