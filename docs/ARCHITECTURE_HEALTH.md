# 《项目架构健康度报告》

**项目名称**: 龙虎榜量化交易分析系统  
**评估日期**: 2026-05-01  
**评估人**: 首席架构师 (Claude)  
**代码库版本**: new-branch (生产分支)

---

## 一、执行摘要

本次架构审计聚焦于**前后端边界清晰度**和**核心引擎与数据层解耦**两个维度。经过对 `backend/domain/`、`backend/api/`、`frontend/src/` 三大模块的深度代码审查，发现系统在快速迭代中积累了**两类架构性债务**，已对可维护性和测试性构成实质威胁。

**健康度评分**: 🟡 **62/100** (中等风险)

- ✅ **优势**: 数据网关层设计优秀 (`data_gateway/`)，ADR-0001 明确禁用 ORM 的决策正确
- ⚠️ **中度风险**: Domain 层与数据库强耦合，前端缺少业务逻辑抽象层
- 🔴 **高风险**: 回测引擎 (`BacktestEngine`) 职责过载，测试覆盖率虚高（集成测试占比过大）

---

## 二、核心问题诊断

### 问题 A：Domain 层与 PostgreSQL 的「硬绑定」

**严重程度**: 🔴 **Critical**

#### 现状证据

```bash
# 统计结果：domain/ 下 39 处直接使用 pg_conn()
$ grep -r 'pg_conn\|psycopg2' backend/domain/ --include='*.py' | wc -l
39
```

**典型案例**:

1. **`backend/domain/lhb/tn_price_backfill.py`** — 直接调用 `pg_conn()` 执行 SQL
2. **`backend/domain/lhb/aggregator.py`** — 聚合逻辑直接导入 `psycopg2.extras`
3. **`backend/domain/lhb/daily_job.py`** — `conn_factory` 默认绑定 `pg_conn`
4. **`backend/domain/lhb/scheduler.py`** — `from backend.db.session import pg_conn` 硬编码
5. **`backend/domain/realtime/snapshot_writer.py`** — `pg_conn_factory` 作为参数注入但实现强依赖 PostgreSQL

#### 架构影响

| 维度 | 影响 |
|------|------|
| **可测试性** | 所有 domain 测试必须启动真实 PostgreSQL，单元测试退化为集成测试 |
| **可移植性** | 无法切换到 ClickHouse/DuckDB 等 OLAP 数据库（回测场景的性能瓶颈） |
| **并发安全** | `pg_conn()` 连接管理分散在各处，事务边界不清晰 |
| **代码复用** | 相同的 SQL 查询逻辑在多个文件中重复出现 |

#### 根因分析

ADR-0001 正确地废弃了 SQLAlchemy ORM，但**未提供替代的数据访问抽象层**。开发者在 domain 层直接使用 `pg_conn()` 成为「最短路径」，导致：

- **Repository 模式缺失**: 没有 `KlineRepository` / `LhbRepository` 等接口
- **查询逻辑散落**: 同一张表的查询 SQL 分散在多个文件中
- **Mock 困难**: 测试时必须 mock `psycopg2.connect`，而非业务接口

> **例外（设计正确的部分）**: `backend/domain/backtest/engine.py` 已通过 `DailyProvider` Protocol 做了正确的数据访问抽象，`backend/domain/robot/robot.py` 也采用不可变快照 + 纯函数模式，不直接接触数据库。这两处是本项目数据解耦的**最佳实践范本**，重构其他模块时应参考此模式。

---

### 问题 B：前端缺少业务逻辑抽象层

**严重程度**: 🟡 **High**

#### 现状证据

```bash
# 前端页面直接调用 API 的情况
$ grep -r 'fetchBacktest\|fetchKline' frontend/src/pages/ --include='*.vue' | wc -l
6
```

**典型案例**:

1. **`frontend/src/pages/Backtest.vue`** (策略对比页) — `<script setup>` 中内联 `compareBacktests`、数据格式化 (`fmtPct`、`fmtAmount`)、表格列配置、CSV 导出逻辑，超过 400 行
2. **`frontend/src/pages/BacktestRun.vue`** — 表单校验逻辑（DATE_RE、`canSubmit`、`disabledReason`）内联在组件中，无法复用

