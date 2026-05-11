# 策略回测 & 回测分析 — 现状审查与优化方案

> 评审日期：2026-05-11
> 范围：`pages/backtest.py`、`pages/backtest_analysis.py`、`modules/backtest/*`、`modules/strategies/*`
> 目的：盘点当前实现的薄弱环节并给出可执行的优化路线

---

## 0. TL;DR — 优先级总览

| 优先级 | 主题 | 主要文件 | 影响 |
|---|---|---|---|
| 🔴 P0 | 回测结果存在**未来函数**与**保守路径偏置** | `executor.py:165-182` | 关键，回测结论失真 |
| 🔴 P0 | T+N 持有日历用「自然日」而非「交易日」 | `executor.py:73-76` | 跨长假持仓窗口异常 |
| 🔴 P0 | `TrailingStop` 跨标的状态污染 | `exits/trailing.py:16-24` | 批量回测时其他股结果被污染 |
| 🟠 P1 | 单笔信号回测**不计资金占用 / 不按组合**汇总 | 全局 | 净值曲线无法外推到真实组合 |
| 🟠 P1 | 累计曲线用「逐笔几何累乘」误导 | `metrics.py:103-113` | 与真实账户净值不一致 |
| 🟠 P1 | KPI 缺**年化收益、Sharpe 缺无风险利率、Sortino、IR、Beta** | `metrics.py:50-92` | 无法满足量化对外汇报标准 |
| 🟠 P1 | 一字涨停过滤判断不严谨 / 缺涨停板换手过滤 | `executor.py:32-37` | 可能买入实际无法成交的标的 |
| 🟡 P2 | 批量回测**单线程**，全市场跑慢 | `engine.py:172-236` | 用户体验差，长任务易超时 |
| 🟡 P2 | `pages/backtest.py` 表单状态/线程模型偏复杂 | `pages/backtest.py` | 维护成本高 |
| 🟡 P2 | 策略回测页面与「机器人对比看板」分裂 | `pages/strategy_comparison.py` | 用户跳转混乱 |
| 🟡 P2 | 缺**参数寻优 / Walk-Forward / 蒙特卡洛**等高阶分析能力 | — | 量化研究下一步 |
| 🟢 P3 | 回测明细缺导出 Excel / 报告（PDF / HTML） | `pages/backtest_analysis.py` | 复盘用 |
| 🟢 P3 | 缺策略「健康分」自动评级 | — | 选策略时缺定量参考 |

---

## 1. 现状梳理

### 1.1 模块结构

```
modules/backtest/
├── data_feed.py       # K 线 / ATR / 情绪 / LHB 统一访问层（带进程缓存）
├── engine.py          # BacktestEngine：单股 run() / 批量 run_batch()
├── executor.py        # T+1 开盘买入 + 持有期 bar 遍历 + Exit 触发
├── exits/             # FixedPct / Time / ATR / Trailing / Sentiment / Composite
├── metrics.py         # KPI、累计曲线、回撤、滚动胜率、Exit 分布、月度
└── portfolio.py       # Position / Bar 数据类

modules/strategies/
├── base.py            # BaseStrategy + Signal / BuyOrder / SellSignal
├── lhb_high_score.py  # 龙虎榜评分（旧 select 接口）
├── single_stock_event.py
└── ma_cross / ma_breakout / macd_golden / boll_reversion / donchian_breakout / volume_breakout

pages/
├── backtest.py                # 单股回测 + 批量回测（线程版）
├── backtest_analysis.py       # 结果可视化（KPI、净值、Exit、月度、K线）
└── strategy_comparison.py     # 多机器人对比（与 backtest 体系并行）
```

### 1.2 关键执行流程

```
UI 表单
  └─→ threading.Thread(_bt_runner)
        └─→ BacktestEngine.run()
              ├─ DataFeed.kline()              # 加载 K 线
              ├─ strategy.scan_signals(ctx)    # 信号扫描（事件驱动）
              │   └─ 旧 LHB 策略 fallback 到 select()
              ├─ strategy.build_exits()        # 退出链
              └─ for sig in signals:
                    └─ Executor.execute_trade()
                         ├─ T+1 开盘买入
                         ├─ 一字涨停 / 高开过滤
                         ├─ for day in 持有期: exit_chain.check()
                         └─ 保守成交价 + 双边手续费
```

