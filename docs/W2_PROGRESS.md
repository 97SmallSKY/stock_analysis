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

---

## 六、2026-04-18 第二轮优化(本轮完成全部 P0/P1/P2)

### 6.1 P0-1 AKShare 日K / 龙虎榜 改用国内可达源

**问题**: jd 京东云到东方财富(push2his.eastmoney.com)的连接 100% `RemoteDisconnected`,导致 `stock_zh_a_hist` / `stock_lhb_detail_em` 全失败。

**改动** `backend/data_gateway/adapters/akshare_adapter.py`:

| 接口 | 源优先级(实测京东云可达性) | 字段完备度 |
|---|---|---|
| `get_daily_kline` | **新浪 `stock_zh_a_daily`** → 东财 `stock_zh_a_hist` → 腾讯 `stock_zh_a_hist_tx` | 新浪源字段最全,含 volume/turnover/outstanding_share |
| `get_lhb_detail` | 东财 `stock_lhb_detail_em` → 新浪 `stock_lhb_detail_daily_sina` | 顺带修复 akshare 新版 API 签名(start_date/end_date) |

### 6.2 P0-2 TuShare initialize ping 策略修复

**问题**: TuShare 120 积分账号 `trade_cal` 需要 2000 积分,`stock_basic` 每日仅 5 次;原每次 init 都 ping 浪费配额,报 ERROR 噪音。

**改动**:
- `base_adapter.py` 新增 `AdapterNotConfigured` 异常 → init 捕获时降级为 INFO(不再 ERROR)
- `tushare_adapter.py`:
  - 无 token → 抛 `AdapterNotConfigured`(而非 ValueError)
  - `skip_ping` 默认 `True`(TuShare 免费接口限额太低,不适合频繁 ping)
  - ping 改用 `stock_basic`(120 积分可调)
- `manager.py`:无 token 时打印 `⏭️ 未配置, 已跳过`(INFO),不再 `⚠️ 初始化失败`(WARN)
- `router.py`:TuShare 从 `DAILY_KLINE` 优先链移除(120 积分无 pro_bar 权限)

### 6.3 P1-3 stock_info 带行业字段

**数据源选择**:申万宏源行业(最权威,乐咕乐股 API 免费)。

**实现** `akshare_adapter.get_stock_info()`:
1. `stock_info_a_code_name()` 拿全市场主表
2. 遍历申万一级 31 个(`sw_index_first_info`),每个通过 `sw_index_third_cons(一级代码)` 拿成份股带行业名
3. left-join 回主表,占位符 `—` 归一为 NaN
4. 失败最多 3 次重试,单个一级失败不影响整体

**结果**:
- 5506 只 / 行业覆盖 3837 (70%) / 25 种申万一级
- TOP: 机械设备 533 / 电子 479 / 基础化工 409
- 未覆盖多为:退市/暂停上市股、部分新股、北交所非申万纳入

### 6.4 P2 龙虎榜灌库

**问题发现**:
1. `ON CONFLICT DO UPDATE command cannot affect row a second time` —— 同一 `(ts_code, trade_date)` 一天多条(多上榜原因)
2. 单天失败后 tx abort 未 rollback,污染后续全部 day
3. 外键约束违反 —— 可转债(1xxxxx)/退市股不在 stock_info 中

**改动** `backend/ingest/lhb.py`:
- `_dedup_same_key()`:同键合并(金额求和、reason 拼接)
- `_ensure_stock_info_skel()`:对缺失 ts_code 自动 upsert 骨架(`is_active=False`)
- catch 异常时 `conn.rollback()`
- 循环末尾 `conn.commit()` 防末轮丢失

**验证**:2024/10/08–10/18 共 9 个交易日
- 863 行 / 526 只股票 / 0 失败
- TOP 净买入真实(长亮科技 6.87 亿 / 深圳华强 5.65 亿)
- 自动补 stock_info 骨架 40 条

### 6.5 P2 K 线灌库(字段完整性闭环)

**改动** `backend/ingest/kline.py`:同 lhb 加批内去重 + rollback + 末轮 commit。

**验证**:10 只龙头 × 50 个交易日 = 500 行

| 字段 | 覆盖 |
|---|---|
| vol | 500/500 ✅ |
| amount | 500/500 ✅ |
| turnover_rate | 500/500 ✅ |
| pct_change | 500/500 ✅ |

