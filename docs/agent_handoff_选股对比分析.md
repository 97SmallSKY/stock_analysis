# Agent 交接提示词 — 选股对比分析系统

> 本文件供后续 agent 继续开发使用。描述当前已完成的工作、待解决的问题、以及继续开发的具体任务。

---

## 背景

这是一个 A 股龙虎榜量化交易系统（路径：`/Users/xiaotian/Develop/personal/stock_analysis`）。

我已完成「选股对比分析系统」的全部代码开发，但还需要你来验证和修复若干已知问题。完整设计方案在 `docs/选股对比分析系统设计方案.md`。

---

## 已完成的工作

| 文件 | 做了什么 |
|------|---------|
| `skills/stock_sim_robot/sim_trade_db.py` | 新增3个表定义（`sim_candidate_results` / `sim_post_tracking` / `sim_news_events`）及对应 CRUD 函数；`close_trade()` 末尾接入新闻拉取钩子 |
| `modules/post_close_tracker.py` | **新文件**：平仓后 T+N 价格跟踪、候选股 T+N 收益填充 |
| `modules/news_fetcher.py` | **新文件**：AKShare `stock_news_em` 新闻拉取 + Claude Haiku 情感分类 |
| `modules/comparison_analyzer.py` | **新文件**：同天候选对比、错失机会统计、持股时间轴、LLM 深度分析 |
| `scripts/daily_update.py` | 新增 step9（平仓后跟踪）和 step10（候选股收益填充） |
| `pages/trade_comparison.py` | **新文件**：Streamlit 三 Tab 复盘页面 |
| `skills/stock_sim_robot/summary_report.py` | `format_weekly_report()` 末尾追加「本周选股复盘」区块 |
| `pages/sim_trading.py` | 新增 `_render_same_day_competitor_widget()` 函数，在持仓列表下展示同天竞品 |

---

## 需要你解决的已知问题

### Bug 1：`sim_candidate_results` 缺 UNIQUE 约束（必修）

**问题**：`save_candidate_results()` 使用 `ON CONFLICT DO NOTHING`，但建表 DDL 未声明 UNIQUE 约束，导致幂等保护失效，会重复写入数据。

**位置**：`skills/stock_sim_robot/sim_trade_db.py`，找到 `_CREATE_SIM_CANDIDATE_RESULTS` 字符串。

**修复**：在 `selection_id INT NOT NULL,` 行末或表尾加 `UNIQUE(selection_id)`。

```python
# 修改前
_CREATE_SIM_CANDIDATE_RESULTS = """
CREATE TABLE IF NOT EXISTS sim_candidate_results (
    id              SERIAL PRIMARY KEY,
    selection_id    INT NOT NULL,
    ...

# 修改后：在表定义末尾加 UNIQUE
    fetched_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(selection_id)          -- ← 新增这行
);
```

---

### Bug 2：`get_kline_raw(code, sell_date, days=0)` 可能返回空（必修）

**问题**：`modules/post_close_tracker.py` 中有这段代码：
```python
kline_before = get_kline_raw(code, sell_date, days=0)
```
`days=0` 时窗口过小，`get_kline_raw` 内部计算 `end_dt = sell_date + days*2+5`，实际只有5天自然日窗口，但目的是取 sell_date **之前**的数据来计算5日均量，这个调用方式不对。

**修复**：
```python
# 修改前
kline_before = get_kline_raw(code, sell_date, days=0)
avg_vol_5d = None
if kline_before is not None and not kline_before.empty and "成交量" in kline_before.columns:
    kline_before["日期"] = kline_before["日期"].astype(str).str.replace("-", "")
    past = kline_before[kline_before["日期"] <= sell_date].tail(5)

# 修改后：改用 get_kline（带缓存，适合历史数据）
from modules.market_data import get_kline
kline_before = get_kline(code, sell_date, lookback=10)
avg_vol_5d = None
if kline_before is not None and not kline_before.empty and "成交量" in kline_before.columns:
    past = kline_before.tail(5)
```

