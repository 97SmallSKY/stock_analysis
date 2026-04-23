# 策略分类体系规划(10 大类 → 近期聚焦 3 类)

> 本文是 STO-48 的设计文档产物(拆自 STO-45 伞 issue)。
> **不动代码**,仅作为 PM 评审依据。PM 评审通过后拆为具体 dev 子 issue 执行。
> 作者:Dev · 2026-04-23 · 状态:待 PM 评审

---

## 1. 现状盘点

### 1.1 数据模型现状

`strategies` 表(`db/init/01_schema.sql:182`):

```sql
CREATE TABLE strategies (
  id          BIGSERIAL PRIMARY KEY,
  name        VARCHAR(64) NOT NULL,
  description TEXT,
  category    VARCHAR(32) DEFAULT 'LHB',  -- LHB/MOMENTUM/CUSTOM(注释声明,实际只用过 LHB)
  entry_rules JSONB NOT NULL DEFAULT '[]',
  exit_rules  JSONB NOT NULL DEFAULT '[]',
  risk_rules  JSONB NOT NULL DEFAULT '[]',
  params      JSONB NOT NULL DEFAULT '{}',
  tags        VARCHAR(128)[],
  ...
);
CREATE INDEX idx_strategies_active ON strategies(is_active, category);
```

观察:
- `category` 已是 `VARCHAR(32)` 自由文本,**无枚举约束**,注释「LHB/MOMENTUM/CUSTOM」实际上只用过 `LHB`
- `tags` 是 `VARCHAR(128)[]`,可承载多维度标签(例如「短线 / 事件驱动 / 游资」)
- 已有索引 `(is_active, category)`,说明 category 查询已经是热路径

### 1.2 代码实现现状

`backend/domain/strategy/` 目录:

| 文件 | 内容 |
|---|---|
| `base.py` | `BaseStrategy` 抽象类,`category: str = "LHB"` class 属性 |
| `builtins.py` | 2 个内置策略 |
| `registry.py` | 策略注册中心 |

现有策略 → 10 类映射:

| 内置策略 | `category` 当前值 | 对应「用户 10 类」 | 备注 |
|---|---|---|---|
| `lhb_hot_money`(龙虎榜·游资跟随) | `LHB` | **事件驱动(Event Driven)** | 龙虎榜是天然事件信号源 |
| `lhb_institution`(龙虎榜·机构席位) | `LHB` | **事件驱动(Event Driven)** | 机构席位也是事件信号 |

**结论**:现网只有 2 个策略,都属于「事件驱动」大类,`LHB` 这个自定义 category 实质是**子分类**。

---

## 2. 10 大类 → 近 6 个月聚焦范围

按用户反馈(STO-45)的 10 类,结合本系统龙虎榜业务定位,分 3 档:

### 2.1 聚焦(近 6 个月落地)

| 大类 | 英文 | 聚焦理由 | 现有/计划策略 |
|---|---|---|---|
| **事件驱动** | Event Driven | 龙虎榜本身就是事件信号;现有 2 策略已归此类;数据齐全 | `lhb_hot_money` / `lhb_institution` / 计划:`lhb_net_inflow`、`zt_pool_follow` |
| **趋势类** | Trend Following | 日 K + 周 K 齐;MA/MACD/ATR 等简单计算即可;与龙虎榜选股天然配合 | 新建 `ma_cross_strategy` / `breakout_strategy`(待拆子 issue) |
| **多因子选股** | Multi-Factor | `scoring` 模块已有 tech/fund/hist 三因子,现成因子库可复用 | 新建 `multi_factor_score_top` — 按综合分 top-N 选股(待拆子 issue) |

### 2.2 可做但非近期(3-12 月)

| 大类 | 英文 | 暂缓理由 |
|---|---|---|
| 均值回归 | Mean Reversion | 需分钟级数据 + 严格换手成本建模,现网 intraday 10s 快照粒度足够但风控门槛高 |
| 统计学习 / ML | Statistical Learning | 需要建模 pipeline + 特征工程基建,等趋势/多因子打底后再上 |
| 资产配置 | Portfolio Allocation | 用户当前关注选股(α),资产配置(β)诉求弱 |

### 2.3 明确排除(不做)

| 大类 | 英文 | 排除理由 |
|---|---|---|
| 套利 | Arbitrage | 需要多市场/跨品种打通(港美/期货/ETF),本系统只做 A 股单一市场 |
| 做市 | Market Making | 需交易所做市商资格 + 低延迟撮合,**牌照门槛** |
| 高频微结构 | HFT | 需 tick 级数据 + 微秒级执行,**频率门槛**(现网 intraday 最小粒度 10s) |
| 期权波动率 | Volatility / Options | 需期权链数据 + IV 曲面构建,本系统仅覆盖股票 |

---

## 3. 数据模型扩展建议

### 3.1 方案对比

