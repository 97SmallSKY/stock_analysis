# 龙虎榜量化交易分析系统 — 停机全量重写方案

> 基于《完整重写方案》和《多数据源重构方案设计》制定
> 策略：**完全停机，抛弃 Streamlit，从零重写**
> 创建日期: 2026-04-16

---

## 一、重写目标

| 维度 | 旧系统 | 新系统 |
|------|--------|--------|
| 前端 | Streamlit (Python) | **Vue3 + TypeScript + Vite + Ant Design Vue** |
| 后端 | Streamlit + 散落脚本 | **FastAPI + Celery + APScheduler** |
| 数据库 | PostgreSQL (裸SQL) | **PostgreSQL 15 + TimescaleDB + SQLAlchemy 2.0 + Alembic** |
| 缓存 | 进程级 dict / 不统一 | **Redis 7 统一缓存** |
| 数据源 | AKShare 硬编码单一 | **多源降级链 (TuShare→AKShare→新浪→Crawler) + 熔断器** |
| 实时推送 | Streamlit轮询 | **WebSocket (行情/PnL/订单)** |
| 异步任务 | 无 | **Celery (回测/数据同步/报表)** |
| 定时调度 | cron 脚本 | **APScheduler 进程内调度** |
| 图表 | Plotly/Streamlit | **LightweightCharts (K线) + ECharts (曲线)** |
| 表格 | st.dataframe | **AG-Grid (虚拟滚动)** |
| 部署 | rsync + 手动重启 | **Docker Compose 一键部署** |
| 日志 | logging | **loguru 结构化日志** |

## 二、重写策略

```
完全停机重写 — 不保留任何 Streamlit 代码
├── 旧代码仅作为业务逻辑参考（评分/交易/风控规则）
├── 全新 lhb-quant/ monorepo 从零搭建
├── 数据库一次性迁移（旧表 → 新表）
└── 切换时：停旧服务 → 迁移数据 → 启新服务
```

**优势：**
- 无兼容负担，架构干净
- 无双写复杂度
- 可一次性优化数据库 schema
- 前后端同步开发，不受旧代码制约

## 三、新项目结构

```
lhb-quant/                              # Monorepo 根目录
├── docker-compose.yml                  # 一键启动: PG+TimescaleDB+Redis+Backend+Frontend+Nginx
├── .env.example
├── Makefile                            # make dev / make migrate / make test
├── README.md
│
├── backend/                            # FastAPI 后端
│   ├── Dockerfile
│   ├── pyproject.toml
│   ├── alembic.ini
│   ├── alembic/versions/
│   ├── app/
│   │   ├── main.py                     # FastAPI 入口
│   │   ├── config.py                   # pydantic-settings
│   │   ├── deps.py                     # 依赖注入
│   │   ├── models/                     # SQLAlchemy ORM (7 个文件)
│   │   ├── schemas/                    # Pydantic 请求/响应 (6 个文件)
│   │   ├── api/v1/                     # REST 路由 (7 个文件)
│   │   ├── api/ws/                     # WebSocket (1 个文件)
│   │   ├── services/                   # 业务逻辑 (11 个文件)
│   │   ├── strategies/                 # 策略插件 (3 个文件)
│   │   ├── data_providers/             # 数据源降级链 (6 个文件)
│   │   ├── tasks/                      # Celery 异步任务 (3 个文件)
│   │   ├── scheduler/                  # APScheduler 定时任务
│   │   ├── core/                       # 异常/日志/Redis/缓存/常量
│   │   └── utils/                      # 交易日历/技术指标/工具函数
│   └── tests/                          # pytest 单元测试
│
├── frontend/                           # Vue3 前端
│   ├── Dockerfile
│   ├── package.json
│   ├── vite.config.ts
│   ├── src/
│   │   ├── api/                        # Axios + WebSocket 封装
│   │   ├── stores/                     # Pinia 状态管理
│   │   ├── router/
│   │   ├── views/                      # 15 个页面
│   │   ├── components/                 # 图表/表格/机器人/通用/布局
│   │   ├── composables/                # 组合式函数
│   │   └── styles/
│
├── nginx/conf.d/default.conf           # 反向代理
│
└── scripts/                            # 运维/迁移脚本
    ├── migrate_data.py                 # 旧表 → 新表
    ├── init_db.py                      # 初始数据
    └── seed_data.py                    # 测试数据
```

