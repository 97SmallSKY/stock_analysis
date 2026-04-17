# W2 Data Ingestion — 进度与下一步

> 更新日期: 2026-04-18(补充 main 分支对比)
> 状态: **Step A 完成 / Step B 部分完成 / 发现 1 个架构级阻塞 / 已对比 main 分支实现**
> 上游计划: [docs/NEXT_STEPS.md](./NEXT_STEPS.md)
> main 对比本地路径: `/Users/xiaotian/Develop/personal/stock_analysis_main/stock_analysis`

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

在 jd 上调用 **东方财富** K 线 API(`push2his.eastmoney.com/api/qt/stock/kline/get`):

```
ConnectionError: ('Connection aborted.',
    RemoteDisconnected('Remote end closed connection without response'))
```

### 排查过程(阶段一:应用层)

| 试验 | 结果 |
|---|---|
| 清除代理环境变量 | 仍失败 |
| 加浏览器 User-Agent | 仍失败 |
| 加 Referer / Host / 全套 Chrome headers | 仍失败 |
| 使用 `curl_cffi` 模拟 Chrome120 TLS 指纹 | 仍失败(curl 56) |
| 测同主机其他路径 `http://www.eastmoney.com` | **200 OK** |
| 测同主机 `http://quote.eastmoney.com` | **200 OK** |
| 测 `https://push2his.eastmoney.com`(无 path) | 404(至少连上) |

### 对比 main 分支实现(关键线索)

> 本地已 clone `main` 分支到 `/Users/xiaotian/Develop/personal/stock_analysis_main/stock_analysis` 做对比。

main 分支的数据采集代码(`modules/data_fetcher.py` / `utils/retry.py`)清楚记录:

> 代理环境说明(京东云 VPS):
> - VPS 配置了全局代理:HTTP_PROXY/HTTPS_PROXY=http://127.0.0.1:10809(新加坡中转)
> - AKShare 内部通过 requests 发请求,**默认会走系统代理**
> - 测试表明:**通过代理访问新浪/东财行情接口均 < 0.5s**

**main 分支从未清代理**,启动脚本 `start.sh` / `scripts/start_stock.sh` 均无任何 `unset HTTP_PROXY` 操作。
换言之:当时 main 分支之所以能采集东财数据,**是因为 jd 的 xray 代理能把出站转到 SG 再回源东财**。

### 排查过程(阶段二:网络层)

| 试验(在 jd 上用 curl) | 结果 |
|---|---|
| `curl -x http://127.0.0.1:10809 https://push2his.eastmoney.com/...` | http=000 time=1.08s → **代理本身现在也出海失败** |
| `curl --noproxy * https://push2his.eastmoney.com/...` | `(52) Empty reply from server` → **jd IP 直连被东财 RST** |
| `curl --noproxy * https://proxy.finance.qq.com/...`(腾讯) | **http=200 time=128ms** ✅ |
| `curl --noproxy * -H 'Referer: https://finance.sina.com.cn' https://hq.sinajs.cn/list=sh600519` | **http=200 time=129ms** ✅ |
| `curl --noproxy * https://quotes.money.163.com/service/chddata.html?...`(网易) | 502 ❌ |
| `ps aux | grep xray` | xray 仍在运行,10809/10808 端口都在监听 |

### 根因最终判定

1. **京东云公网 IP 直连东财 API 会被 RST**(TCP 层黑名单)
2. **jd 的 xray 代理出口现在也不可达东财**(订阅/出海链路问题,不是我们项目能修的)
3. **main 分支之前能采集成功,是因为那时 xray 代理出海链路还通**
4. 我们当前分支在 `backend/core/startup.py` 主动清代理,**不是导致问题的原因**(即使不清,现在也挂);但也不应再强行设 `NO_PROXY=*`,失去代理逻辑的灵活性

### 可用替代源(实测)

| 源 | 接口 | 字段 | 可用性 |
|---|---|---|---|
| 腾讯财经 | `ak.stock_zh_a_hist_tx(sz000001, ...)` | date / open / close / high / low / amount | ✅ **稳定** |
| 新浪财经(日 K) | `ak.stock_zh_a_daily(sh000001, ...)` | AKShare 内部解析 bug (pandas Length mismatch) | ⚠️ 暂不可用 |
| 新浪行情 HTTP | `https://hq.sinajs.cn/list=sh600519` | 实时行情 9 字段 | ✅(main 分支已在用) |
| 东财 `push2his.eastmoney.com` | K 线 / 龙虎榜 | 完整 | ❌ 直连 RST, 代理出海也挂 |

**腾讯接口字段缺口**: `volume / pre_close / pct_change / turnover_rate / pe_ttm / pb / total_mv / circ_mv`
- `pct_change` 可由 `close / prev_close - 1` 计算
- `pre_close` 可取相邻行的 close 填补
- `volume`、换手率、PE/PB 需另找源;部分需 TuShare 升级到 2000 积分

---

## 三、已知 Issue 清单(更新)

