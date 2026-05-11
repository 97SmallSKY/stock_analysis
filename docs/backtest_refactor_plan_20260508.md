# 策略回测 & 回测分析重构方案

**创建日期**：2026-05-08
**范围**：`pages/backtest.py`、`pages/backtest_analysis.py`、`modules/analyzer.py` 的回测部分、`modules/strategies/`
**目标**：解除 LHB 耦合，实现策略/回测可插拔，消除未来函数，增强分析可视化

---

## 0. 现状摸底

### 结构上已有雏形可复用
- `modules/strategies/` 已有 `BaseStrategy` + `registry.py`（`@register_strategy`）；`select()` + `should_sell()` 抽象到位，插件化底子够。
- 现有两个策略：`lhb_high_score`（批量）、`single_stock_event`（单股 LHB 事件）。

### 当前两个页面的硬耦合
1. `pages/backtest.py::_single_runner` 里 `SELECT ... FROM lhb_stock_daily ...` —— "指定股票回测"的"事件来源"写死成 LHB 上榜日，无法对任何非 LHB 数据做单股回测。
2. `pages/backtest_analysis.py` 多数 section 依赖 LHB 专属字段（`has_institution` / `tech_score` / `net_buy` / `seat_score`），换任何通用策略直接半屏空白。
3. `BaseStrategy.should_sell(position)` 只收 `position`，拿不到市场情绪/策略元信息 → 用户反馈"止盈止损太死板"的根因。

### 未来函数 / 失真点
1. `analyzer.py:696-748` —— 单根日 K 内同时得到 `day_high` / `day_low`，代码按 `exit_reason` 关键字匹配成交价（"止损"→ `buy_price*(1+stop_loss)`，"止盈"→ `buy_price*(1+stop_profit)`）。同一日若 high 和 low 都能触发，系统按 reason 先后取而非真实盘中先后，**止盈偏向被放大** —— 典型日内路径未来函数。
2. `simulate_with_stoploss:736-738` 止损/止盈按阈值价精确成交 —— **跳空穿透被当作精确价成交**，系统性高估收益。
3. `filter_one_word` / `max_buy_gap` 用 T+1 开盘价过滤 → T+1 9:25 竞价后可得，合理，不算未来函数。
4. 评分选股链路：`select_stocks_daily` 用 T 日及以前 K 线算 tech_score，T+1 开盘买入 —— 时序正确。

### 分析页缺口
最大回撤曲线、滚动胜率、基准对比（大盘 / 买入持有）、单笔 K 线标注买卖点、参数敏感度、exit_reason 分布 —— 均缺。

---

## 1. 用户决策（2026-05-08）

| 问题 | 决策 |
|---|---|
| 单股回测的信号来源 | **策略内部自扫**（最通用） |
| 动态止盈止损怎么做 | **组件库 + 策略组合** |
| 未来函数修复尺度 | **保守**（日内同触发按止损优先；跳空按真实 open 成交） |
| 分析页新增维度 | 单笔 K 线信号图、最大回撤+水下曲线+滚动 20 笔胜率、基准对比、exit_reason 分布 + 参数敏感度热力图 **全要** |

---

## 2. 目标架构

