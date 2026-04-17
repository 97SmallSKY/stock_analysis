# W2 Data Ingestion — 进度与下一步

> 更新日期: 2026-04-18
> 状态: **Step A 完成 / Step B 部分完成 / 发现 1 个架构级阻塞**
> 上游计划: [docs/NEXT_STEPS.md](./NEXT_STEPS.md)

---

## 一、本次产出

### 1.1 代码提交

| Commit | 说明 |
|---|---|
| `d72271a` | test(ingest): 补 base/writer 单测; 修复 df_to_rows 对 NaN 的处理 |
| `e3d97d5` | fix(akshare): 补齐 get_trade_calendar(此前继承基类返回 None) |

### 1.2 测试

| 用例文件 | 数量 | 覆盖 |
|---|---|---|
| `tests/test_ingest_base.py` | 22 | normalize_date / df_to_rows / chunked / _normalize_pct / stock_info.run / calendar.run(全 mock) |
| `tests/test_db_writer.py` | 12 | _bulk_upsert(SQL/批次/DO NOTHING)+ 5 张表封装 SQL 构造 |
| **合计** | **34** | 本地 81/81 ✅ jd 81/81 ✅ |

### 1.3 Bug 修复

| 位置 | 问题 | 修复 |
|---|---|---|
| `backend/ingest/base.py::df_to_rows` | float 列的 `None/NaN` 经过 `where(notnull, None)` 仍保留为 NaN,会导致 PG DECIMAL 字段写入异常 | 先 `astype(object)` 再 where,NaN 真正落成 Python None |
| `backend/data_gateway/adapters/akshare_adapter.py` | 未实现 `get_trade_calendar`,继承基类默认 `return None` | 用 `ak.tool_trade_date_hist_sina()` 拿历史交易日,按区间扩展成 `exchange/cal_date/is_open/pretrade_date` 四字段 |

### 1.4 环境搭建(jd 服务器)

- jd 系统 `python3 = 3.6.8`(太老),但 pyenv 已装 **3.10.13** 并设为默认版本
- `ssh jd` 非交互 shell 不加载 `~/.bashrc`,需用绝对路径 `~/.pyenv/versions/3.10.13/bin/python`
- 在 `~/stock_analysis/.venv` 新建虚拟环境,已安装 `requirements.txt` 全部依赖
- jd `.env` 缺 `TUSHARE_TOKEN`,已补齐(未入仓)

### 1.5 首批数据入库

| 表 | 数据源 | 行数 | 说明 |
|---|---|---|---|
| `stock_info` | AKShare `stock_info_a_code_name` | **5506** | 仅 `ts_code/symbol/name` 有值,行业/市场/上市日期缺失 |
| `trade_calendar` | AKShare `tool_trade_date_hist_sina` | **2557**(含 1697 交易日) | 2020-01-01 ~ 2026-12-31,五一/国庆标记正确 |
| `kline_daily` | Sina(降级) | **22**(000001.SZ 单只 2024-01) | 降级链路验证成功 |

---

## 二、核心发现:P0-1 架构阻塞

### 现象

在 jd 上调用 **东方财富** 任何 API(`push2his.eastmoney.com/api/qt/stock/kline/get` 等):

```
ConnectionError: ('Connection aborted.',
    RemoteDisconnected('Remote end closed connection without response'))
```

### 排查过程

| 试验 | 结果 |
|---|---|
| 清除代理环境变量 | 仍失败 |
| 加浏览器 User-Agent | 仍失败 |
| 加 Referer / Host / 全套 Chrome headers | 仍失败 |
| 使用 `curl_cffi` 模拟 Chrome120 TLS 指纹 | 仍失败(curl 56) |
| 测同主机其他路径 `http://www.eastmoney.com` | **200 OK** |
| 测同主机 `http://quote.eastmoney.com` | **200 OK** |
| 测 `https://push2his.eastmoney.com`(无 path) | 404(至少连上) |

### 根因判定

**京东云公网 IP 被东方财富在 API 层面加入黑名单**,TCP 层直接 RST,不是 UA / TLS 指纹 / 频率限制问题。
代理不可用(jd 配的是 SG 出海代理,反而会把国内站劫持掉)。

### 可用替代源

AKShare 的**腾讯股票接口可用**:

```python
ak.stock_zh_a_hist_tx(symbol="sz000001", start_date="20240102", end_date="20240108", adjust="")
```

