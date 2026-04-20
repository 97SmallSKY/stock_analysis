# CLAUDE.md — 给接手 agent 的快速上手说明

> 本文件面向任何后续进入本仓库工作的 Claude / sub-agent。阅读顺序:本文 → `docs/PROGRESS.md` → `docs/ADR/` → 具体模块代码。

## 1. 项目定位

**龙虎榜量化交易分析系统**(中国 A 股)。职责链:

```
akshare/tushare → 数据网关(熔断/缓存/优先级) → 原始表 (PostgreSQL)
                                                   ↓
                      领域层(parser/ingestor/aggregator)
                                                   ↓
                      因子计算 / 评分(scoring) / 回测 / 机器人
                                                   ↓
                      FastAPI + Vue3 前端 + APScheduler 定时任务
```

部署目标:**内网服务器 `jd`**(国内源,SG 代理必须绕过,见 `backend/core/startup.py`)。

### 1.1 分支策略(⚠️ 强约束)

本仓库有**两套完全独立的代码**,分别在不同分支上演进,**不合并、不 rebase、不 cherry-pick**:

| 分支 | 内容 | 状态 | 是否部署 |
|---|---|---|---|
| `new-branch` | **重写版代码**,当前唯一在演进的主开发分支 | 🟢 活跃 | ✅ jd 生产 |
| `main` | **旧版代码**(历史遗留),与 new-branch 架构完全不同 | ⚪ 冻结 | ❌ 不部署 |

**铁律**(任何 agent 必须遵守):
- AI 默认始终工作在 `new-branch`
- 禁止做任何 `main` ↔ `new-branch` 的跨分支操作(merge / rebase / cherry-pick)
- 修 bug / 加功能都改 `new-branch`,不要改 `main`
- 若需要引用旧版代码做参考,用 `git show main:path/to/file` 只读查看

## 2. 文档优先级(事实源)

| 优先级 | 文件 | 作用 |
|---|---|---|
| **P0**(强制) | `docs/prompts/linear/README.md` | **Linear 协作规约总纲**,按角色路由到 `linear/{pm,dev,test,ops}.md`,含三方共识仲裁 —— AI 必读 |
| **P0**(按角色选读) | `docs/prompts/linear/pm.md` / `dev.md` / `test.md` / `ops.md` | 各角色自包含闭环(上游/工作/下游/互查/反模式) |
| **P0**(强制) | `docs/prompts/7x24_agent.md` | **7×24 运维巡检手册**,时间驱动的监控循环、修复链路、部署红线 |
| **P0**(强制) | `docs/PROGRESS.md` | 当前真实进度 / 变更日志,唯一事实源 |
| **P0**(强制) | `docs/ADR/*.md` | 架构决策记录,冲突时以此为准 |
| **P0**(强制) | 本文 `CLAUDE.md` | 项目说明书(你在读) |
| **P0**(强制) | `docs/CHANGELOG.md` | **面向用户**的改动记录(新特性/页面改动/影响体验的修复);前端「特性介绍」tab |
| **P0**(强制) | `docs/DEVLOG.md` | **面向开发者**的技术细节(重构/性能/bug 根因/部署基建);前端「开发日志」tab |
| P1 | `docs/designs/` | 复杂任务总体设计(周期长 / 跨模块) |
| P1 | `docs/optimizations/` | 单点优化技术笔记 |
| P1 | `docs/ops/` | 运维手册 / 事故复盘 |
| P1 | `docs/frontend/` | 前端组件契约 & 调用链约定(见 [`kline-modal-contract.md`](./docs/frontend/kline-modal-contract.md) —— 改 K 线 Modal 前必读) |
| P1 | `docs/IMPLEMENTATION_ROADMAP.md` | W1~W12 周次计划,历史参考;已被多个"偏离说明"标注 |
| P1 | `docs/CODE_REVIEW.md` | 正在处理的遗留问题清单 |
| P2 | `README.md` | 对外介绍,面向新接手者 |
| P3 | `docs/重写方案_v2.md` / `docs/龙虎榜量化交易分析系统 — 完整重写方案.md` | 初始设计稿,**历史文档**,与 ADR 冲突时以 ADR 为准 |