| 优先级 | # | 问题 | 影响 | 处理建议 |
|---|---|---|---|---|
| 🔴 P0 | 1 | **东财 API 不可达**(直连黑名单 + 代理出海挂) | AKShare 日 K / 龙虎榜主接口失效 | (a)改 `akshare_adapter.get_daily_kline` 使用腾讯 `stock_zh_a_hist_tx` 作主源,东财作降级;(b)龙虎榜改用新浪源 `ak.stock_lhb_detail_daily_sina` / `ak.stock_lhb_jgmx_sina`(main 分支已在用,国内直连可达) |
| 🔴 P0 | 2 | **TuShare 120 积分 adapter initialize 被拒绝** | K 线不能走主源 | 改 `tushare_adapter.initialize()`:要么不做 ping,要么 ping `pro.daily(...)` 这类 120 积分可用接口 |
| 🟡 P1 | 3 | `stock_info` 行业/市场/上市日期缺失 | 按行业选股不能做 | 换 `ak.stock_info_sh_name_code` + `ak.stock_info_sz_name_code`(带行业);参考 main 分支 `modules/market_data.py` |
| 🟡 P1 | 4 | 腾讯日 K 缺 `volume/turnover_rate/pe_ttm/pb` | 回测/评分数据不全 | TuShare 升级后从 TuShare 补;或用 `ak.stock_individual_info_em` 取估值(需验证可达) |
| 🟡 P1 | 5 | `backend/core/startup.py` 强制 `NO_PROXY=*` | 丢失代理使用灵活性 | 改成**不主动清代理,不设 NO_PROXY**,由系统环境决定;对特定接口在 adapter 内部用 `trust_env=False` 做精细控制 |
| 🟢 P2 | 6 | 尚未跑 `ingest-lhb` / `ingest-sentiment` / 全市场 K 线 | W2 未端到端 | P0 解决后按序跑 |

## 三 (B)、main 分支关键经验(要借鉴)

基于 `/Users/xiaotian/Develop/personal/stock_analysis_main/stock_analysis` 的对比:

1. **`utils/retry.py::safe_akshare_call`** — 带指数退避重试 + 关键字白名单(含中文"系统繁忙"、"RemoteDisconnected"、"ConnectionError")+ fallback。当前 `backend/data_gateway/base_adapter.py::_safe_request` 有类似机制,但关键字覆盖不全,**建议合并 main 的 RETRY_KEYWORDS**。
2. **`utils/retry.py::fetch_sina_quote`** — 新浪实时行情的"代理感知双路保障":先走系统代理,失败再直连 `urllib.request.ProxyHandler({})`。当前 `sina_adapter.py` 未做双路,**可作为 W4+ 实时行情参考**。
3. **龙虎榜新浪优先** — main 分支 `modules/data_fetcher.py` 龙虎榜主源是 `ak.stock_lhb_detail_daily_sina`,东财 `stock_lhb_detail_em` 是次源。**应更新 `backend/data_gateway/router.py::DEFAULT_PRIORITY_CHAINS` 的 LHB_DETAIL 顺序**,让 Sina 优先。
4. **不清代理** — main 分支几年稳定运行,启动脚本里从未清过代理。佐证了"在 jd 让代理默认工作"是比"主动清代理"更稳的做法。

---

## 四、下一步执行计划(按对比后调整)

按优先级 **P0 → P1 → P2**,每一项都尽量有 main 分支的成功实现作参照:

### P0-1 改 akshare_adapter 的数据源选择
- **日 K 线 `get_daily_kline`**:
  - 主源改 `ak.stock_zh_a_hist_tx`(腾讯,国内直连)
  - 降级 1:`ak.stock_zh_a_hist`(东财,当前不可达但未来代理恢复可再用)
  - 降级 2:Sina(已实现)
  - 统一输出字段:`date / open / high / low / close / amount`,补算 `pre_close` / `pct_change`
- **龙虎榜 `get_lhb_detail`**:
  - 主源改 `ak.stock_lhb_detail_daily_sina`(main 分支在用,稳)
  - 降级:东财 `ak.stock_lhb_detail_em`
  - 同步调整 `backend/data_gateway/router.py::DEFAULT_PRIORITY_CHAINS[LHB_DETAIL]`
- 加单测(mock 腾讯/新浪接口返回)

### P0-2 修 TuShare initialize ping
- 读 `tushare_adapter.py::initialize()`,看目前 ping 哪个接口
- 改用 `pro.daily(ts_code='000001.SZ', start_date='20240102', end_date='20240102', limit=1)` 之类 120 积分可用接口
- 或:直接去掉 ping,initialize 只 set_token,首次调用失败再标记不可用

### P0-3 合并 main 的 retry 关键字白名单
- 把 `utils/retry.py::RETRY_KEYWORDS` 的中文限流词、网络错误词合并到 `backend/data_gateway/base_adapter.py::_safe_request` 的可重试判定
- 保证"RemoteDisconnected / 当前系统繁忙 / ReadTimeout"都会触发重试,而不是立即往上抛

### P0-4 去掉 `backend/core/startup.py` 对代理的强干预
- 不再 `os.environ.pop(HTTP_PROXY)`,不再 `NO_PROXY=*`
- 把日志/启动部分保留
- 让代理由系统环境决定(jd 默认走 xray,本地默认无代理)

### P1-5 stock_info 补字段
- 改 `akshare_adapter.get_stock_info`:合并 `ak.stock_info_sh_name_code()` + `ak.stock_info_sz_name_code()`,获取 `market / list_date`
- 行业字段若接口无,留 TODO;后续用 `ak.stock_board_industry_cons_em` 反查

### P2-6 端到端灌库
- P0 搞定后:
  - `make ingest-lhb FROM=20240101`(先 1 年,新浪源)
  - `make ingest-kline FROM=20240101 TO=<today>`(2024 年全市场,验证稳定性)
  - 再决定是否 `FROM=20200101` 全量(tmux 跑)

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