### 1.3 已落地的优点

- ✅ 数据访问层（`DataFeed`）抽象到位：K 线缓存、ATR/情绪/LHB 接口统一
- ✅ Exit 组件可插拔：`CompositeExit` 支持多策略复用
- ✅ 诊断信息（`BacktestDiagnostic`）：能区分「数据缺」「无信号」「全被过滤」
- ✅ Job Store + 后台线程：切页面不中断
- ✅ KPI 已覆盖**总收益、胜率、回撤、Calmar、滚动胜率、月度、Exit 分布**
- ✅ K 线 + 买卖标注图、机构 vs 游资对比、净买入区间分析

---

## 2. 🔴 P0 — 必须立即修复

### 2.1 保守路径止损/止盈引入**未来函数偏置**

**位置**：`modules/backtest/executor.py:171-180`

```python
elif "止损" in exit_reason:
    stop_price = buy_price * (1 + stop_loss)
    sell_price = max(bar.open, stop_price)  # 取更高 → 亏损更大（保守）
elif "止盈" in exit_reason:
    stop_price = buy_price * (1 + stop_profit)
    sell_price = min(bar.open, stop_price)  # 取更低 → 收益更小（保守）
```

**问题**：

1. **概念错乱**：止损线 = 触发线，触发后真实成交价应当是
   - **跳空开盘**：`min(open, stop_price)` 的不利侧 = 实际开盘价（开盘已低于止损线，无法在 stop_price 成交）
   - **盘中触发**：理论成交价 ≈ stop_price（假设有充足流动性，最坏情况是 stop_price 之下的 tick）

   现有代码 `max(open, stop_price)` 当 open < stop_price（跳空跌停跌穿止损线）时**取更高的 stop_price**，反而**虚报亏损更小**——典型的未来函数泄漏。

2. **价格类型语义和 `SellSignal.price_type` 不一致**：`FixedPctStop` 把盘中止损/止盈标为 `"stop_trigger"`，但 executor 里依然按 reason 文本匹配（`"止损" in exit_reason`），换一个 reason 文案（如 `"ATR止损"`）就走不到这一支，价格直接用 `bar.close` ——回测严重失真。

**修复方案**：

```python
# executor.py 重写成交价决策
if price_type == "open":
    sell_price = bar.open
elif price_type == "stop_trigger":
    # 盘中触发型：止损取 min(open, stop_price)，止盈取 max(open, stop_price)
    if "止损" in exit_reason or "ATR" in exit_reason or sell_signal.component in ("fixed_pct", "atr_stop"):
        stop_price = position.buy_price * (1 + stop_loss) if "止损" in exit_reason else None
        # 跳空已穿越：以开盘价成交（最不利）
        if stop_price and bar.open < stop_price:
            sell_price = bar.open
        else:
            sell_price = stop_price or bar.close
    elif "止盈" in exit_reason:
        stop_price = position.buy_price * (1 + stop_profit)
        sell_price = bar.open if bar.open > stop_price else stop_price
    else:
        sell_price = bar.close
else:  # close / 默认
    sell_price = bar.close
```

**关键变化**：
- 路径选择不再靠 reason 字符串模糊匹配，改靠 `price_type` 显式分发
- 跳空场景按开盘价成交（更接近真实）
- 在 SellSignal 中**始终写明 component**（已有字段，目前 `FixedPctStop` 未填）让审计可追溯

---

### 2.2 持有期窗口用「自然日」导致跨假期失真

**位置**：`modules/backtest/executor.py:73-76`

```python
sd = pd.Timestamp(str(signal_date)[:8])
ed = sd + timedelta(days=max_hold_days + 15)   # ← 自然日加法
df = self.data_feed.kline(code, sd, ed)
```

**问题**：
- `max_hold_days=10` + `+15` 缓冲 = 25 自然日，遇国庆/春节最多只剩 12-15 个交易日，K 线窗口可能不够「持有 10 天 + 后续观察」
- 反过来，正常月份会多取 25 → 浪费 IO（缓存层已部分缓解，但批量场景仍偏多）

