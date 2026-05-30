# 策略回测 P0/P1 修复后审查报告（第二轮）

**日期**：2026-05-08（紧接 `backtest_review_findings_20260508.md` 第一轮修复完成后）
**审查对象**：上一轮修复后的 `modules/backtest/` + `modules/strategies/` + `pages/backtest.py` + PG schema
**目标读者**：负责后续修复的下一位 agent。本文档**自包含**。

---

## 0. 第一轮修复验证（实测结果）

| 项 | 状态 | 实证 |
|---|---|---|
| P0-1 `data_feed.py:46` reset_index | ✅ 已修 | `cached[mask].copy().reset_index(drop=True)` 已就位 |
| P0-2 `executor.py:112,116` `_base` → `base` | ✅ 已修 | 已确认 |
| P0-3 `Position.hold_days` 循环外创建 + `update()` | ✅ 已修 | `executor.py:132/145-148` |
| P0-4 PG 表 schema | ⚠️ **部分修复** | 详见 §1.1 |
| P1-1 6 个新策略 `build_exits()` override | ✅ 已修 | 5 个用 ATR/Trailing，`macd_golden` 按设计保留 FixedPct |
| P2-1 `engine.py` 日志计数 | ✅ 已修 | `成交 X / 尝试 Y 笔；跳过明细: {...}` |

**实测 002342.SZ + ma_cross + 默认参数 + 2024-01-01 ~ 2026-05-08**：
```
本地：成交 16 / 尝试 18 笔；跳过明细: {'持仓重叠跳过': 2}
远程：成交 16 / 尝试 18 笔；跳过明细: {'持仓重叠跳过': 2}
exit_reason 分布: {'到期卖出': 14, 'ATR止损(-2.0σ)': 2}
```

**全部 6 个新策略 002342.SZ 实测**：
```
ma_cross           : 16 笔  胜率 75%
ma_breakout        :  7 笔  胜率 29%  exits={'追踪止盈(40%)': 5, '到期卖出': 2}
macd_golden        : 12 笔  胜率 50%  exits={'到期卖出': 8, '盘中止盈': 3, '盘中止损': 1}
boll_reversion     : 12 笔  胜率 50%  exits={'追踪止盈(40%)': 8, '到期卖出': 4}
donchian_breakout  : 29 笔  胜率 17%  exits={'追踪止盈(40%)': 18, '到期卖出': 7, 'ATR止损(-2.5σ)': 4}
volume_breakout    :  6 笔  胜率 67%
```

**结论**：引擎/策略层 002342.SZ 已**不再无信号**。如果用户在 UI 仍看到"在区间内无信号"，根因不在 P0-1，而在下方剩余问题（最可能是 §2.2 / §2.3）。

---

## 1. 仍存在的 P0 问题

### 🔴 P0-A `backtest_results` / `backtest_runs` 表 schema 不全 → 保存按钮**静默失败**

#### 问题描述

执行 SQL 实测：

```sql
\d backtest_results
-- 列: trade_date, code, name, net_buy, seat_score, tech_score, final_score,
--     has_institution, famous_count, gain_20d, buy_date, t1_gap, signal_close,
--     prev_close, sell_date, buy_price, sell_price, return_rate,
--     return_rate_gross, hold_days, exit_reason, cost_rate, run_id,
--     signal_meta, exit_component, benchmark_return

\d backtest_runs
-- 列: task_id, strategy_name, status, config, result_json, error_message,
--     created_at, finished_at, config_json, benchmark_code, universe
```

而 `engine._save_results` (`modules/backtest/engine.py:186-215`) 与 `pages/backtest.py::_save_section` (`pages/backtest.py:265-294`) 写入的字段：

| 表 | 代码写但表里**缺**的字段 |
|---|---|
| `backtest_results` | `run_type`, `strategy_name`, `created_at` |
| `backtest_runs` | `run_id`, `start_date`, `end_date`, `trade_count` |

