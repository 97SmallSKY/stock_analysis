# STO-37 Pydantic 响应 Schema 设计文档

> **状态**:技术面草稿(Dev v1)· 等待 PM 拍板 5 项决策后进入执行
>
> **范围**:本文只做**技术面**(Dev 职责内可决)。业务面决策项在 §5 列出,等 PM。

---

## 0. 背景

**P18.13b 事故**(`docs/ops/2026-04-20-p18.13b-schema-mismatch.md`):API 用 `dict` + `setdefault` 硬塞字段,DB schema 漂移时 API 静默返回不完整数据,前端拿到的字段集与类型都无强约束。后复盘建议引入 Pydantic BaseModel 做响应层强类型校验,CI 能在测试阶段提早发现 schema drift。

**上游**:[STO-37](https://linear.app/xiaotian-dev/issue/STO-37) @ P18.13b 遗留建议

---

## 1. 现状盘点(Dev · 读完 9 个 API 文件)

| 文件 | 响应现状 | `response_model`? | 字典硬塞? |
|---|---|---|---|
| `dashboard.py` | 顶层 `{sentiment: {...}, top_lhb: [...], adapters: [...]}` 嵌套 dict | ❌ | ✅ 6 处 `setdefault`(P18.13b 后补) |
| `lhb.py` | `{total, limit, offset, rows: [dict]}` | ❌ | ❌(但无类型约束,字段偏移易漏) |
| `kline.py` | `{items: [dict], ts_code, freq, ...}` | ❌ | ❌ |
| `robots.py` | `{ok, snapshot: dict}` / `{robots: [dict]}` | ❌ | ❌ |
| `backtest.py` | `SubmitResp / TaskResp / list[TaskResp]` ← 已部分用 Pydantic | ✅ 2 处 | ❌ |
| `data_sources.py` | `dict` | ❌ | ❌ |
| `changelog.py` | `{markdown, ...}` | ❌ | ❌ |
| `reports.py` | `dict` | ❌ | ❌ |
| `ws_snapshot.py` | N/A(WebSocket,不适用 response_model) | — | — |

**结论**:8 个 API 文件全裸 dict,1 个部分 Pydantic;6 处 `setdefault` 是 schema-vs-API 历史债的直接证据。

---

## 2. 三个核心 API 的字段清单

### 2.1 `/api/dashboard`(最复杂,6 组嵌套)

root 结构:`{ sentiment, top_lhb, adapters }`

#### `sentiment` (20+ 字段)

从 `market_sentiment` 主查 + `kline_daily` / `lhb_stock_daily` 聚合补齐:

| 字段 | 类型 | 来源 | 可空? |
|---|---|---|---|
| `trade_date` | `str`(ISO 8601) | market_sentiment | N |
| `limit_up_count` | `int` | market_sentiment | N |
| `limit_down_count` | `int` | market_sentiment | N |
| `first_limit_up` | `int` | market_sentiment | Y |
| `cont_limit_up` | `int` | market_sentiment | Y |
| `max_cont_board` | `int` | market_sentiment | Y |
| `up_down_ratio` | `float` | market_sentiment | N |
| `avg_pct_change` | `float` | market_sentiment | Y |
| `median_pct_change` | `float` | market_sentiment | Y |
| `strong_stock_count` | `int` | market_sentiment | Y |
| `weak_stock_count` | `int` | market_sentiment | Y |
| `lhb_stock_count` | `int` | **lhb_stock_daily 实时聚合** | Y |
| `lhb_total_net` | `float` | **lhb_stock_daily 实时聚合** | Y |
| `lhb_avg_score` | `float` | **lhb_stock_daily 实时聚合** | Y |
| `sentiment_score` | `float` | market_sentiment | N |
| `sentiment_level` | `str`(HOT/WARM/NEUTRAL/COOL/COLD) | market_sentiment | N |
| `sentiment_change` | `float` | **实时 diff 前一日** | Y |
| `total_stocks` | `int` | **kline_daily 实时聚合(P18.13b)** | Y |
| `up_count` | `int` | **kline_daily 实时聚合** | Y |
| `down_count` | `int` | **kline_daily 实时聚合** | Y |

**提议 Pydantic 模型**(Pydantic v2 语法):

```python
from pydantic import BaseModel, Field
from typing import Literal

SentimentLevel = Literal["HOT", "WARM", "NEUTRAL", "COOL", "COLD"]

class SentimentModel(BaseModel):
    trade_date: str
    # 涨跌停
    limit_up_count: int
    limit_down_count: int
    first_limit_up: int | None = None
    cont_limit_up: int | None = None
    max_cont_board: int | None = None
    # 涨跌比
    up_down_ratio: float
    avg_pct_change: float | None = None
    median_pct_change: float | None = None
    strong_stock_count: int | None = None
    weak_stock_count: int | None = None
    # LHB 聚合
    lhb_stock_count: int | None = None
    lhb_total_net: float | None = None
    lhb_avg_score: float | None = None
    # 情绪分
    sentiment_score: float
    sentiment_level: SentimentLevel
    sentiment_change: float | None = None
    # kline 聚合
    total_stocks: int | None = None
    up_count: int | None = None
    down_count: int | None = None
```

#### `top_lhb` (10 字段 × N 行)

```python
class TopLhbItem(BaseModel):
    ts_code: str
    name: str | None
    trade_date: str
    reason: str | None
    close: float | None
    pct_change: float | None
    net_amount: float | None
    buy_amount: float | None
    turnover: float | None
    score: float | None
```

#### `adapters` (5 字段 × N 行)

```python
class AdapterStatus(BaseModel):
    name: str
    healthy: bool | None
    last_ok_at: str | None   # ISO
    last_err_at: str | None
    cb_state: Literal["closed", "half_open", "open"] | None
```

#### Root

```python
class DashboardResponse(BaseModel):
    sentiment: SentimentModel | None
    top_lhb: list[TopLhbItem]
    adapters: list[AdapterStatus]
```

### 2.2 `/api/lhb` (3 端点)

- `GET /api/lhb` → `{total: int, limit: int, offset: int, rows: [LhbRow]}`,含 15+ 字段的 `LhbRow`(条件字段 `t1_pnl_pct` / `t1_limit_up` / `t5_pnl_pct` / `t5_high_pct` 仅 `with_tn=true` 时存在)
- `GET /api/lhb/seats` → `{rows: [SeatRow], n: int}`
- `GET /api/lhb/stat/{type}/{key}` → 单条 `{stat_type, stat_key, stat_period, payload: dict, updated_at}`

### 2.3 `/api/kline/*` (4 端点)

- `GET /daily` / `/weekly` / `/minute` / `/daterange`
- 共享 `KlineBar` 模型(7 字段)
- `daily` / `weekly` / `minute` 返回 `{items: [KlineBar], ts_code, freq, from, to, limit}`
- `daterange` 返回 `{ts_code, freq, min: str, max: str, count: int}`

---

## 3. 严模式 vs 宽模式

| 维度 | 严模式 (`extra='forbid'`) | **宽模式 (`extra='ignore'`,推荐起步)** |
|---|---|---|
| DB 多返回字段 | Pydantic 校验失败,API 500 | Pydantic 忽略多出字段,前端收到约定集合 |
| DB 缺字段 | 如果声明 required 会 500;声明 `None = None` 会静默 | 一样 |
| 优点 | 最强契约保证,schema drift 必然爆红 | 平滑过渡,不破现网 |
| 风险 | **会影响现网**,任何漂移爆 500 | 保留「多返回前端收不到」的隐患,但温和 |
| 迁移成本 | 需回填 DB 所有字段 | 只声明前端需要的字段即可 |

**Dev 推荐**:**宽模式起步 + 部署 7 天后切严模式**。宽模式下 FastAPI 的 `response_model` 仍会做**类型校验**(字段声明为 `int` 但 DB 返回 `str` 就会报错),只是对「多字段」宽容。这比当前**完全无校验**的状态安全得多。

### Pydantic v2 配置示例

```python
from pydantic import BaseModel, ConfigDict

class _LooseModel(BaseModel):
    # 宽模式:允许 DB 多返回字段
    model_config = ConfigDict(extra='ignore')

class SentimentModel(_LooseModel):
    trade_date: str
    ...
```

日后切严模式只需改 `extra='forbid'`,不改模型定义。

---

## 4. 响应层 vs DAO 层

本文档**只做响应层**(FastAPI `response_model=`),不动 DAO / writer。

| 层 | 本 issue | 另开 issue |
|---|---|---|
| **API 响应层**(`response_model=`) | ✅ | — |
| **API 请求层**(`request_model=`) | 已部分做(backtest.py) | 可再顺手补齐 |
| **DAO 层**(writer 返回 Pydantic) | ❌ 不做 | STO-37b(建议 PM 新建) |
| **DB schema 生成**(从 Pydantic 反推 DDL) | ❌ 不做 | 超出本项目范围 |

---

## 5. 分阶段 PR 计划(Phase 1a/1b/1c/2)

### Phase 1a · `/api/kline/*`(最简,作为范式)

- **估时**:2~3h(含测试)
- **交付**:
  - `backend/api/schemas/kline.py`(`KlineBar` + 4 个 resp-wrapper)
  - `backend/api/kline.py` 4 个端点加 `response_model=`
  - `tests/test_api_kline.py` 补「缺字段 / 类型错 / 多余字段静默」3 组契约测试
- **why 先做它**:
  - kline 结构最简单(7 字段 × 1 行类型)
  - 已有 `test_api_kline.py` 23 个测试(P19.13/STO-40 边界测试已立范式),增量补契约测试简单

### Phase 1b · `/api/lhb/*`

- **估时**:3~4h
- **交付**:`backend/api/schemas/lhb.py`(3 row-models + 3 resp-wrapper)
- **难点**:`with_tn=true` 条件字段(t1/t5)需要用 `Optional` + 文档说明

### Phase 1c · `/api/dashboard`

- **估时**:4~6h
- **交付**:`backend/api/schemas/dashboard.py`(6 sub-models + 1 root)
- **难点**:嵌套最深;`sentiment` 20 字段均需分类可空性;有实时聚合逻辑的字段需处理 None

### Phase 2(建议 PM 另开 issue)

- DAO 层 Pydantic 包装(`db/writer.py` 查询返回 Pydantic,彻底根治 schema drift)
- 预估 1~2 天

---

## 6. 需 PM 拍板的 5 项决策

| # | 决策 | Dev 推荐 |
|---|---|---|
| 1 | 起步用严模式还是宽模式? | **宽模式**(`extra='ignore'`),7 天后再切严 |
| 2 | 本 issue 只做响应层 or 一步到位? | **响应层**,DAO 层另开 issue |
| 3 | 分阶段顺序? | **kline → lhb → dashboard**(由简到繁) |
| 4 | 验收 `response_model 校验开启` 的具体含义? | 指**开启类型校验**(字段缺失 + 类型错都红),不 forbid extra |
| 5 | 验收「故意改 DB 查询缺字段 CI 必须红」:若宽模式会静默通过,是否改为「故意改字段类型 CI 必须红」? | **改为后者**,宽模式下这才是可验证的契约红线 |

---

## 7. Dev 准备状态

- ✅ 已读完 9 个 API 文件
- ✅ 已识别 `dashboard.py` 6 处 `setdefault`(P18.13b 遗留,Pydantic 化后可删)
- ✅ `backend/api/backtest.py` 的 `SubmitResp/TaskResp` 可作为 Pydantic v2 范式
- ✅ 熟悉 FastAPI `response_model` / Pydantic v2 的 `ConfigDict`
- ⏳ 等 PM 拍板后立即起 Phase 1a `improve/STO-37a-pydantic-kline` 分支

---

## 8. 相关文档

- P18.13b 事故复盘:`docs/ops/2026-04-20-p18.13b-schema-mismatch.md`
- STO-40 前后端契约同步:`CLAUDE.md §11`(Dev 之前写的血泪教训,互补)
- ADR-0001:本项目不用 SQLAlchemy,psycopg2 直接返回 dict

---

**最后更新**:2026-04-21(Dev · v1 草稿,等 PM 拍板)
