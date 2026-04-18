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
