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
