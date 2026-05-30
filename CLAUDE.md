# CLAUDE.md — Claude Code 行为规范

---

## ⚠️ P0 — 每次修改完成后必须 rsync 上传并重启服务

**这是最高优先级规范,每轮修改完成后必须执行,不得跳过:**

```bash
# Step 1:rsync 同步改动文件(远程服务器无 git 仓库,不能 git pull)
rsync -avz --exclude='__pycache__' --exclude='*.pyc' \
  pages/xxx.py jd:~/stock_analysis/pages/
# 多目录改动时同步整个项目:
rsync -avz --exclude='__pycache__' --exclude='*.pyc' \
  --exclude='.git' --exclude='.claude/' --exclude='data/' --exclude='logs/' \
  ./ jd:~/stock_analysis/

# Step 2:重启 Streamlit 服务使改动生效
ssh jd "cd ~/stock_analysis && ./stop.sh && sleep 1 && STREAMLIT_PORT=8051 ./start.sh"
```

**关键信息(已验证):**
- 服务器别名:`ssh jd`(京东云 VPS)
- 服务器项目路径:`~/stock_analysis`
- Python:`/root/miniconda3/bin/python`(系统 python3 无 streamlit)
- 启动脚本:`./start.sh`(已支持 miniconda 路径)
- 停止脚本:`./stop.sh`
- 线上访问地址:**http://117.72.184.8:8051**

**为什么是 P0:** 项目运行在远程服务器上,本地修改不会自动同步。不部署 = 用户永远看到旧版本。

---

## 项目说明

A 股龙虎榜量化交易分析系统,基于 Streamlit + PostgreSQL 构建。
详细业务规范见 `skill/总控 Prompt.md`,系统架构见 `AGENTS.md`。

---

## 核心架构

### 模块层次结构

```
┌─────────────────────────────────────────────────────────────┐
│  pages/              Streamlit UI 层(仅展示,无业务逻辑)      │
└────────────────────────┬────────────────────────────────────┘
                         │ 调用
┌────────────────────────▼────────────────────────────────────┐
│  modules/            核心算法引擎                            │
│  ├── scorer.py       统一评分引擎(技术/资金/历史胜率)        │
│  ├── market_data.py  统一行情层(K线/T+N日历/T+2价格)         │
│  ├── analyzer.py     回测框架(委托 scorer + market_data)    │
│  ├── data_fetcher.py 龙虎榜/涨跌停/资金流数据拉取            │
│  └── realtime_fetcher.py 实时行情(三级降级链)               │
└────────────────────────┬────────────────────────────────────┘
                         │ 使用
┌────────────────────────▼────────────────────────────────────┐
│  utils/storage.py    存储抽象层(PostgreSQL/Excel 自动切换)  │
└─────────────────────────────────────────────────────────────┘
```

**关键设计原则:**
- **单一权威实现:** 评分逻辑只在 `scorer.py`,行情获取只在 `market_data.py`
- **UI 层零业务逻辑:** `pages/` 只负责展示,所有计算委托给 `modules/`
- **存储模式透明切换:** 通过 `STORAGE_MODE` 环境变量在 PostgreSQL/Excel 间切换

### 回测引擎架构

```python
# modules/backtest/engine.py
BacktestEngine
  ├── DataFeed          # K线数据源
  ├── Executor          # 订单执行器(含成本扣除)
  └── Strategy          # 策略接口
      ├── scan_signals()   # 扫描入场信号
      ├── Entry            # 入场条件组合
      ├── Exit             # 出场条件组合
      └── Filter           # 过滤器链
```

**策略组合模式:**
- Entry/Exit/Filter 可独立组合,支持 AND/OR 逻辑
- 示例:`LianbanStrategy` = `LianbanEntry` + `CompositeExit(StopLoss + TakeProfit)`

### 数据流

```
1. 数据采集
   AKShare API → scripts/daily_update.py (11步)
   ├── Step 1-2: 龙虎榜/涨跌停
   ├── Step 3: T+1 价格补全
   ├── Step 8: 量比计算
   ├── Step 9: 平仓后跟踪
   ├── Step 10: 候选股收益
   └── Step 11: N日收益刷新

2. 存储层
   utils/storage.py → PostgreSQL/Excel
   ├── lhb_top10        # 龙虎榜主表
   ├── kline_daily      # 日K线(含复权列)
   ├── stock_info       # 股票基础信息
   └── sim_trades       # 模拟交易记录

3. 回测/评分
   modules/scorer.py
   ├── score_tech()     # 技术面(40分,含连板加分)
   ├── score_fund()     # 资金面(35分)
   └── score_history()  # 历史胜率(25分)

4. 前端展示
   pages/*.py → Streamlit UI
```

---

## 关键数据表

### lhb_top10 (龙虎榜主表)
```sql
CREATE TABLE lhb_top10 (
    代码 VARCHAR(10),
    名称 VARCHAR(50),
    上榜日 DATE,
    收盘价 NUMERIC(10,2),
    涨跌幅 NUMERIC(10,2),
    龙虎榜净买额 NUMERIC(15,2),  -- 单位:元
    龙虎榜买入额 NUMERIC(15,2),
    龙虎榜卖出额 NUMERIC(15,2),
    流通市值 NUMERIC(15,2),      -- ⚠️ 单位:万元(不是元!)
    t1_price NUMERIC(10,2),      -- T+1 开盘价
    t2_price NUMERIC(10,2),      -- T+2 收盘价
    t5_price NUMERIC(10,2),      -- T+5 收盘价
    PRIMARY KEY (代码, 上榜日)
);
```

