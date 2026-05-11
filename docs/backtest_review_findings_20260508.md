# 策略回测重构代码审查报告（修复指引）

**日期**：2026-05-08
**审查对象**：`modules/backtest/` + `modules/strategies/` + `pages/backtest.py`（基于方案 `docs/backtest_refactor_plan_20260508.md` 的实现）
**审查范围**：用户报告的两个现象 + 静态审查发现的连带 bug
**目标读者**：负责修复的下一位 agent。本文档要求**自包含**，不依赖对话上下文。

---

## 0. 现象与触发条件

### 现象 A：「002342.SZ 在区间内无信号」
- 复现路径：策略回测页 → 股票代码 `002342` → 策略 `ma_cross`（默认参数）→ 区间 `2024-01-01 ~ 2026-05-08` → 开始回测。
- 实际结果：UI 显示 `st.warning("002342.SZ 在区间内无信号")`。
- 期望结果：应有 18 笔 MA5/MA20 金叉信号产生交易记录。

### 现象 B：「还是有止盈止损逻辑」
- 复现路径：选任意新增 K 线策略（`ma_cross` / `ma_breakout` / `boll_reversion` / `donchian_breakout` / `volume_breakout`）。
- 实际结果：UI 上仍出现"止损比例 / 止盈比例"滑块；底层 exit 行为是固定百分比阈值。
- 期望结果：根据方案 §8.1，`ma_cross` 应使用 `ATRStop + TimeStop`，`ma_breakout` / `boll_reversion` 应使用 `TrailingStop + TimeStop`，`donchian_breakout` / `volume_breakout` 应使用 `ATRStop` 系列；UI 不应再展示固定阈值参数。

---

## 1. Bug 清单（按修复优先级排序）

### 🔴 P0-1 `executor.py:90` —— pandas label index 当作位置索引使用，**所有策略所有交易全 return None**

**位置**：`modules/backtest/executor.py:80-94`

```python
df["日期"] = pd.to_datetime(df["日期"])
signal_date_str = str(signal_date)[:10]
matches = df[df["日期"].dt.strftime("%Y-%m-%d") == signal_date_str[:4] + "-" + signal_date_str[4:6] + "-" + signal_date_str[6:8]]
if matches.empty:
    d = str(signal_date)[:8]
    matches = df[df["日期"].dt.strftime("%Y%m%d") == d]
if matches.empty:
    return None
buy_idx = matches.index[0]      # ← BUG: pandas label index, NOT positional
next_idx = buy_idx + 1
if next_idx >= len(df):           # ← 用 label 与 len() 比较，必然炸
    return None
```

**根因链路**：
1. `data_feed.py:38-46`：缓存命中时返回 `cached[mask].copy()`，**保留原 cache 的 RangeIndex 标签**（不重置）。
2. 例：002342.SZ 的 cache 是 0..562 的连续索引；查询 2024-02-28 ~ 2024-03-24 时，slice 出 18 行，但索引为 38..55。
3. `matches.index[0]` 取到 `38`（label），不是 `0`（position）。
4. `next_idx = 38 + 1 = 39`，`len(df) = 18`，`39 >= 18 → True` → 返回 `None`。
5. `engine.py` 把 None 计入 `skip_stats["K线获取失败"]`，所有 18 个信号全跳过 → 结果集为空 → 触发"无信号"警告。

**实证**：
```
$ python3 -c "..."  # 见审查会话
signals: 18                    # scan_signals 正常产出
sig 0 20240228: ret=None       # 全部 None
sig 1 20240402: ret=None
sig 2 20240507: ret=None
sig 3 20240703: ret=None
sig 4 20240715: ret=None
回测完成: 002342.SZ 20240101-20260508 → 0/1 笔
```

**修复方案（任选其一）**：

方案 1（推荐，根治）—— `data_feed.py:46` 切片返回前重置索引：
```python
return cached[(cached_dates >= req_start) & (cached_dates <= req_end)].copy().reset_index(drop=True)
```

