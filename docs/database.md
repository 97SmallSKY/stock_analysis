# 数据库配置与接口说明

本文档记录 `stock_analysis` 项目的 PostgreSQL 数据库完整信息，包括：
拓扑结构、账号配置、表结构定义、存储层 API 说明及运维命令。

---

## 一、数据库拓扑

```
videofetcher-db（Docker 容器，本地/VPS 共用宿主机）
├── 数据库: videofetcher      ← videofetcher 服务专用（不动）
└── 数据库: stock_analysis    ← 本项目专属独立库 ✅
    └── 用户: stock_user
```

`stock_analysis` 数据库与 `videofetcher` **完全隔离**，互不影响。  
无表名前缀（`PG_TABLE_PREFIX=` 为空），表名即逻辑名。

---

## 二、账号与连接信息

### VPS 生产环境（`ssh jd` / 京东云）

| 参数 | 值 |
|------|-----|
| 容器名 | `videofetcher-db` |
| Host（VPS内网） | `127.0.0.1` |
| Port | `5432` |
| **数据库** | **`stock_analysis`** |
| **用户名** | **`stock_user`** |
| **密码** | 见 `.env` 文件 |

```bash
# 通过 Docker 容器直接连接
docker exec -it videofetcher-db psql -U stock_user -d stock_analysis

# 查看所有表
\dt

# 退出
\q
```

### 本地开发环境

| 参数 | 值 |
|------|-----|
| Host | `localhost` |
| Port | `5432` |
| 数据库 | `stock_analysis` |
| 用户名 | `stock_user` |

```bash
psql -h localhost -p 5432 -U stock_user -d stock_analysis
```

---

## 三、.env 配置

### 生产环境（VPS）

```env
STORAGE_MODE=postgres
PG_HOST=127.0.0.1
PG_PORT=5432
PG_DATABASE=stock_analysis
PG_USER=stock_user
PG_PASSWORD=<见.env文件>
PG_TABLE_PREFIX=
```

### 本地开发

```env
STORAGE_MODE=postgres
PG_HOST=localhost
PG_PORT=5432
PG_DATABASE=stock_analysis
PG_USER=stock_user
PG_PASSWORD=<见.env文件>
PG_TABLE_PREFIX=
```

### 纯离线（不依赖数据库）

```env
STORAGE_MODE=excel
```

**无需修改任何 Python 代码**，`utils/storage.py` 自动适配三种模式。

---

## 四、数据表完整说明

> 所有表在首次写入时由 SQLAlchemy `to_sql` **自动建表**，无需手动 CREATE TABLE。

---

### 4.1 `lhb_top10` — 龙虎榜净买入 Top10

**用途**：每日龙虎榜按净买入额排名的前50只股票（含前10重点），  
是策略分析、主板精选策略的基础数据源。

**数据规模**：5,760 行 / 116 个交易日（20250925 ~ 20260326）

| 列名 | 类型 | 说明 |
|------|------|------|
| `序号` | BIGINT | 当日龙虎榜原始排名 |
| `代码` | TEXT | 股票代码（6位，含0补位） |
| `名称` | TEXT | 股票名称 |
| `上榜日` | DATE | 上榜日期 |
| `解读` | TEXT | AKShare 接口提供的上榜解读文字 |
| `收盘价` | DOUBLE | 上榜当日收盘价（元） |
| `涨跌幅` | DOUBLE | 上榜当日涨跌幅（%） |
| `龙虎榜净买额` | DOUBLE | 龙虎榜机构/营业部净买入金额（元）**核心字段** |
| `龙虎榜买入额` | DOUBLE | 买入总额（元） |
| `龙虎榜卖出额` | DOUBLE | 卖出总额（元） |
| `龙虎榜成交额` | DOUBLE | 龙虎榜席位成交总额（元） |
| `市场总成交额` | DOUBLE | 当日全市场成交额（元） |
| `净买额占总成交比` | DOUBLE | 龙虎榜净买额 / 市场总成交额（%） |
| `成交额占总成交比` | DOUBLE | 龙虎榜成交额 / 市场总成交额（%） |
| `换手率` | DOUBLE | 当日换手率（%）**作为量比替代指标** |
| `流通市值` | DOUBLE | 流通市值（元）**市值筛选依据** |
| `上榜原因` | TEXT | 上榜原因标签（如"日涨幅偏离值达到7%"） |
| `上榜后1日` | DOUBLE | 上榜后1个交易日涨跌幅（%，AKShare 自带，以收盘价为基准） |
| `上榜后2日` | DOUBLE | 上榜后2个交易日涨跌幅（%） |
| `上榜后5日` | DOUBLE | 上榜后5个交易日涨跌幅（%） |
| `上榜后10日` | DOUBLE | 上榜后10个交易日涨跌幅（%） |
| `排名` | BIGINT | 净买额降序排名（1=当日净买入最多） |
| `trade_date` | TEXT | 交易日期（`YYYYMMDD` 字符串，**JOIN 键**） |