数据样本(贵州茅台 600519.SH 2024/10/18):open 1417.62 / close 1463.19 / vol 6,861,930 股 / amount 104.18 亿 / turnover 0.55%,**与真实市场一致**。

单只耗时 ~0.3-0.5s(新浪源稳定),5500 只 × 1 年 ≈ 30-45 分钟。

---

## 七、2026-04-18 任务状态总览

| 任务 | 状态 | 备注 |
|---|---|---|
| P0-1 AKShare 国内可达化 | ✅ | 新浪主 + 东财/腾讯降级 |
| P0-2 TuShare init 策略 | ✅ | skip_ping 默认 True + AdapterNotConfigured |
| P0-3 RETRY_KEYWORDS 合并 | ✅ | 见第一轮 |
| P0-4 startup 去代理强干预 | ✅ | 见第一轮 |
| P1-3 stock_info 带行业 | ✅ | 5506/3837 申万一级覆盖 70% |
| P2 龙虎榜灌库验证 | ✅ | 9 天 863 行 |
| P2 K 线灌库字段闭环 | ✅ | 字段 100% 填充 |

下一步候选(未在本轮执行):
- 全市场 K 线 cron 灌库(建议 tmux 跑 `make ingest-kline FROM=20240101 TO=$(date +%Y%m%d)`)
- 全市场龙虎榜历史(`make ingest-lhb FROM=20240101`)
- `market_sentiment` 灌库验证
- 申万 L2/L3 行业补全(离线批跑 258 次 `sw_index_third_cons(三级代码)`)

---

## 八、已知外部源问题 → 详见独立手册

外部源可达性、curl 复现、缓解策略已集中到独立文档:

📄 [docs/EXTERNAL_SOURCES_ISSUES.md](./EXTERNAL_SOURCES_ISSUES.md)

涵盖:
- 东方财富 push2his/push2/push2ex 全线不通(京东云)
- 乐咕乐股申万成份反爬 `No tables found`
- TuShare 120 积分权限受限
- `get_market_sentiment` 历史空洞 & DB 反算方案
- `market_sentiment` DELETE 类型不匹配(已修复)

发现新问题请直接在该手册最后一节"新增:<日期>"下追加条目。

---

## 九、2026-04-18 第三轮成果(sentiment + 全年龙虎榜)

### 9.1 P1 sentiment 从 kline_daily 反算

`ingest/sentiment.py`:
- `_calc_from_kline()` 当外部 adapter 返回空壳(limit_up/limit_down/ratio/avg 全 NULL)时,自动从本地 `kline_daily` 反算
- **零外网依赖**,历史全日期可用,与 K 线同一口径

**验证**(2024/10/08-18,仅 10 只龙头有 kline):

| 日期 | limit_up | up_down_ratio | avg_pct | score | level |
|---|---|---|---|---|---|
| 10/08(十一后首日) | 3 | 9.0 | +8.7% | 100 | HOT ✅ |
| 10/09(集体杀跌) | 1(+3 跌停) | 0.25 | -5.2% | 2.6 | COLD ✅ |
| 10/18 | 2 | 9.0 | +5.7% | 100 | HOT ✅ |

口径合理。全市场 kline 灌库后 score 将自动转为全市场口径。

### 9.2 P1 stock_info industry 覆盖率优化

`db/writer.py · _bulk_upsert()`:新增 `coalesce_cols` 参数,对指定列使用 `COALESCE(EXCLUDED.c, table.c)`,
即"新值为 NULL 时保留旧值"。

`upsert_stock_info()` 对 `industry / area / market / list_date / delist_date / is_hs / exchange` 启用该策略。

**效果**:4 轮累积拉取,行业覆盖率 **70% → 94% (5192/5546)**,31 种申万一级。

### 9.3 P2 全年龙虎榜灌库

```bash
make ingest-lhb FROM=20240101 TO=20241231
```

**结果**:
- 18111 行 / 242 个交易日 / 3534 只股票 / **0 失败**
- 自动补 stock_info 骨架 283 条(可转债/退市股)
- 耗时 ~4 分钟

### 9.4 本轮任务完成状态

