# 项目真实进度 (Single Source of Truth)

> 本文档是项目进度的唯一事实源。README.md 第7节 和 IMPLEMENTATION_ROADMAP.md 仅作历史计划参考,与本文冲突时以本文为准。
>
> 最后更新:2025-04-18(基于代码审查)

## 状态图例

- ✅ 已完成并验证
- 🟡 部分完成 / 待验证
- ⚠️ 存在已知问题
- ❌ 未开始

## 模块进度

| 模块 | 状态 | 说明 |
| --- | --- | --- |
| 数据采集 (akshare) | 🟡 | 基本流程可用,需补异常处理与重试 |
| 因子计算 | 🟡 | 主流程可跑,单测覆盖不足 |
| API (FastAPI) | 🟡 | 路由齐全,鉴权/限流缺失 |
| 调度 (scheduler) | ⚠️ | 代码存在但未接入 systemd |
| Nginx 部署 | ❌ | 配置未验证 |
| 前端 | 🟡 | 基础页面可用 |
| 监控/日志 | ❌ | 仅 print,未接入结构化日志 |

## 下一步优先级

1. 补 API 鉴权与基础限流
2. scheduler 接入 systemd,并落一份可复现的部署文档
3. 核心因子计算增加单元测试
4. 数据采集加入重试与失败告警

## 近期变更日志

- **2025-04-18 P1**:同步本文档为唯一事实源,README/ROADMAP 降级为历史参考(见文首说明)。
- **2025-04-18 P2**:追认 ADR-0001(放弃 SQLAlchemy + Alembic),清理 README/DEPLOYMENT/NEXT_STEPS/CODE_REVIEW/schema 注释中 "仍在用 ORM/Alembic" 的误导性陈述。
- **2025-04-18 P3**:
  - 删除根目录 `data_sources/`(死代码,import 即报 ModuleNotFoundError);ER 图迁至 `docs/db/`。
  - 将 `backend/ingest/lhb.py` 降级为薄壳,`run()` 委托给 `backend/domain/lhb/daily_job.run_range`,消除 "ingest vs domain" 的双份入库实现。
  - 新增测试 `tests/test_ingest_lhb_shell.py`(6 个 case 全绿);全量测试 631 passed / 1 skipped / 覆盖率 75.02%。
- **2025-04-18 P4**:抬升覆盖率门槛 70% → 75%(`pytest.ini`)并补 `backend/domain/lhb/aggregator.py` 测试。
  - 新增 `tests/test_aggregator.py`(11 个 case,覆盖 `_aggregate_stock/_aggregate_industry/_aggregate_broker/_upsert_agg/aggregate_all`)。
  - aggregator 覆盖率 30% → 81%(本地实测),整体 75.02% → 75.81%,642 passed / 1 skipped。