```
┌─────────────────────────────────────────────────────────────────┐
│  pages/backtest.py              pages/backtest_analysis.py      │
│  （薄 UI 层：选股票/策略/时间 + 启动 Job）（只读 runs 表可视化）│
└─────────────────────────────────────────────────────────────────┘
                         ↓                    ↑
┌─────────────────────────────────────────────────────────────────┐
│                modules/backtest/  （新建）                       │
│  ├── engine.py          BacktestEngine（事件/全时序统一循环）    │
│  ├── data_feed.py       DataFeed（K线/LHB/情绪的统一访问层）     │
│  ├── portfolio.py       Position/Portfolio（单股版本：1 仓位）   │
│  ├── executor.py        Executor（T+1 开盘成交 + 跳空穿透处理）  │
│  ├── metrics.py         analyze()：KPI/回撤/滚动/基准/exit 分布  │
│  └── exits/             Exit 组件库                              │
│       ├── base.py       BaseExit                                 │
│       ├── fixed_pct.py  FixedPctStop（旧的死板阈值）            │
│       ├── atr.py        ATRStop                                  │
│       ├── trailing.py   TrailingStop                             │
│       ├── time_stop.py  TimeStop（持有天数）                    │
│       ├── sentiment.py  SentimentReversal（情绪反转）           │
│       └── composite.py  CompositeExit（组合多个 Exit）          │
├─────────────────────────────────────────────────────────────────┤
│                modules/strategies/  （复用 + 扩展）              │
│  ├── base.py            BaseStrategy (扩 prepare/scan_signals)   │
│  ├── registry.py        (不变)                                   │
│  ├── single_stock_event.py   重写为「LHB 事件插件」              │
│  ├── ma_breakout.py          新：均线突破（纯 K 线通用示例）     │
│  └── ...                     用户后续自加                        │
└─────────────────────────────────────────────────────────────────┘
```

**拆分动机**：`modules/analyzer.py` 现在既有 scorer 又有回测引擎，1000+ 行混在一起。把回测相关的 `simulate_with_stoploss` / `run_backtest` / `analyze_backtest` 迁出到 `modules/backtest/`，`analyzer.py` 只留 scorer。

---

## 3. 核心抽象

### 3.1 扩展 `BaseStrategy`

```python
class BaseStrategy(ABC):
    name: str = "base"
    description: str = ""
    param_schema: dict = {}
    mode: str = "event"     # "event" | "bar"（默认 event，第一版只实现 event）
    data_requirements: dict = {"kline": True, "lhb": False, "sentiment": False}

    def prepare(self, ctx: "BacktestContext"): ...

    # 事件驱动：返回一组 Signal(trade_date, code, meta)
    @abstractmethod
    def scan_signals(self, ctx: "BacktestContext") -> list["Signal"]: ...

    # Exit 组件链（默认 FixedPct + TimeStop 的组合，保持向后兼容）
    def build_exits(self) -> "BaseExit":
        return CompositeExit([FixedPctStop(...), TimeStop(...)])
```

**兼容性**：老 `select(trade_date, lhb_df, kline_cache)` 保留为**薄壳兼容层**，底层把 `scan_signals` 的结果按日切片，不破坏 `lhb_high_score` 批量流程。

### 3.2 Exit 组件（回答"太死板"）

```python
class BaseExit(ABC):
    @abstractmethod
    def check(self, position: Position, bar: Bar, ctx: BacktestContext) -> SellSignal | None: ...

class ATRStop(BaseExit):
    def __init__(self, period=5, mult=2.5): ...
    def check(self, position, bar, ctx):
        atr = ctx.data_feed.atr(position.code, bar.date, self.period)
        if bar.low <= position.buy_price - self.mult * atr:
            return SellSignal(reason=f"ATR止损(-{self.mult}σ)", price_type="stop_trigger")

class SentimentReversal(BaseExit):
    """市场情绪由 bullish 转 bearish 且持仓浮盈时落袋"""
    def check(self, position, bar, ctx):
        snap = ctx.data_feed.sentiment(bar.date)
        if snap["trend_flip_to_bearish"] and position.close_return > 0:
            return SellSignal(reason="情绪反转平仓", price_type="close")

class CompositeExit(BaseExit):
    """按优先级顺序链式检查，第一个命中的生效"""
    def __init__(self, exits: list[BaseExit]): self.exits = exits
```

策略里组合使用：
```python
def build_exits(self):
    return CompositeExit([
        ATRStop(period=5, mult=2.0),                 # 动态止损
        TrailingStop(activate=0.05, giveback=0.4),   # 浮盈 5% 后启动追踪
        SentimentReversal(),                         # 情绪反转
        TimeStop(max_days=self.params["max_hold_days"]),
    ])
```