#### 触发后果

`utils/storage.py::write` (line 192-227)：
```python
try:
    df.to_sql(pg_table, engine, if_exists="append", ...)
except Exception as e:
    logger.error(f"写入 PostgreSQL 失败 [{pg_table}]: {e}")
    return False    # ← 静默失败
```

`pandas.DataFrame.to_sql` 在表里缺列时直接抛 `psycopg2.errors.UndefinedColumn`，被 `except` 捕获返回 `False`。但 `pages/backtest.py:284`：
```python
storage.write(df_to_save, "backtest_results", mode="append")
storage.write(run_meta, "backtest_runs", mode="append")
st.success(f"已保存 {len(df_to_save)} 条 (run={run_id})")  # ← 无视返回值
```

**UI 始终显示成功，但 PG 实际未写入**。用户后续在「📊 回测分析」页面**看不到这次回测的 run**，会以为"功能不稳定"或"回测又没结果"。

#### 修复（必做）

新增 migration `db/init/06_backtest_v2_schema_fix.sql`（编号取下一个未占用值；当前 `db/init/` 目录里没看到任何 backtest 相关 migration，需要先核对）：

```sql
-- backtest_results 补充列
ALTER TABLE backtest_results ADD COLUMN IF NOT EXISTS run_type       TEXT;
ALTER TABLE backtest_results ADD COLUMN IF NOT EXISTS strategy_name  TEXT;
ALTER TABLE backtest_results ADD COLUMN IF NOT EXISTS created_at     TIMESTAMP;

-- backtest_runs 补充列
ALTER TABLE backtest_runs ADD COLUMN IF NOT EXISTS run_id      TEXT;
ALTER TABLE backtest_runs ADD COLUMN IF NOT EXISTS start_date  TEXT;
ALTER TABLE backtest_runs ADD COLUMN IF NOT EXISTS end_date    TEXT;
ALTER TABLE backtest_runs ADD COLUMN IF NOT EXISTS trade_count INT;

-- 加索引便于按 run_id 检索
CREATE INDEX IF NOT EXISTS idx_backtest_results_run_id ON backtest_results(run_id);
CREATE INDEX IF NOT EXISTS idx_backtest_runs_run_id   ON backtest_runs(run_id);
```

**部署提醒**：按 CLAUDE.md memory `project_deploy_sql_migration.md`，`deploy.sh` 不跑 migration，必须 Ops 手动 `psql -f` apply。修复 PR 描述要醒目标注。

#### 同时建议加：`storage.write` 失败时让上层感知

在 `pages/backtest.py:284-286` 检查返回值：
```python
ok1 = storage.write(df_to_save, "backtest_results", mode="append")
ok2 = storage.write(run_meta, "backtest_runs", mode="append")
if ok1 and ok2:
    st.success(f"已保存 {len(df_to_save)} 条 (run={run_id})")
else:
    st.error(f"保存失败：results={ok1} runs={ok2}，请查看 logs/streamlit.log")
```

`engine._save_results` 同理。

---

## 2. 仍存在的 P1 问题（用户感知层面）

### 🟡 P1-A `pages/backtest.py:218-221` —— UI 残留 job 与当前表单 ts_code 解绑

```python
if job and job.status == "success" and job.result is not None:
    df = job.result
    if df.empty:
        st.warning(f"{ts_code} 在区间内无信号")    # ← ts_code 用的是当前表单值
```

**触发场景**：
1. 用户跑 A 股票 → job 跑出空（修复前 / 短区间 / 极端参数）。
2. 改成 B 股票（如 002342），但**没点"开始回测"**。
3. UI 上 `ts_code` 已经是 B，但 `job.result` 仍是 A 的（空）。
4. 警告显示 "002342.SZ 在区间内无信号" —— **实际上引擎从没对 002342 跑过**。

这极可能是用户反馈"还是没有修改彻底"的真实原因：他可能看到的是 stale 警告。

