# Linear 导入清单 · 2026-04-20 13h session 积压

> **生成时间**:2026-04-20 20:xx
> **背景**:13 小时开发 session 结束后,本地 TaskList 清空。以下是**尚未解决**的问题清单,请逐条在 [Linear](https://linear.app/xiaotian-dev) 新建 issue。
>
> **工作区约定**:
> - Workspace: `xiaotian-dev`
> - Team: `Personal Dev`(前缀 `STO-`)
> - Project: 龙虎榜量化交易分析系统
> - 所有新 issue 默认 `Backlog` + `@pm` 标签(让 PM 审一遍再纳入 Todo)
>
> **总计 11 条**:3 Bug · 4 Improvement · 2 Task · 2 Docs

---

## 🐛 Bug(3 条)

### STO-?? · 北交所 920* 股票全数据源失败(约 307 只)

**Labels**:`Bug` + `@pm`
**Priority**:3 Medium(已知问题,不阻塞主业务)

#### 背景

每次 kline ingest 运行(包括今日 18:40 daily 实盘),北交所 920xxx 开头的股票全部报 `ERROR ❌ DAILY_KLINE.get_daily_kline 全部数据源失败` —— akshare / sina / crawler 三个源都拿不到数据。当日 daily 错误统计 ~190 条,98% 属于此类。

#### 现状

- 当前三家数据源(新浪 / 东财 / 腾讯)对北交所 9xxxx 均不支持
- daily_update.sh 设计为**不阻断**(failed=0 表示成功入库的非北交所股票)
- 导致 Dashboard / 龙虎榜 / K 线对这 307 只股票无数据

#### 期望修复

方案 A:接入 Tushare `daily` 接口(需升级 2000 积分)
方案 B:东财备用路径 `push2his.eastmoney.com` 的北交所专用接口
方案 C:暂时标记这 307 只为 `is_active=false`,不再拉取,避免日志噪音

#### 验收

- 新 daily 运行后,北交所股票 ERROR 数 < 20 条(允许极少数刚上市或停牌)
- `SELECT COUNT(DISTINCT ts_code) FROM kline_daily WHERE ts_code LIKE '92%'` > 200

#### 来源

- 今日 daily 日志 `logs/daily_update.log` ERROR 总数 187
- Sentiment 回填期间也连续触发
- 详见 `docs/ops/2026-04-20-13h-session-summary.md` §3.2

---

### STO-?? · sentiment_change 逻辑确认(当前总返回 0)

**Labels**:`Bug` + `@pm`
**Priority**:4 Low

#### 背景

P20.5 新增 `sentiment_change` 实时计算(当日 sentiment_score − 前一交易日 score)。验证时返回 `0.0`,因为回填历史 538 天 sentiment_score 基本都是 `45.0` NEUTRAL 常数,没有区分度。

#### 疑问

这是**数据问题**(回填算法太粗糙,都是 45 分)还是**逻辑问题**(算法本身)?

#### 期望

1. 查 `market_sentiment.sentiment_score` 分布,确认是否都是 45
2. 若是:改进 `_calc_from_kline` 的评分公式,增加区分度(当前 score = 50 + ratio 偏移 + 均幅*600 + 涨停 0.5,偏移范围太窄)
3. 若否:sentiment_change 算对了,只是业务上最近两天确实一样

#### 来源

- `backend/ingest/sentiment.py::_calc_from_kline`
- `backend/api/dashboard.py::_latest_sentiment` P20.5

---

### STO-?? · deploy.sh 中 sudo systemctl cat 可能要密码

**Labels**:`Bug` + `@pm`
**Priority**:3 Medium

#### 背景

P18.7 把 deploy.sh 里 service 探测从 `systemctl list-unit-files | grep -q` 改为 `sudo systemctl cat $svc`。当前 jd 部署用 root 执行 `./scripts/deploy.sh`,`sudo` 是 no-op。**若未来换成非 root 用户跑**,`sudo systemctl cat` 会弹出密码提示或失败。

#### 现状

当前 root 用户执行无问题,已连续 29 次 deploy 全绿。但**非 root 部署会坏**。

#### 期望

任选:
- 方案 A:去掉 `sudo`(现在 root 执行不需要)
- 方案 B:`sudo -n systemctl cat`(-n = non-interactive,若要密码直接失败而不是 hang)
- 方案 C:改用 `test -f /etc/systemd/system/$svc`(文件存在性检查,最朴素)

#### 验收

- 非 root 用户也能跑 `./scripts/deploy.sh`
- 或明确在手册里写「必须 root 运行」

---

## ⚡ Improvement(4 条)

### STO-?? · 前端 bundle 体积优化(vendor-antd 1.4MB)

**Labels**:`Improvement` + `@pm`
**Priority**:3 Medium

#### 背景

每次 `vite build` 告警:

```
(!) Some chunks are larger than 1200 kB after minification
  vendor-antd-*.js  1,433.97 kB  (gzip: 436.49 kB)
  vendor-grid-*.js  1,114.20 kB  (gzip: 311.53 kB)
  vendor-echarts-*.js  565.72 kB
```

首屏 3.6MB 未压缩 JS,首次加载慢。

#### 期望

- `build.rollupOptions.output.manualChunks` 精细化拆分:按需加载
- ant-design-vue 按需引入(unplugin-vue-components)
- echarts 按需引入 charts / components(部分已做但不彻底)
- ag-grid-vue3(vendor-grid)评估是否非必要(只 LhbList 用)

#### 验收

- 首屏 gzip 总体积 < 500KB
- 单个 chunk 不超过 600KB gzip
- 无功能回归(vitest 129 全过)

---

### STO-?? · LhbPnl 评分详情 Modal(对标 Signals)

**Labels**:`Improvement` + `@pm`
**Priority**:3 Medium

#### 背景

用户反馈(2026-04-20):「盈亏分析的评分明细多维图」。实际当时定位到 Signals 已做(P19.8/P19.10/P19.12),但 LhbPnl 页本身**没有**评分详情 Modal 入口,该页只展示 reason / stock 汇总。

#### 期望

- LhbPnl 的 stock 维度表格(已存在)每行加「详情」列,弹雷达图 Modal
- 复用 Signals 的 Modal 组件(提取到 `components/ScoreDetailModal.vue`)
- 由于 LhbPnl 可能一只股票多条 LHB 记录,Modal 显示**期间内最高分的那条** score_detail

#### 验收

- LhbPnl 表格有「详情」按钮
- 点击弹雷达图 + 3 列分类明细(和 Signals 一致)
- vitest + vue-tsc 全绿

---

### STO-?? · Robots / BacktestRun 页面空态引导

**Labels**:`Improvement` + `@pm`
**Priority**:4 Low

#### 背景

体检(循环 #6)发现:

- `/api/robots` 返回 `robots: []`(机器人页空)
- `/api/backtest/history` 返回 `total: 0`(回测任务历史页空)

用户第一次打开这两页看到空白 Empty,不知道下一步做什么。

#### 期望

- 空态加 Call-to-Action 按钮(「创建第一个机器人」/「跑第一个回测」)
- 点击跳到对应的**创建表单**
- 若用户没权限 / 数据源未配置,显示「先配置 X 再来」

#### 验收

- 空态有图 + 文案 + 按钮
- 至少一条「帮助」链接到文档

---

### STO-?? · Pydantic 响应 schema 防 API-vs-DB 字段不一致

**Labels**:`Improvement` + `@pm`
**Priority**:3 Medium

#### 背景

P18.13b 事故复盘(`docs/ops/2026-04-20-p18.13b-schema-mismatch.md`)结尾建议:当前 API 用 `dict` + `setdefault` 硬塞字段,容易与 DB schema 错配。应引入 **Pydantic BaseModel** 做响应层校验。

#### 期望

- 为 `/api/dashboard / /api/lhb / /api/kline/*` 等核心端点定义 Pydantic 响应模型
- `response_model=` 强制字段集 + 类型
- 测试:DB 查询返回字段缺失时 Pydantic 报错,CI 能在测试阶段发现

#### 验收

- 至少 3 个核心 API 用上 Pydantic
- pytest + FastAPI 的 response_model 校验开启

#### 来源

`docs/ops/2026-04-20-p18.13b-schema-mismatch.md` §follow-ups

---

## 📋 Task(2 条)

### STO-?? · [Watch] 每日 daily_update.sh 运行巡检

**Labels**:`Task` + `@ops`
**Priority**:3 Medium

#### 背景

`stock_analysis_daily.timer` 每工作日 18:40 触发,跑 daily_update.sh(LHB + 席位 + 情绪 + 日K + 评分,约 1h)。需要 Ops 每日**至少看一眼**结果,确认:

- `elapsed` 是否异常拉长
- `errors` 是否为 0
- kline 当日新增行数是否符合预期(~5100 行)
- LHB / sentiment 是否入库

#### 触发条件

每日 20:00 后手动或由 Agent 自动巡检一次:

```bash
ssh jd 'grep -E "daily_update END" ~/stock_analysis/logs/daily_update.log | tail -1'
ssh jd 'grep -cE ERROR ~/stock_analysis/logs/daily_update.log'
```

#### 告警阈值

- `errors > 0` → 立即调查
- `elapsed > 2h` → 疑似限流或卡死
- 当日 kline_daily 新增 < 4000 行 → 数据源异常

---

### STO-?? · [Watch] smoke_http.sh 冒烟端点扩展追踪

**Labels**:`Task` + `@ops`
**Priority**:4 Low

#### 背景

P20.3 smoke_http.sh 扩到 8 个端点。每次新增 API 端点时,**应当同步**加到 smoke。

#### 触发

每 4 周巡检一次:

```bash
# 对比 OpenAPI 和 smoke 脚本
curl -sS /openapi.json | jq '.paths | keys[]' | sort > /tmp/all.txt
grep -oE '/api/[^ "?]+' scripts/smoke_http.sh | sort -u > /tmp/smoke.txt
comm -23 /tmp/all.txt /tmp/smoke.txt  # 差集 = 漏测的端点
```

#### 验收

新端点上线后一周内进 smoke。

---

## 📚 Docs(2 条)

### STO-?? · 把「前端 API 改参数必须同步后端边界」教训写入 CLAUDE.md

**Labels**:`Docs` + `@pm`
**Priority**:4 Low

#### 背景

P19.13 事故:前端 `limit=2000` 超过后端 `le=1000` → 所有 daily K 线请求 422。整整几个小时用户点日 K 看不了才被发现。

#### 期望

CLAUDE.md 新增一节「前端/后端接口契约同步」:

- 改前端 `fetchXxx` 的 query 参数时,必须 `grep` 后端 `Query(..., le=, ge=)` 验证边界
- 新增 API 单测 checklist:参数越界 case 必须有
- 至少一次真实 curl 验证(不只看 pytest mock 过)

#### 验收

文档更新 + 7x24_agent.md 手册 §3.2 加一条红线

---

### STO-?? · K 线组件依赖的 API 契约文档化

**Labels**:`Docs` + `@pm`
**Priority**:4 Low

#### 背景

今日 K 线组件(StockDrillModal.vue + DateRangeQuickPick)已有以下约定:

- limit 范围:daily ≤ 5000 / weekly ≤ 2000
- rangeKey:`1y / 6m / 3m / 1m / 2w / 1w / 5d / latest` 8 个键固定
- anchorDate 传递链:Signals/LhbList → StockLink → useStockDrill → StockDrillModal
- computeDateRange 支持 anchorOverride 模式(前后均分)

这些约定散落在代码注释里,新人接手容易改坏。

#### 期望

新建 `docs/frontend/kline-modal-contract.md`,记录:

- 各 rangeKey 的语义 + 半宽天数
- anchorDate 传递链路
- limit / from / to 参数与后端的边界对应关系
- 点击日 K 蜡烛的行为(P19.12 改动)

---

## 🏷️ 标签与 ID 速查(复制时用)

```
TEAM_ID     = a6a9d55b-9937-4e65-83bd-ac09d20a18e4  # Personal Dev
PROJECT_ID  = 5c09b8da-a3fc-45be-b866-5dc94cf01523  # 龙虎榜

# 类型标签
Bug         = <在 Linear 里用类型 Bug>
Feature     = 7dc9c1f6-8b9a-4bd5-ad8e-b86db81dd5c8
Improvement = e8df12ff-aed7-42e7-bc95-abcef632b669
Task        = 3d1ea33f-dcab-4b3c-aa15-386fbaed76e8
Refactor    = fc88f692-708a-4503-8452-3562bbea770a
Docs        = b7f64fed-4076-49f8-afc9-6855be074ef8
Urgent      = 5c03b059-a0b4-4683-9311-07bcafa2aa92
Blocked     = 82c6e303-5c10-4307-88fc-c9c8e7b7302c

# 角色标签
@pm   = 1190ea9b-649e-4895-8feb-8c771d3cc76f
@dev  = f725ab92-9754-4220-b05c-6d9961758e36
@test = 9036b957-3142-47d3-9959-4a62095c2636
@ops  = 68c6b943-5dee-479f-a1c6-2c7338153984
```

---

## 📥 粘贴到 Linear 的建议顺序

1. **高优先级先**:3 条 Bug(920 北交所 / sentiment_change / deploy sudo)
2. **Improvement**(bundle / LhbPnl Modal / 空态引导 / Pydantic schema)
3. **Task Watch**(daily / smoke 巡检)
4. **Docs**(CLAUDE 补契约教训 / K 线 API 契约)

每条粘贴后记得:

- 标题去掉 `STO-??·` 前缀(Linear 会自动分配)
- 角色标签默认 `@pm`,由 PM 审后改 `@dev`
- 若直接清楚可做,PM 可跳过 Todo 直接 `@dev`

---

**备注**:粘贴完记得回本仓库**删除此文件**(或移到 `completed/`),避免下次以为还要粘贴。
