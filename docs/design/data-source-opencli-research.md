# opencli 数据源预研(STO-76)

> **产出:** Dev 2026-04-23 · **状态:** 待 PM 评审 · **结论:** 可做 fallback,但不建议替代 AkShare

## 1. 背景

服务器数据源不稳定:

- Tushare 长期 DOWN(STO-57 已查明是 token 未配置 + adapter 未初始化,HealthChecker 每轮告警噪音已降)
- AkShare 偶发失败(东财/新浪上游抖动)

用户建议评估 [opencli](https://github.com/opencli) 的 `astock` 子命令作为补充/替代数据通道。本文预研结果。

---

## 2. opencli astock 可用命令清单

本机 `which opencli` = `/opt/homebrew/bin/opencli`(brew 安装)。`opencli list` 查到 **10 个 astock 子命令**:

| 命令 | 用途 | 后端域名 | 对齐 schema |
|---|---|---|---|
| `astock longhu` | 龙虎榜当日明细(按净买入降序) | `datacenter-web.eastmoney.com` | ✅ `lhb_stock_daily` |
| `astock longhu-seat` | 个股某日龙虎榜席位 | `datacenter-web.eastmoney.com` | ✅ `lhb_seats` |
| `astock kline` | K 线历史 | `push2his.eastmoney.com` | ✅ `kline_daily` |
| `astock info` | 股票 F10 基础信息 | `emweb.securities.eastmoney.com` | ✅ `stock_info` |
| `astock broker` | 营业部战绩排行 | `datacenter-web.eastmoney.com` | ✅ `lhb_agg_stats`(BROKER 维度) |
| `astock market` | 大盘四大指数快照 | `push2his.eastmoney.com` | ⚠️ `market_snapshots` 子集 |
| `astock quote` | 个股实时快照 | `quote.eastmoney.com` | ⚠️ 字段 camelCase 未对齐 |
| `astock rank` | A 股排行榜 | `quote.eastmoney.com` | ⚠️ 字段百分值非小数,未对齐 |
| `astock batch` | 批量实时快照(N ≤ 20) | `quote.eastmoney.com` | 同 quote |
| `astock realtime` | 逐笔成交 SSE 流 | `push2.eastmoney.com` | ❌ 我们未用 tick |

**实测验证**(本机,2026-04-23):

```
opencli astock longhu --limit 2 -f json   → 2 条记录,字段与 lhb_stock_daily 对齐(ts_code/name/trade_date/l_buy/l_sell/l_net...)
opencli astock kline 000001 --limit 3 -f json   → 3 条记录,字段与 kline_daily 对齐
耗时:~2.2s / 次调用(Chromium headless 爬取)
```

---

## 3. 字段对比(核心表)

### 3.1 `lhb_stock_daily`(龙虎榜主表)

| 字段 | 现有(AkShare) | opencli longhu | 说明 |
|---|---|---|---|
| ts_code | ✅ | ✅ | 完全一致 |
| name | ✅ | ✅ | - |
| trade_date | ✅ | ✅ | - |
| close_price | ✅ | ✅ | - |
| pct_change | ✅ | ✅ | 两者均为小数(0.1 = 10%) |
| turnover_rate | ✅ | ✅ | - |
| amount | ✅ | ✅ | 单位:万元(AkShare 原值) |
| l_buy / l_sell / l_net | ✅ | ✅ | - |
| reason / reason_type | ✅ | ✅ | - |
| **lhb_deal_ratio** | ❌(无此字段) | ✅(0.639) | opencli 独有:买入/成交占比 |
| **reason_detail** | ❌ | ✅("1家机构买入,成功率34.81%") | opencli 独有 |
| **change_type** | ❌ | ✅(编码) | opencli 独有 |
| market_cap / float_market_cap | ✅ | ✅(但 market_cap 常 null) | opencli 经常丢 market_cap |
| **d1_return / d5_return** | ❌(我们自己算 t+1/t+5) | ✅(但实测常 null) | opencli 独有但不稳定 |

**结论**:opencli 命中本表 95% 字段,3 个独有字段可选接入(lhb_deal_ratio / reason_detail / change_type)。

### 3.2 `kline_daily`

| 字段 | AkShare | opencli kline | 说明 |
|---|---|---|---|
| ts_code / trade_date | ✅ | ✅ | - |
| open/high/low/close/pre_close | ✅ | ✅ | - |
| pct_change / change | ✅ | ✅ | - |
| vol / amount | ✅ | ✅ | - |
| turnover_rate | ✅ | ✅ | - |
| **amplitude** | ❌(不存) | ✅ | opencli 独有:振幅 |
| **is_limit_up / is_limit_down** | ❌(自己计算) | ✅ | opencli 独有:涨跌停布尔 |

**结论**:opencli kline 新增 3 个有用字段(振幅 + 涨跌停布尔),能节省 scoring 里 `compute_tech_score` 的涨跌停判断逻辑(STO-64 硬编码阈值问题可缓解)。

### 3.3 `lhb_seats`

opencli 独有字段:`rise_probability_3day` / `broker_seen_times_3day` — **统计增强**,用于席位质量打分。

### 3.4 `broker`

opencli 独有字段:`win_rate_10d` / `avg_pnl_10d` / `active_rank` — 现 `lhb_agg_stats` 只有 3/5 日,**10 日粒度是增强**。

### 3.5 `quote` / `rank` / `batch`

字段 camelCase,**未对齐现有 snake_case schema**,如 `changePercent` 而非 `pct_change`,`priceBook` 而非 `pb`。需 adapter 层做字段映射才能接入。

### 3.6 `realtime`(SSE tick 流)

本项目最细粒度是 `intraday_snapshot`(10s 聚合),**无 tick 需求**。此命令暂不考虑。

---

## 4. 缺口清单(我们需要但 opencli 没有)

| 我们的需求 | 现有来源 | opencli 有吗? |
|---|---|---|
| 市场情绪 `market_sentiment`(HOT/WARM/NEUTRAL/COOL/COLD + 涨跌停家数) | AkShare 已下线东财接口,当前依赖本地计算 | ❌ opencli astock 无此维度 |
| 资金流向 `money_flow`(主力/超大单/大单净流入) | AkShare | ❌ 无 |
| 板块数据(行业/概念指数) | AkShare | ❌ 无 |
| 北向资金 | AkShare | ❌ 无 |
| 周 K 线(`kline_weekly`) | AkShare 聚合日 K | ❌ opencli kline 只有日 K,周 K 要自己聚合 |
| **lhb 当日主席位 top10 切片**(现有 `fetch_lhb_seats_for_codes` 带 `top_n` 过滤) | AkShare | ⚠️ opencli longhu-seat 单股一次调用,1000 股 × 2.2s ≈ 36 min,**性能不可用** |

**结论**:opencli **不能替代** AkShare,仅能作为补充。

---

## 5. 集成建议

### 5.1 推荐方案:作为 **fallback 源**(而非主源)

**不做**:把 opencli 当 primary 替代 AkShare。**理由**:

1. 性能:每次调 Chromium headless ≈ 2.2s;lhb_seat 批量查 1000 股要 36 min,不可接受(现有 akshare 批量 API ≤ 5s)
2. 覆盖:缺市场情绪/资金流/板块/北向等关键维度(见 §4)
3. 底层同源:opencli 后端域名仍是 `eastmoney.com`,与 AkShare 走同一数据池,**不解决 AkShare 抖动根因**(都是东财上游问题)

**做**:作为 **AkShare 不可用时的同名字段 fallback**。已有 `data_gateway/router.py::DEFAULT_PRIORITY_CHAINS` 的优先级链机制:`akshare → sina → crawler`,可加 opencli 到末位。

```python
# DEFAULT_PRIORITY_CHAINS(草案)
DataCategory.LHB_DETAIL: [AdapterName.AKSHARE, AdapterName.SINA, AdapterName.OPENCLI]  # 新增末位
DataCategory.LHB_BROKER: [AdapterName.AKSHARE, AdapterName.OPENCLI]
DataCategory.KLINE_DAILY: [AdapterName.AKSHARE, AdapterName.OPENCLI]
```

### 5.2 实现路径 `backend/data_gateway/adapters/opencli_adapter.py`

- 继承 `BaseAdapter`
- 用 `subprocess.run(["opencli", "astock", sub, "-f", "json", ...])` 调 CLI,`json.loads` 解析
- 字段映射:quote/rank 的 camelCase → snake_case(写 `_CAMEL_TO_SNAKE` 表)
- 超时:单次 ≤ 5s(对齐其他 adapter),超时即 fail-over 到链路下一位
- `initialized`:启动时跑 `which opencli` 探测,找不到 → `_initialized=False`(HealthChecker 会自动跳过,见 STO-57 修复)

### 5.3 Dev 估时

- 实现 `opencli_adapter.py`:~150 行(含字段映射)
- 修改 `priority_config` / `DEFAULT_PRIORITY_CHAINS`:~10 行
- 单测:~50 行(mock subprocess.run,断言字段映射)
- jd 部署:`brew install opencli` 不可用,需另寻 Linux 安装路径(GitHub release 或 npm install)

### 5.4 拆 dev 子 issue 候选

1. **STO-76a · opencli 安装脚本 + jd 可用性验证**:写 `scripts/install_opencli.sh` 在 Linux 装 opencli,`ssh jd opencli astock longhu --limit 1` 能出数据
2. **STO-76b · `opencli_adapter.py` 实现**:按 5.2,包含字段映射 + timeout + 单测
3. **STO-76c · `DEFAULT_PRIORITY_CHAINS` 接入** + integration test 验证 AkShare down 时 opencli 兜底
4. (可选)**STO-76d · kline 的 `is_limit_up` / `amplitude` 字段同步写入 `kline_daily`**:扩 schema 便于 STO-64 板块阈值更精确

---

## 6. 风险

- opencli 上游是东财(非独立源),**与 AkShare 同源**,不解决 AkShare 抖动根因。只在 AkShare 解析逻辑 bug 时提供「相同数据、不同解析路径」的冗余。
- 性能:单股查询 2.2s,批量大 N 调用不可行。
- jd 安装:opencli 是 Node.js CLI(从命令行 `which` 能看出 brew 路径),Linux jd 要确认 node 环境。

---

## 7. 结论

**建议**:作为第 4 个 adapter 接入优先级链**末位**,不替代 AkShare。具体实现拆到 STO-76a/b/c 子 issue。

用户看到这份文档后,可选:

- (a) 同意方案 → PM 拆子 issue,Dev 按 5.3 时间线执行
- (b) 结论 "opencli 仅同源冗余无必要" → 直接 close STO-76,不做集成
- (c) 希望优先做一条 fallback(如 kline) → PM 拆 STO-76c 单行执行

Dev 倾向 (a),因为 `is_limit_up` / `broker.win_rate_10d` / `lhb_seat.rise_probability_3day` 这些独有字段对 scoring 模型有**实质增强**,集成成本不高(150 行 adapter + 10 行 priority)。