#### Composables 层现状

`frontend/src/composables/` 目录**存在但极度匮乏**，仅有 2 个文件：

- `useSnapshot.ts` — 实时快照 WebSocket 封装（设计合理）
- `useStockDrill.ts` — 股票钻取 Modal 状态（设计合理）

回测、机器人、龙虎榜等核心业务**均无对应 composable**，逻辑全部堆积在页面组件。

#### 架构影响

| 维度 | 影响 |
|------|------|
| **代码重复** | 收益率/回撤/格式化逻辑在多个页面重复 |
| **测试困难** | Vue 组件测试需要 mock API + 模拟 DOM，成本高昂 |
| **状态管理混乱** | 无统一的数据缓存策略，同一数据可能被重复请求 |
| **类型安全弱** | API 返回的 `any` 类型数据直接在模板中使用，运行时错误风险高 |

---

### 问题 C：回测任务存储的架构局限

**严重程度**: 🟡 **Medium**（已有改善迹象）

#### 现状

`backend/api/backtest.py` 中存在双轨存储：

- **进程内 `TaskStore`（in-memory dict）** — 负责实时任务状态（`PENDING/RUNNING/SUCCESS/FAILED`）
- **`backtest_runs` 表（PostgreSQL）** — STO-88 落地的 append-only audit log

两套存储**不一致**：重启后 `TaskStore` 清空，但 `backtest_runs` 保留历史。`/api/backtest/history` 端点混合读取两者，边界模糊。

#### 架构影响

| 维度 | 影响 |
|------|------|
| **一致性** | 重启后进程内任务消失，但 DB 有记录，UI 状态不一致 |
| **水平扩展** | 多进程/多实例部署时 `TaskStore` 无法共享 |
| **持久化** | 长时间回测任务（几十秒）重启即丢，用户无法恢复 |

---

## 三、量化指标

### 3.1 代码耦合度分析

| 模块 | 直接数据库调用 | 设计质量评分 | 备注 |
|------|----------------|------------|------|
| `domain/backtest/engine.py` | **0 处** ✅ | 9/10 | `DailyProvider` Protocol 设计优秀 |
| `domain/robot/robot.py` | **0 处** ✅ | 9/10 | 不可变快照 + 纯函数模式 |
| `domain/lhb/` | 8+ 处 ⚠️ | 5/10 | `aggregator`、`tn_price_backfill` 直接耦合 |
| `domain/realtime/` | 间接注入 🟡 | 7/10 | `pg_conn_factory` 参数化但实现单一 |
| `frontend/pages/` | N/A | 5/10 | 逻辑内联，composables 覆盖不足 |

### 3.2 测试覆盖率结构分析

```
总覆盖率: 79.26%  ✅ (表面达标)

但结构分析：
- backend/domain/backtest/  — 引擎层覆盖率高，因为 DailyProvider 可 mock
- backend/domain/lhb/       — 覆盖率依赖真实 DB 的集成测试撑起
- frontend/                 — vitest 187 tests，但主要覆盖 utils 纯函数
                              Vue 组件本身（含页面逻辑）覆盖极低
```

**结论**: 当前 75% 覆盖率门槛中，依赖真实 PostgreSQL 的测试占比偏高。纯内存可运行的单元测试覆盖率估计在 50% 以下。

---

## 四、对比业界最佳实践

### 4.1 Domain 层数据访问

| 项目 | 数据访问模式 | 可测试性 |
|------|--------------|----------|
| **本项目 engine.py** | `DailyProvider` Protocol + 依赖注入 | ✅ 可用内存数据 mock |
| **本项目 lhb/aggregator** | 直接调用 `psycopg2` | ❌ 需要真实数据库 |
| **Backtrader** | `DataFeed` 抽象接口 + CSV/DB 实现 | ✅ 可用内存数据 mock |
| **Zipline** | `DataPortal` + `BcolzMinuteBarReader` | ✅ 可用 Pandas DataFrame mock |