| 任务 | 状态 |
|---|---|
| P2 sentiment 灌库验证(外部源空壳) | ✅ 暴露问题 → 记入手册 |
| P1 sentiment DB 反算 | ✅ 9/9 天反算成功 |
| P1 stock_info industry 94% 覆盖 | ✅ COALESCE 多轮累积 |
| P2 全年龙虎榜灌库 | ✅ 18111 行 0 失败 |
| 外部源问题文档化 | ✅ docs/EXTERNAL_SOURCES_ISSUES.md |

### 9.5 下一轮优先级建议

1. **P2 全市场 K 线灌库**(tmux 跑): `make ingest-kline FROM=20240101 TO=20241231 CODES=all` —— 建议 30-90 分钟
2. 完成后重跑全年 `ingest-sentiment`,score 转为全市场口径
3. 申万 L2/L3 行业补全(离线批跑 258 次 `sw_index_third_cons(三级代码)`)
4. ingest-kline / ingest-lhb 的**增量模式**(只拉最近 1 天,cron 定时)

---

## 十、jd 运维速查(2026-04-18 新增)

本节把**日常检查/后台任务管理**的命令固化下来,避免每次回忆。

### 10.1 后台全量灌库

脚本: `scripts/full_ingest.sh` (stock-info 4 轮 → kline 全年 → sentiment → lhb)

```bash
# 在 jd 上后台启动 (用 bash -i 让 ~/.bashrc 的 no_proxy 生效)
ssh jd 'cd ~/stock_analysis && git pull origin new-branch >/dev/null && \
  rm -f logs/full_ingest.log && \
  setsid bash -i scripts/full_ingest.sh > logs/full_ingest.log 2>&1 < /dev/null &'
```

### 10.2 进度/状态检查

```bash
# 最近 20 行日志
ssh jd 'tail -20 ~/stock_analysis/logs/full_ingest.log'

# 阶段里程碑(筛选关键字)
ssh jd 'grep -E "===|完成|失败.*只|失败.*天|行业覆盖|进度" ~/stock_analysis/logs/full_ingest.log'

# 在跑的进程
ssh jd 'ps -ef | grep -E "full_ingest|backend.cli ingest" | grep -v grep'

# DB 当前计数
ssh jd 'cd ~/stock_analysis && .venv/bin/python - <<PY
from backend.db.session import pg_conn
with pg_conn() as conn, conn.cursor() as cur:
    for t in ("stock_info","kline_daily","lhb_stock_daily","market_sentiment","trade_calendar"):
        cur.execute(f"SELECT COUNT(*) FROM {t}"); print(f"{t:20s}={cur.fetchone()[0]}")
    cur.execute("SELECT COUNT(industry) FROM stock_info")
    print("industry non-null =", cur.fetchone()[0])
PY'
```

### 10.3 停任务

```bash
ssh jd 'ps -ef | grep -E "full_ingest|backend.cli ingest" | grep -v grep | \
  awk "{print \$2}" | xargs -r kill -9'
```

### 10.4 代理/no_proxy 自检

```bash
ssh jd 'bash -i -c "env | grep -i proxy"'
# 应看到 no_proxy/NO_PROXY 都含 .eastmoney.com / .sina.com.cn 等后缀

ssh jd 'bash -i -c "python3 -c \"from urllib.request import proxy_bypass; \
print(proxy_bypass(\\\"push2his.eastmoney.com\\\"), proxy_bypass(\\\"hq.sinajs.cn\\\"))\""'
# 必须都为 True; 若为 False 见 docs/EXTERNAL_SOURCES_ISSUES.md 第 6.1 节
```

### 10.5 单次 smoke(手动拉一小样本)

```bash
ssh jd 'cd ~/stock_analysis && bash -i -c ".venv/bin/python -m backend.cli ingest kline \
  --from 20241008 --to 20241018 \
  --codes 000001.SZ,600519.SH"'
```

### 10.6 常用 psql

```bash
# A 股主体覆盖率
ssh jd 'PGPASSWORD=lhb_quant_2024 psql -h 127.0.0.1 -U lhb_admin -d lhb_quant -c "
SELECT
  COUNT(*) FILTER (WHERE is_active) AS active,
  COUNT(*) FILTER (WHERE is_active AND industry IS NOT NULL) AS with_industry
FROM stock_info WHERE symbol ~ '\''^(000|001|002|003|300|301|600|601|603|605|688|920)'\'';
"'
```