方案 2（局部）—— `executor.py:90` 改用位置索引：
```python
buy_pos = df.index.get_loc(matches.index[0])
next_idx = buy_pos + 1
# 后续所有 df.iloc[next_idx] / df.iloc[day_idx] 用 buy_pos 体系
```

**两者必须同时考虑**：方案 1 修了缓存返回；但 executor 内部多处用 `df.iloc[day_idx]` 配合 `buy_idx + offset`，若方案 1 已让索引连续就 OK；否则方案 2 必须把 day_idx 全部改为位置。**强烈建议采用方案 1**。

**回归测试**：
```python
from modules.backtest import DataFeed, BacktestEngine
from modules.strategies import get_strategy
feed = DataFeed()
strat = get_strategy('ma_cross')({})
df = BacktestEngine(feed).run('002342.SZ', '20240101', '20260508', strat)
assert len(df) > 0, "002342.SZ 应产出交易记录"
```

---

### 🔴 P0-2 `executor.py:112,116` —— `_base` NameError

**位置**：`modules/backtest/executor.py:104-116`

```python
base = {"buy_date": buy_date_raw, "t1_gap": round(t1_open_gap, 4),
        "signal_close": prev_close, "prev_close": prev_close}

# 一字涨停过滤
if filter_one_word:
    limit_pct = self.data_feed.limit_pct(code)
    if _is_one_word_limit_up(buy_day, prev_close, limit_pct):
        return {**_base, **_make_skip("T+1一字涨停无法买入")}    # ← _base 未定义

# 高开过滤
if max_buy_gap and t1_open_gap >= max_buy_gap:
    return {**_base, **_make_skip(f"T+1高开{t1_open_gap:.1%}超过阈值{max_buy_gap:.0%}放弃")}  # ← 同
```

**根因**：变量名是 `base`，写成了 `_base`（前缀下划线）。一旦 `filter_one_word` 命中或 `max_buy_gap` 超阈，**抛 NameError**，未被 engine.run 的 try/except 包裹（engine 没有），冒泡到 `_bt_runner` → 整个 Job 标 `failed`。

**修复**：把 `_base` 改成 `base`，2 处：
```python
return {**base, **_make_skip("T+1一字涨停无法买入")}
return {**base, **_make_skip(f"T+1高开{t1_open_gap:.1%}超过阈值{max_buy_gap:.0%}放弃")}
```

---

### 🔴 P0-3 `executor.py:131-144` —— `Position.hold_days` 永远 = 1，`TimeStop` / `TrailingStop` 全部失效

**位置**：`modules/backtest/executor.py:130-144`

```python
last_hold_idx = min(len(df), next_idx + max_hold_days) - 1
for day_idx in range(next_idx, last_hold_idx + 1):
    held_days = day_idx - next_idx + 1
    is_last_day = (day_idx == last_hold_idx)

    row = df.iloc[day_idx]
    bar = Bar(date=..., open=..., high=..., low=..., close=..., volume=...)

    position = Position.from_bar(code, buy_price, buy_date_raw, bar)
    # ↑ 每次循环新建 Position；from_bar 内部固定 hold_days=1
```

**根因**：
- `Position.from_bar` 在 `portfolio.py:35-49` 内部固定 `hold_days=1`。
- 循环每次都 new 一个新 Position，**忽略了 `held_days` 局部变量**。
- 后果：
  - `TimeStop.check`：`if position.hold_days >= self.max_days` —— max_days > 1 时永不触发。这意味着 P0-1 修好后，所有交易都会跑到 `last_hold_idx`，落到第 191-203 行的"数据截止"分支收盘价卖出 —— **TimeStop 名存实亡**。
  - `Position.max_return / min_return` 只反映当根 bar 的 high/low vs buy_price，**不是持仓期累计最大浮盈/浮亏**。
  - `TrailingStop.check`（`exits/trailing.py:20`）依赖 `position.max_return` 判断是否激活，激活逻辑也错。
  - `FixedPctStop.check`（`exits/fixed_pct.py:19`）有 `if position.hold_days > 1 and position.open_return <= self.stop_loss` —— hold_days 永远 1，**T+1 之后的开盘止损永不触发**（不过 line 25 的 min_return 兜底还能触发盘中止损）。

