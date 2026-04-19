# 开发日志 / Changelog

> 本文件是「特性介绍」页面(`/changelog`)的数据源。
> 每次完成修复或新增特性,按时间倒序在顶部追加一条。
> 条目格式:三级标题 `### YYYY-MM-DD · 类型 · 简要标题`,类型 ∈ {feature, fix, refactor, perf, docs, ops}

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