**查询示例**：

```python
from utils.storage import storage
import pandas as pd

# 读取全部
df = storage.read("lhb_top10")

# 按日期过滤
daily = storage.query_by_date("lhb_top10", "20260326")

# 每日净买入 Top10 主板股
df["龙虎榜净买额"] = pd.to_numeric(df["龙虎榜净买额"], errors="coerce")
top10 = (df.sort_values("龙虎榜净买额", ascending=False)
          .groupby("trade_date", group_keys=False)
          .apply(lambda g: g.head(10)))
```

---

### 4.2 `lhb_top10_prices` — T+N 价格与收益

**用途**：记录 `lhb_top10` 中每只股票在 T+1/T+2/T+5/T+10 交易日的价格及收益率，  
是主板精选策略、盈亏分析的回测核心数据。

**数据规模**：507 行（各交易日 Top10 股票，沪深主板/创业板/科创板）  
**注**：北交所（8xxxxx/4xxxxx）因 Sina API 不支持，价格数据为空。

| 列名 | 类型 | 说明 |
|------|------|------|
| `代码` | TEXT | 股票代码 |
| `trade_date` | TEXT | 上榜日期（`YYYYMMDD`，JOIN 键） |
| `T+1买入价` | DOUBLE | T+1 开盘集合竞价价格（元）**策略买入价** |
| `T+1最高价` | DOUBLE | T+1 日内最高价（元） |
| `T+1最低价` | DOUBLE | T+1 日内最低价（元） |
| `T+1收盘价` | DOUBLE | T+1 收盘价（元） |
| `T+1收盘收益%` | DOUBLE | (T+1收盘 / T+1买入 - 1) × 100 |
| `T+1最高收益%` | DOUBLE | (T+1最高 / T+1买入 - 1) × 100 |
| `T+2最高价` | DOUBLE | T+2 日内最高价（元）**策略卖点上限** |
| `T+2最低价` | DOUBLE | T+2 日内最低价（元） |
| `T+2收盘价` | DOUBLE | T+2 收盘价（元） |
| `T+2收盘收益%` | DOUBLE | (T+2收盘 / T+1买入 - 1) × 100 |
| `T+2最高收益%` | DOUBLE | (T+2最高 / T+1买入 - 1) × 100 **冲高收益** |
| `T+5最高价` | DOUBLE | T+5 日内最高价（元） |
| `T+5最低价` | DOUBLE | T+5 日内最低价（元） |
| `T+5收盘价` | DOUBLE | T+5 收盘价（元） |
| `T+5收盘收益%` | DOUBLE | T+5 持有收益率 |
| `T+5最高收益%` | DOUBLE | T+5 最优卖点收益率 |
| `T+10最高价` | DOUBLE | T+10 日内最高价（元） |
| `T+10最低价` | DOUBLE | T+10 日内最低价（元） |
| `T+10收盘价` | DOUBLE | T+10 收盘价（元） |
| `T+10收盘收益%` | DOUBLE | T+10 持有收益率 |
| `T+10最高收益%` | DOUBLE | T+10 最优卖点收益率 |

**价格数据来源**：`ak.stock_zh_a_daily(symbol, adjust="qfq")` 新浪财经前复权日K

**查询示例**：

