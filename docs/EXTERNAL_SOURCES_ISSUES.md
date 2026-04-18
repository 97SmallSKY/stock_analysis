# 外部数据源已知问题复现手册

> 维护人: 数据网关组  
> 最近更新: 2026-04-18  
> 适用范围: 京东云机房(境内运营商 IP)和本地 macOS 两种环境

本文集中记录所有**外部数据源在京东云环境**遇到的可复现问题、curl 级复现方式、当前
adapter 里的缓解策略、以及长期修复 TODO。发现新问题请直接在"### 新增:<日期>"章节下补条目。

---

## 1. 东方财富 push2his / push2 / push2ex 全线不通

### 1.1 现象

京东云机房 100% 触发 `RemoteDisconnected` 或 `Connection reset by peer`。
macOS 本地偶尔可通但抖动严重(`No tables found`, read timeout)。

### 1.2 复现(curl)

```bash
# 日K (push2his)
curl -sS --max-time 10 \
  'https://push2his.eastmoney.com/api/qt/stock/kline/get?secid=0.000001&fields1=f1,f2,f3,f4,f5,f6&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61&klt=101&fqt=1&beg=20241008&end=20241018' \
  -H 'Referer: https://quote.eastmoney.com/' \
  -H 'User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
# 期望: 200 + JSON
# 实测 jd: curl: (56) Recv failure: Connection reset by peer

# 实时快照 (push2)
curl -sS --max-time 10 \
  'https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=20&po=1&np=1&fields=f12,f14,f2,f3' \
  -H 'User-Agent: Mozilla/5.0'

# 涨停池 (push2ex)
curl -sS --max-time 10 \
  'https://push2ex.eastmoney.com/getTopicZTPool?ut=7eea3edcaed734bea9cbfc24409ed989&dpt=wz.ztzt&Pageindex=0&pagesize=100&sort=fbt%3Aasc&date=20241015'
# 注: 即使 curl 能通, akshare 侧 API 也已返回 0 行(历史数据不再开放)
```

### 1.3 受影响的 akshare 接口

| akshare 接口 | 域名 | 说明 |
|---|---|---|
| `stock_zh_a_hist` | push2his.eastmoney.com | 日K线 |
| `stock_zh_a_spot_em` | push2.eastmoney.com | 全市场实时快照 |
| `stock_lhb_detail_em` | data.eastmoney.com | 龙虎榜详情(已修复新版 API 签名) |
| `stock_lhb_stock_detail_em` | data.eastmoney.com | 龙虎榜个股席位 |
| `stock_zt_pool_em` | push2ex.eastmoney.com | 涨停池(历史日期无数据) |
| `stock_zt_pool_dtgc_em` | push2ex.eastmoney.com | 跌停池 |
| `stock_zt_pool_zbgc_em` | push2ex.eastmoney.com | 炸板池 |
| `stock_board_industry_name_em` | 17.push2.eastmoney.com | 板块列表 |
| `stock_board_industry_cons_em` | 17.push2.eastmoney.com | 板块成份 |

### 1.4 当前缓解

| 数据类别 | 替代源 | 位置 |
|---|---|---|
| 日K线 | **新浪 `stock_zh_a_daily`**(主)→ 东财(降级)→ 腾讯 `stock_zh_a_hist_tx`(兜底) | `akshare_adapter.get_daily_kline` |
| 龙虎榜 | 东财主(现在可通)→ 新浪 `stock_lhb_detail_daily_sina`(降级) | `akshare_adapter.get_lhb_detail` |
| 行业映射 | 申万 `sw_index_third_cons` 遍历一级 | `akshare_adapter._fetch_sw_industry_map` |
| 情绪指标 | **从 `kline_daily` 反算**(零外网依赖) | `ingest/sentiment._calc_from_kline` |

### 1.5 TODO

- [ ] 实时快照类(REALTIME_QUOTE): 目前还依赖 `stock_zh_a_spot_em`,可切换到新浪 `stock_zh_a_spot`(单次 23s / 5505 只),接受速度代价
- [ ] 板块(industry)成份仍依赖东财的地方:审计 `stock_board_industry_cons_em` 是否还有上游引用,若有改申万