**重要约定**:
- 任何 agent 修改代码后,必须同步更新 PROGRESS.md 的"近期变更日志"章节
- **7×24 自动运维/开发 Agent 启动前**:先读 `docs/prompts/7x24_agent.md`(定义循环规则、红线、文档规范),再动工
- **改动分流**(P20.1 细化):
  - 新特性 / 页面改动 / 用户能感知的 bugfix → `CHANGELOG.md`(说人话,用户视角)
  - 代码重构 / 性能优化 / bug 根因 / 监控部署基建 → `DEVLOG.md`(技术细节,开发视角)
  - 单点性能笔记 → `docs/optimizations/`
  - 复杂任务 → 先写 `docs/designs/` 再拆子任务
  - 事故复盘 → `docs/ops/YYYY-MM-DD-<slug>.md`

## 3. 已生效的架构决策(摘要)

- **ADR-0001**:放弃 SQLAlchemy ORM 与 Alembic,统一用 psycopg2 + `db/init/*.sql` 版本化 SQL 脚本。**凡现行文档里出现 `alembic upgrade head` / `from sqlalchemy import ...` 的,要么是历史文档,要么是死代码,要核实后清理**。

## 4. 现行目录结构(实际!)

```
stock_analysis/
├── backend/
│   ├── core/                 # bootstrap (清代理) / 日志 / metrics / runtime
│   ├── data_gateway/         # 数据网关(唯一!根目录旧 data_sources/ 已删)
│   │   ├── adapters/         # akshare / tushare / sina / crawler
│   │   ├── manager.py        # DataSourceManager
│   │   ├── priority_config.py
│   │   ├── cache_manager.py
│   │   ├── circuit_breaker.py
│   │   └── health_checker.py
│   ├── db/                   # psycopg2 连接封装(无 ORM)
│   │   ├── session.py        # pg_conn() 上下文管理器
│   │   └── writer.py         # upsert_* 系列 helper
│   ├── ingest/               # 数据采集 CLI 入口(薄壳)
│   │   ├── base.py           # df_to_rows / get_manager / normalize_date
│   │   ├── stock_info.py / calendar.py / kline.py / sentiment.py
│   │   └── lhb.py            # ★薄壳,run() 委托给 domain.lhb.daily_job.run_range
│   ├── domain/
│   │   ├── lhb/              # ★龙虎榜领域层(W3+ 接管 ingest/lhb 的重逻辑)
│   │   │   ├── parser.py     # DataFrame → LhbRecord/LhbSeat
│   │   │   ├── ingestor.py   # ingest_records / ensure_stock_info_skel
│   │   │   ├── aggregator.py # lhb_agg_stats 聚合
│   │   │   ├── daily_job.py  # run_single_day / run_range
│   │   │   └── scheduler.py  # LhbDailyScheduler
│   │   ├── realtime/         # 实时行情 snapshot
│   │   ├── strategy/ / backtest/ / reports/ / robot/
│   ├── api/                  # FastAPI 路由
│   ├── scoring/              # 因子 / 综合分
│   └── cli.py                # python -m backend.cli ingest lhb/kline/...
├── frontend/                 # Vue 3
├── db/
│   ├── init/                 # 版本化 SQL 迁移(NN_xxx.sql,不用 Alembic)
│   └── 01_schema_all.sql     # 完整 schema 快照
├── docs/                     # 文档(见上一节的优先级)
├── scripts/                  # deploy.sh / backfill_lhb.py / backup_pg.sh
├── tests/                    # pytest(含 integration/,默认不跑)
└── pytest.ini
```

**命名陷阱**:
- `backend/ingest/lhb.py` 和 `backend/domain/lhb/` **不重复**:ingest 是 CLI 薄壳,domain 是真实现。改逻辑去 domain。
- 前端 / API 里的 `data_sources` 是**路由/文件名**,不是 Python 包。`import data_sources` 会报错(已删)。
- `backend/ingest/sentiment.py` 仍 `from backend.ingest.lhb import _load_trading_days`,改动 `ingest/lhb.py` 时要保留此符号。

## 5. 测试与质量门槛

- 运行:`python3 -m pytest`(默认读 `pytest.ini`,集成测试已 `--ignore`)
- 覆盖率门槛:`--cov-fail-under=75`(2025-04-18 抬升,截至 P4 完成实际 75.81%)
- 集成测试:`tests/test_db_crud.py` / `tests/test_data_fetch.py` 需真 PG + 外网,**本地显式跑**
- 规则:新功能必须配单元测试,禁止用"太难测"作为理由跳过

