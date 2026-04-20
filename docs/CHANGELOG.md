# 开发日志 / Changelog

> 本文件是「特性介绍」页面(`/changelog`)的数据源。
> 每次完成修复或新增特性,按时间倒序在顶部追加一条。
> 条目格式:三级标题 `### YYYY-MM-DD · 类型 · 简要标题`,类型 ∈ {feature, fix, refactor, perf, docs, ops}

---

### 2026-04-20 · feature · 新增「日期范围快捷选择」组件

**是什么**:新增一个通用组件,提供 1 年 / 季度 / 6 个月 / 3 个月 / 1 个月 / 1 周 / 5 日 / 今天 共 8 个快捷按钮,**点一下就能把表格的日期范围切到对应区间**,不用手动选日期。

**为什么**:之前龙虎榜、决策信号、盈亏分析、策略对比、周报等表格的日期范围都要手动点日期选择器,操作繁琐。而且默认范围不统一,用户很容易看到错误的数据。

**交互规则**:
- 默认范围 = 一年前 → 今天
- 点快捷钮只改**开始日期**,结束日期保持不变(默认今天)
- 手动编辑日期则清除快捷钮高亮,按用户输入为准

**影响**:本次只上线通用组件 + 单元测试(24 条,全部通过)。接下来几次更新会把这个组件接入 5 个主要表格页面,届时所有表格的「默认一年 + 快捷切换」行为将统一。

---

### 2026-04-20 · feature · K 线支持查询可用日期范围(P19 页面升级开工)

**是什么**:新增接口 `GET /api/kline/daterange`,可以查询某只股票在 K 线表里**实际有数据的最早日期和最晚日期**。例如贵州茅台可能从 2020-01-02 到 2026-04-17 都有数据,平安银行可能只有最近两年。

**为什么**:之前前端 K 线图的日期选择器硬编码最早日期是 2026-01-20,不管哪只股票都这样,让人以为数据只有最近三个月。实际上数据库里存的日期范围因股而异。

**影响**:
- 后端接口已上线,前端 K 线组件接下来几天会接入(参见 docs/designs/2026-04-20-p19-page-ux-upgrade.md)
- 顺手修了一个部署日志上的小告警:每次 deploy 结尾健康检查会报「stock_analysis_api 未安装」(实际是活的),原因是检查命令在某些情况下误判,现已改为单 unit 精确探测,告警消失。

---

### 2026-04-20 · docs · Agent 工作手册 + 文档分层骨架

**是什么**:
- 新增 **Agent 工作手册** `docs/prompts/7x24_agent.md`,定义 24 小时自动运维 / 开发 Agent 的循环规则、修复流程、部署命令、红线约束
- 新增 3 个文档子目录分层:
  - `docs/designs/` 放**大工程的总体规划**(周期长、跨多模块)
  - `docs/optimizations/` 放**单点优化技术笔记**
  - `docs/ops/` 放**运维手册和事故复盘**
- 同步更新 `CLAUDE.md` / `README.md`,把新入口加进文档导航

**为什么**:之前所有改动都堆在 CHANGELOG 里,技术笔记和用户视角的记录混在一起。现在分清楚:
- **CHANGELOG**:说人话,面向用户
- **optimizations**:半技术半白话,面向开发者
- **designs**:完整工程设计,面向架构师
- **ops**:运维手册和事故复盘,面向运维

**影响**:后续新 Agent 接手时,第一时间能读到完整的工作手册,减少交接成本;CHANGELOG 条目会更通俗,方便非技术用户看懂每次更新修了啥。

---

### 2026-04-20 · fix · 首页情绪卡片不再空白:取最新「完整」一天而不是今天盘中

**现象**:今天盘中(A 股还没收盘)打开首页,「涨跌比 / 平均涨幅 / 中位涨幅 / 强弱势股」等卡片全部显示「-」或空白。

