# 实施路线图

> 配套 [重写方案_v2.md](重写方案_v2.md) 第 9 章的 12 周路线图，本文给出**每周具体任务拆解 + 交付标准**。
>
> ⚠️ **本文档各章标题上的 ✅ 标记不可信**(历史遗留,未随代码同步更新)。
> 真实进度请以 [PROGRESS.md](PROGRESS.md) 为准。本文保留作为**计划参考**。

---

## W0 方案与骨架 ✅

**交付**：
- [x] `docs/重写方案_v2.md`
- [x] `docs/CODE_REVIEW.md`
- [x] `docs/IMPLEMENTATION_ROADMAP.md` (本文)
- [x] `backend/data_gateway/` 骨架 (已修复致命 bug)
- [x] `backend/core/startup.py` 代理清理
- [x] `README.md` + `.env.example` + 部署脚本骨架

---

## W1 基础设施 ✅

**目标**：具备在 jd 上跑起来的最小后端。

> ⚠️ 偏离说明(2025-04-18 追认):下方任务 1 中的 `sqlalchemy>=2.0` 与 `alembic`,以及任务 2 中的 `models.py`(SQLAlchemy declarative)、`migrations/`(Alembic)均已**废弃**。
> 现行方案:原生 SQL + `db/init/*.sql` 版本化脚本,详见 [ADR-0001](ADR/0001-drop-sqlalchemy-and-alembic.md)。
> 本节保留原文仅作历史参考,实际实现以 ADR-0001 为准。

### 任务清单
1. **`pyproject.toml` + poetry / requirements.txt**
   依赖：`fastapi`, `uvicorn`, `sqlalchemy>=2.0`, `alembic`, `psycopg2-binary`, `redis`, `celery`, `apscheduler`, `akshare`, `tushare`, `pandas`, `pytest`, `python-dotenv`
2. **`backend/db/`**
   - `session.py` — engine + SessionLocal (从 `.env` 读 DSN)
   - `models.py` — SQLAlchemy 2.x declarative，按 `db/init/01_schema.sql` 复刻
   - `migrations/` — Alembic 初始化，`alembic upgrade head` 等价于跑现有 SQL
3. **`backend/api/main.py`**
   - `FastAPI()` 实例，顶部 `from backend.core.startup import bootstrap; bootstrap()`
   - 最小 `/healthz` 端点
4. **`scripts/deploy.sh` / `scripts/restart.sh` / `scripts/healthcheck.sh`**（已提供骨架，补 poetry 细节）
5. **systemd service 单元文件**（放 `deploy/systemd/`）
6. **GitHub Actions**：跑 `pytest tests/test_circuit_breaker.py` (离线即可)

### 交付标准
- `ssh jd "cd ~/stock_analysis && ./scripts/deploy.sh"` 能完整跑通
- `curl http://<jd>/healthz` 返回 `{"status":"ok"}`
- `alembic upgrade head` 在 jd 上成功建全部表

---

## W2 数据网关 Ⅰ (Router + CB + Proxy + Cache + PriorityConfig) ✅

### 任务清单
1. **Router 补熔断显式检查**
   `router.py::fetch` 循环里先 `if not adapter.circuit_breaker.allow_request(): continue`
2. **`proxy_policy.py`**（可选，v2 决策是在 startup.py 里一次性清掉环境代理，此文件保留给未来需要访问境外源时用）
3. **`cache_manager.py`**
   - Redis wrapper，key = `dg:{category}:{sha1(params)}`
   - category → TTL 表（见 v2 §2）
4. **`priority_config.py` + migration `data_source_config` 表**
   ```python
   PriorityConfig.load() / save() / move_up() / move_down() / toggle_enabled()
   ```
5. **TuShare adapter 收窄**
   增加 `_supported_categories = {DataCategory.DAILY_KLINE}`，其他方法 `raise NotImplementedError`，并把 `supports()` 逻辑改为检查此集合。