---

## 2. 乐咕乐股(申万行业成份股)反爬

### 2.1 现象

`pd.read_html` 抛 `ValueError: No tables found`,约 **4–6/31 个一级行业**失败率。失败是伪随机(同 code 多次调部分成功)。

### 2.2 复现(curl)

```bash
# 正常返回 HTML 含 <table>
curl -sS --max-time 10 \
  'https://legulegu.com/stockdata/index-composition?industryCode=801010.SI' \
  -H 'User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36' \
  | grep -c '<table'
# 正常: >=1
# 异常: 0  (反爬返回无 table 的占位页,但 HTTP 200)
```

### 2.3 受影响的 akshare 接口

- `sw_index_third_cons(symbol=<三级/一级代码>)`

### 2.4 当前缓解

- adapter 里每个 code 做 3 次重试 + 1 秒延迟(`_fetch_sw_industry_map`)
- `writer.upsert_stock_info` 对 industry 列改用 `COALESCE(EXCLUDED.industry, stock_info.industry)`,
  支持**多轮增量补全**(只补缺失,不覆盖已采到)
- 实测:4 轮累积覆盖率 70% → 94% (5192/5546)

### 2.5 TODO

- [ ] 离线批跑 258 个三级行业代码,补全 L2/L3 字段(当前 L2/L3 因 adapter 仅遍历一级导致为 NULL)
- [ ] 调研备选源:东财板块(已墙)/ 同花顺 `stock_board_industry_info_ths`

---

## 3. TuShare Pro 权限受限(120 积分)

### 3.1 现象

```
抱歉，您没有接口访问权限
```
对 `daily` / `daily_basic` / `pro_bar` / `trade_cal` 全部返回该错误。仅 `stock_basic` 能调,且**每日限 5 次**。

### 3.2 复现(curl)

```bash
curl -sS 'https://api.tushare.pro' -X POST -H 'Content-Type: application/json' -d '{
  "api_name": "daily",
  "token": "<YOUR_TOKEN>",
  "params": {"ts_code": "000001.SZ", "start_date": "20241001", "end_date": "20241015"}
}'
# 返回: {"code": -2002, "msg": "抱歉，您没有接口访问权限..."}

curl -sS 'https://api.tushare.pro' -X POST -H 'Content-Type: application/json' -d '{
  "api_name": "stock_basic",
  "token": "<YOUR_TOKEN>",
  "params": {"exchange": "", "list_status": "L"},
  "fields": "ts_code,symbol,name"
}'
# 首日 5 次内: 200 + data
# 超过 5 次: {"code": -2002, "msg": "抱歉，您每天最多访问该接口5次..."}
```

### 3.3 当前缓解

- TuShare adapter `skip_ping=True` 默认,init 不消耗配额
- `_do_initialize` 里无 token 抛 `AdapterNotConfigured` → base 层降级为 INFO
- TuShare 从 `DAILY_KLINE` 优先链**移除**
- 熔断器对持续错的 TuShare 5 次后 OPEN 300s,避免刷屏

### 3.4 TODO

- [ ] 升级到 2000/5000 积分后,恢复 `tushare` 为 `DAILY_KLINE` / `FINANCIAL_DATA` / `STOCK_INFO` / `INDEX_DAILY` 链首
- [ ] 为 TuShare adapter 加"权限探测缓存":init 时 probe 一次,把可用接口列表缓存,后续不做尝试

---

## 4. 市场情绪 `get_market_sentiment` 历史空洞

### 4.1 现象

即便 `get_market_sentiment(20241015)` 返回 1 行,但
`limit_up_count=0 / limit_down_count=0 / up_down_ratio=None / avg_pct_change=None`。

### 4.2 根因

