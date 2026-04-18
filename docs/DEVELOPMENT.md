# 本地开发指南

---

## 1. 环境准备

### macOS
```bash
# 必须
brew install python@3.11 postgresql@15 redis
# 可选 (用于跑本地测试 DB)
brew install --cask docker
```

### 克隆 & 依赖
```bash
git clone <repo-url>
cd stock_analysis
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

---

## 2. 本地 vs jd 的分工

| 事情 | 本地 | jd 服务器 |
|---|---|---|
| 代码编辑 / git | ✅ | ❌ |
| 单元测试 (离线, CB/解析) | ✅ | ✅ |
| 数据库集成测试 | ✅ (连 jd 或本地 docker) | ✅ |
| **AKShare / 东财爬虫联调** | ❌ **IP 会被封** | ✅ |
| 跑 API / worker / scheduler | ❌ | ✅ |
| 前端 dev server (热重载) | ✅ | ❌ |

**核心规则**：所有涉及境内数据源实际 HTTP 请求的测试都在 jd 上跑。本地只做纯 Python / 数据解析 / DB 的单元测试。

---

## 3. 运行测试

```bash
# 纯内存，最快，随时跑
pytest tests/test_circuit_breaker.py -q

# 需要 DB (连 jd 或本地 compose)
docker compose -f docker-compose.postgres.yml up -d  # 本地 DB
pytest tests/test_db_tables.py tests/test_db_crud.py -q

# 联调数据源 — 只在 jd 上跑
ssh jd "cd ~/stock_analysis && source .venv/bin/activate && pytest tests/test_data_fetch.py -q"
```

---

## 4. 开发工作流

```
开发循环:
  vim code → pytest (本地离线部分) → git push
         ↓
  ssh jd "cd ~/stock_analysis && git pull && ./scripts/deploy.sh"
         ↓
  ssh jd "tail -f /var/log/stock_analysis/api.log"  # 或 journalctl
         ↓
  验证效果 → 下一轮
```

常驻快捷命令（加到本地 `~/.zshrc`）：
```bash
alias sa-deploy='git push && ssh jd "cd ~/stock_analysis && git pull && ./scripts/deploy.sh"'
alias sa-log='ssh jd "sudo journalctl -u stock_analysis_api -f"'
alias sa-psql='ssh -t jd "psql -U lhb_admin -d lhb_quant"'
```

---

## 5. 代码组织约定

### 5.1 目录职责
- `backend/core/`：启动初始化、全局配置、日志、异常，**不含业务**
- `backend/data_gateway/`：数据源抽象，只管"从外部拿数据"，**不写 DB**
- `backend/db/`：ORM 模型、migration、session 管理
- `backend/domain/`：业务逻辑，依赖 `data_gateway` 拿数据，依赖 `db` 持久化
- `backend/api/`：FastAPI 路由，**薄层**，只做参数校验 + 调 domain + 序列化
- `backend/tasks/`：Celery 任务
- `backend/scheduler/`：APScheduler 任务注册

### 5.2 依赖方向
```
api → domain → data_gateway
   ↓      ↓
   └──→  db
```
下层不能 import 上层。`data_gateway` 绝对不 import `db` 或 `domain`。

### 5.3 命名
- 模块：snake_case
- 类：PascalCase
- 数据库表：snake_case, 复数（`lhb_stock_daily`, `robots`）
- API 路径：kebab-case (`/api/data-sources/...`)
- DataFrame 列：snake_case, 统一小写

---

## 6. 数据 / 单位约定

| 项目 | 形式 | 示例 |
|---|---|---|
| 股票代码 (内部) | `ts_code` 格式 | `000001.SZ` / `600000.SH` / `430000.BJ` |
| 日期 (内部) | 字符串 `YYYYMMDD` | `20260417` |
| 时间戳 (DB) | `TIMESTAMPTZ` UTC | 存 UTC，展示时转东八区 |
| 价格 | `DECIMAL(12,4)` | `12.3456` |
| 金额 | `DECIMAL(16,2)` 元 或 万 (看字段注释) | — |
| 百分比 | **小数形式** | 5.23% → `0.0523` |

所有 adapter 必须在返回 DataFrame 前把百分比除以 100。测试里会检查 `abs(pct_change) < 1`。

---

## 7. 常见坑

### 7.1 本地跑 AKShare 报错
正常：本地 IP 已被封。改到 jd 上跑：
```bash
ssh jd "cd ~/stock_analysis && source .venv/bin/activate && \
        python -c 'import akshare as ak; print(ak.stock_zh_a_spot_em().head())'"