**修复方案**：

方案 A（最小改动）：把缓冲加到 `+30` 自然日，覆盖最长假期组合

方案 B（推荐）：改用交易日历
```python
# DataFeed 新增一个交易日历方法
def trading_days_after(self, date: str, n: int) -> str:
    """返回 date 之后第 n 个交易日（含 date 当天为第 0 天）"""
    # 从 kline_daily 取一段，取第 n 行的 trade_date
    ...

# executor 用它
ed_str = self.data_feed.trading_days_after(signal_date, max_hold_days + 5)
```

**额外收益**：批量回测 IO 减少约 20%。

---

### 2.3 `TrailingStop` 实例状态在多次回测之间污染

**位置**：`modules/backtest/exits/trailing.py:16-24`

```python
class TrailingStop(BaseExit):
    def __init__(...):
        self._peak: dict[str, float] = {}   # ← 实例级 dict
```

**问题**：
- `strategy.build_exits()` 在 `engine.run()` 里只调一次 → 同一个 `TrailingStop` 实例处理所有信号
- 单股回测 OK（`code` 始终一样）
- **批量回测**多只股票共用一个 `TrailingStop`：A 股的 peak 记录了，B 股第一根 bar 进来时 `peak` 还是 A 的旧值 → 极端情况下立刻触发卖出
- 连续两次跑同一只股票：上次的 peak 残留到本次（只要没 reset）

**修复方案**：

```python
# trailing.py — 用 (code, buy_date) 作为复合 key
def check(self, position, bar, ctx=None):
    if position.max_return < self.activate:
        return None
    key = (position.code, position.buy_date)
    peak = self._peak.get(key, position.buy_price)
    self._peak[key] = max(peak, bar.high)
    drawdown_from_peak = (bar.low - self._peak[key]) / self._peak[key]
    if drawdown_from_peak <= -self.giveback * self.activate:
        # 卖出后清除该 key
        self._peak.pop(key, None)
        return SellSignal(reason=f"追踪止盈({self.giveback*100:.0f}%)",
                          price_type="close", component="trailing_stop")
```

**或更彻底**：在 `Executor.execute_trade` 每次开仓前 `exits.reset()`（要求所有 stateful Exit 实现 `reset()` 接口）。

---

## 3. 🟠 P1 — 强烈建议尽快修复

### 3.1 单笔逐笔信号 ≠ 真实组合收益

**现状**：
- 页头明确写「不考虑资金占用和持仓重叠限制」「不代表真实组合收益」
- 但 KPI 卡片+净值曲线给出的是**逐笔等权累乘**（`np.cumprod(1 + rates)`），用户极易误读为「这就是策略收益」

**问题**：
- 同一日多个信号都被算入累乘 → 隐含「无限资金、可任意拆分」假设
- 持仓重叠时间被忽略 → 实际组合 Sharpe 会低于此处展示

**优化方案**：

**方案 A（轻量）**：在 KPI 卡片 + 累计曲线下方加显眼提示，并新增一个**等权 N 槽组合**模拟器（参数：组合槽位数 N=3/5/10）
```
等权 N=5 组合（每天最多持 5 只，新信号若槽位满则跳过）
──────────────────────────────────────────
  组合年化收益: 18.3%
  组合最大回撤: 12.1%
  组合 Sharpe: 1.42
  实际成交率: 73% （信号被「无槽位」过滤掉的占比）
```

**方案 B（彻底）**：引入一个最小化的 `Portfolio` 回测模式
- 每日按时间顺序处理信号
- 维护 `cash` 与 `positions` 字典（支持初始资金参数）
- 槽位/资金不足时按规则取舍（FCFS / 评分排序 / 随机）
- 输出真实 NAV 序列，复用现有 metrics 函数

**最小落地**：先做方案 A，给两条曲线（逐笔 vs 组合），用户一目了然。

---

### 3.2 累计曲线在「同一信号日多笔」下不正确

**位置**：`modules/backtest/metrics.py:103-113`

```python
def _cumulative_curve(df):
    df_sorted = df.sort_values("trade_date").copy()
    rates = df_sorted["return_rate"].astype(float).values
    cum = np.cumprod(1 + rates) - 1
```