```python
# 获取某日的价格数据
prices = storage.query_by_date("lhb_top10_prices", "20260302")

# 合并 Top10 与价格，计算冲高收益
merged = top10_daily.merge(prices, on=["代码", "trade_date"], how="left")
merged["冲高收益%"] = ((merged["T+2最高价"] / merged["T+1买入价"]) - 1) * 100
```

---

### 4.3 `lhb_detail` — 龙虎榜明细

**用途**：龙虎榜每日上榜股票的简要明细，含成交量、成交额、上榜原因。  
供每日选股、策略回测使用。

**数据规模**：8,901 行（20250925 ~ 20260326）

| 列名 | 类型 | 说明 |
|------|------|------|
| `代码` | TEXT | 股票代码 |
| `名称` | TEXT | 股票名称 |
| `收盘价` | DOUBLE | 上榜日收盘价（元） |
| `偏离值` | DOUBLE | 价格偏离值（%） |
| `成交量(万股)` | DOUBLE | 当日成交量（万股） |
| `成交额(万元)` | DOUBLE | 当日成交额（万元） |
| `上榜原因` | TEXT | 原因标签，多条以 `/` 分隔 |
| `trade_date` | TEXT | 交易日期（`YYYYMMDD`） |

---

### 4.4 `market_sentiment` — 市场情绪

**用途**：记录每日大盘情绪指标，用于情绪趋势分析和策略信号过滤。

**数据规模**：1 行（仅最新当日，历史数据需每日运行 `daily_update.py` 积累）

| 列名 | 类型 | 说明 |
|------|------|------|
| `trade_date` | TEXT | 交易日期（`YYYYMMDD`） |
| `limit_up` | BIGINT | 涨停家数 |
| `limit_down` | BIGINT | 跌停家数 |
| `up_down_ratio` | DOUBLE | 涨停/跌停比值（>3 偏多，<1 偏空） |
| `continuous_limit` | BIGINT | 连续涨停家数 |
| `index_close` | DOUBLE | 上证指数收盘点位 |
| `index_ma5` | DOUBLE | 上证指数5日均线 |
| `index_ma20` | DOUBLE | 上证指数20日均线 |
| `index_trend` | TEXT | 趋势判断（`up`/`down`/`sideways`） |
| `index_change` | DOUBLE | 指数当日涨跌幅（%） |
| `trade_signal` | TEXT | 交易信号（`buy`/`sell`/`hold`） |
| `signal_reason` | TEXT | 信号原因文字描述 |

---

### 4.5 `lhb_jigou` — 机构席位汇总

**用途**：龙虎榜机构席位近5/10日买卖行为汇总，用于识别机构持续关注的标的。

**数据规模**：341 行

| 列名 | 类型 | 说明 |
|------|------|------|
| `股票代码` | TEXT | 股票代码 |
| `股票名称` | TEXT | 股票名称 |
| `上榜次数` | BIGINT | 近期上榜次数 |
| `累积购买额` | DOUBLE | 机构累积买入金额（元） |
| `累积卖出额` | DOUBLE | 机构累积卖出金额（元） |
| `净额` | DOUBLE | 净买入金额（元） |
| `fetch_date` | TEXT | 数据拉取日期（`YYYYMMDD`） |

---

### 4.6 `lhb_ggtj` — 个股上榜统计

**用途**：个股在龙虎榜上的历史上榜统计，含近5日/10日净买入汇总。

**数据规模**：1,153 行

| 列名 | 类型 | 说明 |
|------|------|------|
| `股票代码` | TEXT | 股票代码 |
| `股票名称` | TEXT | 股票名称 |
| `上榜次数` | BIGINT | 统计周期内上榜总次数 |
| `累积购买额` | DOUBLE | 龙虎榜累积买入额（元） |
| `累积卖出额` | DOUBLE | 龙虎榜累积卖出额（元） |
| `净额` | DOUBLE | 净买入额（元） |
| `统计周期` | TEXT | 统计周期标识（如 `5日`/`10日`） |
| `fetch_date` | TEXT | 数据拉取日期（`YYYYMMDD`） |

---

### 4.7 `lhb_seat_detail` — 当日席位明细

**用途**：龙虎榜当日具体买卖方营业部及金额明细，用于识别知名游资席位（赵老哥、拉萨天团等）。

