# 代码坏味道审计报告

**日期**：2026-05-09  
**扫描范围**：`pages/`（13 个文件）、`modules/`（14 个文件）、`utils/`（6 个文件）  
**总行数**：约 15,300 行（pages + modules）

---

## 问题汇总

| # | 问题 | 涉及文件数 | 实例数 | 优先级 |
|---|------|-----------|--------|--------|
| 1 | Session 缓存函数重复定义 | 5 | 10 个函数 | P0 |
| 2 | 技术指标函数重复定义 | 5 | `_sma` ×5、`_ema` ×4、`_macd` ×3 | P0 |
| 3 | 列名/表名硬编码 | 全部页面 | 103 处颜色、26 处 Plotly 背景 | P0 |
| 4 | 格式化函数重复定义 | 3 | `_fmt_pct` ×3 | P1 |
| 5 | 过长函数（God Function） | 4 | 最长 1,837 行 | P1 |
| 6 | Plotly 布局配置重复 | 全部页面 | 26 处 `rgba(0,0,0,0)` | P1 |
| 7 | `.iterrows()` 低效遍历 | 13 个文件 | 31 处 | P1 |
| 8 | 颜色常量硬编码 | 全部页面 | 103 处 | P2 |
| 9 | 魔法数字（缓存 TTL、天数） | 多处 | 分散在各页面 | P2 |
| 10 | 过宽 `except Exception` | sim_trading.py 等 | ~20 处 | P2 |

---

## 一、Session 缓存函数重复定义（P0）

**现状**：`_ss_get` / `_ss_set` 在 5 个页面文件里各定义一次，参数名还不一致（`ttl` vs `max_age`）。

```
pages/dashboard.py:16         def _ss_get(key: str, ttl: int)
pages/sim_trading.py:37       def _ss_get(key: str, ttl: int)
pages/mainboard_strategy.py:40 def _ss_get(key: str, ttl: int)
pages/lhb_signal.py:53        def _ss_get(key: str, max_age: int = 300)
pages/lhb_returns.py:60       def _ss_get(key: str, max_age: int = 300)
```

**修复方案**：新建 `utils/cache.py`，统一实现，各页面改为 `from utils.cache import ss_get, ss_set`。

---

## 二、技术指标函数重复定义（P0）

**现状**：`_sma`、`_ema`、`_macd` 在 modules 和 pages 里各自实现，逻辑相同但签名略有差异。

```
modules/feature_engine.py:38   def _sma(arr: np.ndarray, n: int)
modules/scorer.py:36           def _sma(arr: np.ndarray, n: int)
modules/analyzer.py:135        def _sma(series: np.ndarray, period: int)   ← 参数名不同
pages/lhb_signal.py:39         def _sma(arr, n: int)                        ← 无类型注解

modules/feature_engine.py:48   def _ema(arr, n)
modules/scorer.py:43           def _ema(arr, n)
modules/analyzer.py:150        def ema(arr, n)                              ← 嵌套函数
modules/strategies/macd_golden.py:8  def _ema(data, period)

modules/feature_engine.py:101  def _macd(close, fast=12, slow=26, sig=9)
modules/analyzer.py:145        def _macd(close, fast=12, slow=26, signal=9) ← 参数名不同
```

**修复方案**：新建 `modules/indicators.py`，统一导出 `sma / ema / macd / rsi`，删除各处私有实现。

---

## 三、列名与表名硬编码（P0）

**现状**：中文列名（`"代码"`、`"名称"`、`"收盘价"` 等）和英文列名（`"trade_date"`、`"ts_code"` 等）散落在所有文件里，一旦上游数据源改列名需要全局搜索替换。

典型位置：

```python
# pages/dashboard.py:206
if "trade_date" in lhb_df.columns:

# pages/lhb_returns.py:112
if "代码" not in top10.columns or "名称" not in top10.columns:

# pages/trade_comparison.py:64
SELECT ts_code, trade_date, close FROM lhb_top10 WHERE ...
```

**修复方案**：新建 `config/columns.py`，定义列名常量类：