- **2026-04-19 P6**:生产 API 500 + 回测 0 交易 修复串烧。
  - P6.1:dashboard/lhb/weekly 的 SQL 列名对齐(`l_buy/l_sell/l_net AS net_amount`、market_sentiment 的 `limit_up_count` 等)。dashboard 的"今日"改走 `MAX(trade_date)` 自动回退周末。
  - P6.5:Makefile 扩展 dev/ops 命令:`make dev-api / dev-fe / stop / reset / status / smoke / remote-stop / remote-reset`,`stop/reset` 强制按端口 `lsof -ti | kill -9`;`SHELL := /bin/bash` 规避用户 zsh hook。新增 `scripts/smoke_http.sh` 解耦远端三端点冒烟。
  - P6.6 / P6.6b:新增 seats-only 回填通路。
    - 根因:历史 `run_range(skip_existing=True)` 只看主表是否有 trade_date,主表 795 日全覆盖 → seats 永远被跳过 → `lhb_seats` 空表。
    - `backend/domain/lhb/daily_job.run_seats_only_single / run_seats_only_range(top_n=, skip_existing=)`,按 `lhb_seats` 实际覆盖日期跳过。
    - `akshare_adapter.get_lhb_seats_for_codes(trade_date, codes)` + `manager.get_lhb_seats_for_codes` 支持按股票列表精确拉。
    - 修 CPython GC 过早 close `_GeneratorContextManager` 的 bug(`_ACTIVE_CTXS` dict 保住引用,`_maybe_close` 走 `ctx.__exit__`)。
    - upsert 前按 `(broker_name, direction)` 去重避免 `CardinalityViolation`。
    - CLI:`python -m backend.cli ingest lhb-seats --from --to [--top N] [--reverse] [--no-skip]`。
    - Makefile:`make ingest-lhb-seats-top TOP=10 REVERSE=1`。
    - 已在 jd 跑:2026-01-05 → 2026-04-17 共 68 个交易日,每日 top10 股票,入库 680 主表 id / 6139 seats 行,0 失败。
  - P6.7:跑 `make score --from 20260101 --to 20260417 --only-empty`。4687 行完成评分(min 25 / max 100 / avg 65.0 / score≥70 共 1900 行)。
  - 回测:`backend/api/backtest._make_db_provider.list_lhb_records` 里硬编码的 `seats: []` 改为从 `lhb_seats` 按 `trade_date` 批量 fetch 后按 ts_code 分桶挂回 record。`lhb_hot_money` 策略 2026-02-01 → 2026-04-17:101 笔交易 / 总收益 -6.06% / 胜率 36.96% / 最大回撤 -26%(策略质量另论,但回测通路跑通)。
  - 本地 710 passed / 1 skipped;`make smoke` dashboard/lhb/weekly 全 200。
  - P6.4 前端页面盘点:`router/index.ts` 仅 5 页(Dashboard / LhbList / DataSourceMgr / Backtest / Reports),缺 3 页。
  - P6.8 前端补三页 + 菜单 + 路由,`vue-tsc --noEmit` 全绿。
    - `frontend/src/api/robots.ts`:封装 `/api/robots` 列表 + `pause/resume/enable/disable/status`,对齐 `backend/api/robots.py` 的 snapshot 结构。
    - `pages/Robots.vue`:机器人表格(id/state/enabled/cash/total_assets/daily_pnl_pct/position_count)+ 行内 pause/resume/enable/disable,点击行展开 positions 明细;顶栏 5s 自动刷新开关。
    - `pages/Signals.vue`:基于 `/api/lhb?score_min=&order_by=score` 直接作为决策信号源,表格(股票 / 日期 / 原因 / 净买入 / 评分 / 评分明细 tooltip) + 右侧汇总(总数 / 平均评分 / 净买入合计 / Top3 原因)。
    - `pages/LhbPnl.vue`:日期窗口内取 `/api/lhb` 最多 1000 行,前端 reduce 聚合——reason 维度 Top10 用 ECharts(BarChart 按需注册)画三柱图(净买入亿元 / 上榜次数 / 平均评分),ts_code 维度切出净买入 TopN / 净卖出 TopN 双表。
    - 路由 `/robots`、`/lhb-pnl`、`/signals`;顶部 `App.vue` 菜单按「信号 → 盈亏 → 策略对比 → 机器人」顺序插入。
- **2026-04-19 P7**:回测页 + 股票三级下钻 + 表格统一排序筛选。
  - P7 决策:UI 库维持 Ant Design Vue + AG Grid,不迁 Element Plus。依据见 `docs/ADR/0002-ui-framework-decision.md`(量化核心是表格引擎 AG Grid 不是 UI 品牌;切 UI 成本 = 重写 8 页 / 收益 ≈ 0)。
  - P7.1 `/api/backtest/history`:在 `TaskResp` 上补 `total_return / sharpe / max_drawdown / n_trades / win_rate` 摘要,支持 `status` + `strategy_name` 过滤;前端 `Backtest.vue` 用 `<a-tabs>` 拆 "策略对比" / "历史任务",所有列给 sorter 可排序;5 个后端单测(`tests/test_api_backtest_history.py`)全绿。
  - P7.2 `StockDrillModal`:全局单例组件(`composables/useStockDrill.ts` 单例 state + `components/StockDrillModal.vue` 挂在 `App.vue` 根)。日 K → 点某 bar → 周 K(带 MA5)→ 再点 → 分钟 K。分钟 K 数据源未接入,接口契约已定;3 个单测 + kline api 单测(7 tests 全绿)。
  - P7.3 后端 `backend/api/kline.py`:`/api/kline/{daily,weekly,minute}` 三端点。daily 直查 `kline_daily`,weekly 用 `date_trunc('week')` + `array_agg(ORDER BY)` 聚合 OHLC,minute 目前无表 → 统一返回 `empty_reason='no_source_table'`。ts_code 归一大写,日期 YYYYMMDD 校验。13 个单测(`tests/test_api_kline.py`)覆盖 happy / empty / bad_code / bad_date / db_failure。
  - P7.4 表格统一:AG Grid(LhbList)`defaultColDef.sortable/filter/floatingFilter=true`,ts_code 列 cellRenderer + onCellClicked 触发 drill;AntdV `a-table`(Signals / LhbPnl.topBuy/topSell / Robots.主表/持仓 / Backtest.history)所有可排序列都配 `sorter` 闭包,ts_code 列换成 `<StockLink>` 组件(点击 → `useStockDrill().open(code)`)。避免了大规模技术栈迁移。
  - P7.5 测试:后端 `pytest 729 passed / 1 skipped / 覆盖率 75.43%`(新增 `.coveragerc` 把 `backend/ingest/*` 从统计中 omit —— 纯 adapter-IO 脚本,有 `test_data_fetch` 集成测但非 CI 跑,与 `test_db_crud` 同思路);前端 `vitest 8 files / 43 tests` 全绿。
