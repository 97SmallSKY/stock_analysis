# 2026-05-01 页面巡检问题清单 PM 修复版

> 巡检对象: http://117.72.184.8:8000/
> 巡检方式: 使用 `.worktrees/sa-ops/.claude/skills/playwright-cli/SKILL.md` 指定的 `playwright-cli` 浏览器自动化方式, 实际打开页面、点击导航、筛选、分页、弹窗, 并记录控制台与网络请求。
> 目标读者: PM / Dev / Ops。本文只记录可复现问题、影响、疑似文件和验收口径, 供拆单修复。

## 总体结论

本次巡检发现的问题不是单纯视觉瑕疵, 主要集中在三类:

1. 线上部署产物不一致: 回测任务页懒加载 JS/CSS 资源 404。
2. 后端 schema/SQL 与线上库不一致: 周报页暴露 `entry_type does not exist`。
3. 核心业务展示错误: LHB 金额字段单位混乱, 万元字段被错误显示成“亿”。

建议 PM 优先拆 P0 三项, 再处理列表空态、决策信号、指数模块和导航体验。

## P0-1 回测任务页资源 404

### 现象

点击左侧导航“回测任务”时, 浏览器请求以下资源失败:

- `/assets/BacktestRun-Q_5U7FMf.js` -> 404 Not Found
- `/assets/BacktestRun-CaBUNYl3.css` -> 404 Not Found

控制台错误:

```text
Error: Unable to preload CSS for /assets/BacktestRun-CaBUNYl3.css
```

### 影响

回测任务页懒加载组件无法可靠进入。用户从导航点击“回测任务”时, 可能看到错误页面、停留旧页面或功能缺失。

### 疑似文件

- `frontend/src/router/index.ts`
- `frontend/src/pages/BacktestRun.vue`
- `frontend/vite.config.ts`
- `scripts/deploy_frontend.sh`
- `scripts/deploy.sh`
- `backend/api/main.py` 或 FastAPI 静态文件托管相关逻辑

### 根因方向

优先怀疑线上 `index.html` 引用了新 hash 文件, 但 `frontend/dist/assets` 中对应文件没有被部署完整, 或浏览器/服务端缓存混用了新旧产物。

### 建议验收

1. 重新部署后访问 `/backtest-run` 或点击“回测任务”。
2. Network 中不再出现 `BacktestRun-*.js/css` 404。
3. 控制台无 `Unable to preload CSS`。
4. 页面标题和内容明确为“回测任务/提交新回测”, 与“策略对比”区分清楚。

## P0-2 周报页暴露 SQL/schema 错误

### 现象

周报页底部直接显示后端内部错误:

```text
部分模块失败: robot_section: column "entry_type" does not exist LINE 2: SUM(CASE WHEN entry_type = 'SELL' ... ^ ; backtest_section: current transaction is aborted, commands ignored until end of transaction block
```

### 影响

- 周报中的“机器人盈亏”和“回测 TOP 10”模块失败。
- SQL/schema 细节暴露给前端用户。
- 一个 section SQL 失败后事务进入 aborted 状态, 后续 backtest section 被连带失败。

### 疑似文件

- `backend/domain/reports/weekly.py`
- `backend/api/reports.py`
- `frontend/src/pages/Reports.vue`
- `frontend/src/api/reports.ts`
- `db/01_schema_all.sql`
- `db/init/08_STO-88_backtest_runs.sql`
- 机器人相关 schema 初始化文件, 需确认 ledger 表真实字段名

### 根因方向

`backend/domain/reports/weekly.py` 的 `_robot_section()` 使用了 `entry_type`, 但线上数据库对应表没有该列。需要以当前 schema 为准修 SQL 字段名, 并避免一个 section 失败污染后续查询事务。

### 建议验收

1. `/reports` 不再展示 SQL 原文。
2. `/api/reports/weekly?days=7&end=2026-05-01` 不返回内部 SQL 文本。
3. 机器人 section 失败时不影响 backtest section。
4. 日志中保留可排查信息, 前端只展示用户可理解的模块级降级提示。

## P0-3 LHB 金额单位显示错误

### 现象

多处金额显示为“亿”, 但字段语义和表头是“万元”:

- Dashboard: `净买入 14293.93 亿`
- 龙虎榜列表: 表头为 `买入 (万) / 卖出 (万) / 净额 (万)`, 行内却显示 `123162.95 亿`、`64515.29 亿`
- 周报净买入 TOP 10: `204207.83 亿`

后端领域模型已说明 LHB 金额单位是万元:

- `backend/domain/lhb/parser.py`: `buy_amount`, `sell_amount`, `net_amount` 为万元
- `frontend/src/utils/format.ts` 已存在 `fmtAmountWan()`

### 影响

