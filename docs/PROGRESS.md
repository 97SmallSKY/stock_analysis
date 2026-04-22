# 项目真实进度 (Single Source of Truth)

> 本文档是项目进度的唯一事实源。README.md 第7节 和 IMPLEMENTATION_ROADMAP.md 仅作历史计划参考,与本文冲突时以本文为准。
>
> **最后更新**:2026-04-22(STO-61 全量校对 by Dev)
> **上次全量校对**:2025-04-18(陈旧约 1 年,期间多个模块已完成,PM Tick #6 发现脱节后由 Dev 重新核对)

## 状态图例

- ✅ 已完成并验证
- 🟡 部分完成 / 待验证
- ⚠️ 存在已知问题
- ❌ 未开始
- ⛔ N/A(架构决策后不再需要)

## 模块进度(2026-04-22 校对)

| 模块 | 状态 | 说明 |
| --- | --- | --- |
| 数据采集 (akshare/tushare/sina/crawler) | 🟡 | `data_gateway/circuit_breaker.py` 熔断已有;**显式 retry(tenacity)未引入**,靠 circuit breaker 的 cooldown 降级 |
| 因子计算 / 评分 | ✅ | `backend/scoring/lhb_score.py` 82% / `runner.py` 94%;`domain/lhb/aggregator.py` 81%(STO-8 补强 daily_job 50%→89%、scheduler 67%→95%) |
| API (FastAPI) | ✅ | 路由齐全,**鉴权/限流已接**(STO-6:`core/auth.py` API Key + slowapi,`AUTH_ENABLED=false` 平滑上线) |
| 调度 (scheduler) | ✅ | **已嵌入 `stock_analysis_api.service`**(uvicorn 单进程,`RUNTIME_ENABLE_INTRADAY=1` / `RUNTIME_ENABLE_WEEKLY=1`);独立 `stock_analysis_scheduler.service` 已弃用(`install_units.sh` LEGACY_UNITS) |
| Nginx 部署 | ⛔ N/A | P5.1b 架构决定:FastAPI `StaticFiles + SPA fallback` 直接托管 `frontend/dist`(端口 8000),不再过 nginx(Ops self-probe 已确认) |
| 前端 | ✅ | Vue 3 + AntdV + AG Grid,8+ 页面(Dashboard / Signals / LhbPnl / LhbList / Robots / Backtest / BacktestRun / Reports / DataSourceMgr);ADR-0002 决定 UI 栈不迁 |
| 监控/日志 | 🟡 | `core/logging.py` W11-3 已接 loguru + 脱敏;`core/metrics.py` 已就位;**Prometheus 拉取 + Grafana 看板未部署**(内网优先级低) |
| 部署 (systemd) | ✅ | `scripts/install_units.sh` + 3 个 unit(api/daily/smoke)+ 2 个 timer;`deploy.sh` 流水线(清代理→pip→前端 build→migration→systemctl)已稳定 29+ 次 |
| 测试 / 覆盖率 | ✅ | pytest 789 passed,TOTAL 78.84%(门槛 75%);前端 vitest 139 tests |

## 下一步优先级(2026-04-22 重拍)

按 Linear 当前 Todo 列表(priority 降序 + createdAt 升序),与真实 Issue ID 绑定:

1. **STO-7** (High) — scheduler systemd 恢复验证(AC 重写后:**验证 `systemctl restart` / reboot 后 APScheduler job 正确重新注册**,而非新建 unit;需 Ops 协作跑 journalctl)
2. **STO-61** (High) — 本 issue · PROGRESS.md 全量校对(进行中)
3. **STO-62** (Medium) — 前端 limit 参数边界化(LhbPnl=1000 恰撞后端 le=1000,任何边界微调会触发 422)
4. **STO-58** (Medium · Sec) — CORS `allow_origins=*` 过宽,配合 STO-6 鉴权收紧
5. **STO-57** (Medium) — Tushare 长期 DOWN,确认 token / 降优先级

已完成(从旧版优先级移除):
- ~~#1 API 鉴权/限流~~ → STO-6 In Review(ready to deploy)
- ~~#2 scheduler 接入 systemd~~ → 已嵌入 api.service(STO-7 Dev 循环发现 AC 原文作废,PM 重写 AC 为验证而非新建)
- ~~#3 核心因子单测~~ → STO-8 Done(domain/lhb daily_job/scheduler 补强,TOTAL 覆盖率 78.84%)
- ~~#4 数据采集重试~~ → 降级为 🟡(circuit_breaker 已兜底,显式 retry 未引入,暂未列入优先级)

## 近期变更日志

- **2026-04-22 STO-61**:本文档全量校对(PM Tick #6 发现模块表陈旧约 1 年)。API 从 🟡→✅(STO-6 已 test-passed)、调度 ⚠️→✅(STO-7 发现已嵌入 api.service)、Nginx ❌→⛔ N/A(P5.1b 架构决定不用)、日志 ❌→🟡(loguru 已接,Prometheus/Grafana 未部署)。下一步优先级从 4 条裁为 5 条,每条绑定真实 Linear issue ID。加「最近校对日期」行,防止再次漂移。
- **2026-04-22 STO-59**:`scripts/ai-digest.py` 补 `bootstrap()` + `trust_env=False` session,修复 CLAUDE.md §9.3 铁律违规(jd 代理劫持 Linear API 会静默失败)。全项目 grep 0 条裸 `requests.*` 调用。789 passed / 78.84%。
- **2026-04-22 STO-56 🚨Urgent**:SPA fallback 路径遍历漏洞修复。`backend/api/main.py::_spa_fallback` 的 `dist / path` 拼接未校验路径是否逃出 dist,raw HTTP 可读 `/../package.json` / `/../../backend/config.py` / `/../../.env` 等敏感文件。修复:`.resolve().is_relative_to(dist_resolved)` 校验 + 越界兜底 index.html。新增 `tests/test_static_security.py` 8 case(关键技巧:raw ASGI scope 绕过 httpx URL 归一化)。覆盖率 78.83%;789 passed。详见 DEVLOG.md。
- **2026-04-22 STO-8**:domain/lhb 核心模块单测补强。新增 `tests/test_lhb_ingestor.py`(21 case)+ `tests/test_lhb_seats_only.py`(15 case)+ `tests/test_lhb_scheduler_loop.py`(7 case),共 43 case。覆盖率 delta:`daily_job.py` 50%→89%、`scheduler.py` 67%→95%、`ingestor.py` 51%→全覆盖;全项目 TOTAL 75.79%→78.53%。767 passed。详见 DEVLOG.md。
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
- **2026-04-20 P10.1–P10.5**:龙虎榜 name 透出 + Drill 空态美化 + Signals 多维度。
  - P10.1 `/api/lhb` + `/api/lhb/seats` JOIN `stock_info` 透出 `name`,后端单测覆盖 happy / empty / join-missing。
  - P10.2 新增 `StockLink.vue` 支持 `name` prop(code · 简称一行),Dashboard / Signals / LhbPnl / Robots / Backtest 五页统一接入。
  - P10.3 `StockDrillModal` 分钟 K 空态改造:120px 细体大号「无」+ 小字原因;2 个组件单测覆盖 minute / daily 空态双分支。
  - P10.4 `LhbList` 席位抽屉美化:`rank_no` 圆角徽章(rank-1 红 / 2 橙 / 3 金 / 其他灰),`net_amount` 正负染色 + 加粗;`rankClass` 提取 `utils/format.ts` 并附 7 分支单测。
  - P10.5 `Signals` 多维度分析:新增 `utils/signals_agg.ts` 纯函数(`scoreBuckets` / `netBuckets` / `dailyBuckets` / `summarize`),页面右侧统计卡补「涉及股票 / 净买入均值」、下方新增 3 张维度卡(评分段 / 净买入区间 / 分日数量)均用水平进度条展示占比(同页占比归一);9 个聚合单测 + 现有 46 个,vitest 10 files / 55 tests 全绿,vue-tsc 0 error。
  - P10.6 jd 部署 + smoke:`git push` c501d96 → jd,`make deploy` 成功(npm install fallback + vite build 28 files);systemd 未装,手动 `setsid .venv/bin/uvicorn` 拉起 pid 4151017;11 端点 smoke 全 200(含 kline/daily/weekly/minute + robots + lhb/seats)。数据核对:`/api/lhb?limit=3` 返回 `000581.SZ 威孚高科`、`000586.SZ 汇源通信`、`000711.SZ ST京蓝` — `name` 字段真实透出;`/api/lhb/seats?trade_date=2026-04-17&ts_code=000581.SZ` 返回 5 行席位(rank 1/1/2/2/4,方向 BUY/SELL 并存,broker_name「机构专用 / 中泰证券 / 深股通专用」真实生产数据)。
- **2026-04-20 P12**:机器人手动添加。
  - 后端:`POST /api/robots/create`(Pydantic `CreateRobotReq`:`robot_id?` / `cash` / `max_positions` / `per_position_pct` / `enabled`),`robot_id` 未传自动分配 `max(reg)+1`;冲突返回 409,参数越界返回 422;3 个 e2e 单测覆盖 auto-id / explicit-id 409 / bad-params 422。732 passed。
  - 前端:`api/robots.ts` 新增 `createRobot`;`Robots.vue` 顶部「新建机器人」按钮 + `<a-modal>` 表单(机器人 ID 留空自动分配 / 初始现金 / 最大持仓 / 单仓占比 / 立即启用),提交后即插入列表头并弹 `message.success`;`api/robots.test.ts` 5 个单测覆盖 list/status/create/4 个动作端点。vitest 11 files / 60 tests 全绿,vue-tsc 0 error。
- **2026-04-20 P13**:回测页独立路由(保留 `Backtest.vue` 策略对比原 Tab 不动,新增 `/backtest-run` 页 `BacktestRun.vue`)。
  - 新增 `utils/backtest_history.ts` 纯函数 `summarizeHistory`(total / success / successRate / avgReturn / bestSharpe),3 个单测覆盖空数组 / null 过滤 / 正常聚合。
  - `BacktestRun.vue` 顶部 4 个 KPI (`a-statistic`:总任务 / 成功率 / 平均收益 / 最佳 Sharpe,颜色依赖值正负),保留 Backtest.vue 历史 Tab 原列定义;搜索头部加「去策略对比」按钮跳 `/backtest`;`router/index.ts` 注册 `/backtest-run`。vitest 12 files / 63 tests 全绿,vue-tsc 0 error。
- **2026-04-20 P14**:LHB 席位金额单位修复(单位 = 万元,不是元)。
  - **发现**:AkShare / `parser.py:32` 注明 `buy_amount: float = 0.0  # 万元`,但前端一直用 `fmtAmount` 把数值当元处理,导致 `5811.81 万` 被误显示成 `5811.81`。
  - **`utils/format.ts`** 新增 `fmtAmountWan(v)`:输入即万元,绝对值 ≥ 1e4 万(= 1 亿)转 `x.xx 亿`,否则 `x.xx 万`;4 个单测覆盖 null / 正常 / 边界 9999万 vs 10000万 / 负值。
  - **替换 5 个页面**:LhbList(AgGrid header 标注「买入 (万) / 卖出 (万) / 净额 (万)」+ drawer bodyCell)、LhbPnl(buy_sum/sell_sum/net_sum)、Signals(net_amount/netSum/netAvg)、Dashboard(Top1 net_amount + 龙虎榜表 net_amount)、Reports(lhb_top_net.net_amount)。Robots / Backtest / Reports.net_pnl 维持 `fmtAmount`(单位是元)。
  - **`signals_agg.ts`** `netBuckets` 的阈值从 `/1e8`(亿元)改 `/1e4`(万→亿),同步更新 6 行单测的构造数据(`net_amount: 2e8 → 2e4` 等)。
  - 全绿:vitest 12 files / 67 tests,vue-tsc 0 error。