### 3.3 `BacktestContext`

```python
@dataclass
class BacktestContext:
    data_feed: DataFeed
    sentiment_cache: dict   # 预加载整个回测期情绪
    strategy_params: dict
    extras: dict = field(default_factory=dict)  # 策略自己也能塞预计算指标
```

### 3.4 `DataFeed` —— 解除 LHB 硬耦合

```python
class DataFeed:
    def kline(code, start, end) -> DataFrame: ...      # 复用 _kline_get
    def atr(code, date, period) -> float: ...
    def sentiment(date) -> dict: ...                   # 读 market_sentiment 表
    def lhb_events(code, start, end) -> DataFrame: ... # 仅 LHB 策略调用
    def limit_pct(code) -> float: ...                  # 涨跌停限制
```

策略只声明 `data_requirements`，引擎在 `prepare()` 阶段预加载。

---

## 4. 保守路径的未来函数修复

`executor.py` 里单 bar exit 检查的优先级规则：

```python
def evaluate_bar(position, bar, exits, ctx) -> SellSignal | None:
    # 1) 开盘优先：开盘即突破止损/止盈阈值的，直接按 open 成交（含跳空穿透真实价格）
    if bar.open_return <= max_stop_loss_threshold:
        return SellSignal("开盘跳空止损", price_type="open")  # 真实开盘价成交

    # 2) 日内：让所有 exit 判一遍，应用【止损优先】
    triggers = [exit.check(position, bar, ctx) for exit in exits.items]
    triggers = [t for t in triggers if t]

    if any("止损" in t.reason for t in triggers) and any("止盈" in t.reason for t in triggers):
        # 同一 bar low 和 high 都被触发 → 保守按止损先触发
        return pick_stop_loss_trigger(triggers)

    return triggers[0] if triggers else None
```

**成交价规则**（杀掉"阈值价成交"的失真）：
- 开盘价触发 → `sell_price = bar.open`（跳空穿透用真实 open）
- 日内止损触发（阈值在 low 之上）→ `sell_price = max(bar.open, buy_price*(1+stop_loss))`（保守：跳空取 open）
- 日内止盈触发 → `sell_price = min(bar.open, buy_price*(1+stop_profit))`（保守：不用最优价）
- ATR / 追踪止损 → 同理，阈值价 vs open 取更差一侧

---

## 5. 持久化表结构增量

保留 `backtest_runs` / `backtest_results`，**新增 2 个字段 + 1 个表**：

```sql
-- backtest_runs 新增
ALTER TABLE backtest_runs ADD COLUMN config_json  TEXT;     -- 完整策略参数
ALTER TABLE backtest_runs ADD COLUMN benchmark_code TEXT;   -- 基准代码
ALTER TABLE backtest_runs ADD COLUMN universe     TEXT;     -- 回测股票池描述

-- backtest_results 新增
ALTER TABLE backtest_results ADD COLUMN signal_meta     JSONB;   -- 策略产信号时的 meta
ALTER TABLE backtest_results ADD COLUMN exit_component  TEXT;    -- 命中的 Exit 名
ALTER TABLE backtest_results ADD COLUMN benchmark_return NUMERIC; -- 同期基准收益

-- 新表：参数敏感度扫描结果
CREATE TABLE backtest_param_sweeps (
    sweep_id    TEXT PRIMARY KEY,
    run_id      TEXT,
    param_x     TEXT,
    param_y     TEXT,
    x_value     NUMERIC,
    y_value     NUMERIC,
    avg_return  NUMERIC,
    win_rate    NUMERIC,
    trade_count INT,
    created_at  TIMESTAMP
);
```

---

## 6. 页面蓝图

### 6.1 `pages/backtest.py`（简化）

