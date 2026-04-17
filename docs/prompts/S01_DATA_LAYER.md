# S01 — 数据层（Data Layer）

> **阶段目标**：搭建完整的数据采集与存储基础设施，为后续策略计算提供干净、可靠的数据源。

---

## 0. 前置阅读（必须先读）

在开始编码前，请完整阅读以下文档：

- `docs/lhb-quant-v2/DATABASE.md` — 所有表结构定义、索引策略、分区方案
- `docs/lhb-quant-v2/DATA_SOURCE.md` — Akshare 接口清单、字段映射、限速规则
- `docs/lhb-quant-v2/ARCHITECTURE.md` — 整体分层架构，理解数据层在系统中的位置

---

## 1. 任务目标

构建以下四个子系统，使数据能够从外部 API 采集、清洗后落入 PostgreSQL，供策略层消费：

1. **数据库初始化**：建表、索引、分区、初始数据
2. **Akshare 采集器**：龙虎榜、连板、市场情绪等数据的抓取与入库
3. **定时调度**：APScheduler 管理采集任务的触发时机与重试
4. **数据质量监控**：缺失检测、异常告警、补采机制

---

## 2. 输入依赖

| 依赖 | 说明 |
|------|------|
| `docker-compose.yml` | PostgreSQL 16 + Redis 7 已定义，`make dev-up` 启动 |
| `.env.example` | 数据库连接串、Akshare Token 等环境变量模板 |
| `docs/lhb-quant-v2/DATABASE.md` | 权威表结构，不得自行新增字段 |
| `docs/lhb-quant-v2/DATA_SOURCE.md` | Akshare 接口名称和调用参数 |

---

## 3. 具体任务

### 3.1 数据库 Schema（`backend/db/migrations/`）

```
目标文件结构：
backend/db/
├── migrations/
│   ├── 001_init_schema.sql        # 建表 DDL（完全按 DATABASE.md）
│   ├── 002_init_indexes.sql       # 复合索引、BRIN 分区索引
│   └── 003_init_seed_data.sql     # 交易所、行业基础数据
├── models.py                      # SQLAlchemy ORM 模型（与 DDL 一一对应）
└── session.py                     # 异步 session 工厂（asyncpg + SQLAlchemy 2.0）
```

**关键要求**：
- 所有时间字段统一用 `TIMESTAMPTZ`，时区 `Asia/Shanghai`
- `lhb_records` 表按 `trade_date` 月度范围分区
- 龙虎榜净买入金额用 `NUMERIC(18,2)` 而非 `FLOAT`，避免精度丢失
- 每张表必须有 `created_at`、`updated_at` 字段（触发器自动维护）

### 3.2 Akshare 采集器（`backend/collector/`）

```
backend/collector/
├── base.py             # BaseCollector 抽象类：retry、限速、指标上报
├── lhb_collector.py    # 龙虎榜数据（stock_lhb_detail_em）
├── limit_up_collector.py  # 连板数据（stock_zt_pool_em / stock_zt_pool_strong_em）
├── market_collector.py    # 市场情绪（涨跌停统计、情绪指数）
└── scheduler.py        # APScheduler 调度入口
```

**BaseCollector 必须实现**：
```python
class BaseCollector:
    MAX_RETRIES = 3
    RETRY_BACKOFF = [1, 3, 10]  # 秒，指数退避
    
    async def fetch(self) -> pd.DataFrame: ...       # 调用 Akshare，返回原始数据
    async def transform(self, df) -> list[dict]: ... # 字段映射 + 数据清洗
    async def save(self, records: list[dict]): ...   # upsert 入库（on conflict do update）
    async def run(self): ...                         # fetch → transform → save，含重试
```

**采集时间窗口**（严格遵守，避免 Akshare 封禁）：
- 龙虎榜：每个交易日 **15
<function_calls>
<invoke name="Bash">
<parameter name="command">cat ~/Develop/personal/stock_analysis/docs/prompts/S01_DATA_LAYER.md | tail -30:30 后首次采集，16:00 补采一次
- 连板池：每个交易日 **15:05** 采集（涨停池数据在收盘后 5 分钟稳定）
- 市场情绪：每个交易日 **15:10** 采集
- 历史回填：首次部署时补采最近 **60 个交易日**数据