**数据规模**：77 行

| 列名 | 类型 | 说明 |
|------|------|------|
| `代码` | TEXT | 股票代码 |
| `名称` | TEXT | 股票名称 |
| `收盘价` | DOUBLE | 当日收盘价 |
| `涨跌幅` | DOUBLE | 当日涨跌幅（%） |
| `龙虎榜净买额` | DOUBLE | 净买额（元） |
| `买方席位1`~`买方席位5` | TEXT | 买方营业部名称 |
| `买方金额1`~`买方金额5` | DOUBLE | 对应买入金额（元） |
| `卖方席位1`~`卖方席位5` | TEXT | 卖方营业部名称 |
| `卖方金额1`~`卖方金额5` | DOUBLE | 对应卖出金额（元） |
| `trade_date` | TEXT | 交易日期（`YYYYMMDD`） |

---

### 4.8 `sim_trades` — 模拟交易记录

**用途**：AI 模拟炒股机器人每次交易的完整记录，支持分批卖出（最多3档）和止损。

**数据规模**：1 行（随每日交易持续增长）

| 列名 | 类型 | 说明 |
|------|------|------|
| `id` | INTEGER NOT NULL | 主键，自增 |
| `trade_date` | VARCHAR(8) NOT NULL | 上榜日（选股日）`YYYYMMDD` |
| `code` | VARCHAR(6) NOT NULL | 股票代码 |
| `name` | VARCHAR(20) | 股票名称 |
| `signal_score` | DOUBLE | 综合评分（0~100，来自决策辅助信号4维度） |
| `t2_win_rate` | DOUBLE | 历史 T+2 冲高胜率（0~1） |
| `seat_tags` | TEXT | 命中的知名游资席位标签（JSON 字符串） |
| `buy_price` | DOUBLE | 实际买入价（T+1 开盘集合竞价） |
| `buy_amount` | DOUBLE | 买入金额（元） |
| `buy_shares` | INTEGER | 买入股数 |
| `list_price` | DOUBLE | 上榜日收盘价 |
| `list_reason` | TEXT | 上榜原因 |
| `sell_price` | DOUBLE | 最终卖出均价 |
| `sell_date` | VARCHAR(8) | 卖出日期 |
| `sell_type` | VARCHAR(16) | 卖出类型（`target`/`stop_loss`/`force`） |
| `profit` | DOUBLE | 盈亏金额（元） |
| `profit_pct` | DOUBLE | 盈亏百分比（%） |
| `status` | VARCHAR(10) | 持仓状态（`open`/`closed`） |
| `week_tag` | VARCHAR(20) | 所属周标签（`2026W13`） |
| `llm_reason` | TEXT | LLM 选股理由（自然语言） |
| `reason_tech` | TEXT | 技术面选股理由 |
| `reason_fund` | TEXT | 资金面选股理由 |
| `reason_history` | TEXT | 历史胜率选股理由 |
| `reason_seat` | TEXT | 席位选股理由 |
| `batch_mode` | BOOLEAN | 是否启用分批卖出模式 |
| `sell1_pct`/`sell1_price`/`sell1_shares`/`sell1_date`/`sell1_done` | - | 第1档卖出目标（+3%）及执行记录 |
| `sell2_pct`/`sell2_price`/`sell2_shares`/`sell2_date`/`sell2_done` | - | 第2档卖出目标（+5%）及执行记录 |
| `sell3_pct`/`sell3_price`/`sell3_shares`/`sell3_date`/`sell3_done` | - | 第3档卖出目标（+8%）及执行记录 |
| `stop_loss_pct` | DOUBLE | 止损阈值（默认 -5%） |
| `stop_loss_done` | BOOLEAN | 是否已触发止损 |
| `created_at` | TIMESTAMP | 记录创建时间 |
| `updated_at` | TIMESTAMP | 记录最后更新时间 |
| `buy_source` | VARCHAR(32) | 买入来源（`phase_b`/`manual`） |

---

### 4.9 `sim_summary` — 模拟交易周汇总

**用途**：每周交易结果汇总，用于统计周度胜率、总盈亏等指标。