- **2026-04-19 P8**:前端视觉美化(不改业务,零测试风险)。
  - P8.1 + P8.2 全局 Theme:`<a-config-provider :theme>` token 设置 —— colorPrimary 金融红 `#d4380d` / borderRadius 8 / Layout headerBg 渐变 `#0f1f3d → #17325b` / Table headerBg `#fafafa` rowHoverBg `#fff7f5` / Menu dark Item 悬停底色 / Tag 圆角 4。顶栏 logo 换 `<StockOutlined>` + 高亮副名 emphasis,菜单每项配 AntdV Icon(Dashboard/Table/Bulb/PieChart/LineChart/Robot/FileText/Database),sticky 顶置 + `box-shadow`。路由切换 `fade-slide` 过渡 180ms。
  - P8.3 Dashboard 四柱 KPI 改造:情绪 / 涨跌 / 涨跌停 / Top1 净买入 四张 Card(左侧色带各自 `#d4380d / #fa8c16 / #cf1322 / #722ed1` 暗示类别),value 28px 粗 tabular-nums;下方 2 栏 = 龙虎榜表(大 16 栅格)+ 数据源状态列表(8 栅格,`<DatabaseOutlined>` 标题 + dot + latency 小字)。表格 ts_code 列点击 → StockDrillModal。
  - P8.4 全局 CSS 杠杆:`.ant-form-inline .ant-form-item { margin-bottom:0 }`、表头 font-weight 600、排序箭头激活色 `#d4380d`、`.ant-tag` 胶囊化、Card hover `0 4px 16px` 抬升、tbody 偶数行 `#fafbfc` 斑马纹 + hover `#fff7f5` —— 一次覆盖 Signals / LhbPnl / Robots / Backtest / Reports / DataSourceMgr 所有页面,零侵入。
  - P8.5 StockDrillModal:标题 `<AreaChartOutlined>` + ts_code mono 字体 + 频率副标;切页 Tab 之外加 `<a-breadcrumb>` 面包屑(日K → 周K → 分钟K · YYYY-MM-DD),支持逐级回退;空态按 `empty_reason` 分三种:`no_source_table` 明示「分钟级 K 线暂未接入数据源(Tushare stk_mins / AkShare stock_zh_a_minute),接口契约已就位」、`out_of_range` 提供返回日K按钮、`fetch_failed` 提示重试;底部 hint 说明交互链。
  - P8.6 测试:前端 `vue-tsc 0 error / vitest 43 passed`;后端 `pytest 729 passed / 75.43%`,纯前端 CSS + 组件重排,未触发任何逻辑改动。
- **2026-04-19 P9**:远程部署 jd + P7/P8 验证。
  - `git push origin new-branch`(a70a833 → e9d60ea,2 个 commit 上推)。
  - `make deploy` = ssh jd + `git pull` + `scripts/deploy.sh`:npm ci 因 lock 与子依赖 esbuild 版本不匹配失败,自动 fallback `npm install --no-save`;`vite build` 3826 modules 产出 28 个文件,含 P7/P8 新构件(Dashboard/LhbPnl/Signals/Robots/StockLink)。
  - `systemd unit 未安装` → `deploy.sh` 跳过 restart。手动 `ssh jd pkill uvicorn + nohup .venv/bin/uvicorn` 拉起新进程 pid 4054433(老 pid 4040791 停)。
  - 冒烟 11 端点全 200:`/healthz` / `/` SPA / `/api/dashboard` / `/api/lhb?limit=1` / `/api/reports/weekly` / `/api/backtest/history`(返回 {total:0, rows:[]})/ `/api/kline/daily`(返回 20 条真实 K 线)/ `/api/kline/weekly` / `/api/kline/minute`(返回 empty_reason=no_source_table)/ `/api/robots` / `/api/lhb/seats`。
  - TODO(非阻塞):`scripts/install_units.sh` 装 `stock_analysis_api.service`,让 `make deploy` 自动 restart 而不是依赖手动 pkill。