## 6. 部署路径(要让 P5 能跑)

- 服务器:`jd`(别名见 `~/.ssh/config`)
- 入口:`ssh jd "cd ~/stock_analysis && git pull && ./scripts/deploy.sh"`
- `deploy.sh` 做:清代理 → pip install → 前端 build → 应用 `db/init/*.sql` 未执行的迁移 → `systemctl restart` → nginx reload → healthcheck
- **proxy 是核心坑**:jd 默认注入 SG 代理,所有入口脚本**第一行**必须 `from backend.core.startup import bootstrap; bootstrap()`

## 7. 当前任务循环(agent 接手请先看 TaskList)

用户期望模式:**自主决策循环 → 实现+测试+文档 → 进入下一个**,直到**成功部署在 jd 服务器**循环才停。例外:
- 实现 **sub-agent** 之前,把 sub-agent 设计问题抛给用户并暂停循环
- 每步必须保证 pytest 全绿,覆盖率 ≥ 75%

已完成 / 进行中任务见 `TaskList` 工具输出。截至本文写入时:
- ✅ P1 同步进度文档
- ✅ P2 追认 ADR-0001,清理 Alembic/SQLAlchemy 死引用
- ✅ P3 删除 `data_sources/`,ingest/lhb 降级为薄壳
- ✅ P4 覆盖率门槛:已从 70% 抬到 75%,补 aggregator 测试 +11 case,实际 75.81%
- ⏳ P5 jd 服务器端集成测试定时任务 → 最终部署验证

## 8. 常用自检命令

```bash
# 快速导入冒烟(检查模块能否被导入)
python3 -c 'from backend.ingest import lhb, sentiment; from backend.domain.lhb import daily_job, aggregator; print("ok")'

# 跑全量测试 + 覆盖率
python3 -m pytest

# 只跑某模块的测试(开发时)
python3 -m pytest tests/test_aggregator.py --no-cov -q

# 搜残留的 SQLAlchemy/Alembic 死引用
grep -rn --include='*.py' -E '(from|import) (sqlalchemy|alembic)' backend/ scripts/ tests/
```

## 9. 写代码时的硬约束

1. **数据库**:只能用 `backend.db.session.pg_conn()` 获取连接,事务由上下文管理器管。严禁 `import sqlalchemy`。
2. **SQL 迁移**:新建 `db/init/NN_xxx.sql`,文件名顺序即执行顺序。不要生成 Alembic 版本。
3. **代理**:任何新入口脚本第一行必须调用 `bootstrap()`;所有 `requests.Session` 都要 `trust_env=False`。
4. **文件编码**:文档中文习惯用全角标点(`:` `(` `,`),搜索替换时注意码点差异,推荐用 Python 脚本做精确字节匹配。
5. **测试**:新代码必须配测试,`MagicMock` + `_FakeConn/_FakeCursor` 模式见 `tests/test_aggregator.py`。
6. **金额单位**(2026-04-20 P14):
   - **LHB 席位 + 主表的 `buy_amount / sell_amount / net_amount` 单位是「万元」**(AkShare 原始;后端未做单位换算)。前端展示需要用 `fmtAmountWan`,**不能**用 `fmtAmount`(后者把数值当元处理,会多除一次 1e4)。
   - K 线 `amount` / 回测 `total_assets / cash` / 机器人 `cash / total_assets` 单位是「元」,用 `fmtAmount`。
   - 改任何 `*_amount` 字段的展示前,先确认后端 SQL 是直接存 AkShare 原值(万元)还是有换算(元)。
7. **前端页面拆分原则**(2026-04-20 P13):新功能能独立成路由的就独立(`/backtest`-对比 + `/backtest-run`-任务历史),而不是塞 Tab;Tab 用于同一业务的「视图切换」而非「功能切换」。
8. **聚合/KPI 纯函数必须独立到 `utils/*.ts` 并配单测**(参考 `utils/signals_agg.ts` / `backtest_history.ts`),不要把统计逻辑留在 `.vue` 的 `<script setup>` 里(Vue 组件测试成本高)。

## 10. 常见任务入口映射