**结论**: 本项目 `engine.py` 的 `DailyProvider` 模式已经达到业界水准，问题在于**其他模块未跟进**。

### 4.2 前端业务逻辑层

| 项目 | 架构模式 | 逻辑复用 |
|------|----------|----------|
| **本项目** | 部分 composables（仅 2 个）| ⚠️ 核心业务逻辑内联在页面 |
| **Vue 官方推荐** | Composables + Pinia Store | ✅ 逻辑可独立测试 |
| **Grafana** | `DataSource` 插件 + Panel 组件 | ✅ 数据源可插拔 |

---

## 五、全局重构路线图（三步走）

### 🎯 第一步：补齐 LHB / Realtime Domain 层的数据访问抽象（预计 1~2 周）

**目标**: 以 `engine.py` 的 `DailyProvider` 为范本，将 `domain/lhb/` 和 `domain/realtime/` 中残留的直接 `pg_conn()` 调用替换为 Protocol 接口。

**不是引入全新架构层**，而是**补齐已有模式的缺口**。

#### 核心改动

```python
# 新增：backend/domain/lhb/provider.py
from typing import Protocol

class LhbDataProvider(Protocol):
    def get_lhb_records(self, date: str) -> list[dict]: ...
    def get_agg_stats(self, ts_code: str) -> dict | None: ...
    def save_agg_stats(self, rows: list[dict]) -> None: ...

# 重构：backend/domain/lhb/aggregator.py
class LhbAggregator:
    def __init__(self, provider: LhbDataProvider):  # ✅ 依赖注入
        self.provider = provider

    def aggregate(self, date: str) -> list[dict]:
        records = self.provider.get_lhb_records(date)  # ✅ 不直接碰 DB
        ...

# 生产实现：backend/infrastructure/lhb_pg_provider.py
class LhbPgProvider:
    def get_lhb_records(self, date: str) -> list[dict]:
        with pg_conn() as conn, conn.cursor() as cur:  # DB 只在 infrastructure 层
            cur.execute("SELECT * FROM lhb_records WHERE trade_date = %s", (date,))
            return cur.fetchall()
```

#### 受益模块

- `domain/lhb/aggregator.py` — 聚合逻辑可纯内存单测
- `domain/lhb/tn_price_backfill.py` — 回填逻辑可 mock
- `domain/lhb/scheduler.py` — `conn_factory` 替换为 Provider

#### 验收标准

- [ ] `domain/lhb/` 下无直接 `import psycopg2` / 直接 `pg_conn()` 调用
- [ ] 新增纯内存单测覆盖 `aggregator.aggregate()` 主路径
- [ ] pytest 全绿，覆盖率 ≥ 79%
- [ ] 新增 `docs/ADR/ADR-0004-domain-provider-pattern.md`

---

### 🎯 第二步：前端 Composables 扩充（预计 2~3 周）

**目标**: 将 `Backtest.vue`、`BacktestHistory.vue` 等页面中的业务逻辑提取到 composables，使页面组件只负责模板渲染。

#### 优先提取的 Composables

```typescript
// frontend/src/composables/useBacktestCompare.ts
export function useBacktestCompare() {
  const running = ref(false)
  const report = ref<ComparisonReport | null>(null)

  async function runCompare(params: CompareReq) {
    running.value = true
    try {
      report.value = await compareBacktests(params)
    } finally {
      running.value = false
    }
  }

  function exportCsv() { ... }  // CSV 导出逻辑从 Backtest.vue 移入

  return { running, report, runCompare, exportCsv }
}

// frontend/src/composables/useBacktestForm.ts
export function useBacktestForm() {
  const DATE_RE = /^\d{4}-\d{2}-\d{2}$/
  const form = reactive({ ... })

  const canSubmit = computed(() => ...)
  const disabledReason = computed(() => ...)

  return { form, canSubmit, disabledReason }
  // ✅ 可在 vitest 中直接测试，无需 mount 组件
}
```