**原因**:系统每天抓一次市场情绪数据,但今日盘中只能拿到「涨停/跌停家数」,其余统计字段要等收盘后的日 K 线汇总算出来。首页取最新日期那条记录展示,于是看到了「半成品」。

**修复**:首页接口改成取最新一条**有完整统计数据**的记录,通常是昨日收盘。这样即使盘中数据还没跑完,首页展示的仍是完整信息。

**效果**:首页「涨跌比 0.74 · 平均 +0.21% · 强势 203 只 · 弱势 35 只」等字段都能正常显示。

---

### 2026-04-20 · fix · P18.13b 紧急修正:`total_stocks/up_count/down_count` 不入库 + Dashboard 实时聚合

**发现错误**:上一条 P18.13 把 `total_stocks/up_count/down_count` 加进了 `writer.upsert_market_sentiment.cols`,但 **DB 表实际没这 3 列**(只是 `api/dashboard.py` 硬塞 None 占位)!运行 insert 会 UndefinedColumn 报错。回填进程第一条就会爆。

**紧急止损**:
1. 立即停止回填进程(pkill)
2. `writer.cols` 去掉这 3 个字段(保留 `median_pct_change` 真实列)
3. `api/dashboard.py::_latest_sentiment` 从 `kline_daily` 实时聚合 `total_stocks/up_count/down_count`,按 `trade_date` 一次 COUNT 查询,+2ms 可忽略

**教训**:改动跨 writer + schema 时必须先验证真实 DB schema,不能假定 API 字段 = DB 列。

---

### 2026-04-20 · fix · market_sentiment 衍生字段 NULL 修复(\_is\_empty\_sentiment + \_merge\_db\_fill)🎯

**背景**:体检发现 `/api/dashboard` 的 sentiment 段里 11 个字段(`up_down_ratio`、`avg_pct_change`、`median_pct_change`、`strong/weak_stock_count`、`total_stocks`、`up_count`、`down_count`、`lhb_*`、`sentiment_change`)一直为 NULL,Dashboard 大面积空态。

**根因 3 层**:
1. **`_is_empty_sentiment` 判断错误**:用 `all(字段全 0/None)`,只要 akshare 返回了 `limit_up_count=71` 就不算空壳,于是不走 DB 反算。**结果**:limit_up/down 有值但 up_down_ratio/avg_pct_change 永远 None。
2. **`_calc_from_kline` 返回字段不全**:没有 `total_stocks/up_count/down_count/median_pct_change`,表里这些列始终 NULL。
3. **`writer.upsert_market_sentiment.cols` 缺列**:4 个新字段即使有值也不会落库。

**修复(方案 C)**:
- `_calc_from_kline`:SQL 加 `percentile_cont(0.5)` 计算 median,返回 dict 扩展到 `total_stocks/up_count/down_count/median_pct_change`
- 新增 `_merge_db_fill()`:外部源有数据但字段 None 时,从 DB 反算补齐(不覆盖已有值)
- `run()`:外部源有 rows 时也调用 `_merge_db_fill`(而不是只在完全空壳时兜底)
- `writer.cols`:补齐 4 个新字段

**测试**:+4 case(`_merge_db_fill` 四种分支)+ 调整 `_calc_from_kline` 的 3 个 case 适配新 row shape。pytest **760 passed**,coverage 75.79%。

**需要回填**:`python -m backend.cli ingest sentiment --from 20240101 --to 20260420 --force` 覆盖 347 天历史。

---

### 2026-04-20 · fix · `ingest_status.sh` 异常段 ANSI 转义清理

- 现象:最近 5 条异常输出里有 tqdm 进度条残留的 `\x1b[A` 光标上移码,显示为 `[A[A[A2026-04-20 ERROR...`
- 之前只过滤了 `%|` 和 `\[0m`,不完整
- 修复:加 `sed -E 's/\x1b\[[0-9;]*[A-Za-z]//g'` 清掉所有 ANSI 转义序列
- 纯监控输出改良,bash -n 通过