**问题**：
- 排序按 `trade_date` 即信号日，但**真正的盈亏发生在 sell_date**
- 当多笔同日触发 → 排序不稳定，绘图毛刺
- 跨多个标的时，`cumprod` 隐含「上一笔卖完才进下一笔」假设，与并行持仓不符

**修复方案**：
```python
# 按卖出日聚合后再累乘，更接近真实复利曲线
df_sorted = df.sort_values("sell_date").copy()
# 同一卖出日多笔 → 取等权平均（或按笔数加权）
daily = df_sorted.groupby("sell_date")["return_rate"].mean()
cum = np.cumprod(1 + daily) - 1
```

并配套提示：「此曲线为信号等权日合并后的几何累乘，非真实 NAV」。

---

### 3.3 KPI 指标不全 / 计算口径偏弱

**问题**：

| 当前缺失 | 量化标准做法 | 优先级 |
|---|---|---|
| **年化收益** | `(1 + total_return) ** (365 / days_span) - 1` | 🔴 |
| **年化波动率** | `daily_returns.std() * sqrt(252)` | 🔴 |
| **Sharpe 含 rf** | `(年化收益 - rf) / 年化波动率`，目前是 `mean/std` 的逐笔 Sharpe，量级失真 | 🔴 |
| **Sortino** | 只用下行波动 | 🟡 |
| **Information Ratio** | `(策略 - 基准) / 跟踪误差` | 🟡 |
| **Beta / Alpha** | 对基准日收益做线性回归 | 🟡 |
| **回撤持续天数** | DD 区间最大长度 + 修复天数 | 🟡 |
| **VaR / CVaR (95%)** | `np.quantile(rates, 0.05)` | 🟢 |
| **Profit Factor** | `sum(wins) / abs(sum(losses))`（与盈亏比 PL ratio 不同）| 🟡 |

**建议**：
1. 先把**年化收益、年化波动、Sharpe(rf)** 纳入 KPI 卡，标注「以信号日为日频对齐」
2. KPI 卡用 tab 切分（基础 / 风险 / 风险调整后），减少视觉拥挤
3. `analyze()` 增加 `period_days` / `rf` 参数（默认 rf=0.02）

---

### 3.4 一字涨停过滤的边界情况

**位置**：`modules/backtest/executor.py:32-37`

```python
def _is_one_word_limit_up(row, prev_close, limit_pct, eps=0.001):
    limit_price = prev_close * (1 + limit_pct)
    if row["开盘"] < limit_price * (1 - eps):
        return False
    return abs(row["开盘"] - row["最高"]) < 0.0001 and abs(row["开盘"] - row["最低"]) < 0.0001
```

**问题**：
1. **价格判等用绝对差 1e-4**：低价股（< ¥5）OK，但高价股（如 ¥200+）波动 0.01 就 > 1e-4 → 漏判一字涨停
2. **没考虑当日成交量**：开盘 = 涨停价 + 成交量极小（封板） vs 高开后回落同价 vs 一字板都可能 OHL 相等
3. **缺涨停打开过滤**：开盘一字涨停但盘中开板 → 现在直接**用开盘价（涨停价）成交**，与现实严重不符

**修复方案**：
```python
def _is_unbuyable(row, prev_close, limit_pct, vol_threshold=None) -> tuple[bool, str]:
    """判断 T+1 是否实际无法买入。返回 (是否跳过, 原因)。"""
    limit_price = prev_close * (1 + limit_pct)
    rel_eps = max(0.005, limit_price * 0.0005)  # 高价股放大容差

    # 1) 一字涨停（OHLC 全在涨停价）
    is_limit_open = row["开盘"] >= limit_price - rel_eps
    if is_limit_open and abs(row["最高"] - row["最低"]) < rel_eps:
        return True, "T+1一字涨停封板"

    # 2) 开盘涨停但成交量过小 → 视为难买入（可选阈值）
    if vol_threshold and is_limit_open and row.get("成交量", 0) < vol_threshold:
        return True, "T+1涨停封板成交不足"

    return False, ""
```

**进一步**：在数据层缓存每只股票的近 20 日均量，作为 `vol_threshold = avg_vol * 0.05` 的动态阈值。

