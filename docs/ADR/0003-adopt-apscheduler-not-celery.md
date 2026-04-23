# ADR 0003:用 APScheduler 替代 Celery 做调度

- 状态:已生效(追认)
- 日期:2026-04-23(决策发生在 P5.2 早期,本文为 STO-60 补录)
- 相关:docs/DEPLOYMENT.md §1 架构速查、STO-7(systemd 恢复验证)

## 背景

项目初期设计稿(`docs/重写方案_v2.md` / `docs/IMPLEMENTATION_ROADMAP.md`)计划用 **Celery + Redis broker** 做异步任务(回测 / 数据同步 / 报表)。落地时评估后放弃:

1. **调度场景简单**:实际需要的 job 只有「每日 18:40 灌库」「周一 09:00 报表」「盘中 10s 快照」三类 cron 驱动,没有真正意义上的任务队列 / 多消费者负载均衡需求。
2. **运维复杂度不划算**:Celery 要额外拉起 `stock_analysis_worker.service` + `beat.service`,加上 broker(Redis)状态管理,进程数 2→4,监控/部署/重启心智负担显著升高。
3. **进程内状态共享更顺**:`IntradaySnapshotScheduler` 的 adapter 池、熔断器、`failed_registry`、Redis pubsub 订阅都是**进程级单例**;跨进程要么走序列化 IPC,要么每个 worker 重新初始化一次,复杂度指数级上升。
4. **单机部署,单 worker 足够**:jd 生产单 uvicorn 进程,无水平扩展需求;APScheduler 嵌入 uvicorn 同进程,`workers=1` 天然避免多副本重复调度。

## 决策

- 调度层用 **APScheduler** 的 `BackgroundScheduler`,嵌入 `stock_analysis_api.service` 的 uvicorn 进程内(`backend/core/runtime.py` 统一拉起)。
- 通过环境变量开关:`RUNTIME_ENABLE_INTRADAY=1` / `RUNTIME_ENABLE_WEEKLY=1` 控制各调度是否启用(见 unit 文件)。
- **不引入** Celery / broker / worker 进程。Redis 保留但只作缓存 + pubsub,不作 broker。
- **明确不做** `stock_analysis_scheduler.service` 独立进程(见 `scripts/install_units.sh::LEGACY_UNITS`,已主动清理 legacy unit 文件)。

## 后果

正面:
- 部署链路单 unit 单进程,`systemctl restart` 一次到位;systemd reboot 自动拉起所有调度,无调度丢失风险(已 STO-7 验证)。
- 代码与 job 共享进程内对象(adapter / 熔断器 / Redis 连接),无 IPC。
- 本地开发 `uvicorn --reload` 就能跑到完整调度,不需要额外进程。

负面:
- **无水平扩展**:多 worker 会重复订阅 Redis pubsub + 重复调度,所以 `workers=1` 是架构硬约束。未来若需扩展,要么拆独立调度进程(重大变更,需新 ADR),要么改分布式锁 + 抢占式执行。
- **无重试队列**:任务失败就失败,不会自动重入队列。当前通过 `failed_registry` + 熔断器 + 下一次调度自然重试覆盖,但不是严格意义的 durable queue。
- **长任务阻塞**:APScheduler job 跑在线程池,和 uvicorn 异步 loop 共享进程资源。如果某个 job 跑很久(> 1 min)要注意 CPU 竞争。

## 何时重新评估

出现以下任一信号时重新评估 Celery 引入必要性:

- 单 job 真实执行时间 > 5 min 且需要隔离
- 业务扩到多 worker 水平扩展
- 需要 durable 任务队列(任务失败必须落盘重试,而不是下个周期覆盖)
- 多租户调度(不同租户优先级隔离)

## 后续行动

- (已完成)`requirements.txt` 注释引用本 ADR(STO-60 同步修)
- (已完成)`docs/DEPLOYMENT.md` 架构速查列出「不使用 Celery」
- (已完成)STO-7 / STO-54 等 issue 里明确「单 unit 单进程」铁律写入 `deploy/systemd/stock_analysis_api.service` 内注释
