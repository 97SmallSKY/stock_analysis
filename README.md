# 龙虎榜量化交易分析系统

A 股龙虎榜量化交易全栈系统，覆盖**多数据源聚合 → 龙虎榜解析 → 选股打分 → 回测 → 机器人下单 → 盘中 10s 快照 → 实时监控**全链路。

> 设计文档：[docs/重写方案_v2.md](docs/重写方案_v2.md)
> 路线图：[docs/IMPLEMENTATION_ROADMAP.md](docs/IMPLEMENTATION_ROADMAP.md)
> 代码审查：[docs/CODE_REVIEW.md](docs/CODE_REVIEW.md)

---

## 1. 系统组成

| 层 | 技术栈 | 说明 |
|---|---|---|
| 前端 | Vue 3 + Vite + TypeScript + Ant Design Vue | Dashboard / 龙虎榜列表 / 回测 / 数据源管理 |
| API | FastAPI | REST + WebSocket (盘中推送) |
| 异步任务 | Celery + APScheduler | 回测、日终批、盘中 10s 快照 |
| 数据库 | PostgreSQL 15 + TimescaleDB | 20+ 张表，K 线 / 快照为 hypertable |
| 缓存 | Redis 7 | 行情缓存 + Celery broker + WebSocket pub/sub |
| 数据网关 | 自研 | 4 路适配器 (TuShare / AKShare / Sina / Crawler) + 熔断 + 优先级链 |

---

## 2. 部署拓扑

**单节点**，全部跑在 **jd 服务器**。本地 macOS 只做开发机。

```
本地 macOS          →  git push  →  jd 服务器 (117.72.184.8)
                                    ├── nginx (:80/:443)
                                    ├── FastAPI (:8000)
                                    ├── Celery worker
                                    ├── APScheduler
                                    ├── PostgreSQL + TimescaleDB (localhost:5432)
                                    └── Redis (localhost:6379)
```

**关键约束**：
- 本地 IP 会被 AKShare/东方财富封禁，**不要在本地跑联调测试**；集成测试只在 jd 上跑。
- jd 默认走 SG 代理（用于出海）。项目所有出站都是国内源，启动时已由 `backend/core/startup.py` 清除环境代理变量。
- 数据库在 jd 本地访问 (`PG_HOST=localhost`)，不对公网暴露。

---

## 3. 快速开始 (本地开发)

### 3.1 准备环境
```bash
cp .env.example .env
# 编辑 .env，至少填 TUSHARE_TOKEN 和数据库连接信息
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt  # 待 W1 交付
```

### 3.2 启动本地依赖
本地开发如果需要离线数据库，可启动容器：
```bash
docker compose -f docker-compose.postgres.yml up -d
```
否则 `.env` 中 `PG_HOST` 直接指向 jd。

### 3.3 运行单元测试
```bash
pytest tests/test_circuit_breaker.py -q    # 纯内存，离线
pytest tests/test_db_tables.py -q          # 需要 DB
pytest tests/test_data_fetch.py -q         # ⚠️ 本地会被封 IP，只在 jd 上跑
```

### 3.4 部署到 jd
```bash
ssh jd "cd ~/stock_analysis && git pull && ./scripts/deploy.sh"
```

详见 [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)。

---

## 4. 目录结构

```
stock_analysis/
├── backend/
│   ├── core/                 # 启动初始化 (代理/日志)
│   ├── data_gateway/         # 数据网关：适配器 + 路由 + 熔断
│   │   └── adapters/         # tushare / akshare / sina / crawler
│   ├── db/                   # psycopg2 连接封装(不使用 ORM,见 docs/ADR/0001)
│   ├── domain/               # 业务层：LHB / 选股 / 策略 / 回测 / 机器人 (W4+)
│   ├── api/                  # FastAPI 路由 + WebSocket  (W6+)
│   ├── tasks/                # Celery 任务  (W5+)
│   └── scheduler/            # APScheduler  (W8+)
├── frontend/                 # Vue 3 前端  (W7+)
├── db/                       # 原始 SQL schema + Docker 初始化脚本
├── docs/                     # 设计文档、路线图、运维手册
├── scripts/                  # 部署脚本
├── tests/                    # pytest 测试套件
└── docker-compose.postgres.yml
```

---

## 5. 数据源能力矩阵

| 品类 | 主 | 备 1 | 备 2 | 备 3 |
|---|---|---|---|---|
| 日 K 线 | TuShare | AKShare | Sina | Crawler |
| 龙虎榜明细 | AKShare | Sina | Crawler | — |
| 龙虎榜席位 | AKShare | Crawler | — | — |
| 机构数据 | Sina | AKShare | Crawler | — |
| 实时行情 | AKShare | Sina | — | — |
| 市场情绪 | AKShare | Sina | — | — |
| 财务数据 | AKShare | — | — | — |
| 股票基础信息 | AKShare | Sina | — | — |
| 交易日历 | AKShare | — | — | — |
| 指数日线 | AKShare | Sina | — | — |
| 盘中 10s 快照 | Crawler (自研) | — | — | — |

> TuShare 当前 120 积分仅开日 K；升级到 2000 积分后可恢复 `INDEX_DAILY` / `FINANCIAL_DATA` / `STOCK_INFO` 等品类。

---

## 6. 文档导航

- **[docs/PROGRESS.md](docs/PROGRESS.md) — 项目真实进度(单一事实源)**
- [docs/重写方案_v2.md](docs/重写方案_v2.md) — 完整设计方案 (主文档)
- [docs/IMPLEMENTATION_ROADMAP.md](docs/IMPLEMENTATION_ROADMAP.md) — 12 周实施步骤(计划参考,状态以 PROGRESS.md 为准)
- [docs/CODE_REVIEW.md](docs/CODE_REVIEW.md) — 当前代码审查与修复记录
- [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) — 部署 / 运维手册
- [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) — 本地开发指南
- [docs/龙虎榜量化交易分析系统 — 完整重写方案.md](docs/龙虎榜量化交易分析系统%20—%20完整重写方案.md) — 早期完整方案 (参考)
- [docs/多数据源重构方案设计.md](docs/多数据源重构方案设计.md) — 数据网关专题 (参考)

---

## 7. 当前进度

**进度单一事实源**:[docs/PROGRESS.md](docs/PROGRESS.md)

简要摘录(2025-04-18):

- 代码功能骨架:**~80%**(W1~W10 主要业务代码均已落地)
- 质量门(测试/覆盖率/集成):**~40%**(pytest 覆盖率实测 24.81%,集成测试在 CI 被 `--ignore`)
- 文档同步度:**~40%**(`IMPLEMENTATION_ROADMAP.md` 历史勾选不可信,以 `PROGRESS.md` 为准)

> 历史计划文档 [IMPLEMENTATION_ROADMAP.md](docs/IMPLEMENTATION_ROADMAP.md) 仅作参考,其中的 ✅ 标记与真实状态存在偏差。

---

## 8. License

Private. 本项目仅供作者本人使用，任何商用请联系作者。