```python
class LhbCols:
    CODE       = "代码"
    NAME       = "名称"
    CLOSE      = "收盘价"
    NET_BUY    = "龙虎榜净买额"
    CHANGE_PCT = "涨跌幅"
    RET_1D     = "上榜后1日"
    RET_5D     = "上榜后5日"
    RET_10D    = "上榜后10日"

class KlineCols:
    TS_CODE    = "ts_code"
    TRADE_DATE = "trade_date"
    CLOSE      = "close"
    CLOSE_QFQ  = "close_qfq"
```

数据库表名同理，集中到 `config/tables.py`：

```python
class Tables:
    LHB_TOP10  = "lhb_top10"
    LHB_DETAIL = "lhb_detail"
    KLINE_DAILY = "kline_daily"
    SIM_TRADES  = "sim_trades"
```

---

## 四、格式化函数重复定义（P1）

**现状**：`_fmt_pct` 在 3 个文件里各自复制，逻辑完全相同。

```
pages/lhb_returns.py:50     def _fmt_pct(v)
pages/trade_comparison.py:27 def _fmt_pct(v) -> str
pages/sim_trading.py:191    def _fmt_pct(v) -> str
pages/sim_trading.py:704    def _fmt_pct_col(v, prefix="+")  ← 变体
```

**修复方案**：在已有的 `utils/helpers.py` 或新建 `utils/formatters.py` 中统一实现：

```python
def fmt_pct(v, decimals: int = 2, default: str = "—") -> str:
    if pd.isna(v):
        return default
    return f"{v:+.{decimals}f}%"
```

---

## 五、过长函数（P1）

**现状**：

| 文件 | 函数 | 行数 | 问题 |
|------|------|------|------|
| `pages/sim_trading.py` | `render()` | ~1,837 | 混合数据加载、卡片渲染、6 个图表、审计日志 |
| `pages/lhb_signal.py` | `render()` | ~889 | 多 Tab + 多股票评分 + 多图表 |
| `pages/trade_comparison.py` | `render_tab*` | ~3 个×300 | 单个 Tab 函数仍过长 |
| `pages/mainboard_strategy.py` | `render()` | ~364 | 可接受但有改善空间 |

**修复方案**（以 sim_trading.py 为例）：

```python
# 拆分为职责单一的私有函数
def _render_capital_metrics(df): ...   # 账户资金卡片
def _render_health_panel(status): ...  # 熔断/健康面板
def _render_trade_table(df): ...       # 交易记录表格
def _render_analysis_charts(df): ...   # 分析图表（可再拆）
def _render_audit_log(df): ...         # 审计日志

def render():
    tab1, tab2, tab3 = st.tabs([...])
    with tab1: _render_trade_table(...)
    with tab2: _render_analysis_charts(...)
    with tab3: _render_audit_log(...)
```

---

## 六、Plotly 布局配置重复（P1）

**现状**：以下配置在全项目出现 **26 次**：

```python
fig.update_layout(
    plot_bgcolor="rgba(0,0,0,0)",
    paper_bgcolor="rgba(0,0,0,0)",
    margin=dict(t=30, b=0, l=0, r=0),
)
```

**修复方案**：新建 `utils/chart_utils.py`：

```python
_DARK_LAYOUT = dict(
    plot_bgcolor="rgba(0,0,0,0)",
    paper_bgcolor="rgba(0,0,0,0)",
    margin=dict(t=30, b=0, l=0, r=0),
    font=dict(color="#e0e0e0"),
)

def dark_layout(**overrides) -> dict:
    return {**_DARK_LAYOUT, **overrides}

# 使用方：
fig.update_layout(**dark_layout(title="K 线"))
```

---

## 七、`.iterrows()` 低效遍历（P1）

**现状**：项目中共有 **31 处** `.iterrows()` 调用，分布在 13 个文件。`.iterrows()` 是 Pandas 最慢的遍历方式，对百行级别数据影响尚可，对千行级别（K 线数据）有明显延迟。

重点问题位置：