**修复方案**：在循环**外** new 一次 Position，循环内调 `position.update(bar)`（`portfolio.py:51-60` 已实现）。

```python
position = None
for day_idx in range(next_idx, last_hold_idx + 1):
    is_last_day = (day_idx == last_hold_idx)
    row = df.iloc[day_idx]
    bar = Bar(
        date=row["日期"].strftime("%Y%m%d"),
        open=float(row["开盘"]), high=float(row["最高"]),
        low=float(row["最低"]), close=float(row["收盘"]),
        volume=float(row.get("成交量", 0)),
    )
    if position is None:
        position = Position.from_bar(code, buy_price, buy_date_raw, bar)
    else:
        position.update(bar)
    # ... 后续 sell_signal 检查不变
```

**回归测试**：
```python
strat = get_strategy('ma_cross')({"max_hold_days": 5})
df = BacktestEngine(feed).run('002342.SZ', '20240101', '20260508', strat)
# 预期：大量交易 exit_reason 应为 "到期卖出"，hold_days 分布在 1-5
assert df['hold_days'].max() <= 5
assert (df['exit_reason'] == "到期卖出").sum() > 0
```

---

### 🔴 P0-4 表 schema 与写入字段不匹配，保存功能崩

**位置**：`modules/backtest/engine.py:186-215`、`pages/backtest.py:265-294`

**问题**：代码写入了下列字段，但方案 §5 中的 `ALTER TABLE` 仅是设计态，**未见对应的 `db/init/NN_*.sql` migration**：

写入字段清单（来自 engine + page）：
- `backtest_results` 新写入：`signal_meta`、`exit_component`、`run_type`
- `backtest_runs` 新写入：`task_id`、`status`、`config`

**风险**：
1. 若 PG 表无对应列 → INSERT 报 `column "xxx" of relation "yyy" does not exist`，整个保存流程崩。
2. 若 `utils/storage.py::write` 自动 dropna columns 容错 → 静默丢字段，后续分析页拿不到 signal_meta。

**修复方案**：
1. **先核查现状**（必做）：
   ```sql
   \d backtest_results
   \d backtest_runs
   ```
2. **新增 migration** `db/init/NN_backtest_v2_schema.sql`（NN 取下一个未占用编号）：
   ```sql
   ALTER TABLE backtest_results ADD COLUMN IF NOT EXISTS signal_meta     JSONB;
   ALTER TABLE backtest_results ADD COLUMN IF NOT EXISTS exit_component  TEXT;
   ALTER TABLE backtest_results ADD COLUMN IF NOT EXISTS run_type        TEXT;
   ALTER TABLE backtest_results ADD COLUMN IF NOT EXISTS benchmark_return NUMERIC;

   ALTER TABLE backtest_runs ADD COLUMN IF NOT EXISTS task_id        TEXT;
   ALTER TABLE backtest_runs ADD COLUMN IF NOT EXISTS status         TEXT;
   ALTER TABLE backtest_runs ADD COLUMN IF NOT EXISTS config         TEXT;
   ALTER TABLE backtest_runs ADD COLUMN IF NOT EXISTS config_json    TEXT;
   ALTER TABLE backtest_runs ADD COLUMN IF NOT EXISTS benchmark_code TEXT;
   ALTER TABLE backtest_runs ADD COLUMN IF NOT EXISTS universe       TEXT;

   CREATE TABLE IF NOT EXISTS backtest_param_sweeps (
       sweep_id    TEXT PRIMARY KEY,
       run_id      TEXT,
       param_x     TEXT,
       param_y     TEXT,
       x_value     NUMERIC,
       y_value     NUMERIC,
       avg_return  NUMERIC,
       win_rate    NUMERIC,
       trade_count INT,
       created_at  TIMESTAMP DEFAULT NOW()
   );
   ```
3. **同步部署提醒**：CLAUDE.md 的 memory 项 `project_deploy_sql_migration.md` 显示部署 deploy.sh 不跑 migration，必须由 Ops 手动 `psql apply`。修复时在 PR 描述里写清楚 SQL 路径，提示部署步骤。