| 方案 | 描述 | 优点 | 缺点 |
|---|---|---|---|
| A | 新建 `strategy_category` 字典表 + FK | 规范、可挂元数据(中文名、排序、icon) | 引入 JOIN,小表 overkill |
| B | `strategies.category` 改枚举 CHECK 约束 | 最小改动,SQL 层 enforce | 加新分类要迁移 |
| **C(推荐)** | 保持 VARCHAR,在代码层定义 `STRATEGY_CATEGORIES` 常量 + 可选 `parent_category` 列承载两级(大类/子类) | 柔性最大,与现有 `tags` 配合可表达多维 | 无 DB 层约束,靠代码层自律 |

### 3.2 推荐方案 C · DDL 草案

```sql
-- 为 strategies 增加 parent_category(大类)列,原 category 降级为子类
-- 例:parent_category='EVENT_DRIVEN', category='LHB_HOT_MONEY'
ALTER TABLE strategies
  ADD COLUMN parent_category VARCHAR(32) DEFAULT NULL;

-- 索引:按大类 + 活跃过滤(覆盖前端导航场景)
CREATE INDEX IF NOT EXISTS idx_strategies_parent_active
  ON strategies(parent_category, is_active)
  WHERE parent_category IS NOT NULL;

-- 回填:现有 LHB → EVENT_DRIVEN
UPDATE strategies SET parent_category = 'EVENT_DRIVEN' WHERE category = 'LHB';
```

**不做**:不建 `strategy_category` 独立表。理由:分类数量有限(≤ 10),用 Python/TS 常量维护更敏捷;真正需要挂元数据(中文名/icon)时直接放前端 i18n。

### 3.3 代码层常量(Python)

```python
# backend/domain/strategy/categories.py (新文件)
PARENT_CATEGORIES: dict[str, str] = {
    "EVENT_DRIVEN":  "事件驱动",
    "TREND_FOLLOW":  "趋势跟踪",
    "MULTI_FACTOR":  "多因子选股",
    # 下列已决策暂缓/排除,在 UI 不显示:
    # "MEAN_REV":       "均值回归",   # 暂缓
    # "ML":             "ML 统计学习", # 暂缓
    # "PORTFOLIO":      "资产配置",    # 暂缓
}
```

---

## 4. 前端分类导航

### 4.1 页面影响

- `/backtest`(策略对比)+ `/backtest-run`(任务历史):当前按 `strategy_name` 平铺,未来按 `parent_category` 分组折叠:
  - 「事件驱动」折叠 → lhb_hot_money, lhb_institution
  - 「趋势跟踪」折叠 → ma_cross, breakout
  - 「多因子」折叠 → multi_factor_score_top
- 新建策略时提供「大类」下拉筛选,减少用户面对扁平列表的认知负担

### 4.2 URL 查询参数

- `?cat=EVENT_DRIVEN` → 策略列表默认只展开该类
- 默认全部展开,不强制筛选

---

## 5. 与 backtest / robot 的关系

- **不影响回测引擎选择**:所有分类都复用现有 `backend/domain/backtest/engine.py`,分类仅影响**展示聚合**
- 机器人(`robots` 表)通过 `strategy_id` 间接继承分类,robot 列表可按大类筛选(未来工作)
- 评分(`backend/scoring/`)不需要改,技术/资金/历史三因子是**基础因子**,各大类策略组合使用

---

## 6. 风险与回滚

- `ALTER TABLE ADD COLUMN` 加 `DEFAULT NULL` 是 **online DDL**(PG 11+),不锁表
- 回滚方法:`ALTER TABLE strategies DROP COLUMN parent_category;` + 删索引
- 前端导航若不完成,后端字段可独立存在,无强依赖

---

## 7. 待 PM 评审通过后的子 issue 候选(≥ 3)

| 候选 issue | 类型 | 体量 | 依赖 |
|---|---|---|---|
| **STO-48a** — `strategies.parent_category` 列 + `categories.py` 常量 + 回填迁移 | type:chore | 低(~30 行 + 1 个 SQL 迁移) | - |
| **STO-48b** — 前端策略列表/对比页按 parent_category 分组折叠 | type:feature | 中(~100 行 Vue + utils 分组函数 + vitest) | STO-48a |
| **STO-48c** — 实现 `ma_cross_strategy`(趋势类第一个策略) | type:feature | 中(~150 行 + 回测测试) | STO-48a |
| **STO-48d** — 实现 `multi_factor_score_top`(复用 scoring 的综合分选股) | type:feature | 中(~100 行,大部分逻辑 scoring 已有) | STO-48a |
| STO-48e — 实现 `breakout_strategy`(趋势类第二个) | type:feature | 中 | STO-48a / 48c |

---

## 8. 不做(范围外)

- ❌ 不引入自动化策略元学习框架(太重,与现网定位不符)
- ❌ 不改现有 `scoring/` 的 tech/fund/hist 三因子权重(与分类正交)
- ❌ 不重构 `BaseStrategy` ABC 接口(现有接口够用,不为分类而改)

---

## 变更记录

- 2026-04-23 · Dev 初稿 · 待 PM 评审(STO-48)