| 列名 | 类型 | 说明 |
|------|------|------|
| `week_tag` | VARCHAR(20) NOT NULL | 周标签（主键，`2026W13`） |
| `total_trades` | INTEGER | 本周总交易笔数 |
| `win_trades` | INTEGER | 盈利笔数 |
| `lose_trades` | INTEGER | 亏损笔数 |
| `win_rate` | DOUBLE | 胜率（0~1） |
| `total_profit` | DOUBLE | 本周总盈亏（元） |
| `avg_profit_pct` | DOUBLE | 平均盈亏百分比（%） |
| `max_profit` | DOUBLE | 单笔最大盈利（元） |
| `max_loss` | DOUBLE | 单笔最大亏损（元） |
| `updated_at` | TIMESTAMP | 最后更新时间 |

---

## 五、存储层 API（`utils/storage.py`）

`storage` 是全局单例，自动根据 `STORAGE_MODE` 环境变量选择 Excel 或 PostgreSQL 后端。

```python
from utils.storage import storage
```

### 5.1 `storage.read(table)` — 全量读取

```python
df = storage.read("lhb_top10")
# 返回: pd.DataFrame，表不存在返回空 DataFrame
```

- 自动检测表是否存在（不存在返回空 DataFrame，不报错）
- PostgreSQL 模式下等同于 `SELECT * FROM {table}`
- **注意**：`lhb_top10` 有 5760 行，全量读取约 10-50ms

---

### 5.2 `storage.write(df, table, mode)` — 写入数据

```python
# 覆盖整张表（默认）
storage.write(df, "lhb_top10", mode="replace")

# 追加新日期数据（按日期去重后合并再写入）
storage.write(df, "lhb_top10_prices", mode="append")
```

**`mode="append"` 的内部逻辑**（重要）：

1. 读取现有表数据
2. 自动探测日期列（`trade_date` / `上榜日` / `fetch_date` 等）
3. 从现有数据中删除与新数据日期重叠的行
4. 将新旧数据合并后**全量覆盖写回**（`if_exists="replace"`）

> ⚠️ **注意**：`mode="append"` 并非 SQL 的 `INSERT`，而是「按日期去重后覆盖」，
> 适合幂等的每日增量更新。如需真正追加（不去重），直接使用 SQLAlchemy：
> ```python
> df.to_sql("lhb_top10_prices", engine, if_exists="append", index=False)
> ```

---

### 5.3 `storage.query_by_date(table, trade_date)` — 按日期查询

```python
# 查询 20260302 这一天的价格数据
prices = storage.query_by_date("lhb_top10_prices", "20260302")
# 等同于: SELECT * FROM lhb_top10_prices WHERE trade_date = '20260302'
```

---

### 5.4 `storage.query(table, filters)` — 带条件查询

```python
# 单条件
df = storage.query("sim_trades", filters={"status": "open"})

# 多条件（AND 关系）
df = storage.query("lhb_detail", filters={
    "trade_date": "20260326",
    "代码": "600487"
})
```

> PostgreSQL 模式下使用 SQLAlchemy 参数化查询，防止 SQL 注入。

---

### 5.5 `storage.get_table_stats()` — 查看表统计

```python
stats = storage.get_table_stats()
# 返回 DataFrame: tablename | row_count
print(stats)
```

---

### 5.6 `storage._get_engine()` — 获取 SQLAlchemy Engine

```python
# 直接执行自定义 SQL（高级用法）
from sqlalchemy import text
engine = storage._get_engine()
with engine.connect() as conn:
    result = conn.execute(
        text("SELECT COUNT(*) FROM lhb_top10 WHERE trade_date = :d"),
        {"d": "20260326"}
    ).fetchone()
    print(result[0])
```

连接池配置：
- `pool_size=5`（常驻连接数）
- `max_overflow=10`（最大溢出连接）
- `pool_pre_ping=True`（每次使用前检测连接存活）
- `pool_timeout=30`（获取连接超时秒数）

---

## 六、初始化建库（首次部署）

SSH 到 VPS 后执行：