6. **单元测试**
   - `tests/test_router_cb.py` — mock adapter，验证 CB 打开后 router 跳过
   - `tests/test_priority_config.py` — DB roundtrip
   - `tests/test_cache_manager.py` — Redis 存取 + TTL

### 交付标准
- `DataSourceManager.get_status()` 包含每个 adapter 的 CB + 当前优先级链
- 手动打开某 adapter 熔断，请求自动降级并在 500ms 内返回
- 重启进程后优先级配置从 DB 恢复

---

## W3 数据网关 Ⅱ (HealthChecker + 审计 + 管理 API) ✅

### 任务清单
1. **`health_checker.py`**
   APScheduler 每 5 分钟轮询 `health_check()`，写 `adapter_health` 表
2. **`data_fetch_log`**
   Router 每次 fetch 记录 `(category, adapter, latency, ok, err)` 到 DB
3. **FastAPI `/api/data-sources/*` 管理端点**
   - `GET /status` — 返回 CB + 优先级 + 健康状态
   - `POST /priority` — 调整链
   - `POST /adapter/:name/retry` — 重初始化
   - `POST /adapter/:name/circuit/{close|open}`
4. **前端 `DataSourceMgr.vue`**（W7 交付，但后端 API 先就绪）

### 交付标准
- `/api/data-sources/status` 返回完整 JSON
- `adapter_health` 表每 5 分钟新增一行
- `data_fetch_log` 可支撑"最近 24h 各源成功率"查询

---

## W4 龙虎榜入库 ✅

### 任务清单
1. **`backend/domain/lhb/`**
   - `parser.py` — AKShare/Sina/Crawler 返回的 DataFrame → 统一 `LhbRecord` dataclass
   - `ingestor.py` — 写入 `lhb_stock_daily` / `lhb_seats` / `lhb_tn_prices`
   - `aggregator.py` — 写入 `lhb_agg_stats` / `lhb_institution_stats`
2. **补全 `akshare_adapter.get_lhb_seats`** (见 CODE_REVIEW §3.6)
3. **回填脚本** `scripts/backfill_lhb.py --from 20200101 --to 20260417`
4. **日终 APScheduler 任务** 每日 18:00 增量拉取
5. **Celery 任务** `lhb.aggregate_daily` 计算当日营业部 / 机构统计

### 交付标准
- 全量回填 ~6 年龙虎榜数据，`SELECT COUNT(*) FROM lhb_stock_daily` > 100k
- 每日自动增量，无重复（靠 `(ts_code, trade_date)` 唯一约束）

---

## W5 机器人框架 + 资金台账 ✅

### 任务清单
1. **`domain/strategy/`**
   - `base.py::BaseStrategy` (select_stocks / calc_buy_amount / check_sell)
   - `registry.py` — 装饰器注册策略类
2. **`domain/robot/`**
   - `robot.py` — 机器人状态机，读 `robots` 表
   - `risk.py` — 仓位限制、单日最大亏损、冷静期
   - `ledger.py` — 资金台账（依赖 `cash_ledger` 表）
3. **`trade_events` / `selection_log` / `audit_log` 写入**
4. **单元测试**：风控场景（超仓位、爆亏停止、手动暂停）

### 交付标准
- 可注册新策略类 → DB 出现一条 `strategies` → 关联机器人 → 模拟执行 → 账户资金正确结算

---

## W6 回测引擎 Ⅰ ✅

### 任务清单
1. **Celery worker** 进程 + systemd 单元
2. **`domain/backtest/`**
   - `engine.py` — 事件驱动回放（日 K 粒度）
   - `portfolio.py` — 组合 / 仓位 / 净值
   - `report.py` — `daily_nav` / 夏普 / 最大回撤 / 胜率
3. **API** `POST /api/backtest` 异步启动，`GET /api/backtest/:id` 查询状态
4. **前端脚手架** (Vue 3 + Vite，但不实现 UI)

### 交付标准
- 单策略 3 年回测 < 5 分钟
- 回测结果落 `backtests` / `daily_nav` / `trade_events`

---

## W7 前端 MVP ✅

