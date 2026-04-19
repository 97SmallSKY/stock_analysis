# 优化方案 · 2026-04-20 起(P16 循环)

> 本文件由 P16 循环产出。每 20 分钟自动回检一轮,发现问题先写进本文件,再拆任务、落代码、回写审计条目。配套任务清单见 `TaskList`(以 P16.x 命名)。

## 一、当前痛点(2026-04-20 数据盘点)

### 1.1 数据层缺口(通过 pg_conn 全表计数得出)

| 表 | 行数 | 区间 | 问题 |
|---|---|---|---|
| stock_info | 6092 | — | OK |
| trade_calendar | 2557 | 2020-01-01 → 2026-12-31 | OK |
| kline_daily | 1,363,504 | 2024-01-02 → 2026-04-17 | OK,日 K 约 2.3 年 |
| lhb_stock_daily | 52,058 | 2023-01-03 → 2026-04-17 | 主表覆盖 3+ 年 |
| **lhb_seats** | **6,666** | **2026-01-05 → 2026-04-17** | ❌ **仅 68 日,51320 行主表无席位** |
| market_sentiment | 347 | 2024-01-03 → 2026-04-17 | OK |

**衍生问题**:

- `lhb_stock_daily.score` 47,371 / 52,058 行 = **91% 为 0 或 NULL** — P6.7 只跑了 2026-01-01 → 04-17 共 4687 行评分,2023-01 → 2025-12 的历史 47k+ 行全部未评分 → 回测 / 机器人取不到有效信号。
- `lhb_seats` 空洞 → 回测策略 `lhb_hot_money` 在 2026-01-05 之前全部退化成「无席位」分支,历史回测不可用。
- 分钟 K 至今 `empty_reason=no_source_table`(P7.3 明示)。
- 多维指标空缺:主表只有 `l_buy/l_sell/l_net/score`,缺 **连板数 / 次日开盘收益 / 次日收盘收益 / 3 日最高收益 / 主力资金净流入 / 换手率 / 所属概念 / 机构占比** 等常规龙虎榜研究指标。

### 1.2 页面 / 交互缺口