---

### 🟡 P1-1 6 个新策略全部退化为 FixedPctStop（现象 B 的根因）

**问题清单**：以下文件均未 override `build_exits()`，且 `param_schema` 仍声明 `stop_loss` / `stop_profit`：

| 文件 | 应改为 | 当前状态 |
|---|---|---|
| `modules/strategies/ma_cross.py` | `ATRStop(period=14, mult=2.0) + TimeStop(max_hold_days)` | 默认 FixedPctStop ❌ |
| `modules/strategies/ma_breakout.py` | `TrailingStop(activate=0.05, giveback=0.4) + TimeStop(15)` | 默认 FixedPctStop ❌ |
| `modules/strategies/macd_golden.py` | `FixedPctStop(-0.05, 0.08) + TimeStop(5)` | 默认 FixedPctStop ✓（但参数应固化） |
| `modules/strategies/boll_reversion.py` | `TrailingStop + TimeStop(8)` | 默认 FixedPctStop ❌ |
| `modules/strategies/donchian_breakout.py` | `ATRStop(period=14, mult=2.5) + TrailingStop` | 默认 FixedPctStop ❌ |
| `modules/strategies/volume_breakout.py` | `ATRStop(period=14, mult=2.0) + TimeStop(7)` | 默认 FixedPctStop ❌ |

**修复模板**（以 `ma_cross.py` 为例）：

```python
"""双均线金叉策略：MA5 上穿 MA20"""
from .base import BaseStrategy, Signal
from .registry import register_strategy
from modules.backtest.exits import CompositeExit, ATRStop, TimeStop


@register_strategy("ma_cross")
class MaCrossStrategy(BaseStrategy):
    description = "双均线金叉：MA5 上穿 MA20，ATR 止损"
    mode = "event"
    data_requirements = {"kline": True, "lhb": False, "sentiment": False}
    param_schema = {
        "fast":           {"type": "int",   "default": 5,    "label": "快速均线"},
        "slow":           {"type": "int",   "default": 20,   "label": "慢速均线"},
        # ── Exit 参数（替换原 stop_loss / stop_profit）──
        "atr_period":     {"type": "int",   "default": 14,   "label": "ATR 周期"},
        "atr_mult":       {"type": "float", "default": 2.0,  "label": "ATR 止损倍数"},
        "max_hold_days":  {"type": "int",   "default": 10,   "label": "最大持有天数"},
        # ── 入场过滤（保留）──
        "max_buy_gap":    {"type": "float", "default": 0.09, "label": "T+1最大高开%"},
        "filter_one_word":{"type": "bool",  "default": True, "label": "过滤T+1一字涨停"},
        "allow_overlap":  {"type": "bool",  "default": True, "label": "允许重叠持仓"},
    }

    def select(self, trade_date, lhb_df, kline_cache):
        return []

    def scan_signals(self, ctx):
        # ... 不变
        ...

    def build_exits(self):
        return CompositeExit([
            ATRStop(period=self.params["atr_period"], mult=self.params["atr_mult"]),
            TimeStop(max_days=self.params["max_hold_days"]),
        ])
```

**关键点**：
1. **从 `param_schema` 删掉 `stop_loss` / `stop_profit`**（让 UI 不再显示死板阈值）。
2. **新增对应的 ATR/Trailing 参数**到 schema（让 UI 能调）。
3. **override `build_exits()`** 并使用对应组件。

**针对每个策略的具体配置**：

| 策略 | 删除参数 | 新增参数 | `build_exits()` 内容 |
|---|---|---|---|
| `ma_cross` | stop_loss, stop_profit | atr_period=14, atr_mult=2.0 | `[ATRStop(p,m), TimeStop(max_hold_days)]` |
| `ma_breakout` | stop_loss, stop_profit | trail_activate=0.05, trail_giveback=0.4 | `[TrailingStop(a,g), TimeStop(15)]` |
| `macd_golden` | （保留）| —— | `[FixedPctStop(stop_loss, stop_profit), TimeStop(5)]`（**唯一保留 FixedPct 的策略**，按方案 §8.1） |
| `boll_reversion` | stop_loss, stop_profit | trail_activate=0.05, trail_giveback=0.4 | `[TrailingStop(a,g), TimeStop(8)]` |
| `donchian_breakout` | stop_loss, stop_profit | atr_period=14, atr_mult=2.5, trail_activate, trail_giveback | `[ATRStop(p,m), TrailingStop(a,g)]` |
| `volume_breakout` | stop_loss, stop_profit | atr_period=14, atr_mult=2.0 | `[ATRStop(p,m), TimeStop(7)]` |