1. `stock_zt_pool_em(date=20241015)` 在 jd 能连但返回 **0 行**(东财历史涨停池不再对外开放)
2. `stock_zh_a_spot_em` jd 连接失败(同第 1 节)
3. 新浪 `stock_zh_a_spot` 虽然通但**只返回当前时刻**,无 date 参数

### 4.3 当前缓解

在 `ingest/sentiment.py` 里加了 `_calc_from_kline`,识别"空壳"情绪后自动从
本地 `kline_daily` 反算。字段口径:

| 字段 | 反算口径 |
|---|---|
| `limit_up_count` | pct_change ≥ 0.095 |
| `limit_down_count` | pct_change ≤ -0.095 |
| `up_down_ratio` | up_count / max(down_count, 1) |
| `avg_pct_change` | AVG(pct_change) |
| `strong_stock_count` | pct_change > 0.05 |
| `weak_stock_count` | pct_change < -0.05 |
| `sentiment_score` | 50 + (ratio-1)·20 + avg·600 + (lu-ld)·0.5 , clamp [0,100] |
| `sentiment_level` | HOT ≥70 / WARM ≥55 / NEUTRAL ≥45 / COOL ≥30 / COLD |

### 4.4 TODO

- [ ] 当 `kline_daily` 覆盖股票不全(如本轮仅 10 只龙头)时,score 会偏斜。全市场 kline 灌库后,score 才是全市场口径
- [ ] 增加 `cont_limit_up_count`(连板数)—— 目前 schema 已支持但反算里未实现(kline 无"连续涨停"状态)

---

## 5. `market_sentiment` DELETE 类型不匹配(已修复)

### 5.1 现象

```
operator does not exist: date = text
LINE 1: DELETE FROM market_sentiment WHERE trade_date = ANY(ARRAY['20241015',...])
```

### 5.2 根因

`writer.upsert_market_sentiment` 原 SQL:
```sql
DELETE FROM market_sentiment WHERE trade_date = ANY(%s)
```
`trade_date` 列是 DATE,Python 传入 list[str] 被 psycopg2 编码成 `text[]`。

### 5.3 修复

已改为 `ANY(%s::date[])` 显式 cast。

---

## 6. 新增:2026-04-18 (重大发现)

### 6.1 Python requests 的 NO_PROXY 大小写陷阱

**重大发现**: Python 的 `urllib.request.getproxies()` 读取代理环境变量时,
**小写 `no_proxy` 的优先级高于大写 `NO_PROXY`**。
如果 jd 上 `~/.bashrc` 里只导出了小写 `no_proxy="localhost,127.0.0.1"`,
那么即便你设置 `NO_PROXY="*.eastmoney.com,..."`, Python 仍然只会 bypass
小写那份,导致 akshare 请求依然走代理 10809 → ProxyError。

**复现**:
```bash
export HTTP_PROXY=http://127.0.0.1:10809
export HTTPS_PROXY=http://127.0.0.1:10809
export NO_PROXY="localhost,127.0.0.1,push2his.eastmoney.com"
export no_proxy="localhost,127.0.0.1"   # 注意小写只写了 localhost

python -c "
from urllib.request import getproxies, proxy_bypass
print('getproxies:', getproxies())
# 会输出 {'no': 'localhost,127.0.0.1', ...} ← 读的是小写!
print('bypass push2his:', proxy_bypass('push2his.eastmoney.com'))
# False ← NO_PROXY 失效
"
```

**正确做法**:必须在 `~/.bashrc` 里**同时导出大小写两份**,
且内容完全一致:

```bash
# jd 上当前配置 (2026-04-18 已修正)
export no_proxy="localhost,127.0.0.1,.feishu.cn,.eastmoney.com,.sina.com.cn,.sinajs.cn,.qq.com,.gtimg.cn,.gtimg.com,.bse.cn,.cninfo.com.cn,.legulegu.com,.swsresearch.com,.tushare.pro,.akfamily.xyz,push2his.eastmoney.com,push2.eastmoney.com,hq.sinajs.cn"
export NO_PROXY="localhost,127.0.0.1,.feishu.cn,.eastmoney.com,.sina.com.cn,.sinajs.cn,.qq.com,.gtimg.cn,.gtimg.com,.bse.cn,.cninfo.com.cn,.legulegu.com,.swsresearch.com,.tushare.pro,.akfamily.xyz,push2his.eastmoney.com,push2.eastmoney.com,hq.sinajs.cn"
```