```
┌─ 指定股票回测 ──────────────────────────┐
│ 股票代码 [000001]                       │
│ 回测区间 [2024-01-01] ~ [2026-05-08]    │
│ 策略    [ma_breakout        ▼]          │
│ ─ 策略参数 ────────────────────          │
│   (动态渲染 param_schema)                │
│ ─ Exit 组合 ──────────────────          │
│   □ ATR止损  period=5 mult=2.0          │
│   □ 追踪止盈 activate=5% giveback=40%   │
│   □ 情绪反转                             │
│   ☑ 到期卖出 max_days=10                │
│ ─ 基准     [000300.SH 沪深300   ▼]       │
│ ─ 参数敏感度扫描（可选） ☐              │
│   X轴 [stop_loss] 范围 [-10%~-3%] 步长   │
│   Y轴 [max_hold]  范围 [3~15] 步长       │
│                                          │
│ [🚀 开始回测]                            │
└─────────────────────────────────────────┘
```

**去掉"指定股票 vs 批量"两 tab 的分裂** —— 单股聚焦，批量留在主板策略页（已有 `pages/mainboard_strategy.py` 承接）。事件研究/策略过滤的区分也消失，因为策略**自己**声明工作模式。

### 6.2 `pages/backtest_analysis.py`（按多选新增）

```
选择 run [dropdown]
───────────────────────────────────────────────
📊 核心 KPI  (保留 + 新增：最大回撤、Calmar、平均持有天数)
───────────────────────────────────────────────
📈 净值曲线 / 水下曲线 / 滚动20笔胜率  (3 图联动，共享 x 轴)
  ├─ 策略净值（红）
  └─ 基准净值（灰）同期个股买入持有 + 大盘
───────────────────────────────────────────────
🎯 每笔 K 线信号图（展开一行看一张）
  candlestick [T-30 ~ sell_date+5]
  标注：🔷信号日 🟢买入 🔴卖出 + exit 原因
───────────────────────────────────────────────
🔚 Exit 原因分布
  饼图 + 每类 exit 的平均收益/胜率
───────────────────────────────────────────────
🌡️ 参数敏感度热力图（仅 sweep 类 run 才显示）
  avg_return  /  win_rate  /  max_drawdown  三张热力图切换
───────────────────────────────────────────────
📋 明细表（保留下载 CSV）
  去掉对 has_institution / tech_score 的死依赖，
  改成读 signal_meta JSONB 动态列
```

---

## 7. 实施里程碑

每步可独立 ship 不破坏现状，每个 Phase 一个 PR。

| Phase | 内容 | 验收 |
|---|---|---|
| **P1** 引擎抽离 | 新建 `modules/backtest/` 把 `simulate_with_stoploss`/`run_backtest` 迁入；`analyzer.py` 只留 scorer；提供 shim 兼容旧调用 | 现有 `pages/backtest.py` 旧流程跑通、结果一致 |
| **P2** DataFeed + Exit 基础 | 引入 `DataFeed` / `BaseExit` / `FixedPctStop` / `TimeStop`；把现有 `should_sell` 包成 `CompositeExit` | 旧策略输出**逐笔数值不变** |
| **P3** 保守路径修复 | `executor.py` 改日内 exit 判断（止损优先 + 跳空穿透真实价）；加 `return_rate_conservative` 字段 | 历史 run 重跑，胜率/avg_return 下降在预期范围（文档记录基线对比） |
| **P4** 单股回测重写 + 策略库第一批 | `backtest.py::_single_runner` 去掉 `lhb_stock_daily` 硬查询，改走 `strategy.scan_signals`；同步落地第一批 6 个纯 K 线策略（`ma_cross`/`ma_breakout`/`macd_golden`/`boll_reversion`/`donchian_breakout`/`volume_breakout`） | 能对 000001 跑通 6 个策略，不碰 LHB 表 |
| **P5** 高级 Exit + 策略库第二批 | `ATRStop` / `TrailingStop` / `SentimentReversal` 实现 + UI 可勾选；落地第二批 4 个量价/情绪策略（`pullback_to_ma`/`oversold_bounce`/`mean_reversion`/`sentiment_swing`） | 单元测试 + 金标股票回测对比 |
| **P6** 分析页重构 + 策略库第三批 | 水下曲线、滚动胜率、基准对比、K 线信号图、Exit 分布；落地第三批 5 个 A 股特色策略（`first_limit_up_relay`/`limit_up_fail_rebound`/`lhb_institution_follow`/`lhb_famous_trader_follow`/`lhb_second_day_relay`） | 对同 run 的可视化覆盖率 ≥ 现状的 2x；策略库累计 ≥ 15 个 |
| **P7** 参数敏感度扫描 | 新增 `param_sweeps` 表 + sweep 作业（沿用 Job Store 线程模型）+ 热力图 | 2D sweep 5×5=25 个任务能并行跑完 |