### 任务清单
1. **脚手架**：Vue 3 + Vite + TypeScript + Pinia + Ant Design Vue + vue-router
2. **页面**
   - `/` Dashboard — 今日情绪 + 涨停池 + 机器人状态卡片
   - `/lhb` 龙虎榜列表 (AG Grid)
   - `/data-sources` 数据源管理 (优先级拖拽 + 健康灯 + CB 状态 + 缓存清理)
3. **接入后端 REST**，ECharts 出今日行情分布图
4. **Nginx 配置** 静态包 + `/api/*` 反向代理到 uvicorn

### 交付标准
- `https://<jd>/` 可访问 Dashboard
- 数据源管理页可拖拽 + 即时生效 + 持久化

---

## W8 实时 Ⅰ (盘中 10s 快照) ✅

### 任务清单
1. **用户实现** `CrawlerAdapter.get_intraday_snapshot(codes)`
2. **APScheduler**
   ```python
   @scheduler.scheduled_job("cron", second="*/10",
                            hour="9-11,13-15", day_of_week="mon-fri")
   def intraday_snapshot_job(): ...
   ```
3. **订阅池**：龙虎榜 T-20 日出现过的 code ∪ 今日涨停池 (由 AKShare `stock_zt_pool_em` 每分钟刷)
4. **写 `market_snapshots` hypertable** (batch insert，~100 条/秒)
5. **WebSocket** `/ws/snapshot?codes=xxx,yyy` 从 Redis 频道 `snapshot:{code}` 订阅推送

### 交付标准
- 交易时段每 10s 有新快照入库
- 前端订阅 code 可实时看到价格跳动
- CB 打开时 scheduler 自动降级到 30s 间隔

---

## W9 实时 Ⅱ (盘中机器人 + Phase A/B) ✅

### 任务清单
1. **Phase A 调度**：开盘前选股 → 9:25 集合竞价挂单
2. **Phase B 调度**：盘中监控触发卖出条件
3. **机器人盘中循环**（每 10s）读 snapshot → 风控 → 下模拟单
4. **实时选股**：基于当日 AKShare `stock_zt_pool_em` 的涨停池 + LLM 打分

### 交付标准
- 开盘到收盘机器人全自动运行，日志可追溯每次决策
- 手动暂停 / 恢复通过 API 立即生效

---

## W10 回测 Ⅱ + 报表 ✅

### 任务清单
1. 多策略对比（并行跑 N 个回测）
2. 绩效归因：按席位/行业/上榜原因分桶
3. Celery 定时任务 `reports.weekly` 周一生成上周报表
4. 前端 `/backtest` / `/reports` 页面 + 导出 PDF / Excel

### 交付标准
- 一键对比 3 个策略在同时间窗的表现
- 周报自动生成并邮件 / 飞书推送

---

## W11 优化 ✅

### 任务清单
1. `kline_daily` / `market_snapshots` 压缩策略调参（压缩率目标 >70%）
2. 关键查询上覆盖索引，`EXPLAIN ANALYZE` 无全表扫描
3. Redis 热路径缓存 (Dashboard 首屏 < 500ms)
4. 日志脱敏（token / cookie），`loguru` 接入
5. pytest 覆盖率 > 70%

---

## W12 上线演练 ✅

### 任务清单
1. 灰度：机器人开关，默认 OFF，可逐个打开
2. 监控：Prometheus + Grafana（Postgres / Redis / FastAPI / Celery 指标）
3. 告警：错误率 / CB 打开 / 资金异动 → 飞书 / 邮件
4. 备份：`pg_dump` 每日 → 对象存储，保留 30 天
5. 回滚演练：`git checkout <tag> && ./scripts/deploy.sh` 能 3 分钟恢复
6. 文档完成度：DEPLOYMENT / DEVELOPMENT / API 文档齐全

---

## 当前即可着手的下一步

> 本节已过时。请查阅 [PROGRESS.md §5 修复优先级](PROGRESS.md#5-修复优先级)。