**`base.py:57-66` 的默认 `build_exits` 改造建议**：保留默认实现作为兜底（向后兼容老策略），但加一行 logger.warning 提示当前用了固定阈值，让接入新策略时容易发现遗漏。

```python
def build_exits(self):
    from loguru import logger
    logger.warning(f"策略 {self.name} 未 override build_exits()，使用默认 FixedPctStop")
    # ... 原有逻辑
```

---

### 🟡 P1-2 ATRStop / TrailingStop 实际不可用（依赖 P0-3）

P0-3 修好之前，即使 P1-1 把策略改完，`TrailingStop` 和 `ATRStop` 也跑不对：
- `TrailingStop` 需要 `position.max_return` 累积值 → 受 P0-3 拖累。
- `ATRStop` 调用 `ctx.data_feed.atr(code, bar.date, period)` —— 这个不依赖 Position，可以独立工作；但要确保 `data_feed.atr` 在缓存命中场景下也能正常计算（同样要核查是否受 P0-1 索引问题影响）。

**核查点**：`data_feed.py:79-97` 的 `atr()` 方法。它调 `self.kline(code, start, date)` 拿子区间，再用 numpy 算 TR/ATR。如果 P0-1 用方案 1 修复了，atr 会得到 reset_index 后的连续 df，numpy 切片正常。

---

### 🟡 P1-3 UI 未对新参数做 slider 适配

**位置**：`pages/backtest.py:46-75` `_render_params_form`

```python
elif field_type == "float":
    if key in ("stop_loss",):
        params[key] = st.slider(label, -30, 0, int(default * 100), key=field_key) / 100
    elif key in ("stop_profit",):
        params[key] = st.slider(label, 1, 50, int(default * 100), key=field_key) / 100
    ...
```

新增的 `atr_mult` / `trail_activate` / `trail_giveback` 没有匹配分支，会落到最后的 `st.number_input`。可读性能用，但 slider 会更友好。

**修复建议**（非阻塞）：
```python
elif key == "atr_mult":
    params[key] = st.slider(label, 0.5, 5.0, float(default), 0.1, key=field_key)
elif key == "trail_activate":
    params[key] = st.slider(label, 0.0, 0.3, float(default), 0.01, key=field_key)
elif key == "trail_giveback":
    params[key] = st.slider(label, 0.1, 0.8, float(default), 0.05, key=field_key)
elif key == "atr_period":
    params[key] = st.number_input(label, 5, 50, int(default), key=field_key)
```

---

### 🟢 P2-1 `engine.py:107` 日志笔数计算错误

**位置**：`modules/backtest/engine.py:107`

```python
logger.info(f"回测完成: {code} {start}-{end} → {len(df)}/{len(results)+len(skip_stats)} 笔")
```

**问题**：`skip_stats` 是 dict，`len()` 返回去重后的 reason 数量（通常 1-3），不是总跳过笔数。审查会话中显示 `0/1 笔`（实际 18 笔全跳）就是这个 bug 误导。

**修复**：
```python
total_attempted = len(results) + sum(skip_stats.values())
logger.info(f"回测完成: {code} {start}-{end} → 成交 {len(df)} / 尝试 {total_attempted} 笔；跳过明细: {skip_stats}")
```

---

### 🟢 P2-2 默认 `allow_overlap=True` 导致信号重叠不被过滤

**位置**：6 个新策略的 `param_schema` 默认 `allow_overlap=True`；老 `lhb_high_score.py` 默认 `False`。