**等价性测试**：P1-P4 回测数值需有同参数旧引擎 vs 新引擎逐笔对账。P3 之后因修复失真会产生 diff，**预期并记录**对比基线。

---

## 8. 策略库扩展清单

现有策略只有 2 个（`lhb_high_score` 批量、`single_stock_event` LHB 单股事件），且都绑定 LHB 数据。补齐一批市面上常用的 A 股单股策略，分批实施，每个策略一个 PR（~100-200 行）。

### 8.1 分批路线

**第一批：纯 K 线技术形态**（P4 阶段同步落地，验证 DataFeed 通用性）

| 策略 | 描述 | 买入逻辑 | Exit 默认组合 | 依赖 |
|---|---|---|---|---|
| `ma_cross` | 双均线金叉 | MA5 上穿 MA20 当日收盘为信号，T+1 开盘买 | ATRStop(2.0) + TimeStop(10) | K 线 |
| `ma_breakout` | 均线突破 | 收盘站上 MA60 且近 5 日未站上 | TrailingStop(5%/40%) + TimeStop(15) | K 线 |
| `macd_golden` | MACD 金叉 | DIF 上穿 DEA 且 0 轴下方（趋势反转）| FixedPct(-5%/8%) + TimeStop(5) | K 线 |
| `boll_reversion` | 布林带回归 | 收盘跌破下轨 2σ 后反弹上穿下轨 | TrailingStop + TimeStop(8) | K 线 |
| `donchian_breakout` | 唐奇安突破（海龟） | 收盘突破 20 日最高价 | ATRStop(2.5) + TrailingStop | K 线 |
| `volume_breakout` | 放量突破 | 放量（vol ≥ MA5 vol × 2）+ 突破 20 日箱体上沿 | ATRStop(2.0) + TimeStop(7) | K 线 |

**第二批：量价 + 情绪**（P5 阶段，依赖 SentimentReversal 组件落地）

| 策略 | 描述 | 买入逻辑 | 依赖 |
|---|---|---|---|
| `pullback_to_ma` | 强势股回踩均线 | 20 日涨幅 > 15%，回踩 MA10 不破 | K 线 |
| `oversold_bounce` | 超跌反弹 | 连续 3 根阴线且 RSI6 < 20 | K 线 |
| `mean_reversion` | 均值回归 | 收盘偏离 MA20 低于 -10%，次日博反弹 | K 线 |
| `sentiment_swing` | 情绪修复 | 市场情绪 `bearish → neutral` 切换日买入 | K 线 + market_sentiment |

**第三批：A 股特色 + LHB 衍生**（P6 阶段后期，需要涨跌停池 / LHB 数据）