#### 修复

把 ts_code 持久化到 job 自身，而不是从当前表单读：

```python
# pages/backtest.py BacktestJob dataclass 新增字段
@dataclass
class BacktestJob:
    job_type: str = ""
    status: str = "running"
    progress: float = 0.0
    message: str = ""
    result: object = None
    error: str = None
    ts_code: str = ""           # 新增
    strategy_name: str = ""     # 新增
    start: str = ""             # 新增
    end: str = ""               # 新增

# 创建 job 时填入
_JOBS[new_id] = BacktestJob(
    job_type="single", ts_code=ts_code,
    strategy_name=selected_strategy_name, start=sd, end=ed,
)

# 显示警告时用 job 自带值
if df.empty:
    st.warning(f"{job.ts_code} 在 {job.start}~{job.end} 区间内、用 {job.strategy_name} 无信号产生")
```

加上 `start`/`end`/`strategy_name` 后，警告还能告诉用户**到底是哪次回测、什么参数下**没结果，避免歧义。

---

### 🟡 P1-B `pages/backtest.py:189` `roundtrip_cost` 与 `benchmark` UI 配置形同虚设

```python
benchmark = st.selectbox("对比基准", [...])         # ← 没传给 engine
roundtrip_cost = st.number_input("双边手续费", ...)  # ← 没传给 engine
```

但 `_bt_runner` (`pages/backtest.py:94-109`) 里：
```python
def _bt_runner(job_id, ts_code, sd, ed, strategy):
    feed = DataFeed()
    engine = BacktestEngine(feed)         # ← Executor 内部 hardcoded 0.0025
    ctx = BacktestContext(...)            # ← benchmark_code 默认 "000300.SH"
    df = engine.run(ts_code, sd, ed, strategy, ctx)
```

**后果**：用户调"双边手续费 0.005"按钮，引擎照样按 0.0025 算；切换基准也无效。

#### 修复

1. `BacktestEngine.__init__` 接受 `roundtrip_cost`：
   ```python
   def __init__(self, data_feed=None, roundtrip_cost=0.0025):
       self.data_feed = data_feed or DataFeed()
       self.executor = Executor(self.data_feed, roundtrip_cost=roundtrip_cost)
   ```
2. `_bt_runner` 接受额外参数：
   ```python
   def _bt_runner(job_id, ts_code, sd, ed, strategy, roundtrip_cost, benchmark_code):
       feed = DataFeed()
       engine = BacktestEngine(feed, roundtrip_cost=roundtrip_cost)
       ctx = BacktestContext(data_feed=feed, code=ts_code, start=sd, end=ed,
                             strategy_params=strategy.params,
                             benchmark_code=benchmark_code)
       df = engine.run(ts_code, sd, ed, strategy, ctx)
   ```
3. page 启动线程时传入：
   ```python
   bench_code = benchmark.split()[0] if benchmark != "无" else None  # "000300.SH"
   threading.Thread(target=_bt_runner, args=(
       new_id, ts_code, sd, ed, strategy, roundtrip_cost, bench_code,
   ), daemon=True).start()
   ```

**注意**：`benchmark` 只是存到 ctx，**还没人用**（基准对比图属于 P6 阶段方案 §6.2 才落地）。但接入参数链路是必需的，否则 P6 无法工作。

---

### 🟡 P1-C `engine.py` 真无信号 vs 全部被过滤的语义混淆

`engine.py:60-62`：
```python
if not signals:
    logger.info(f"无信号: {code} {start}-{end}")
    return pd.DataFrame()
```

`engine.py:107`：
```python
logger.info(f"回测完成: ... → 成交 {len(df)} / 尝试 {total_attempted} 笔；跳过明细: {skip_stats}")
```

但**返回到 page 的都是 `pd.DataFrame()`**，page 无法区分：
- A. scan_signals 真的没产出（区间太短、MA 凑不齐）
- B. scan_signals 产出 N 个但全部被过滤（持仓重叠、一字涨停、高开过滤、K 线缺失）