---

### Bug 3：Excel 模式兼容性（建议修复）

**问题**：当 `STORAGE_MODE="excel"` 时（本地开发默认模式），新增的所有 CRUD 函数会调用 `_get_engine()` 导致失败。需要在入口处加检查。

**涉及文件**：
- `modules/comparison_analyzer.py` — `build_comparison_table()` 等函数
- `modules/post_close_tracker.py` — `batch_track_pending()` 等函数

**修复方式**：在每个函数开头加：
```python
from utils.storage import storage
if storage.mode != "postgres":
    logger.info("当前非 PostgreSQL 模式，跳过")
    return pd.DataFrame()  # 或 return 0 / {}
```

**查看 storage.mode 的方式**：
```python
# utils/storage.py 中有 storage 单例，可读取 .mode 属性
from utils.storage import storage
print(storage.mode)  # "excel" or "postgres"
```

---

## 验证步骤（完成 Bug 修复后执行）

### Step 1：建表
```bash
cd /Users/xiaotian/Develop/personal/stock_analysis
python -c "
from skills.stock_sim_robot.sim_trade_db import create_extended_tables
create_extended_tables()
"
```
预期：控制台输出 `sim_candidate_results / sim_post_tracking / sim_news_events 已就绪 ✅`

### Step 2：填充候选股历史收益
```bash
python scripts/daily_update.py --steps 10
```
预期：输出 `Step 10 完成：处理 N 个日期的候选股收益`

### Step 3：验证对比分析函数
```python
from modules.comparison_analyzer import build_comparison_table, find_missed_opportunities
# 用实际存在的日期
table = build_comparison_table("20260320")
print(table.head())
missed = find_missed_opportunities("20260301", "20260331", threshold=3.0)
print(missed.head())
```

### Step 4：验证新闻拉取
```python
from modules.news_fetcher import fetch_news_during_hold
df = fetch_news_during_hold("600519", "20260301", "20260310")
print(df.head())
```

### Step 5：启动 Streamlit 验证 UI
```bash
streamlit run app.py
```
进入「选股决策复盘」页面，选一个有数据的日期，验证3个 Tab 都能正常渲染。

---

## 代码中的关键依赖

```
新增文件依赖关系:
  post_close_tracker.py
    → market_data.get_kline / get_kline_raw
    → market_data._get_nth_trading_day
    → sim_trade_db.save_post_tracking / get_pending_post_tracking_trades
    → sim_trade_db.save_candidate_results / get_pending_candidate_dates

  news_fetcher.py
    → akshare.stock_news_em (via safe_akshare_call)
    → anthropic.Anthropic() (Claude Haiku)
    → sim_trade_db.save_news_events
    → comparison_analyzer.analyze_same_day_candidates

  comparison_analyzer.py
    → sim_trade_db.get_candidate_results / get_post_tracking / get_news_events
    → sim_trade_db._get_engine (直接 SQL 查询)
    → market_data.get_kline_raw
    → anthropic.Anthropic() (Claude Haiku，仅高价值案例)

  pages/trade_comparison.py
    → comparison_analyzer.*
    → sim_trade_db._get_engine (直接 SQL)

  summary_report.py (修改)
    → comparison_analyzer.build_weekly_review_summary

  pages/sim_trading.py (修改)
    → comparison_analyzer.build_comparison_table
```

---

## 注意事项

1. **不要动 `utils/storage.py` 和 `modules/scorer.py`**，这两个是核心共享模块，改动影响大。
2. **PostgreSQL 表的 DDL 修改**需要幂等（`CREATE TABLE IF NOT EXISTS`，`ALTER TABLE ... ADD COLUMN IF NOT EXISTS`）。
3. 所有 AKShare 调用都必须通过 `safe_akshare_call` 包装（`utils/retry.py`），不能直接调用 `ak.*`。
4. 飞书消息格式：纯文本 Markdown，不支持 HTML 颜色，用 emoji 代替。