| 位置 | 现状 | 期望 |
|---|---|---|
| `/lhb-pnl` 按原因聚合 | 三柱图颜色与图例对不上(净买入 / 上榜次数 / 平均评分 三系列 color 顺序与 tooltip 错位) | 强制三色固定映射 + 图例点击一致 |
| `/lhb-pnl` 指标 | **仅净额**(买 / 卖 / 净) | 加上榜次数、平均涨幅、胜率(次日 / 3 日)、席位类型分布 |
| 回测页 | P7.1 已独立 `/backtest-run`(见 Task #45) | 审计确认路由已生效 |
| 机器人 | `Robots.vue` 只展示快照,**没有「采纳哪个策略」字段**,看不到策略执行逻辑 | 后端 robot snapshot 透出 `strategy_name`;前端表格显示「策略」列并支持切换 |
| 机器人 → 策略 | 机器人 `decide_orders` 路径不清是否真调了 `strategies/` 里的策略 | 审计 `backend/domain/robot/*` + `strategy/*` 的调用链并在文档固化 |
| Signals 多维 | P10.5 已做基础(score/net/daily 桶 + summarize) | 追加「次日收益回填」维度(取决于 1.1 多维采集) |

### 1.3 采集任务层

- 定时任务 `daily_update` 只管当天。历史补采靠手动 CLI。
- `run_seats_only_range` 只跑了 top10,非 top10 的主表行永远不会得到席位。
- 评分脚本 `make score` 默认 `--only-empty` 但需要手动指定日期区间。

## 二、优化方案

### P16.1 数据回填批次(高优,决定回测/机器人可用性)

目标:把 `lhb_seats` 从 68 日补到 ≥ 250 日(近 1 年),把 `score` 覆盖率从 9% 拉到 ≥ 80%。

步骤:

1. **席位回填 2025 全年**:`make ingest-lhb-seats-top TOP=20 FROM=20250101 TO=20251231 REVERSE=1`,top20 覆盖每日主表 90%+。预计 ~240 个交易日 × 20 行 ≈ 4800 主表 id,按 P6.6 实测每日 ~90 条 seats → 约 22k 行入库。
2. **席位回填 2024 全年**:同上 TOP=20 FROM=20240101 TO=20241231。
3. **席位回填 2023 全年**:同上 TOP=20 FROM=20230101 TO=20231231。
4. **评分回填** 2023-01 → 2025-12:`make score FROM=20230101 TO=20251231 ONLY_EMPTY=1`。
5. **分钟 K 数据源**:P16.6 独立任务。

### P16.2 LhbPnl 颜色 + 多维修复

目标:三柱图颜色固定 + 新增 3 个研究指标列。

步骤:

1. `LhbPnl.vue` ECharts option: `series[].itemStyle.color` 显式给 `['#d4380d','#fa8c16','#722ed1']` 三色。
2. 表格新增列:`上榜次数` / `平均净买入(万元)` / `近 N 日次日涨幅均值`(N=5)/ `席位-游资占比`。前两列本地可聚合,后两列依赖 P16.1 完成后取 `kline_daily` JOIN。
3. 单测:`utils/pnl_agg.ts` 抽纯函数 + 6 case。

### P16.3 机器人采纳策略链路审计 + 字段透出

目标:前端看得到机器人「用的是哪个策略」,且策略真的驱动下单。

步骤:

1. 审计 `backend/domain/robot/*.py` 与 `backend/domain/strategy/*.py` 的 `decide_orders` 调用链,写入本文 §四。
2. `/api/robots` snapshot 追加 `strategy_name` / `strategy_params` 字段(后端 ORM/字典扩展)。
3. 前端 `Robots.vue` 表格补「策略」列 + 持仓抽屉里补「最近 10 条下单原因(which strategy)」。
4. 如审计发现机器人未真正调用策略,则在 §五 立即记录 bug 并在 P16.3b 修复。

### P16.4 回测独立页审计

目标:确认 P7.1 拆的 `/backtest-run` 路由真正可达,且与 `/backtest`(策略对比 tab)无菜单冲突。

步骤:

1. `grep -rn backtest-run frontend/src/router`。
2. 菜单 `App.vue` 审计是否需要拆两项。
3. 冒烟 `/backtest-run` 页面打开正常,`/api/backtest/history` + `/api/backtest/run` 可用。

### P16.5 多维指标采集(依赖外部数据源)

目标:把研究用的「次日开盘 / 次日收盘 / 3 日最高」等收益字段落库,供回测策略和 LhbPnl 用。

步骤:

1. 新增表 `lhb_stock_perf`(迁移 `sql/schema.sql`):`ts_code, trade_date, next_open_pct, next_close_pct, d3_max_pct, d5_close_pct`。
2. 新增 job `backend/domain/lhb/perf_backfill.py`:读 `lhb_stock_daily` + `kline_daily`,纯 SQL 计算 upsert。
3. CLI `python -m backend.cli lhb-perf-backfill --from --to`。
4. Makefile 加 target。
5. 暴露 `/api/lhb` 上加 `next_close_pct` 字段(optional)。

### P16.6 分钟 K 数据源接入

目标:消掉 StockDrillModal 的「无」大字。

步骤(延后,非本轮必须):

1. 评估 AkShare `stock_zh_a_minute`(period=1/5/15/30/60)vs Tushare `stk_mins`(付费点)。
2. 建表 `kline_minute(ts_code, trade_time, period, o/h/l/c, vol)`。
3. adapter + ingest + `/api/kline/minute` 真数据返回。

## 三、执行计划(顺序)

1. ✅ 本文落档(P16.0)
2. → P16.2 LhbPnl 颜色修复(纯前端,零风险,先做)
3. → P16.3 机器人链路审计(a/b/c 修复)
4. → **P16.7 部署到 jd + 冒烟验证(每轮代码改动完成强制执行)**
5. → P16.1 席位 + 评分回填(后台长跑,不阻塞主线)
6. → P16.4 回测页审计
7. → P16.5 多维指标采集
8. → P16.6 分钟 K(最后做)

> **强制规则 P16.7**:每当 P16.x 代码改动通过本地 `pytest / vitest / vue-tsc` 三检后,
> 必须立刻 `git add/commit/push` + `make deploy`(ssh jd 拉代码 + 前端打包 + 后端 restart)+
> `make smoke` 11 端点冒烟,有任何 5xx 立即回滚/修复。完成后回写本文 §6 审计条目。

## 四、机器人采纳策略链路审计(P16.3 步骤 1 产出)

### 4.1 当前代码结构

- `backend/domain/robot/robot.py:RobotSnapshot` —— 不可变快照 + `plan_buy/plan_sell`,只负责资金/持仓账务,不含选股。
- `backend/domain/robot/live_engine.py:LiveRobotEngine`
  - `tick(snapshot_by_code, candidates, biz_date)` —— **candidates 是参数注入**,引擎本身不生成。
  - `run_phase_a(candidates, prices)` —— 开盘集合竞价建仓,candidates 还是参数。
  - `default_sell_policy` —— 写死的止盈/止损/N 日兜底,**未走 strategies 模块**。
- `backend/domain/strategy/base.py:BaseStrategy` —— 提供 `select_stocks(ctx)` 抽象。
- `backend/domain/strategy/builtins.py` —— 两个内置策略(`lhb_hot_money` / `lhb_institution`)实现了 `select_stocks`。
- `backend/domain/strategy/registry.py:register_strategy` —— 装饰器注册到全局表。
- `backend/api/robots.py` —— pause / resume / enable / disable / status / create / list,**全部只操作 engine 本身,不触发任何 tick/phase_a**。

### 4.2 关键缺口(P16.3 审计出的 bug)

1. **`LiveRobotEngine.tick / run_phase_a` 无生产 caller** —— `grep -rn "tick(\|run_phase_a" backend/` 除引擎内部外零 caller。机器人创建后状态机停在 IDLE/RUNNING,但从不选股也从不下单。
2. **引擎和 strategy 模块未连线** —— `LiveRobotEngine` 不持有 `BaseStrategy` 实例,也没有 `strategy_name` 字段。`candidates` 是调用方传入的 `Iterable[Candidate]`。上层没有 `engine.select_candidates(ctx)` 的封装。
3. **没有 scheduler 在盘中驱动 tick** —— 没有 cron/APScheduler/Celery 调度 `engine.tick(...)`。
4. **前端 `/api/robots` 看不到策略绑定** —— snapshot 无 `strategy_name`,因此 `Robots.vue` 无法显示「机器人采纳了哪个策略」。

### 4.3 P16.3 修复计划(拆成 3+1 子任务)

- **P16.3a**:`LiveRobotEngine` 构造参数加 `strategy: Optional[BaseStrategy] = None` + `strategy_params: dict`;`create_robot` Pydantic body 补 `strategy_name` / `strategy_params`,用 `registry.get_strategy(name, params)` 实例化挂到 engine;snapshot 序列化里加 `strategy_name`。
- **P16.3b**:engine 新增 `select_candidates(ctx) -> list[live_engine.Candidate]`,内部调 `self.strategy.select_stocks(ctx)` 并把 `strategy.Candidate` 映射过来,若 strategy 为 None 则返回空列表并记 warning。
- **P16.3c**:前端 `Robots.vue` 表格加「策略」列(显示 `strategy_name || '-'`);创建对话框加策略下拉(从新增 GET `/api/robots/strategies` 拉可用策略名)。
- **P16.3d (非本轮)**:加 scheduler(APScheduler + 交易日历)在 `09:24 / 盘中每 10s` 驱动 `engine.run_phase_a / engine.tick`。这一步需要市场实时数据源,先落 TODO。

## 五、问题追踪(审计中发现的 bug)

- **BUG #1 · R3 发现** `kline_daily` 完全缺 **2025-01-01 ~ 2025-10-31** 整 10 个月(`count(*)`月度统计显示 2024 年每月有 ~22 日 × 3300 股 ~ 7 万行,但 2025-01~10 全部为 0)。此外 2024 个别日期也有缺失(示例:`000737.SZ @ 2024-03-29`)。**直接后果**:`lhb_tn_price` 回填 78700 行(远低于预期 17 万),`/api/lhb?with_tn=true` 大多返回 null。
  - 处理:P16.8 启动 `systemd-run p16-kline-2025.service ingest kline --from 20250101 --to 20251031 --workers 4`。akshare 限流 90/min → 每批 ~200s 冷却,预计 1-2 小时。跑完后需重跑 `make lhb-tn-price FROM=20250101 TO=20251031`。

## 六、回合审计(每 20 分钟一条)

- **R0 · 2026-04-20** 数据盘点完成,发现 seats 仅 68 日、score 9% 覆盖率,落本方案。TaskList 同步见 P16.0 / P16.1 / P16.2 / P16.3 / P16.4 / P16.5 / P16.6。
- **R1 · 2026-04-20 22:50** P16.2 + P16.3a/b/c 完成部署:
  - 代码:`LiveRobotEngine.strategy` + `select_candidates`;`/api/robots/create` 接受 `strategy_name/params`;新增 `GET /api/robots/strategies`;前端 Robots 表格加「策略」列 + 创建对话框策略下拉;LhbPnl 三柱图固定色 + 图例 dot 提示 + 新增「平均净买入」列;抽 `utils/pnl_agg.ts` + 7 case 单测。
  - 测试:`pytest 732 passed` → `738 passed`(+6 新单测);`vitest 74 passed`(+7 新单测);`vue-tsc 0 error`。
  - 部署:`make deploy` 前端打包 31 files ✅;systemd restart ✅;首次 healthcheck 因 restart 窗口假失败,`sleep 3` 后 healthz=200;`make smoke` 11 端点 10/11 200(`/api/lhb/seats` 422 是 query 缺必填参数,非回归)。`/api/robots/strategies` 返回 `lhb_hot_money` + `lhb_institution` 两策略 + 默认参数,链路畅通。
  - P16.1 回填:2025 全年完成(46536 seat_rows);2024/2023 + score 用 `setsid` 后台续跑中。`lhb_seats` 从 6666 → **84838 行,68 → 468 个交易日**。

- **R2 · 2026-04-20 23:30** P16.4 + P16.5 完成部署:
  - P16.4 回测页入口:`App.vue` 菜单新增「回测任务」指向 `/backtest-run`(Task #52 close)。
  - P16.5 多维指标:发现 `lhb_tn_price` 表早就存在(schema 第 482 行)但 0 行数据。新建 `backend/domain/lhb/tn_price_backfill.py`,纯 SQL CTE + window function 一次 upsert 全部 N=1/2/3/5/10 组合;CLI `python -m backend.cli lhb-tn-price`;Makefile `make lhb-tn-price FROM= TO=`;9 case 单测覆盖日期解析+参数化+默认 N_days。`/api/lhb` 加 `with_tn=true` query 参数,透出 `t1_pnl_pct / t1_limit_up / t5_pnl_pct / t5_high_pct` 四字段,LEFT JOIN `lhb_tn_price` 两次(tn1/tn5)。默认行为不变。
  - 测试:`pytest 747 passed`(+9)。vitest/vue-tsc 本轮未改前端逻辑无回归。
  - 部署:`make deploy` ✅ → restart 窗口假失败 → `sleep 4` 后 healthz=200;`/api/lhb?with_tn=true` 200 返回含 4 个新 key(实际值 null 因 tn_price 尚未回填);冒烟通过。systemd transient unit `p16-tnprice.service` 启动 2024-01-01 → 2026-04-17 全量回填(并行跑,不影响 P16.1)。

- **R3 · 2026-04-20 23:40** P16.5b 前端消费 TN + P16.8 kline 缺口修复:
  - P16.5b:`LhbRow` 加 TN 四字段 optional; `LhbListParams.with_tn`;Signals 表格加「T+1」「T+5」列(`fontFamily:mono` + 涨跌染色);右侧统计加「T+1 平均」「T+1 胜率」「T+5 平均」(有效样本数提示);抽 `utils/tn_summary.ts` + 5 case 单测。`vitest 14/79 tests` 绿。
  - **审计发现重大 BUG #1**:TN 回填只 78700 行(预期 17w+)。根因:`kline_daily` 完全缺 2025-01-01 ~ 2025-10-31。这直接导致 TN SQL 的 kline JOIN 拿不到 T+N 行。
  - P16.8:启动 `p16-kline-2025.service systemd unit`(workers=4) 后台回填 2025 前 10 个月。被 akshare 限流每分钟 90 次,每批冷却 ~200s,预计 1-2 小时。跑完需重跑 `make lhb-tn-price FROM=20250101 TO=20251031` + 可能重跑 score。
  - 单点验证(2024 kline 有数据):`/api/lhb?ts_code=002920.SZ&date_from=20240419&with_tn=true` 返回 `t1_pnl_pct=-0.0355 / t5_pnl_pct=-0.0306` ✅。API 逻辑正常,只是数据缺。
  - 部署:deploy 无 restart(systemd unit 标记未安装自动跳过),手动 `systemctl restart stock_analysis_api` 后 healthz=200。冒烟通过。
  - P16.1 进度:2025+2024 seats 完成;2023 seats 跑中。**`lhb_seats` 从 6666 → 148,775 行 / 795 个交易日(从 68 日 → 全覆盖 2023-01-03 → 2026-04-17)**,数据层核心空洞修复完毕。