这是核心业务数据展示错误。若原始值 `204207.83` 表示万元, 正确显示应为 `20.42 亿` 或 `204207.83 万`, 当前 `204207.83 亿` 放大了 10000 倍。

### 疑似文件

- `frontend/src/utils/format.ts`
- `frontend/src/pages/Dashboard.vue`
- `frontend/src/pages/LhbList.vue`
- `frontend/src/pages/Reports.vue`
- `frontend/src/pages/Signals.vue`
- `frontend/src/pages/LhbPnl.vue`
- `frontend/src/api/lhb.ts`
- `frontend/src/api/dashboard.ts`
- `frontend/src/api/reports.ts`
- `backend/api/lhb.py`
- `backend/api/dashboard.py`
- `backend/domain/lhb/parser.py`

### 根因方向

万元字段应统一使用 `fmtAmountWan()`。当前部分页面可能仍使用 `fmtAmount()` 或把万元字段当元级金额处理。

### 建议验收

1. LHB `buy_amount/sell_amount/net_amount` 小于 `10000` 万时显示 `x.xx 万`。
2. 大于等于 `10000` 万时显示 `x.xx 亿`, 且换算为 `value / 10000`。
3. Dashboard、龙虎榜、Signals、LhbPnl、Reports 同一股票同一字段单位一致。
4. 补充/修复前端格式化测试, 覆盖 `9999`, `10000`, `204207.83`, 负数和 null。

## P1-1 龙虎榜有数据但仍显示 No Rows To Show

### 现象

`/lhb` 页面同时显示:

- `No Rows To Show`
- 实际表格数据和分页: `共 16291 条`

### 影响

用户会误判列表为空。该问题尤其影响首次进入页面时的可信度。

### 疑似文件

- `frontend/src/pages/LhbList.vue`
- `frontend/src/stores/lhb.ts`
- `frontend/src/api/lhb.ts`

### 根因方向

AG Grid 空态 overlay 没有随 rows 更新关闭, 或 pinned column / 主表 row model 状态不同步。

### 建议验收

1. `/lhb` 有 rows 时不出现 `No Rows To Show`。
2. 搜索无结果时才显示空态。
3. 重置、分页、排序、筛选后 overlay 状态正确切换。

## P1-2 决策信号页默认空数据

### 现象

决策信号页默认条件下显示:

- `候选信号 暂无数据`
- `信号总数 0`
- `评分段分布` 全为 0

但同一时间 Dashboard / 龙虎榜 / 周报均存在高分 LHB 记录, 例如 `300885.SZ` 评分 `87.7`, `688256.SH` 评分 `85.25`。

巡检中相关接口请求返回 200:

```text
/api/lhb?date_from=20250501&date_to=20260501&score_min=70&order_by=score&order_desc=true&limit=500&with_tn=true&skip_total=true
```

### 影响

决策信号页作为核心分析入口没有有效输出, 与其他页面数据矛盾。

### 疑似文件

- `frontend/src/pages/Signals.vue`
- `frontend/src/utils/signals_agg.ts`
- `frontend/src/api/lhb.ts`
- `backend/api/lhb.py`

### 根因方向

优先检查 `Signals.vue` 对 `/api/lhb` 响应的字段映射和 rows 读取路径, 其次检查筛选参数、score 类型和聚合函数是否误过滤。

### 建议验收

1. 默认 `score_min=70` 时展示与 `/api/lhb` 返回一致的候选记录。
2. 统计卡的信号总数、涉及股票、平均评分、净买入合计与表格数据一致。
3. 调低评分阈值后数据量不应减少。

## P1-3 Dashboard 指数模块显示不可用

### 现象

首页顶部出现:

```text
指数数据暂不可用
```

同时市场卡片出现 `均幅 -` 等缺省展示。巡检中 `/api/index/snapshot` 请求曾返回 200。

### 影响

首页第一屏存在降级提示, 用户无法判断是远端指数源失败、接口返回空、缓存 miss, 还是前端解析失败。

### 疑似文件

- `frontend/src/pages/Dashboard.vue`
- `frontend/src/stores/dashboard.ts`
- `frontend/src/api/dashboard.ts`
- `frontend/src/api/index.ts`
- `backend/api/dashboard.py`
- `backend/api/index.py`

### 根因方向

需要区分三类状态: 接口失败、接口成功但 items 为空、接口带 warning 降级。前端不应把所有情况混成“暂不可用”。

### 建议验收

1. `/api/index/snapshot` 有指数 items 时首页正常展示四大指数。
2. 空数据时显示明确的轻量提示, 不影响 Dashboard 主体。
3. 后端 warning 能在前端合理呈现, 不展示内部异常堆栈。

## P2-1 策略对比 / 回测任务导航混乱

### 现象