## 四、分阶段规划总览

```
Phase 0: 基础设施 + Docker 环境       ██░░░░░░░░  [2 天]
Phase 1: 数据库 + ORM                  ████░░░░░░  [3 天]
Phase 2: 数据源降级链                   ██████░░░░  [3 天]
Phase 3: 核心业务逻辑层                 ████████░░  [5 天]
Phase 4: FastAPI 后端 API               ██████████  [5 天]
Phase 5: Vue3 前端                      ██████████  [7 天]
Phase 6: 数据迁移 + 集成部署            ██████████  [3 天]
                                        ─────────────────
                                        总计: 28 个工作日
```

**关键路径：** Phase 0 → 1 → 2 → 3 → 4 → 6
**并行路径：** Phase 5 (前端) 可在 Phase 4 (API) 开始后并行开发

## 五、核心业务逻辑迁移清单

以下是从旧代码中提取、需要在新系统中重新实现的核心逻辑：

| 旧文件 | 核心逻辑 | 新位置 |
|--------|----------|--------|
| `modules/scorer.py` | 技术面40+资金面35+历史25评分 | `services/scorer.py` |
| `modules/data_fetcher.py` | 数据获取 | `data_providers/` 整个模块 |
| `modules/analyzer.py` | 数据分析 | `services/market_data.py` |
| `modules/monitor.py` | 盘中监控 | `services/monitor_service.py` |
| `modules/feature_engine.py` | 特征工程 | `services/scorer.py` (合并) |
| `robot/` | 机器人管理+交易 | `services/robot_service.py` + `trade_service.py` |
| `risk/` | AHS + 熔断 | `services/risk_service.py` |
| `strategy/` | 策略逻辑 | `strategies/lhb_multifactor.py` |
| `engine/` | 回测引擎 | `services/backtest_service.py` |
| `scripts/daily_update.py` | 日常数据更新 | `scheduler/jobs.py` |
| `scripts/daily_cron.py` | 定时任务 | `scheduler/jobs.py` |

## 六、数据迁移策略

```
停机迁移流程:
1. 停止旧服务 (Streamlit + cron)
2. pg_dump 全量备份旧数据库
3. 执行新 DDL (22 张表 + TimescaleDB)
4. 运行 migrate_data.py (旧表 → 新表映射)
5. 运行 verify_migration.py (行数 + 抽样校验)
6. 启动新服务 (docker-compose up -d)
7. 验证功能正常
8. 归档旧代码到 legacy/ 分支
```

## 七、风险与对策

| 风险 | 对策 |
|------|------|
| 停机期间无法交易 | 选择周末或节假日执行迁移 |
| 数据迁移丢失 | 迁移前 pg_dump 备份，迁移后逐表校验 |
| 业务逻辑遗漏 | 逐文件对照旧代码，单元测试覆盖关键路径 |
| 前端开发周期长 | 优先核心 5 页面（Dashboard/机器人/龙虎榜信号/回测/监控），其余后续迭代 |
| TuShare API 额度 | 降级链确保多源兜底 |

## 八、对接说明

继续任务时的操作流程：

```
1. 阅读 PROGRESS.md → 了解当前进度
2. 阅读 PHASES.md → 找到下一个待做任务
3. 参考《完整重写方案》→ 获取详细代码设计
4. 参考《多数据源重构方案设计》→ 获取数据源层设计
5. 完成任务后更新 PROGRESS.md
```

配套文件：
- **[PHASES.md](./PHASES.md)** — 详细任务分解 + 验收标准
- **[PROGRESS.md](./PROGRESS.md)** — 实时进度追踪表