**注意事项**:
- `proxy_bypass` 不支持 `*.domain.com` 通配符, 要用**裸点前缀** `.domain.com` 做后缀匹配
- 修改 `~/.bashrc` 后, 交互式 shell 要 `source ~/.bashrc` 或重新登录
- **非交互式 shell**(如 `ssh jd '...'`)**默认不 source ~/.bashrc**!
  如需在脚本中生效, 用 `ssh jd 'bash -i -c "..."'` 强制交互模式,
  或在脚本开头明确 `source ~/.bashrc`

### 6.2 新浪日K `ak.stock_zh_a_daily` 对非普通 A 股会抛异常

**重大发现**: 新浪日K接口对**可转债、基金、某些异常代码**会抛
`JSONDecodeError` / `KeyError('date')` / `AttributeError`,不是优雅返回 None。

**案例**:
```python
ak.stock_zh_a_daily(symbol="sz123157", ...)   # 可转债 → JSONDecodeError: No value to decode
ak.stock_zh_a_daily(symbol="sz000001", ...)   # 普通股 → DataFrame(7行)
```

**缓解**:
1. `backend/ingest/kline.py::_load_all_ts_codes()` 加**前缀白名单**,
   只取 000/001/002/003/300/301/600/601/603/605/688/920 开头的 A 股主体
2. `akshare_adapter.get_daily_kline()` 里新浪分支 catch 所有异常并检查
   `"date" in df.columns`, 否则 `df=None` 进入东财降级

### 6.3 腾讯 `ak.stock_zh_a_hist_tx` 对异常代码抛 `list index out of range`

**现象**: `ak.stock_zh_a_hist_tx(symbol="sz123xxx", ...)` 对可转债等非股票代码,
 akshare 内部 split 响应时越界。同样被白名单过滤后规避。

### 6.4 东财 `stock_zh_a_hist` 在京东云仍 `RemoteDisconnected`

**已知**: 第 1 节原有结论保持不变。NO_PROXY 只是排除代理影响,
**东财网络本身在京东云不通**。依赖 adapter 内降级到新浪/腾讯。

### 6.5 运维操作 Checklist (2026-04-18)

- [x] jd `~/.bashrc` no_proxy 白名单已更新, 包含所有 akshare 目标域
- [x] `backend/ingest/kline.py` 加 A 股白名单
- [x] `akshare_adapter.get_daily_kline` 新浪分支列名容错
- [x] `scripts/full_ingest.sh` 撤回 unset 代理的代码 (改由环境变量配置)
- [ ] 后续: 把 no_proxy 配置固化到 `deploy.sh` 自动追加 (避免新机器忘记)

### 6.6 验证命令 (手动)

```bash
# 1. 验证 no_proxy 生效
ssh jd 'bash -i -c "python -c \"
from urllib.request import proxy_bypass
for h in [\\\"push2his.eastmoney.com\\\", \\\"hq.sinajs.cn\\\", \\\"www.bse.cn\\\"]:
    print(h, proxy_bypass(h))
\""'
# 全部应为 True

# 2. 验证 akshare 全套通
ssh jd 'bash -i -c "cd ~/stock_analysis && .venv/bin/python -c \"
import akshare as ak
print(len(ak.stock_zh_a_daily(symbol=\\\"sz000001\\\", start_date=\\\"2024-10-08\\\", end_date=\\\"2024-10-10\\\", adjust=\\\"qfq\\\")))
print(len(ak.stock_lhb_detail_em(start_date=\\\"20241015\\\", end_date=\\\"20241015\\\")))
\""'
```

---

## 7. 新增:<YYYY-MM-DD>

(按时间倒序追加新发现的问题)