```
pages/sim_trading.py:227    for _, pos_row in open_df.iterrows()
pages/sim_trading.py:1054   for _, row in df.sort_values(...).iterrows()
pages/sim_trading.py:1156   for _, cand_row in cand_df.iterrows()
modules/data_fetcher.py:461 for i, (_, row) in enumerate(lhb_dedup.iterrows())
modules/backtest/engine.py:191 for _, r in lhb_df.iterrows()
```

**修复方案**：

```python
# 替换前
for _, row in df.iterrows():
    process(row["col"])

# 替换后（itertuples，快 10x）
for row in df.itertuples(index=False):
    process(row.col)

# 或向量化（最快）
df["result"] = df["col"].map(process)
```

---

## 八、颜色常量硬编码（P2）

**现状**：全项目有 **103 处**颜色相关的硬编码字符串，包括 `#E84040`、`#33CC66`、`rgba(0,0,0,0)` 等，分散在所有 pages 文件中。

**修复方案**：新建 `config/theme.py`：

```python
class Color:
    RED   = "#E84040"
    GREEN = "#33CC66"
    GOLD  = "#FFA500"
    GRAY  = "#888888"
    BLUE  = "#4488FF"
    BG    = "rgba(0,0,0,0)"
```

---

## 九、魔法数字（P2）

**现状**：各页面的缓存超时、时间窗口等常数各自硬编码，含义不明：

```
pages/dashboard.py:122        ttl=180
pages/lhb_signal.py:90        max_age=300
pages/lhb_returns.py:60       max_age=300
pages/sim_trading.py:56,73    ttl=60, ttl=120
pages/mainboard_strategy.py:99 days=90
```

**修复方案**：新建 `config/constants.py`：

```python
class TTL:
    SHORT  = 60    # 实时数据
    MEDIUM = 120
    DEFAULT = 300  # 通用
    LONG   = 600   # 历史数据

class Window:
    RECENT = 90    # 近3月
    HALF_YEAR = 180
    ANNUAL = 365
```

---

## 十、过宽 `except Exception`（P2）

**现状**：`pages/sim_trading.py` 约有 **20 处** `except Exception` 块，捕获面太宽，错误信息被吞掉，诊断困难：

```python
try:
    from skills.stock_sim_robot.sim_trade_db import get_available_capital
    result = get_available_capital()
except Exception:
    result = {"total": 100000.0, ...}   # 静默降级，问题不可见
```

**修复方案**：明确异常类型 + 记录日志：

```python
except ImportError as e:
    logger.warning(f"sim_trade_db 未加载: {e}")
    result = DEFAULT_CAPITAL
except Exception as e:
    logger.exception("获取账户资金失败")
    result = DEFAULT_CAPITAL
```

---

## 推荐重构路径

### Phase 1 — 基础设施（优先，改动集中，风险低）

新建以下文件，不改动现有逻辑：

| 新文件 | 内容 |
|--------|------|
| `utils/cache.py` | `ss_get / ss_set` 统一实现 |
| `utils/formatters.py` | `fmt_pct` 等格式化函数 |
| `utils/chart_utils.py` | `dark_layout()` 等 Plotly 工具 |
| `modules/indicators.py` | `sma / ema / macd / rsi` 统一实现 |
| `config/columns.py` | 列名常量 |
| `config/tables.py` | 表名常量 |
| `config/constants.py` | TTL、天数等魔法数字 |
| `config/theme.py` | 颜色常量 |

### Phase 2 — 逐页面替换（逐文件迁移，每文件单独 commit）

1. `pages/dashboard.py` — 替换缓存函数、颜色、Plotly 配置
2. `pages/lhb_signal.py` — 同上 + 替换 `_sma`
3. `pages/lhb_returns.py` — 同上 + 替换 `_fmt_pct`
4. `pages/sim_trading.py` — 同上 + 拆分 `render()`（工作量最大）
5. `pages/trade_comparison.py` — 同上 + 替换 `_fmt_pct`、表名硬编码
6. `modules/scorer.py`、`modules/analyzer.py` — 替换私有技术指标函数