```bash
# 连接到 videofetcher-db 容器（以 postgres 超级用户操作）
docker exec -i videofetcher-db psql -U videofetcher_user -d videofetcher << 'SQL'

-- 新建独立数据库
CREATE DATABASE stock_analysis
    ENCODING = 'UTF8'
    LC_COLLATE = 'en_US.utf8'
    LC_CTYPE = 'en_US.utf8';

-- 新建专属用户
CREATE USER stock_user WITH PASSWORD 'your_password';
GRANT ALL PRIVILEGES ON DATABASE stock_analysis TO stock_user;
SQL

# 进入新库授权 public schema（PostgreSQL 15+ 必须）
docker exec -i videofetcher-db psql -U videofetcher_user -d stock_analysis << 'SQL'
GRANT ALL ON SCHEMA public TO stock_user;
ALTER DATABASE stock_analysis OWNER TO stock_user;
SQL
```

建库后，首次运行以下命令即可自动建表：

```bash
cd ~/stock_analysis
/root/miniconda3/bin/python scripts/daily_update.py
```

---

## 七、常用运维命令

```bash
# 查看所有表及行数
docker exec videofetcher-db psql -U stock_user -d stock_analysis \
  -c "SELECT tablename, n_live_tup AS rows FROM pg_stat_user_tables ORDER BY tablename;"

# 查看某表字段结构
docker exec videofetcher-db psql -U stock_user -d stock_analysis \
  -c "\d lhb_top10_prices"

# 检查价格数据覆盖情况
docker exec videofetcher-db psql -U stock_user -d stock_analysis \
  -c "SELECT trade_date, COUNT(*) FROM lhb_top10_prices GROUP BY trade_date ORDER BY trade_date DESC LIMIT 10;"

# 备份整个 stock_analysis 数据库
docker exec videofetcher-db pg_dump \
  -U stock_user -d stock_analysis \
  > backup_stock_analysis_$(date +%Y%m%d).sql

# 恢复数据库
psql -h localhost -p 5432 -U stock_user -d stock_analysis \
  < backup_stock_analysis_YYYYMMDD.sql

# Python 检查各表统计（推荐）
python -c "
import sys; sys.path.insert(0, '.')
from utils.storage import storage
print(storage.get_table_stats().to_string(index=False))
"

# 迁移本地 Excel 数据到 PostgreSQL
python utils/migrate.py

# 预览迁移（不写入）
python utils/migrate.py --dry-run
```

---

## 八、数据更新流程

### 每日自动更新（Cron）

```
18:40 daily_update.py
  ├── Step 1: 拉取今日龙虎榜 Top10      → lhb_top10 (append)
  ├── Step 2: 拉取今日市场情绪           → market_sentiment (replace)
  ├── Step 3: 补全到期的 T+N 价格 NULL   → lhb_top10_prices (append)
  ├── Step 4: 拉取龙虎榜明细             → lhb_detail (append)
  ├── Step 5: 拉取机构席位汇总           → lhb_jigou (append)
  ├── Step 6: 拉取个股上榜统计           → lhb_ggtj (append)
  └── Step 7: 拉取当日席位明细           → lhb_seat_detail (append)

09:05 daily_update.py（次日开盘前）
  └── Step 3 重跑: 补全 T+1 开盘价       → lhb_top10_prices (append)
```

### 手动补全历史数据

```bash
# 补全 lhb_top10 历史数据（指定天数）
python /tmp/backfill_history.py

# 补全 lhb_top10_prices 缺失的价格
python /tmp/fill_gaps2.py
```

---

## 九、存储模式对比

| 特性 | Excel 模式 | PostgreSQL 模式 |
|------|-----------|----------------|
| 适用场景 | 纯离线开发、快速原型 | 生产部署、多人协作 |
| 依赖 | openpyxl | PostgreSQL + SQLAlchemy |
| 性能 | 慢（百毫秒级） | 快（毫秒级，5000行<10ms） |
| 并发 | ❌ 不支持 | ✅ 连接池支持 |
| 数据持久化 | Excel 文件 | PostgreSQL WAL |
| 切换方式 | 改 `.env` `STORAGE_MODE` | 改 `.env` `STORAGE_MODE` |
| API 兼容性 | ✅ 完全相同 | ✅ 完全相同 |
