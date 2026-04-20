# 开发日志 / Dev Log

> 本文件记录**面向开发者**的技术细节:代码重构、性能优化、bug 根因、监控/部署基建改动等。
> 面向**用户**的新特性 / 页面改动在 `CHANGELOG.md`。
>
> 前端页面:
> - 「特性介绍」tab = `CHANGELOG.md`(新特性/用户可见改动)
> - 「开发日志」tab = 本文件(技术细节/优化/修复根因)
>
> 条目格式:三级标题 `### YYYY-MM-DD · 类型 · 简要标题`,类型 ∈ {refactor, perf, fix, devops, docs}

---

### 2026-04-20 · perf · Dashboard sentiment 3 组实时聚合(对抗 schema-vs-API 错配)

**P20.4 / P20.5**:`/api/dashboard.sentiment` 里 `lhb_stock_count / lhb_total_net / lhb_avg_score / sentiment_change` 4 个字段长期为 NULL/0。market_sentiment 表**有这 4 列**(不像 P18.13b 里的 total_stocks/up_count/down_count 那样完全不存在),但从未跑回填脚本。

**决策**:不启动新的回填任务,改为 **API 层实时聚合**,和 P18.13b 的策略一致:
- `lhb_stock_count` = `SELECT COUNT(*) FROM lhb_stock_daily WHERE trade_date=%s`
- `lhb_total_net` = `SUM(l_net)`
- `lhb_avg_score` = `AVG(score) FILTER (WHERE score IS NOT NULL)`
- `sentiment_change` = 当日 `sentiment_score` − 前一个完整交易日 `sentiment_score`

**好处**:
- 避免再踩 schema-vs-API 错配(P18.13b 的教训)
- 数据永远最新,无需定时任务
- 单次查询 +3ms 级开销可忽略

**E2E 验证**:`sentiment_change = 0.0`(前后两日 45→45),`lhb_total_net = 36.2 亿`,`lhb_avg_score = 65.66`。**Dashboard sentiment 20 字段全覆盖**。

---

### 2026-04-20 · devops · smoke_http.sh 覆盖从 3 个扩到 8 个端点

新增覆盖:`/healthz` / `/api/changelog` / `/api/devlog`(P20.1 新端点)/ `/api/kline/daterange`(P19.1)/ `/api/kline/daily?limit=5`(P19.13 回归)。

jd 实跑 8/8 通过,未来 API 回归防控更可靠。

---

### 2026-04-20 · devops · 文档分流:特性 vs 开发

**是什么**:新增 `docs/DEVLOG.md`,把面向开发者的技术笔记从 CHANGELOG 拆出去。

**为什么**:前端「特性介绍」页更新太频繁,用户翻页都看不过来。其中大部分是开发期的小修复和技术重构,用户不关心。拆分后:
- `CHANGELOG.md`:用户视角的 feature / 影响使用体验的 fix
- `DEVLOG.md`:开发视角的 refactor / perf / 部署基建改动 / bug 根因

**前端**:Changelog 页增加两个 tab,默认「特性介绍」,切换「开发日志」能看这里。

**影响**:不改变代码功能,只是文档组织方式调整。

---

### 2026-04-20 · fix · K 线 API limit 边界不对称导致 422

**根因**:P19.2 commit `771546c` 把前端 `fetchKlineDaily` 硬编码 `limit=2000`,但后端 `Query(default=250, le=1000)` 的上限忘记同步,所有请求秒 422。

**修复**(commit `d4cfc37` P19.13):
- `backend/api/kline.py::/daily` `le` 1000 → 5000
- `backend/api/kline.py::/weekly` `le` 520 → 2000
- 单测 +4:边界值 2000/5000 应 200,5001 应 422

**E2E 验证**:
| 场景 | HTTP | 备注 |
|---|---|---|
| daily 2000 | 200 | 11875B |
| weekly 500 | 200 | 2721B |
| Signals 6M anchor | 200 | 121 行真实数据 |
| LhbList 近期 | 200 | 68 行到 2026-04-17 |
| limit=5001 | 422 | 越界保护仍生效 |

**教训**:改前端 API 参数时,必须同步看后端 `Query(le/ge)` 边界。单元测试 mock 后端,不等于真测了后端。每次前端改动涉及 API 请求参数,需要 curl 至少跑一次真实链路。

---

### 2026-04-20 · devops · Agent 7x24 手册完善:空闲期不得待命

**问题**:之前 Agent 在「无紧急任务」时进入「待命」模式,只做监控不推进优化。用户明确指出这违背「监控优先,优化让路」的本意 —— **优化让路给监控,不是让路给空闲**。

**手册修订**(`docs/prompts/7x24_agent.md`):
- 2.1 节:监控 checklist 完成后,若无异常,**立即**进入 4.1 节优化队列
- 4.1 节:明确「空闲期 ≥ 5min 才能待命;< 5min 优先做短平快优化(改错字/补注释/补文档)」
- 4.2 节:新增「优化积压队列」概念,每轮循环从里面挑一项做

---

### 2026-04-20 · fix · 监控脚本「进程 (none) 时误报卡死」

**根因**:`ingest_status.sh` 只按日志 mtime 判断卡死,忽略了「进程是否存在」。

**修复**(commit `f437909` P19.11):缓存 `ps -ef | grep backend.cli ingest` 结果,**无进程时不报卡死**,只显示「任务已完成,最后活动 Xs 前」。