UI 只能千篇一律地说"无信号"。

#### 修复（强烈建议）

让 `engine.run` 返回 `(df, diagnostic)` 元组：

```python
@dataclass
class BacktestDiagnostic:
    signals_total: int = 0      # scan_signals 产出数量
    trades_total: int = 0       # 实际成交数
    skip_stats: dict = field(default_factory=dict)
    kline_rows: int = 0         # 加载的 K 线行数

def run(self, ...) -> tuple[pd.DataFrame, BacktestDiagnostic]:
    diag = BacktestDiagnostic()
    diag.kline_rows = len(self.data_feed.kline(code, start, end))
    signals = strategy.scan_signals(ctx) if ... else ...
    diag.signals_total = len(signals)
    if not signals:
        return pd.DataFrame(), diag
    ...
    diag.skip_stats = skip_stats
    diag.trades_total = len(results)
    return df, diag
```

page 显示警告时：
```python
df, diag = job.result
if df.empty:
    if diag.kline_rows == 0:
        st.error(f"{job.ts_code} 数据库无 K 线数据")
    elif diag.signals_total == 0:
        st.warning(f"{job.ts_code} 在区间内无信号产生（K 线 {diag.kline_rows} 行 → 信号 0 个，请检查策略参数或扩大区间）")
    else:
        skip_detail = "、".join(f"{k}:{v}" for k, v in diag.skip_stats.items())
        st.warning(f"{job.ts_code} 产生 {diag.signals_total} 个信号但全被过滤：{skip_detail}")
```

这样用户能立即定位是哪类问题，不会再误以为"代码没修对"。

---

## 3. 仍存在的 P2 问题

### 🟢 P2-A `pages/backtest.py:212-214` 进度条 sleep + rerun 模式

```python
if is_running:
    st.progress(int(job.progress), text=job.message)
    import time as _t; _t.sleep(1.5); st.rerun()
```

每 1.5 秒整页 rerun → 所有 cache miss 的查询重跑（K 线已经在 _KLINE_CACHE 兜底，但策略表单状态会反复重建）。性能不阻塞，但可以改 `st_autorefresh` 或 streamlit 1.30+ 的 fragment-level rerun。

非阻塞，可后置。

---

### 🟢 P2-B `base.py:57-66` 默认 `build_exits` 仍 silent fallback

```python
def build_exits(self) -> "BaseExit":
    """构建退出组件链（默认：固定止损止盈 + 时间止损）"""
    from modules.backtest.exits import FixedPctStop, TimeStop, CompositeExit
    stop_loss = self.params.get("stop_loss", -0.05)
    stop_profit = self.params.get("stop_profit", 0.08)
    max_hold_days = self.params.get("max_hold_days", 10)
    return CompositeExit([
        FixedPctStop(stop_loss=stop_loss, stop_profit=stop_profit),
        TimeStop(max_days=max_hold_days),
    ])
```

未来再加新策略时，**忘了 override `build_exits`** 会**静默退化为 FixedPctStop**。建议加 warning：

```python
def build_exits(self) -> "BaseExit":
    from loguru import logger
    logger.warning(
        f"策略 {self.name} 未 override build_exits()，回退到默认 FixedPctStop+TimeStop。"
        f"如需 ATR/Trailing/Sentiment exit，请实现 build_exits() 方法。"
    )
    ...
```

---

### 🟢 P2-C `engine.py:142-153` 老策略兼容路径未规范化 code

```python
for o in orders:
    code = o.code if hasattr(o, 'code') else o.get("code", "")
    ret = self.executor.execute_trade(code, date_str, ctx, strategy=strategy)
```

`single_stock_event.select()` 返回的 `BuyOrder` 里 code 用了 `"000001"` 这种 6 位裸号，executor 调 `data_feed.kline(code, ...)` 走 `_normalize_code` 自动补后缀 —— 链路能跑，但 engine 层 `code` 语义不一致。