---

### 3.5 基准对比逻辑覆盖不全

**位置**：`modules/backtest/metrics.py:182-218`

**问题**：
- 仅返回基准期间的 buy & hold 总收益（首末两点）
- 没有逐日比较 → 无法画**超额收益曲线**、计算 Beta/IR
- `pages/backtest_analysis.py:152` 的 `_get_benchmark` 是用比例缩放的占位实现，不是真实指数收益序列

**修复方案**：
```python
def benchmark_curve(df, benchmark_code, data_feed):
    """返回与策略 trade_date 对齐的基准累计收益序列"""
    dates = sorted(df["trade_date"].unique())
    bench = data_feed.kline(benchmark_code, dates[0], dates[-1])
    bench["daily_ret"] = bench["收盘"].pct_change().fillna(0)
    bench["cum_ret"] = (1 + bench["daily_ret"]).cumprod() - 1
    # 对每个 trade_date 取最近的 cum_ret
    return bench.set_index("日期")["cum_ret"].reindex(dates, method="ffill")
```

并在 KPI 中加入：
- **Alpha** = 策略年化 - β × (基准年化 - rf)
- **Beta** = `Cov(strategy_d, bench_d) / Var(bench_d)`
- **IR** = `(策略 - 基准).mean() / (策略 - 基准).std()`

---

## 4. 🟡 P2 — 体验与可维护性

### 4.1 批量回测单线程 + 全市场扫描慢

**位置**：`modules/backtest/engine.py:172-236`

**问题**：
- 按 `trade_dates` 串行调 `select()` → 每天再串行调 `Executor.execute_trade`
- 全市场 1 年回测可能 > 10 分钟，UI 没有可中断按钮

**优化方案**：
1. **`DataFeed.prefetch()` 已存在**但没在 `run_batch` 里被显式利用 → 应在循环外一次性预取
2. 每日的 `select()` 是 IO+计算混合，可用 `concurrent.futures.ThreadPoolExecutor(max_workers=4)` 并发
3. 引入 **`progress_callback`**：每完成 N 笔回写 `_job_update(progress=...)`
4. 加**取消按钮**（`stop_event = threading.Event()`，`engine.run_batch(stop_event=...)`）

---

### 4.2 `pages/backtest.py` 状态管理过于膨胀

**当前问题**：
- `BacktestJob` 有 12 个字段，单股/批量混用同一类
- `_JOBS` 是模块级 dict，多用户/多浏览器 tab 会串数据（虽然单机 Streamlit 风险低）
- `_render_params_form` 用一堆 `if key in ("stop_loss",)` 的硬编码分支

**优化方案**：
1. 拆 `SingleStockJob` / `BatchJob` 两类（共享一个 `BaseJob`）
2. **参数表单驱动化**：`param_schema` 增加 `widget`/`min`/`max`/`step` 字段，`_render_params_form` 只查表
   ```python
   param_schema = {
     "stop_loss": {
       "type": "float", "widget": "slider",
       "min": -0.30, "max": 0, "step": 0.01,
       "format": "pct", "default": -0.05, "label": "止损比例",
     },
     ...
   }
   ```
   这样**新增策略参数零成本**。
3. `_JOBS` 用 `streamlit.session_state` 分用户隔离（但要保留后台线程引用）

---

### 4.3 回测 / 对比 / 复盘三块割裂

**当前**：
- `pages/backtest.py` — 跑回测、保存
- `pages/backtest_analysis.py` — 看单 run
- `pages/strategy_comparison.py` — 看多机器人（不读 `backtest_results`，读 `sim_robots`）

**优化方案**：
- 加一个**「批次对比」标签页**：从 `backtest_runs` 选 2-5 个 run_id，对比 KPI、净值、回撤
- 复用 `strategy_comparison.py` 的 `chart_*` 系列函数，把 run 也作为「策略实例」纳入对比
- 减少用户路径：跑完回测 → 自动跳转「分析」页 → 一键加入对比池

---

### 4.4 缺**参数寻优 / Walk-Forward / 蒙特卡洛**

**目标用户**：策略研究员