**数据清洗规则**：
- 去除 `stock_code` 为空或格式非 6 位数字的行
- `net_buy_amount` 单位统一为**元**（Akshare 原始数据单位为万元，需 ×10000）
- 日期字段统一解析为 `datetime.date`，拒绝字符串入库
- 同一 `(trade_date, stock_code, seat_name)` 出现重复时取 `net_buy_amount` 绝对值较大者

### 3.3 定时调度（`backend/collector/scheduler.py`）

```python
# 调度配置示例（APScheduler AsyncIOScheduler）
scheduler.add_job(lhb_collector.run,    'cron', hour=15, minute=30, timezone='Asia/Shanghai')
scheduler.add_job(limit_up_collector.run,'cron', hour=15, minute=5,  timezone='Asia/Shanghai')
scheduler.add_job(market_collector.run,  'cron', hour=15, minute=10, timezone='Asia/Shanghai')
# 补采：每天 16:00 检查当日数据完整性，缺失则重采
scheduler.add_job(integrity_check.run,  'cron', hour=16, minute=0,  timezone='Asia/Shanghai')
```

- 使用 `jobstores` 将调度状态持久化到 PostgreSQL（`apscheduler_jobs` 表）
- 节假日判断：通过 `chinese_calendar` 库跳过非交易日
- 任务失败时写入 `collector_error_log` 表，并通过 Redis Pub/Sub 推送告警

### 3.4 数据质量监控（`backend/collector/integrity_check.py`）

每日 16:00 执行以下检查，任一失败写告警并触发补采：

| 检查项 | 阈值 | 处理 |
|--------|------|------|
| 龙虎榜记录数 | 当日 ≥ 5 条 | 重新采集 |
| 连板池记录数 | 当日 ≥ 1 条 | 重新采集 |
| 字段空值率 | 核心字段 < 5% | 告警 + 人工审查 |
| 金额异常 | `net_buy_amount` 绝对值 < 1e9 元 | 检查单位换算是否正确 |

---

## 4. 验收标准

运行以下命令，全部通过则视为完成：

```bash
# 4.1 数据库迁移无报错
make db-migrate

# 4.2 采集器单测通过（mock Akshare 接口）
pytest backend/tests/test_collectors.py -v

# 4.3 手动触发采集，验证数据落库
python -m backend.collector.lhb_collector --date 2024-01-15 --dry-run

# 4.4 查询验证（至少有 1 个交易日的完整数据）
psql $DATABASE_URL -c "SELECT trade_date, COUNT(*) FROM lhb_records GROUP BY trade_date ORDER BY 1 DESC LIMIT 5;"

# 4.5 调度器启动不报错
python -m backend.collector.scheduler --check
```

---

## 5. 禁止事项

- ❌ 不得使用 `FLOAT` / `DOUBLE` 存储金融金额，必须用 `NUMERIC`
- ❌ 不得在采集器里直接 `print`，必须用 `logging`（结构化 JSON 格式）
- ❌ 不得硬编码数据库连接串，必须从 `settings.py` 读取环境变量
- ❌ 不得删除 `DATABASE.md` 中已定义的字段（可新增，不可删减）
- ❌ 不得在非交易日触发 Akshare 采集（检查 `chinese_calendar`）
- ❌ 不得在单个事务里 bulk insert 超过 10000 条，分批提交

---

## 6. 输出产物

完成本阶段后，以下文件/资源应已就绪，供 S02 消费：

```
backend/db/models.py            ← ORM 模型
backend/db/session.py           ← 异步 DB session
backend/collector/*.py          ← 所有采集器
PostgreSQL:
  lhb_records                   ← 龙虎榜明细
  limit_up_pool                 ← 连板池快照
  market_sentiment              ← 市场情绪指标
  collector_error_log           ← 采集错误日志
```