**问题**：方案文档里没明确这个行为差异。默认 `True` 意味着 ma_cross 在 002342 上 18 个信号全部下单，即使前一笔还没卖；统计上一笔交易和下一笔交易在持仓期重叠，**累计收益曲线会被复利放大**（这一点 `pages/backtest_analysis.py` 现有警告已说明）。

**修复建议**：
- **方案 A**（推荐）：所有 K 线类策略默认 `allow_overlap=False`，与 LHB 策略保持一致。文档里可以提"事件类策略可改 True"。
- **方案 B**：保持 True 但在 UI 上加一行 caption 提示"当前允许重叠持仓 → 累计收益会被放大"。

---

### 🟢 P2-3 `engine.py:142-145` 老策略兼容路径忽略 `o.code`

**位置**：`modules/backtest/engine.py::run_batch`

```python
for date_str in trade_dates:
    if hasattr(strategy, 'select'):
        orders = strategy.select(date_str, lhb_df, self.data_feed._cache)
    else:
        continue
    for o in orders:
        code = o.code if hasattr(o, 'code') else o.get("code", "")
```

**问题**：`single_stock_event` 策略（`modules/strategies/single_stock_event.py`）的 `select()` 返回的 `BuyOrder` 里 `code` 用了**未规范化**的 6 位数字（如 `"000001"`），而 executor 里 `data_feed.kline(code, ...)` 会调 `_normalize_code` 自动补后缀 —— 这条链路目前能跑，但 `code` 的语义在 engine 层不一致。

**修复建议**（非阻塞）：在 engine.run_batch 里统一调 `_normalize_code(code)` 一次，确保下游所有地方拿到的 ts_code 一致。

---

### 🟢 P2-4 `modules/analyzer.py` 旧回测逻辑未清理

老的 `simulate_with_stoploss` / `run_backtest` 仍然存在（`modules/analyzer.py:614-985`），方案 §7 P1 阶段说"提供 shim 兼容旧调用"。当前现状：
- `lhb_high_score` 策略仍走 `select_stocks_daily`（`modules/analyzer.py:381`）路径。
- `pages/mainboard_strategy.py` / `pages/lhb_signal.py` 等可能仍引用 `analyzer.run_backtest`。

**修复建议**（暂不阻塞）：本批 PR 不必清理；P5 完成后做收尾 PR，确认无引用后删除。

---

## 2. 修复执行顺序

强烈建议按下面顺序，**每步独立提交**，便于回滚定位：

| 步骤 | 任务 | 涉及文件 | 验收 |
|---|---|---|---|
| 1 | P0-1 + P0-2（一次提交） | `modules/backtest/data_feed.py`、`modules/backtest/executor.py` | `python3 -c "from modules.backtest import BacktestEngine, DataFeed; from modules.strategies import get_strategy; df = BacktestEngine(DataFeed()).run('002342.SZ','20240101','20260508',get_strategy('ma_cross')({})); assert len(df) > 0; print(len(df))"` 输出 > 0 |
| 2 | P0-3 | `modules/backtest/executor.py`（循环外 new Position） | `df['hold_days'].max() <= max_hold_days`；`(df['exit_reason']=="到期卖出").sum() > 0` |
| 3 | P0-4 | 新增 `db/init/NN_backtest_v2_schema.sql`；提示 Ops 在 jd 上 `psql -f` 执行 | `\d backtest_results` 显示新列；保存按钮成功不报错 |
| 4 | P1-1 | 6 个 `modules/strategies/*.py`（按表格逐个改） | 每个策略跑通；UI 不再出现 stop_loss/stop_profit 滑块（除 `macd_golden`） |
| 5 | P1-2 + P1-3 | `pages/backtest.py::_render_params_form` slider 分支 | 新参数 UI 友好 |
| 6 | P2-1 ~ P2-3 | 各文件局部 | 日志/重叠默认/code 规范化 |

---

## 3. 修复后必做：部署到远程

**这是项目硬性要求**（来自 `CLAUDE.md` P0 与 memory `feedback_deploy_workflow.md`）。修复一旦提交，必须立即：