返回字段: `date / open / close / high / low / amount`
**缺的字段**: `volume / pre_close / pct_change / turnover_rate / pe_ttm / pb / total_mv / circ_mv`

缺的里面:`pct_change` 可从 `close / prev_close` 计算出来;`volume` 可从 amount 反推不出,需要另找源;换手率/PE/PB 这些需要 TuShare 或其它接口补。

---

## 三、已知 Issue 清单

| 优先级 | # | 问题 | 影响 | 处理建议 |
|---|---|---|---|---|
| 🔴 P0 | 1 | **京东云 IP 被东财 API 黑名单** | AKShare 日 K / 龙虎榜等核心品类都挂 | 改 `akshare_adapter.get_daily_kline` 使用 `stock_zh_a_hist_tx`(腾讯)作主源,东财作降级;龙虎榜同理评估 |
| 🔴 P0 | 2 | **TuShare 120 积分 adapter initialize 被拒绝** | K 线不能走主源 | 改 `tushare_adapter.initialize()`:要么不做 ping,要么 ping 一个 120 积分能用的接口 |
| 🟡 P1 | 3 | `stock_info` 行业/市场/上市日期缺失 | 按行业选股不能做 | 换 `ak.stock_info_sh_name_code` + `ak.stock_info_sz_name_code`(带行业) |
| 🟡 P1 | 4 | Sina 日 K 缺 `vol/amount/turnover_rate/pe_ttm` | 回测/评分数据不全 | P0-1 解决后自然好 |
| 🟢 P2 | 5 | 尚未跑 `ingest-lhb` / `ingest-sentiment` / 全市场 K 线 | W2 未端到端 | P0 解决后按序跑 |

---

## 四、下一步执行计划

按优先级 **P0 → P1 → P2**:

### P0-1 改 akshare_adapter 使用腾讯日 K 主源
- 修改 `get_daily_kline`:优先调 `stock_zh_a_hist_tx`,失败回退到东财 `stock_zh_a_hist`
- 补 `pct_change` 计算:按日 `close / prev_close - 1`
- 补 `pre_close` 字段(等于上一行 close)
- 加对应单测(mock 腾讯接口返回)

### P0-2 修 TuShare initialize ping
- 读 `tushare_adapter.py::initialize()`,看目前 ping 哪个接口
- 改用 `pro.daily(ts_code='000001.SZ', start_date='20240102', end_date='20240102', limit=1)` 之类 120 积分可用的接口
- 或:直接去掉 ping,initialize 只 set_token,首次调用失败再标记不可用

### P1-3 stock_info 补字段
- 改 `akshare_adapter.get_stock_info` 为:
  ```python
  sh = ak.stock_info_sh_name_code()
  sz = ak.stock_info_sz_name_code()
  # 合并,补 market/list_date,行业先留空(两个接口也没行业)
  ```
- 行业字段可能需另外调 `ak.stock_board_industry_cons_em`,本期可先放一个 TODO

### P2-4 跑 ingest-lhb + 扩大 kline
- P0 搞定后:
  - `make ingest-lhb FROM=20240101`(先 1 年)
  - `make ingest-kline FROM=20240101 TO=<today>`(2024 年全市场,验证稳定性)
  - 再决定是否进入 `FROM=20200101` 的全量(tmux 里跑)

---

## 五、操作手册补充(踩坑记录)

### 在 jd 上跑 Python 任务

```bash
ssh jd
cd /root/stock_analysis

# 非交互 shell 里 python3 = 3.6,不要直接用
# 用 .venv(之前已建好)
.venv/bin/python -m backend.cli ingest stock-info

# 或 make(Makefile 里走 python,需在 venv 激活的交互 shell 里)
source .venv/bin/activate && make ingest-stock-info
```

### jd `.env` 必需字段

```
TUSHARE_TOKEN=<填真实 token>  # ⚠️ 不要提交到 git
TUSHARE_POINTS=120
POSTGRES_USER=lhb_admin
POSTGRES_PASSWORD=lhb_quant_2024
POSTGRES_DB=lhb_quant
POSTGRES_PORT=5432
# PG_HOST 留空 → 默认 localhost,本机访问更快
```

### DB 查验

```bash
# 用 127.0.0.1 避免 pg_hba md5/peer 差异
ssh jd "PGPASSWORD=lhb_quant_2024 psql -h 127.0.0.1 -U lhb_admin -d lhb_quant -c '\\dt'"
```