**最小可行版**：
1. **参数网格搜索（Grid Search）**：在策略参数表单旁加「网格寻优」按钮，给每个数值参数填 `[start, end, step]`，后端跑笛卡尔积，输出热力图（X=参数1, Y=参数2, color=Sharpe）
2. **Walk-Forward**：把回测期切成 N 段（例如 12 段月度），每段用前 6 段优化、后 1 段验证，输出**样本外胜率/收益**
3. **蒙特卡洛**：对历史交易序列**有放回抽样 1000 次** → 输出收益/回撤的 95% 置信区间，识别策略是「真有 alpha」还是「运气好」

建议先实现 Grid Search（实现成本最低，价值最高）。

---

## 5. 🟢 P3 — 锦上添花

### 5.1 报告导出
- 一键生成 **Markdown 报告**（KPI 表 + 净值图 + Top10 / Bottom10 笔）
- 可选 PDF（用 `weasyprint`）

### 5.2 策略健康度自动评分
- 综合：胜率、盈亏比、Calmar、最大连亏、样本数 → 输出 0-100 分 + 等级（A/B/C/D）
- 直接落库 `backtest_runs.health_score`

### 5.3 K 线信号图重渲染优化
- `pages/backtest_analysis.py:_render_candlestick_charts` 当 trades > 50 时极慢（每笔单独 plotly figure）
- 改成「分页 + 默认折叠」+ 「按收益排序后只渲染 Top/Bottom 各 10」

### 5.4 加明细字段
- `max_drawdown_per_trade`：单笔持有期最大回撤（用于评估 exit 是否过早或过晚）
- `mfe / mae`：最大有利偏移 / 最大不利偏移，研究 exit 优化的关键指标

---

## 6. 落地路线图

### Sprint 1（1-2 天，P0）
- [ ] 修 `executor.py` 成交价路径分发（基于 `price_type`）
- [ ] `TrailingStop` 改 `(code, buy_date)` key
- [ ] T+N 改交易日历（或先 +30 自然日临时方案）
- [ ] 加 1 个回归用例：跨长假持仓 + 跳空止损 → 期望成交价正确

### Sprint 2（3-5 天，P1）
- [ ] KPI 加年化、Sharpe(rf)、Sortino、Profit Factor
- [ ] 累计曲线按 `sell_date` 聚合后绘制
- [ ] 一字涨停过滤完善（动态阈值）
- [ ] 基准曲线改逐日对齐 + Beta/Alpha/IR
- [ ] 加「等权 N 槽组合」第二条曲线

### Sprint 3（5-7 天，P2）
- [ ] 批量回测并发 + 进度回写 + 取消按钮
- [ ] 参数表单 schema 驱动化
- [ ] 「批次对比」页面
- [ ] Grid Search 参数寻优（最小版）

### Sprint 4（按需，P3）
- [ ] Markdown / PDF 报告
- [ ] 策略健康分
- [ ] MFE/MAE 字段

---

## 7. 验证清单（每个 PR 都要过）

- [ ] `executor.py` 修改后，跑 `002342.SZ + ma_cross + 2024-01-01~2026-05-08`，与 review 文档历史结果对比：成交 16 笔 / 胜率 75% 不应变化（除非这次是修 bug）
- [ ] 跨春节 + 跳空止损用例：手动构造 K 线 → 验证成交价 = 开盘价（不是 stop_price）
- [ ] 批量回测前后跑两次同区间，结果完全一致（验证无状态污染）
- [ ] 累计曲线相对 buy & hold 同期沪深 300：年化、Beta、Alpha 三者**自洽**（数学校验）
- [ ] 单股回测 → 保存 → 「回测分析」页能立即看到该 run（验证 PG schema 完整）

---

## 8. 引用历史文档

本方案与下列既有文档**互补不冲突**，前两份的 P0-P1 已基本落地：

- `docs/backtest_review_findings_20260508.md`：第一轮审查（已修）
- `docs/backtest_review_round2_20260508.md`：第二轮审查（已修）
- `docs/backtest_refactor_plan_20260508.md`：架构方案（参考）
- `docs/code_smell_audit_20260509.md`：通用代码异味

本文档新增的核心是：**第三轮 — 业务正确性与量化研究能力**层面的优化。