**教训**:心跳检测要区分三种状态:「有进程+日志刚更新」「有进程+日志静默」(真卡死)「无进程+日志静默」(任务完成)。

---

### 2026-04-20 · perf · LHB API 加 skip_total 消除深分页 COUNT 开销

**现象**:`GET /api/lhb?offset=10000` 耗时 2.2s,首页仅 309ms。

**根因**:每次查询都跑 `COUNT(*) FROM lhb_stock_daily`(52K 行)+ `OFFSET 10000` 跳行。

**修复**(commit `73c3189` P18.11):
- 新增 `skip_total: bool = False` 参数,True 时 `total=-1`,不跑 COUNT
- 前端翻页场景第 2 页后可启用,5~10x 加速
- 默认 False,向后兼容

**测试**:+2 case,pytest 756 passed。

---

### 2026-04-20 · fix · market_sentiment 11 字段永久 NULL(两阶段修复)

**阶段 1 (P18.13)**:
- `_is_empty_sentiment` 判断错误:用 `all()` 导致部分 None 字段不走 DB 反算
- `_calc_from_kline` 缺字段 median_pct_change
- 新增 `_merge_db_fill()`:外部源有数据时也走 DB 反算补齐缺失字段

**阶段 2 (P18.13b)**:紧急止损,schema 错配事故。上一条误把 total_stocks/up_count/down_count 加进 writer.cols,但表里没这 3 列,INSERT 直接 UndefinedColumn 爆错。回填进程首条就失败。
- `pkill -f backend.ingest.sentiment` 止损
- `writer.cols` 回滚 3 列
- `api/dashboard.py::_latest_sentiment` 改为实时从 kline_daily 聚合这 3 字段

**教训**:改 writer + schema 的改动,必须先用 `information_schema.columns` 验证真实列。完整复盘见 `docs/ops/2026-04-20-p18.13b-schema-mismatch.md`。

---

### 2026-04-20 · fix · `daily.service` systemd 239 兼容性(CentOS 8)

**现象**:systemd timer 已排程,但 service 跑完后日志丢到 journal,看起来像「从未运行」。

**根因**:`StandardOutput=append:/path` 需要 systemd 240+,CentOS 8 自带 239 直接忽略这行配置。

**修复**(commit `0657653` P18.4):改用 `StandardOutput=journal` + `SyslogIdentifier=stock_daily`,`daily_update.sh` 内部 `tee -a` 负责写 logs 文件。

---

### 2026-04-20 · perf · healthcheck.sh 加重试消除 deploy 时序假失败

**现象**:`deploy.sh` 末尾跑 healthcheck,systemctl 刚 restart 完 API 还没稳,一次性探测报 `postgres ✗ / API ✗ / SPA ✗`,几秒后手工 curl 全部正常。

**修复**(commit `95a78b3` P18.5):`check()` / `warn_check()` 共用 `_probe_with_retry`,默认 3 次 × 1s 间隔。

---

### 2026-04-20 · fix · deploy.sh / healthcheck.sh 「service 未安装」误报

**根因**:`systemctl list-unit-files | grep -q "^$svc"` 在 `set -o pipefail` 下管道退出码非零偶发误判,打印「Skip」但 healthcheck 显示 `active ✓`。

**修复**(commits `8470639` P18.7 + `4371667` P19.1):改用 `systemctl cat $svc >/dev/null 2>&1`,单 unit 探测,不依赖管道。

---

### 2026-04-20 · fix · ingest_status.sh 业务日志被心跳日志抢焦点

**现象**:长任务 chain.log 每分钟心跳 `waiting` 导致 mtime 最新,脚本只 grep 它 → 「阶段里程碑」「最近异常」两段全空。

**修复**(commit `20b5a8b` P18.8):新增 `find_business_log()`,若活跃日志无业务关键词,从 Top5 依次回退扫描;输出区分「活跃日志」vs「业务日志」。

---

### 2026-04-20 · perf · CHANGELOG API 显式按日期倒序

**现象**:之前依赖 md 文件内原顺序,一旦追加者没插在顶部,时间线跳乱。

**修复**(commit `147314d` P18.6):后端 `sort(key=date, reverse=True)`(稳定排序)。补单测 `test_api_changelog_sorted_by_date_desc`。

---

### 2026-04-20 · fix · ingest_status.sh ANSI 转义清理

**现象**:「最近 5 条异常」输出里有 tqdm 进度条残留的 `\x1b[A` 光标上移码。

**修复**(commit `3da749c` P18.12):加 `sed -E 's/\x1b\[[0-9;]*[A-Za-z]//g'`。

---

### 2026-04-20 · devops · 新增文档分层骨架

新建三个文档子目录和 README:
- `docs/designs/` — 大工程总体设计(周期长/跨模块)
- `docs/optimizations/` — 单点优化技术笔记
- `docs/ops/` — 运维手册 / 事故复盘

配合 `docs/prompts/7x24_agent.md`(Agent 工作手册),形成完整的文档分层。

---

### 2026-04-20 · devops · 新增 7×24 Agent 工作手册

新建 `docs/prompts/7x24_agent.md`,10 节覆盖:角色定位 / 核心循环 / 修复链路 / 优化链路 / 文档规范 / 任务管理 / 红线 / 输出格式。

新 session 接手时第一时间读本文件即可上手。