```

### 7.2 jd 上 AKShare 报 403
说明 SG 代理未清掉。检查入口脚本是否在**第一行** `import` 了 `backend.core.startup`：
```python
from backend.core.startup import bootstrap
bootstrap()   # ← 必须先于 import akshare / tushare
```

### 7.3 TuShare 返回空
大概率是 120 积分不支持的接口（如 `index_daily`）。Router 会自动降级到 AKShare。

### 7.4 pytest 找不到模块
确保以项目根为工作目录运行 pytest：
```bash
cd <项目根>
pytest tests/xxx.py
```
或设置 `PYTHONPATH=.`。已在 `tests/conftest.py` 里注入。

---

## 8. 提交规范

Conventional Commits：
- `feat:` 新功能
- `fix:` bug 修复
- `refactor:` 重构
- `docs:` 文档
- `test:` 测试
- `chore:` 构建/配置
- `perf:` 性能

中英文混用可接受，commit 正文建议列出**做了什么**和**为什么**：
```
fix: 修复 SinaAdapter 所有方法嵌套在 __init__ 中导致类为空壳

- 所有方法从 8 空格缩进提升到 4 空格（类作用域）
- _build_session() 显式禁用环境代理
- 修复 _parse_sina_quote 除零崩溃

关联: docs/CODE_REVIEW.md §1.2
```

---

## 9. API 索引 (W12-6)

所有 HTTP 端点由 FastAPI app 自动暴露, 完整 schema 可运行时拉取:

```bash
# 交互式 UI
open http://localhost:8000/docs            # Swagger UI
open http://localhost:8000/redoc           # ReDoc

# 导出 JSON / YAML
python scripts/dump_openapi.py --out docs/openapi.json
python scripts/dump_openapi.py --yaml --out docs/openapi.yaml
```

### 9.1 路由一览

| 前缀 | 模块 | 说明 |
|---|---|---|
| `/healthz` | main | 进程存活探针 |
| `/metrics` | core.metrics | Prometheus 指标 (W12-2) |
| `/api/data-sources/status` | api.data_sources | 适配器 + CB + 健康快照 |
| `/api/data-sources/priority` | 同上 | POST 优先级链 |
| `/api/data-sources/adapter/{name}/retry` | 同上 | 重新初始化某适配器 |
| `/api/data-sources/adapter/{name}/circuit/{action}` | 同上 | 手动 open/close CB |
| `/api/lhb` | api.lhb | 龙虎榜列表分页/多维过滤 |
| `/api/lhb/seats` | 同上 | 单日单股席位明细 |
| `/api/lhb/stat/{stat_type}/{stat_key}` | 同上 | 席位/行业聚合统计 |
| `/api/dashboard` | api.dashboard | 首屏: 情绪 + Top LHB + 适配器 (@cached 15s) |
| `/api/backtest` (POST) | api.backtest | 提交单策略回测任务 |
| `/api/backtest` (GET) / `/{task_id}` | 同上 | 任务列表 / 单任务状态 |
| `/api/backtest/strategies` | 同上 | 已注册策略列表 |
| `/api/backtest/compare` (POST) | 同上 | W10-1 多策略并行对比 |
| `/api/robots` (GET) | api.robots | 机器人列表 (带 enabled/state) |
| `/api/robots/{id}/status` | 同上 | 单个机器人快照 |
| `/api/robots/{id}/pause` \| `/resume` | 同上 | 状态机切换 |
| `/api/robots/{id}/enable` \| `/disable` | 同上 | W12-1 灰度开关 |
| `/api/reports/weekly` (GET) | api.reports | 指定区间周报 |
| `/api/reports/weekly/generate` (POST) | 同上 | 立即生成当周周报 |
| `/ws/snapshot?codes=a,b` | api.ws_snapshot | WebSocket 订阅盘中 10s 快照 (W8-4) |

### 9.2 运行时开关 (env)

| 变量 | 默认 | 用途 |
|---|---|---|
| `RUNTIME_ENABLE_REDIS` | `0` | 启用 RedisCache + SnapshotPublisher |
| `RUNTIME_ENABLE_INTRADAY` | `0` | 启用盘中 10s 快照 scheduler (W8-3) |
| `RUNTIME_ENABLE_WEEKLY` | `0` | 启用周一 09:00 周报 scheduler (W10-2) |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis 连接串 |
| `CLEAR_PROXY_ON_STARTUP` | `0` | 启动清代理 (历史兼容) |
| `LOG_LEVEL` | `INFO` | stdlib logging level |

所有开关打开失败都走 logger.warning, 不阻塞 FastAPI 启动; 见
`backend/core/runtime.py::wire_runtime`.