#### 验收标准

- [ ] `Backtest.vue` `<script setup>` 缩减至 ≤ 80 行
- [ ] `BacktestRun.vue` `<script setup>` 缩减至 ≤ 50 行
- [ ] 新 composables 配对 `.test.ts`，vitest 覆盖主路径
- [ ] 前端 vitest 全绿

---

### 🎯 第三步：统一回测任务存储（预计 1 周）

**目标**: 消除 `TaskStore`（in-memory）与 `backtest_runs`（PostgreSQL）的双轨存储，以 DB 为单一事实源。

#### 核心改动

```python
# backend/api/backtest.py
# 重构后：TaskStore 仅作为进程内 cache（TTL 短），
# 所有持久化读写走 backtest_runs 表

def get_task_status(task_id: str) -> BacktestTask:
    # 1. 先查内存 cache（快）
    if task := _store.get(task_id):
        return task
    # 2. 内存没有则读 DB（重启恢复）
    return _load_from_db(task_id)
```

#### 验收标准

- [ ] 服务重启后 `/api/backtest/history` 仍能返回历史任务
- [ ] `backtest_runs` 表数据与进程内状态一致
- [ ] 新增 `test_backtest_persist.py` 重启恢复场景测试

---

## 六、风险评估

| 风险项 | 概率 | 影响 | 缓解措施 |
|--------|------|------|----------|
| **重构周期过长** | 中 | 高 | 分三步走，每步独立交付，可随时暂停 |
| **测试覆盖率下降** | 低 | 高 | 每步完成后必须保持 ≥ 79% 门槛 |
| **性能回退** | 低 | 中 | Provider 层使用连接池，不额外增加 DB 调用次数 |
| **破坏现有稳定功能** | 低 | 高 | 每步重构保持接口不变，仅移动逻辑位置 |

---

## 七、结论与建议

### 7.1 核心发现

1. **回测引擎 `engine.py` + `robot.py` 已是最佳实践** — `DailyProvider` Protocol + 不可变快照是本项目数据解耦的成功案例
2. **`domain/lhb/` 未跟进** — 聚合、回填、调度层仍直接耦合 PostgreSQL，是优先改造目标
3. **前端 composables 骨架存在但覆盖不足** — `useSnapshot` / `useStockDrill` 已正确抽象，需向核心业务扩展
4. **双轨任务存储** — 近期 STO-88 引入了 `backtest_runs` 表，但与 in-memory `TaskStore` 共存，需要在第三步统一

### 7.2 行动优先级

| 优先级 | 任务 | 预计工期 | 收益 |
|--------|------|----------|------|
| P0 | 第一步：LHB domain 层 Provider 化 | 1~2 周 | 单元测试速度提升，可离线开发 |
| P1 | 第二步：前端 composables 扩充 | 2~3 周 | 代码复用，前端逻辑可测 |
| P2 | 第三步：任务存储统一 | 1 周 | 重启恢复，为多进程扩展铺路 |

### 7.3 成功标准

- ✅ `domain/lhb/` 单元测试无需真实数据库
- ✅ 前端核心页面 composable 测试覆盖
- ✅ 服务重启后任务历史不丢失
- ✅ pytest 覆盖率保持 ≥ 79%
- ✅ vitest 全绿

---

## 附录：快速诊断命令

```bash
# 检查 Domain 层数据库耦合（目标：降至 0）
grep -r 'pg_conn\|psycopg2' backend/domain/ --include='*.py' | wc -l

# 检查前端 composables 覆盖情况
ls -la frontend/src/composables/

# 验证回测引擎 Provider 模式
grep -n 'DailyProvider\|Protocol' backend/domain/backtest/engine.py

# 跑全量测试 + 覆盖率
python3 -m pytest
```

---

**报告完成日期**: 2026-05-01  
**下次审计建议**: 完成第一步重构后复查（预计 2026-05-15）  
**相关 ADR**: `docs/ADR/0001-drop-sqlalchemy-and-alembic.md`、`docs/ADR/0002-ui-framework-decision.md`