**已知问题:**
- `流通市值` 单位是**万元**,计算时需 `×10000`
- `stock_info.exchange` 大面积为空(6297/5507),枚举全 A 股用 `ts_code LIKE '%.SH/.SZ/.BJ'`

### kline_daily (日K线)
```sql
CREATE TABLE kline_daily (
    ts_code VARCHAR(10),
    trade_date DATE,
    open NUMERIC(10,2),
    close NUMERIC(10,2),
    high NUMERIC(10,2),
    low NUMERIC(10,2),
    vol NUMERIC(20,2),
    adj_factor NUMERIC(10,6),    -- 复权因子
    close_qfq NUMERIC(10,2),     -- 前复权收盘价
    close_hfq NUMERIC(10,2),     -- 后复权收盘价
    PRIMARY KEY (ts_code, trade_date)
);
```

**数据更新:**
- 每日 19:00 cron 触发 `daily_update.py` 增量推送
- 历史数据用 `scripts/backfill_v4.py` 全量补全

### sim_trades (模拟交易)
```sql
CREATE TABLE sim_trades (
    id SERIAL PRIMARY KEY,
    code VARCHAR(10),
    signal_date DATE,           -- T 日(龙虎榜上榜日)
    buy_date DATE,              -- T+1 日(实际买入日)
    buy_price NUMERIC(10,2),    -- T+1 开盘价
    sell_date DATE,
    sell_price NUMERIC(10,2),
    profit_pct NUMERIC(10,2),
    reason VARCHAR(50),         -- 平仓原因(止盈/止损/到期)
    lianban_count INT,          -- 连板高度
    tech_score NUMERIC(10,2),   -- 技术面评分
    fund_score NUMERIC(10,2),   -- 资金面评分
    history_score NUMERIC(10,2) -- 历史胜率评分
);
```

---

## 开发工作流

### 本地开发
```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 配置 .env
STORAGE_MODE=excel  # 本地用 Excel 模式

# 3. 启动 Streamlit
streamlit run app.py --server.port 8051
```

### 数据库迁移
```bash
# ⚠️ db/init/*.sql 不会自动执行,需手动 apply
ssh jd
docker exec -it videofetcher-db psql -U stock_user -d stock_analysis
\i /path/to/new_migration.sql
```

### 添加新页面
```python
# 1. 创建 pages/new_page.py
def render():
    st.title("新页面")
    # 业务逻辑调用 modules/,不要在这里写算法

# 2. 在 app.py 注册
from pages import new_page
_new_page = st.Page(new_page.render, title="新页面", icon="🆕")
_pages.append(_new_page)
```

### 添加新策略
```python
# 1. 继承 BaseStrategy
from modules.strategies.base import BaseStrategy, Signal

class MyStrategy(BaseStrategy):
    def scan_signals(self, ctx) -> list[Signal]:
        # 扫描入场信号
        return [Signal(date=..., code=...)]

# 2. 在回测页面使用
from modules.backtest.engine import BacktestEngine
engine = BacktestEngine()
results, diag = engine.run(code, start, end, MyStrategy())
```

---

## 常见陷阱

### 1. 全角标点 Edit 陷阱
项目中文文档用全角标点(`。，()；`),`Edit` 工具搜索半角会失败。

**解决:**
```bash
# 先确认实际编码
python3 -c "print(repr(open('file.md').read()[:200]))"
# 或直接用 Python 脚本替换
```

### 2. Linear MCP 多 state+label 组合 bug
`state='A,B,C'` + `label='X'` 返回假空,必须分 state 查询。

### 3. Plotly 旧版兼容性
服务器 Plotly 版本较旧,不支持:
- `add_shape(yref="paper")` → 用 `Scatter` trace 代替
- `add_vline()` / `add_hline()` → 用 `shapes` 参数

### 4. 缓存函数名重构陷阱
`sim_trading.py` 中用 `st.session_state.pop(key)` 清缓存,不要用函数名 `.clear()`。

---

## 🧠 Plan 拷问与决策沉淀(grill-with-docs)

本项目已注入 `grill-with-docs` skill(在 Hermes / Claude Code 通用 skill 库里)。

**何时触发:**
- 用户写完 plan、提出新设计、问"这个方案靠谱吗 / 帮我 review 思路"
- 多个概念在对话里反复打架(命名/边界不清)
- 要做架构改动前,先确认共识

**4 个动作**(一次只一题,每题给推荐答案,等用户回复):
1. 挑战术语冲突(对照本项目 CONTEXT.md / 既有命名)
2. 锐化模糊语言(提议 canonical term)
3. 具体场景压测(造边界场景探概念)
4. 代码 vs 主张交叉验证(看代码是否真支持你说的)

**沉淀位置**(懒创建,写在本 git repo 根目录):
- `CONTEXT.md` — 领域术语词典(一句话定义、_Avoid_ 同义词、Relationships、Flagged ambiguities)
- `docs/adr/0001-slug.md` — 架构决策记录,1-3 句话即可

**ADR 三必要条件**(缺一不写):难逆转 + 无上下文会困惑 + 真存在 trade-off。Hermes 加一条:用户对同类决策反悔过 ≥ 1 次的话,强烈建议落 ADR。

**不要**:把通用编程概念(timeout/retry/cache)塞进 CONTEXT.md;给可逆/显然决策建 ADR。

详细规范见 `~/.hermes/skills/software-development/grill-with-docs/SKILL.md`。