修复（非阻塞）：engine 内统一调一次 `_normalize_code`。

---

### 🟢 P2-D `data_feed.py::sentiment` / `lhb_events` 返回类型与字段未文档化

```python
def sentiment(self, date: str) -> dict | None:
    return {
        "trade_date": str(row.get("trade_date", ""))[:10],
        "temperature": float(row.get("temperature", 50)),
        "index_trend": str(row.get("index_trend", "neutral")),
        "trend_flip_to_bearish": str(row.get("index_trend", "")) == "bearish",
        "trade_signal": str(row.get("trade_signal", "normal")),
    }
```

`SentimentReversal` exit (`exits/sentiment.py`) 依赖 `snap["trend_flip_to_bearish"]` —— 但当前实现里这个 key **永远为当日 trend == "bearish"**，**不是真正的"翻转"**（需要对比前一日）。这违反了字段名的承诺。

修复：在 `data_feed.sentiment()` 里加上前一日比较：
```python
prev = df[df["trade_date"] < target].iloc[-1] if len(df[df["trade_date"] < target]) > 0 else None
trend_flip = (
    prev is not None and
    str(prev.get("index_trend", "")) == "bullish" and
    str(row.get("index_trend", "")) == "bearish"
)
```

非阻塞（暂无策略真用 SentimentReversal），但 P5 阶段会用到。

---

## 4. 用户感知层面：为什么"还是没修改彻底"？

按可能性从高到低：

1. **§2.2 P1-A**：用户在 UI 上**改了股票代码但没点"开始回测"**，看到的警告是上一次空 job 的残留，但 ts_code 显示的是新代码。修 §2.2 后能彻底消除歧义。

2. **§1.1 P0-A**：用户**点击保存后看到 success，但其实写入失败** → 在「📊 回测分析」页面看不到 → 误以为"回测没成功"。修 §1.1 后保存可见。

3. **§2.3 P1-C**：用户用了某种**导致全部被过滤**的参数（比如把 `max_buy_gap` 调到 0.01，所有 T+1 高开都被过滤），UI 只说"无信号"，他不知道是被过滤了。修 §2.3 后能看到诊断详情。

4. **场景：极端参数 / 区间太短**：实测 `002342.SZ + ma_cross + fast=50 slow=200 + 2 年` → 1 笔；`+ 仅 14 天区间` → 0 笔。这是**真的无信号**，不是 bug，但前端没告知用户。修 §2.3 后能告知。

5. **远程 Streamlit 缓存了旧字节码**：实测远程 Streamlit PID 1657903 启动于 15:47:30，**晚于**所有代码改动 mtime（15:42-15:43）。所以**这条不成立**，远程加载的是新代码。

---

## 5. 修复执行顺序建议

| 步骤 | 任务 | 文件 | 优先级 |
|---|---|---|---|
| 1 | §1.1 PG schema 补列 + write 失败感知 | `db/init/06_backtest_v2_schema_fix.sql`、`pages/backtest.py:284`、`engine.py:186` | P0 |
| 2 | §2.1 Job 对象绑定 ts_code/strategy/start/end | `pages/backtest.py` BacktestJob + 警告显示 | P1 |
| 3 | §2.3 引擎返回 diagnostic + 详细警告 | `engine.py::run` 返回元组、`pages/backtest.py` 渲染 | P1 |
| 4 | §2.2 roundtrip_cost / benchmark 接入 engine | `engine.py`、`pages/backtest.py` | P1 |
| 5 | §3.* P2 系列 | 各文件局部 | P2 |

每步独立 commit。

---

## 6. 部署节奏

按 CLAUDE.md P0：