```bash
# 1. 同步代码到远程服务器
rsync -avz --exclude='__pycache__' --exclude='*.pyc' \
  --exclude='.git' --exclude='.claude/' --exclude='data/' --exclude='logs/' \
  --exclude='.worktrees/' --exclude='.venv/' \
  ./ jd:~/stock_analysis/

# 2. 若改了 db/init/，提示用户在 jd 上手动 apply：
#    ssh jd "cd ~/stock_analysis && psql -f db/init/NN_backtest_v2_schema.sql"
#    （不要自动执行，等用户确认）

# 3. 重启 Streamlit 使改动生效
ssh jd "cd ~/stock_analysis && ./stop.sh && sleep 1 && STREAMLIT_PORT=8051 ./start.sh"
```

线上访问：http://117.72.184.8:8051

---

## 4. 验收清单（修复全部完成后跑一遍）

- [ ] `002342.SZ + ma_cross + 默认参数 + 2024-01-01 ~ 2026-05-08` → 至少 5 笔交易
- [ ] `002342.SZ + ma_breakout + 默认参数` → 至少 1 笔交易
- [ ] `002342.SZ + ma_cross` → UI **不再显示** "止损比例" / "止盈比例" 滑块
- [ ] `002342.SZ + ma_cross` → UI 显示 "ATR 周期" / "ATR 止损倍数"
- [ ] `002342.SZ + macd_golden` → UI **保留** "止损比例" / "止盈比例"（这是该策略的设计）
- [ ] 启用 `filter_one_word=True` 且某 K 线 T+1 一字涨停 → 不抛 NameError，正常计入跳过
- [ ] `df['hold_days'].max() <= 策略参数 max_hold_days`
- [ ] `df['exit_reason'].value_counts()` 出现的不只是 "数据截止"
- [ ] 「💾 保存到回测结果表」按钮点击后显示成功，PG 表能查到新行（含 signal_meta JSONB 不为空）
- [ ] `pages/backtest_analysis.py` 选中刚保存的 run，KPI 指标正常显示
- [ ] `loguru` 日志中"回测完成"行的成交笔数 / 尝试笔数 / 跳过明细完整可读

---

## 5. 不在本次修复范围

- 方案 §6.2 `pages/backtest_analysis.py` 重构（最大回撤、水下曲线、滚动胜率、基准对比、单笔 K 线信号图、Exit 分布、参数敏感度热力图）—— 属于方案 P6 阶段。
- 方案 §8.1 第二批 / 第三批策略（`pullback_to_ma`、`oversold_bounce`、`mean_reversion`、`sentiment_swing`、LHB 衍生 5 个）—— P5/P6 阶段。
- 参数敏感度扫描 sweep 作业 —— P7 阶段。
- 老的 `modules/analyzer.py::run_backtest` 兼容 shim 清理 —— P5 完成后单独 PR。

---

## 6. 附：审查会话证据

```
$ python3 -c "from modules.backtest import DataFeed; df = DataFeed().kline('002342.SZ','20240101','20260508'); print(len(df))"
563

$ python3 -c "..."  # scan_signals 测试
signals: 18
  20240228 002342.SZ {'ma_fast': 3.43, 'ma_slow': 3.41}
  20240402 002342.SZ {'ma_fast': 3.56, 'ma_slow': 3.56}
  20240507 002342.SZ {'ma_fast': 3.41, 'ma_slow': 3.35}
  ...

$ python3 -c "..."  # 完整 engine.run 测试
回测完成: 002342.SZ 20240101-20260508 → 0/1 笔
trades: 0

$ python3 -c "..."  # per-signal trace
sig 0 20240228: ret=None
sig 1 20240402: ret=None
sig 2 20240507: ret=None
sig 3 20240703: ret=None
sig 4 20240715: ret=None
```

PG schema 实证：
```
column type: [('trade_date', 'date')]
002342.SZ count/min/max: (563, '2024-01-02', '2026-05-06')
BETWEEN string YYYYMMDD: (563,)
BETWEEN string YYYY-MM-DD: (563,)
```

数据库正常，K 线正常，scan_signals 正常 → 全部问题都在 executor 层。