点击“策略对比”和“回测任务”时, 巡检中都曾落到 `/backtest`, 页面标题为 `策略对比 · LHB 量化分析`。同时“回测任务”资源 404。

### 影响

用户无法明确区分“策略对比”和“提交新回测任务”。

### 疑似文件

- `frontend/src/router/index.ts`
- `frontend/src/App.vue`
- `frontend/src/pages/Backtest.vue`
- `frontend/src/pages/BacktestRun.vue`
- `frontend/src/pages/BacktestHistory.vue`

### 建议验收

1. “策略对比”进入 `/backtest`。
2. “回测任务”进入 `/backtest-run`。
3. “回测历史”进入 `/backtest-history`。
4. 三个页面标题、主按钮和内容语义明确区分。

## P2-2 侧边导航“数据源 / 特性介绍”不是正常 menuitem

### 现象

Playwright 快照里前 9 个导航项是 `menuitem`, 但“数据源”“特性介绍”表现为普通文本和图标, 旁边有 `ellipsis`。

### 影响

用户看到文字但不确定能否点击。可访问性和自动化测试也无法通过 role 稳定定位。

### 疑似文件

- `frontend/src/App.vue`
- `frontend/src/router/index.ts`
- `frontend/src/pages/DataSourceMgr.vue`
- `frontend/src/pages/Changelog.vue`

### 建议验收

1. “数据源”“特性介绍”在正常桌面宽度下是可点击 menuitem。
2. 当前路由能正确 active 高亮。
3. 溢出菜单只在宽度不足时出现。

## P2-3 K 线弹窗图表需视觉确认

### 现象

点击 Dashboard Top1 股票 `300885.SZ 海昌新材` 可打开 `300885.SZ · 日 K 线` 弹窗, 并看到日 K、周 K、分钟 K tab 和区间按钮。可访问快照中未能确认图表主体是否真实渲染, 只看到交互说明。

### 影响

暂不能判定为明确 bug, 但 K 线图是核心交互, 需要截图或 canvas 像素检查确认图表非空。

### 疑似文件

- `frontend/src/components/StockDrillModal.vue`
- `frontend/src/composables/useStockDrill.ts`
- `frontend/src/api/kline.ts`
- `backend/api/kline.py`
- `docs/frontend/kline-modal-contract.md`

### 建议验收

1. 打开任意有 K 线数据股票, 日 K canvas 非空。
2. 点击日 K 蜡烛后, 周 K / 分钟 K 切换路径符合 `docs/frontend/kline-modal-contract.md`。
3. 空数据股票显示明确空态, 不显示空白图表。

## 建议拆单顺序

| 顺序 | 优先级 | 建议标题 | 主要文件 |
| --- | --- | --- | --- |
| 1 | P0 | 修复线上 BacktestRun 懒加载资源 404 | `router/index.ts`, `deploy_frontend.sh`, `deploy.sh` |
| 2 | P0 | 修复周报 robot_section schema 错误并隐藏 SQL | `backend/domain/reports/weekly.py`, `backend/api/reports.py`, `Reports.vue` |
| 3 | P0 | 统一 LHB 万元字段格式化 | `format.ts`, `Dashboard.vue`, `LhbList.vue`, `Reports.vue`, `Signals.vue`, `LhbPnl.vue` |
| 4 | P1 | 修复 LhbList AG Grid 空态 overlay | `LhbList.vue`, `stores/lhb.ts` |
| 5 | P1 | 修复 Signals 默认空数据 | `Signals.vue`, `signals_agg.ts`, `api/lhb.ts` |
| 6 | P1 | 梳理 Dashboard 指数降级状态 | `Dashboard.vue`, `api/index.ts`, `backend/api/index.py` |
| 7 | P2 | 梳理回测导航和菜单可访问性 | `App.vue`, `router/index.ts` |
| 8 | P2 | 补 K 线弹窗视觉/像素验收 | `StockDrillModal.vue`, `kline-modal-contract.md` |

## 巡检证据摘要

已观察到的关键请求:

```text
GET /api/dashboard?top_n=10 -> 200
GET /api/index/snapshot -> 200
GET /api/lhb?limit=50&offset=0&order_by=trade_date&order_desc=true&with_tn=true&date_from=20250501&date_to=20260501 -> 200
GET /api/lhb?...score_min=70&order_by=score... -> 200
GET /api/backtest/strategies -> 200
GET /api/backtest/history?limit=200 -> 200
GET /api/robots -> 200
GET /api/reports/weekly?days=7&end=2026-05-01 -> 200
GET /assets/BacktestRun-Q_5U7FMf.js -> 404
GET /assets/BacktestRun-CaBUNYl3.css -> 404
```

已观察到的控制台错误:

```text
Failed to load resource: the server responded with a status of 404 (Not Found)
Error: Unable to preload CSS for /assets/BacktestRun-CaBUNYl3.css
```