```bash
# 1. 同步代码
rsync -avz --exclude='__pycache__' --exclude='*.pyc' \
  --exclude='.git' --exclude='.claude/' --exclude='data/' --exclude='logs/' \
  --exclude='.worktrees/' --exclude='.venv/' \
  ./ jd:~/stock_analysis/

# 2. 若改 schema：提示用户在 jd 上手动 apply（不要自动执行）
#    ssh jd "cd ~/stock_analysis && psql -h <pg_host> -U <user> -d lhb_quant -f db/init/06_backtest_v2_schema_fix.sql"

# 3. 重启 Streamlit
ssh jd "cd ~/stock_analysis && ./stop.sh && sleep 1 && STREAMLIT_PORT=8051 ./start.sh"
```

线上：http://117.72.184.8:8051

---

## 7. 验收清单

修复全部完成后：

- [ ] 002342.SZ + ma_cross + 默认 + 2024-01-01 ~ 2026-05-08 → UI 显示 "✅ 16 笔交易"
- [ ] 改成 600519 但**不点击开始回测** → UI 警告**不**显示 600519，仍显示上一次回测的代码（或显示"请点击开始回测"）
- [ ] 极端参数（fast=50 slow=200）→ 警告显示 "K 线 X 行 → 信号 0 个，请检查策略参数或扩大区间"
- [ ] 短区间（仅 14 天）→ 同上
- [ ] `max_buy_gap=0.01` 让所有信号被过滤 → 警告显示 "产生 N 个信号但全被过滤：T+1高开X%超过阈值1%放弃:N"
- [ ] 「💾 保存到回测结果表」→ UI 显示成功后，去 PG 查 `SELECT * FROM backtest_results WHERE run_id='...'` 能查到完整记录（含 `signal_meta`、`exit_component`、`strategy_name`、`run_type`、`created_at`）
- [ ] 「📊 回测分析」页面下拉能看到刚保存的 run，KPI 正常显示
- [ ] UI 修改"双边手续费"为 0.005 → 跑完后 `df["cost_rate"].iloc[0] == 0.005`
- [ ] 数据库不存在的股票（如 `999999.SZ`）→ 警告显示 "数据库无 K 线数据"

---

## 8. 不在本次修复范围

- 方案 §6.2 `pages/backtest_analysis.py` 重构（最大回撤 / 水下曲线 / K 线信号图 / Exit 分布 / 参数敏感度）—— P6 阶段。
- 方案 §8.1 第二/第三批策略 —— P5/P6。
- 参数敏感度 sweep 作业 —— P7。
- 老 `modules/analyzer.py::run_backtest` shim 清理 —— P5 之后。

---

## 9. 附：实证数据

```
$ python3 -c "...end-to-end test..."
回测完成: 002342.SZ 20240101-20260508 → 成交 16 / 尝试 18 笔；跳过明细: {'持仓重叠跳过': 2}
trades: 16
exit_reason 分布: {'到期卖出': 14, 'ATR止损(-2.0σ)': 2}
hold_days 范围: 4 ~ 10

$ ssh jd "...remote test..."
远程实测: 16 笔
```

PG 表 schema 实证：
```
backtest_results 列: ['trade_date', 'code', 'name', ..., 'run_id', 'signal_meta', 'exit_component', 'benchmark_return']
                  缺: run_type, strategy_name, created_at

backtest_runs 列:    ['task_id', 'strategy_name', 'status', 'config', 'result_json', ..., 'config_json', 'benchmark_code', 'universe']
                  缺: run_id, start_date, end_date, trade_count

backtest_param_sweeps 表存在: backtest_param_sweeps (P7 准备好了)
```

代码 / streamlit 部署时间核对：
```
executor.py mtime:         2026-05-08 15:42:59
data_feed.py mtime:        2026-05-08 15:42:07
ma_cross.py mtime:         2026-05-08 15:43:47
streamlit (PID 1657903):   Started 2026-05-08 15:47:30  ← 在所有改动之后
```
**结论：远程 Streamlit 加载的是修复后的代码，不存在"重启遗忘"问题。**