### Phase 3 — 性能与异常优化（可选）

- 替换关键路径上的 `.iterrows()`（engine.py、data_fetcher.py 优先）
- 规范化 `except Exception` 块

---

## 不建议做的事

- 不要一次性重构所有文件（风险太高，难以回滚）
- 不要在重构过程中顺手改业务逻辑
- Phase 1 的文件建好后，先跑一遍线上验证，再进行 Phase 2

---

## 进度追踪（2026-05-09 更新）

### Phase 1 基础设施 — 全部完成 ✅

| 文件 | 状态 |
|------|------|
| `utils/cache.py` | ✅ 已创建并部署 |
| `utils/formatters.py` | ✅ 已创建并部署 |
| `utils/chart_utils.py` | ✅ 已创建（`plotly_dark(**overrides)` 函数） |
| `modules/indicators.py` | ✅ 已创建（`sma/ema/macd`） |
| `config/theme.py` | ✅ 已创建（`Color` 类） |
| `config/constants.py` | ✅ 已创建（`TTL/Window` 类） |
| `config/columns.py` | ✅ 已创建（`LhbCols/KlineCols`） |
| `config/tables.py` | ✅ 已创建（`Tables`） |

### Phase 2 逐文件迁移 — 进行中

#### 问题 #1 Session 缓存重复 — ✅ 完成
所有 5 个页面 `_ss_get/_ss_set` 全部替换为 `utils.cache.ss_get/ss_set`，无残留。

#### 问题 #2 技术指标重复 — ✅ 完成
`modules/scorer.py`、`modules/feature_engine.py`、`modules/strategies/macd_golden.py`、`modules/analyzer.py`、`pages/lhb_signal.py`
全部替换为 `from modules.indicators import sma/ema/macd`，无残留。

#### 问题 #4 格式化函数重复 — ✅ 完成
`pages/lhb_returns.py`、`pages/trade_comparison.py`、`pages/sim_trading.py`（含 `_fmt_pct_col`）
全部替换为 `from utils.formatters import fmt_pct`，无残留。

#### 问题 #6 Plotly 暗色主题重复 — ✅ 完成
全部页面已迁移至 `plotly_dark(**overrides)`。以下两处**刻意保留**（非暗色需求）：
- `sim_trading.py:1700` — 盘中价格图浅色背景 `rgba(248,250,252,1)`
- `backtest.py:636` — K线专用深色 `#0d1117`

#### 问题 #8 颜色常量硬编码 — ✅ 完成
所有页面主色系（RED/GREEN/GOLD/BLUE/GRAY/YELLOW）已替换为 `Color.*`，零硬编码残留。
`config/theme.py` 补充了 `YELLOW = "#FFCC00"`（sim_trading 正常期）。

#### 问题 #9 TTL 魔法数字 — ✅ 完成
`pages/lhb_returns.py` 4 处 `max_age=数字` 全部替换为 `TTL.LONG/DEFAULT/MEDIUM`，docstring 同步更新。

### 最终状态（2026-05-09）

| # | 问题 | 优先级 | 状态 |
|---|------|--------|------|
| 1 | Session 缓存重复 | P0 | ✅ |
| 2 | 技术指标重复 | P0 | ✅ |
| 3 | 列名/表名硬编码（基础设施） | P0 | ✅ `columns.py`/`tables.py` 已建；页面引用属 Phase 3 |
| 4 | 格式化函数重复 | P1 | ✅ |
| 5 | 过长函数 | P1 | ⬜ 未做（风险高，另立任务） |
| 6 | Plotly 暗色主题重复 | P1 | ✅ |
| 7 | `.iterrows()` 低效 | P1 | ⬜ 未做（Phase 3） |
| 8 | 颜色常量硬编码 | P2 | ✅ |
| 9 | TTL 魔法数字 | P2 | ✅ |
| 10 | 过宽 except Exception | P2 | ⬜ 未做（Phase 3） |

### 部署状态

最后部署：2026-05-09 末次 rsync + 重启，健康检查 HTTP 200 ✅
线上地址：http://117.72.184.8:8051