| 用户诉求 | 改动文件 |
|---|---|
| 加 API 端点 | `backend/api/<mod>.py` + `tests/test_api_<mod>.py` |
| 改 LHB 字段 | `backend/api/lhb.py`(SQL)+ `frontend/src/api/lhb.ts`(类型)+ 消费页 |
| 加前端页 | `frontend/src/pages/X.vue` + `router/index.ts` + `App.vue`(菜单)|
| 加机器人字段 | `backend/domain/robot/robot.py`(`RobotSnapshot`)+ `api/robots.py`(`_snapshot_to_dict`)+ `frontend/src/api/robots.ts` |
| 加聚合纯函数 | `frontend/src/utils/<x>.ts` + `<x>.test.ts` |

## 11. 前后端接口契约同步(P19.13 血泪 · STO-40)

> **事故回放**(2026-04-20 P19.13):前端 `fetchDailyKline({ limit: 2000 })` 超过后端 `Query(..., le=1000)` 边界 → 所有日 K 请求 422 Unprocessable Entity。用户几小时看不到日 K,最后才被发现。根因:前后端各改各的,互不看对方边界。

### 11.1 红线(改 API 参数时必做)

1. **改前端 `fetchXxx` 的 query 参数前**,必须 `grep` 后端 FastAPI 的 `Query(..., le=, ge=, pattern=)` 拿到真实边界:
   ```bash
   # 例:改 /api/kline/daily 的 limit 前
   grep -n 'Query' backend/api/kline.py
   # 看到 limit: int = Query(500, ge=1, le=1000) → 前端 limit 不能超过 1000
   ```
2. **改后端边界前**,必须 `grep` 所有前端 `fetchXxx` 调用,确认没有调用方在用超出新边界的值:
   ```bash
   grep -rn 'fetchDailyKline\|/api/kline/daily' frontend/src/
   ```
3. **边界绝对值不走小本子**:边界写在后端 `Query(...)` 作为单一事实源,前端 `api/*.ts` 里的 TypeScript 类型仅提供「可选性」,数值边界不在前端定义(防小本子脱节)。

### 11.2 API 单测 checklist(新端点必写)

写新 FastAPI 端点时,**参数越界 case 必须有**。最小模板:

```python
# tests/test_api_xxx.py
def test_limit_above_max_returns_422(client):
    """契约测试:limit > 后端上限 (le=1000) 应 422,不是被 clip 到 1000。"""
    r = client.get("/api/kline/daily?ts_code=000001.SZ&limit=1001")
    assert r.status_code == 422
    # 422 的 detail 里应包含哪个参数越界,前端看得懂
    assert "limit" in r.text


def test_limit_below_min_returns_422(client):
    r = client.get("/api/kline/daily?ts_code=000001.SZ&limit=0")
    assert r.status_code == 422
```

### 11.3 至少一次真实 `curl` 验证

PR 提交前,必须对**每个改动的端点**做一次真实 `curl`(不只看 pytest 里的 TestClient mock):

```bash
# 健康路径 (200)
curl -i 'http://localhost:8000/api/kline/daily?ts_code=000001.SZ&limit=500'
# 边界正上方 (422)
curl -i 'http://localhost:8000/api/kline/daily?ts_code=000001.SZ&limit=1001'
# 边界正下方 (200)
curl -i 'http://localhost:8000/api/kline/daily?ts_code=000001.SZ&limit=1000'
```

原因:pytest 的 `TestClient` 共享 ASGI app 内存,可能掩盖 uvicorn/nginx 层的真实行为(超时、body size、gzip)。一次 curl 相当于最后 80 分到 99 分的保险。

### 11.4 现有端点边界速查(改之前先看)

| 端点 | 关键参数边界 | 定义位置 |
|---|---|---|
| `GET /api/kline/daily` | `limit: 1 ≤ ≤ 5000` / `ts_code: required` | `backend/api/kline.py` |
| `GET /api/kline/weekly` | `limit: 1 ≤ ≤ 2000` | `backend/api/kline.py` |
| `GET /api/lhb` | `limit: 1 ≤ ≤ 1000` / `order_by` 白名单 3 值 | `backend/api/lhb.py` |
| `GET /api/lhb/seats` | `limit: 1 ≤ ≤ 2000` / `direction ∈ {BUY, SELL}` | `backend/api/lhb.py` |

(发现新的请在此补,不要散在代码里)

---

**最后更新:2026-04-21**(若与 PROGRESS.md 冲突,以 PROGRESS.md 为准)