---

### 2026-04-20 · perf · LHB API 加 `skip_total` 消除深分页 COUNT 开销

- 体检发现 `GET /api/lhb?offset=10000` 耗时 2.2s,而首页仅 309ms
- 瓶颈:每次查询都跑 `COUNT(*) FROM lhb_stock_daily`(52K 行)+ `OFFSET 10000` 跳行
- 修复:新增 `skip_total: bool = False` 参数,True 时 `total` 返回 `-1`,不跑 COUNT
- 使用建议:前端翻页场景第 2 页后启用,即可 5~10x 加速
- 默认 False,**向后兼容**,不影响现有前端调用
- 单测 +2 case (skip_total=true / default false),pytest 756 passed,覆盖率 75.81%

---

### 2026-04-20 · fix · Dashboard 涨跌家数卡片 fallback 改 `'-'` 避免误导

- 体检发现 `market_sentiment` 表的 `up_count/down_count` 大量 NULL(任务 #11 待修)
- 之前前端 fallback 是 `?? 0`,数据缺失时显示「0 / 0」,用户会误以为「今天全市场没涨跌」
- 改为 `?? '-'`,清晰传递「数据缺失」语义
- 涨停/跌停卡片 `limit_up_count/limit_down_count` 有真实数据,保持 `?? 0`

---

### 2026-04-20 · feature · Changelog 页面加「返回顶部」按钮 + 刷新按钮 icon 写法改良

- 条目多时滚到底部可一键回顶(antd `a-back-top`,visibility-height=240)
- 刷新按钮从 `:icon="h(ReloadOutlined)"` 改为 `<template #icon>` 插槽写法,更符合 antd 惯用法
- 顺手去掉 `h` 的 import
- vue-tsc clean,vitest 88 passed

---

### 2026-04-20 · fix · `ingest_status.sh` 业务日志回退扫描(Top5)

- 现象:循环 #4 巡检时,`p16_9_chain.log` 每分钟心跳 `waiting p16-kline-2025...` 让它成为「最活跃」日志,于是脚本只 grep 它 → 「阶段里程碑」「最近异常」两段全空,丢失 kline 真实进度
- 修复:新增 `find_business_log()`,若活跃日志无业务关键词,从 Top5 依次回退扫描;输出区分「活跃日志」与「业务日志」
- 效果:心跳/噪音日志不再劫持监控焦点

---

### 2026-04-20 · fix · `deploy.sh` service 探测改用 `systemctl cat` 避免误报

- 旧实现:`systemctl list-unit-files | grep -q "^$svc"` 在 `set -o pipefail` + 全量 unit 输出下偶发误判,打印「Skip … 未安装」,但紧接着 healthcheck 却显示 `active ✓`
- 新实现:`sudo systemctl cat $svc >/dev/null 2>&1`,针对单个 unit 的轻量探测,不存在时 exit≠0
- 收益:deploy 输出不再矛盾,后续观察 1~2 轮部署无误报即关闭

---

### 2026-04-20 · perf · Changelog API 显式按日期倒序

- 之前依赖 md 文件内原顺序,一旦追加者没按顺序插入,时间线会跳乱
- 现在后端 `sort(key=date, reverse=True)`(稳定排序,同日期保留文件内顺序)
- 补单测 `test_api_changelog_sorted_by_date_desc`
- pytest 754 passed · coverage 75.78%

---

### 2026-04-20 · perf · `healthcheck.sh` 加重试(3×1s)消除 deploy 时序假失败

- 现象:`deploy.sh` 末尾跑 healthcheck,systemctl 刚 restart 完 API,uvicorn/PG 还没稳,一次性探测报 `postgres ✗ / API ✗ / SPA ✗`,但几秒后手工 curl 全部正常
- 修复:`check()` / `warn_check()` 共用 `_probe_with_retry`,默认 3 次 × 1s 间隔,可通过环境变量 `HEALTHCHECK_RETRY` / `HEALTHCHECK_RETRY_INTERVAL` 覆盖
- 影响:deploy 日志不再假红,真实失败依旧会在 3 次都失败后报告

---

### 2026-04-20 · fix · `daily.service` systemd 239 兼容性(CentOS 8)

- `StandardOutput=append:/path` 需要 systemd 240+,CentOS 8 的 systemd 是 239 → 两行被 systemd 直接忽略
- 现象:timer 已排程,但 service 跑完后日志丢失到 journal,看起来像「从未运行」
- 修复:改为 `StandardOutput=journal` + `SyslogIdentifier=stock_daily`,daily_update.sh 内部 tee -a 已经负责写 `logs/daily_update.log`,journal 只做兜底
- 验证:`journalctl -t stock_daily -n 100 --no-pager`

---

### 2026-04-20 · feature · 新增「特性介绍」页面

- 前端新增 `/changelog` 路由,顶部菜单「特性介绍」
- 页面渲染本文档(时间倒序 + 类型标签过滤)
- 数据源:后端 `GET /api/changelog`,直接返回本 md 的结构化解析结果

**动机**:之前每次修了什么、加了什么功能,只有 git log 和 PROGRESS.md 记录,用户难以直观感知迭代节奏。

---

### 2026-04-20 · fix · 灌库状态速览 `ingest_status.sh` 误报卡死

- 旧版只监控 `logs/full_ingest.log`,但实际 P16 K 线回填日志分流到 `logs/p16_kline_2025.log`,导致误报「静默 1832 分钟 · 疑似卡死」
- 新版改为扫描 `logs/*.log`,自动定位 mtime 最新的那个作为活跃日志,并列出 Top5 最近修改的日志文件

**事故教训**:监控路径硬编码 → 任务日志路径变化时监控失效。

---

### 2026-04-20 · ops · 灌库进度巡检建立 20 分钟循环

- 定时巡检:每 20 分钟自动 `ssh jd bash ~/stock_analysis/scripts/ingest_status.sh`
- 对比上一轮进度,判断长耗时回填是否完成/卡死
- P16 K 线 2025 全年回填进入尾声(~96%),预计 20 分钟内完成

---

### 2026-04-19 · fix · P18.2 Signals 评分详情 [object Object] 与 LhbList 列筛选误导

- Signals 页评分详情某字段对象被直接字符串化
- LhbList 列筛选表现与用户预期不一致
- commit: `58f2d6e`

---

### 2026-04-19 · feature · P16 K 线数据回填 2025 全年

- 全市场 5504 只股票,2025-01-01 → 2025-10-31,workers=4 并行
- 数据源链路:akshare → sina → 自建 crawler,自动降级
- 北交所 92* 开头股票暂无可用数据源(三家都返回异常),待后续适配

---

### 2026-04-18 · perf · 覆盖率门槛抬升到 75%

- pytest `--cov-fail-under` 从 70 → 75
- aggregator 补 11 个 case,实际覆盖率 75.81%

---

### 2026-04-18 · ops · 事故:进程活但日志静默 = HTTP 卡死

- full_ingest 进程看起来在跑,但 10 分钟无日志输出
- 根因:某个 akshare HTTP 调用 socket 卡死,未触发超时
- 应急:`kill -9 <pid>` 让 full_ingest 进入下一阶段
- 长期:`ingest_status.sh` 增加心跳检测

---

### 2026-04-17 · refactor · ADR-0001 清理 SQLAlchemy / Alembic 死引用

- 统一使用 `psycopg2 + db/init/NN_xxx.sql` 版本化迁移
- 删除 `data_sources/` 旧包(已被 `backend/data_gateway/` 替代)
- `backend/ingest/lhb.py` 降级为薄壳,核心逻辑迁 `backend/domain/lhb/`

---