| 策略 | 描述 | 买入逻辑 | 依赖 |
|---|---|---|---|
| `first_limit_up_relay` | 首板次日接力 | 昨日首次涨停（非 N 连板）+ T+1 低开/平开 | 涨停池 + K 线 |
| `limit_up_fail_rebound` | 炸板回封 | 盘中曾涨停炸板收长上影，次日阳线回封 | 分时数据（暂用日 K 近似） |
| `lhb_institution_follow` | 机构席位跟随 | 龙虎榜买一为机构专用 + 净买入 > 5000 万 | LHB |
| `lhb_famous_trader_follow` | 知名游资跟随 | 龙虎榜命中 FAMOUS_TRADERS 列表 | LHB |
| `lhb_second_day_relay` | 龙虎榜次日接力 | 上榜 + T+1 低开 2% 以内 + T+2 介入 | LHB + K 线 |

### 8.2 统一实现规范

每个策略文件遵循模板：

```python
# modules/strategies/ma_cross.py
from .base import BaseStrategy, Signal
from .registry import register_strategy
from modules.backtest.exits import CompositeExit, ATRStop, TimeStop

@register_strategy("ma_cross")
class MaCrossStrategy(BaseStrategy):
    description = "双均线金叉：MA5 上穿 MA20"
    mode = "event"
    data_requirements = {"kline": True}
    param_schema = {
        "fast":          {"type": "int", "default": 5,  "label": "快速均线"},
        "slow":          {"type": "int", "default": 20, "label": "慢速均线"},
        "stop_atr_mult": {"type": "float", "default": 2.0, "label": "ATR 止损倍数"},
        "max_hold_days": {"type": "int", "default": 10, "label": "最大持有天数"},
    }

    def scan_signals(self, ctx):
        df = ctx.data_feed.kline(ctx.code, ctx.start, ctx.end)
        df["ma_fast"] = df["收盘"].rolling(self.params["fast"]).mean()
        df["ma_slow"] = df["收盘"].rolling(self.params["slow"]).mean()
        # 金叉：昨日 fast<=slow 且今日 fast>slow
        cross = (df["ma_fast"] > df["ma_slow"]) & (df["ma_fast"].shift(1) <= df["ma_slow"].shift(1))
        return [Signal(date=d, code=ctx.code, meta={"ma_fast": f, "ma_slow": s})
                for d, f, s in zip(df.loc[cross, "日期"], df.loc[cross, "ma_fast"], df.loc[cross, "ma_slow"])]

    def build_exits(self):
        return CompositeExit([
            ATRStop(period=14, mult=self.params["stop_atr_mult"]),
            TimeStop(max_days=self.params["max_hold_days"]),
        ])
```

**所有策略必须遵守的约束**（防未来函数再次滋生）：
- `scan_signals` 中所有指标计算只用**截至 T 日及之前**的数据；信号日期 = T 日；买入 = T+1 开盘（由 Executor 统一执行）。
- 不直接在策略内部查 `kline_daily` 或 `lhb_stock_daily`，一律走 `ctx.data_feed.*`。
- 参数全部在 `param_schema` 声明，便于 UI 自动渲染 + 参数敏感度扫描。

### 8.3 测试数据集

每类策略至少跑一次"金标股票"做基线：
- 趋势延续类 → `600519.SH`（茅台）、`000858.SZ`（五粮液）
- 高波动类 → `300750.SZ`（宁德时代）、`688981.SH`（中芯国际）
- 题材炒作类 → `002230.SZ`（科大讯飞）、任何近一年热门 LHB 常客

预期输出存档在 `docs/backtest_baselines/`，后续回归时对账。

---

## 9. 非目标（避免越界）

- 多股票组合回测（留接口不实现）
- 分钟级路径回测（用户选保守日 K，搁置）
- 实盘接入
- 蒙特卡洛 / Bootstrap 置信区间
- 机器学习/因子挖掘类策略（本期仅规则类）

---

## 10. 待决议事项

暂无阻塞项。P1 启动前的最后一次对齐：等用户确认本方案后，按 P1 开始迁移引擎代码；策略库第一批与 P4 合流。
