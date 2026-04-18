# 部署与运维手册

> 目标环境:jd 服务器 (117.72.184.8)。本地 macOS **不部署运行时服务**,只做代码编辑与 git push。
>
> 架构速查(P5.2 后):
>
> - **1 个 systemd unit**:`stock_analysis_api.service`(uvicorn + 嵌入的 APScheduler + WebSocket)
> - **不使用 Celery**(调度走进程内 APScheduler;详见 docs/ADR 与 progress)
> - **不使用 Alembic**(schema 通过 `db/init/*.sql` 手动 psql 应用;ADR-0001)
> - **不使用 nginx**(FastAPI 直接 serve `frontend/dist`,一端口通吃;P5.1b)
> - **PostgreSQL**:远程 `117.72.184.8`(生产默认),本机可选
> - **Redis**:通过 `docker-compose.redis.yml` 起,仅作 cache,不起也能降级运行

---

## 1. 首次部署 (jd 服务器)

### 1.1 系统依赖
```bash
sudo apt update
sudo apt install -y python3.10 python3.10-venv python3-pip git curl
# 可选: Docker(用于起 Redis 容器)
#   curl -fsSL https://get.docker.com | sh
# 可选: postgresql-client(用于 pg_isready / psql)
#   sudo apt install -y postgresql-client
# 可选: Node(前端构建,node >= 20)
#   curl -fsSL https://deb.nodesource.com/setup_20.x | sudo bash -
#   sudo apt install -y nodejs
```

> jd 现状:Python 走 pyenv(`/root/.pyenv/shims/python3.10`),`scripts/deploy.sh::pick_python` 会自动兜底。

### 1.2 PostgreSQL

生产数据库部署在远程 (117.72.184.8)。本机无需安装 PG。`.env` 填好:
```
PG_HOST=117.72.184.8
PG_PORT=5432
POSTGRES_DB=lhb_quant
POSTGRES_USER=lhb_admin
POSTGRES_PASSWORD=***
```

Schema 初始化(一次性,在有权限的机器上执行):
```bash
psql -h "$PG_HOST" -U "$POSTGRES_USER" -d "$POSTGRES_DB" -f db/01_schema_all.sql
```

### 1.3 拉代码
```bash
cd /root
git clone <repo-url> stock_analysis
cd stock_analysis
cp .env.example .env   # 编辑 .env 填入真实 token / 密码
```

### 1.4 安装 systemd unit
```bash
sudo bash scripts/install_units.sh
```

等价于:
```bash
sudo cp deploy/systemd/*.service deploy/systemd/*.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now stock_analysis_api
sudo systemctl enable --now stock_analysis_smoke.timer  # 每天 16:30 冒烟
```

详见 `deploy/systemd/README.md`。

### 1.4.1 Redis(可选,软依赖)

```bash
docker compose -f docker-compose.redis.yml up -d
docker compose -f docker-compose.redis.yml ps
```

- 只作 cache;不起也能跑(自动降级)
- `restart: unless-stopped`,机器重启会自动拉回
- `scripts/deploy.sh` 里有"存在 compose 文件且 docker 可用则启动"的幂等逻辑

### 1.5 首次部署(自动跑全流程)
```bash
bash scripts/deploy.sh
```

该脚本会:
1. 清 HTTP(S)_PROXY
2. 自适应选 Python ≥ 3.10
3. venv + `pip install -r requirements.txt`
4. `npm ci && npm run build`(若 `frontend/package.json` 存在)
5. 若存在 `docker-compose.redis.yml` 且 docker 可用,起 Redis 容器(不覆盖已跑)
6. `sudo systemctl restart stock_analysis_api`
7. 跑 `scripts/healthcheck.sh`

---

## 2. 日常部署流程 (本地 → jd)

```bash
# 本地
git push origin <branch>
# 触发 jd 部署
ssh jd "cd /root/stock_analysis && git pull && bash scripts/deploy.sh"
```

部署失败时 `deploy.sh` 非 0 退出,不会破坏正在跑的旧进程(systemd `restart` 先起新后杀旧)。

只重启服务(不拉代码):
```bash
ssh jd "cd /root/stock_analysis && bash scripts/restart.sh"
```

只重新构建前端:
```bash
ssh jd "cd /root/stock_analysis && bash scripts/deploy_frontend.sh"
# 前端产物就地挂在 FastAPI 下,无需 reload nginx;改完刷新浏览器即可
```

---

## 3. 代理与网络

jd 服务器默认注入了 SG 代理 (`HTTP_PROXY=...`, `HTTPS_PROXY=...`),用于访问境外服务。

**本项目所有出站都是国内源** (TuShare / AKShare / EastMoney / Sina),**必须绕过 SG 代理**,否则会 403 / 超时。

实现(三层防护):
- `scripts/deploy.sh` 开头 `unset HTTP_PROXY ...`
- `deploy/systemd/stock_analysis_api.service` 里 `Environment="HTTP_PROXY="` 显式清空
- `backend/core/startup.py::bootstrap()` 进程启动时再 `os.environ.pop(...)` 并设 `NO_PROXY=*`
- 所有适配器的 `requests.Session` 都 `trust_env=False` + `proxies={}`

验证:
```bash
ssh jd "cd /root/stock_analysis && source .venv/bin/activate && \
        python -c 'from backend.core.startup import bootstrap; bootstrap(); \
                   import akshare as ak; print(ak.stock_zh_a_spot_em().head())'"
```
应 2 s 内返回数据,无代理错误。

---

## 4. 数据库备份

每日 02:30 通过 cron 调用 `scripts/backup_pg.sh`:
```
30 2 * * *  /bin/bash /root/stock_analysis/scripts/backup_pg.sh >> /var/log/lhb_backup.log 2>&1
```

该脚本从 `.env` 读 PG 连接信息,写到 `BACKUP_DIR`(默认 `/var/backups/lhb_quant`),按保留天数清理旧文件。

恢复:
```bash
gunzip -c /var/backups/lhb_quant/lhb_YYYYMMDD.sql.gz | \
    psql -h "$PG_HOST" -U "$POSTGRES_USER" -d "$POSTGRES_DB"
```

---

## 5. 服务管理速查

| 操作 | 命令 |
|---|---|
| 查看 API 日志 | `sudo journalctl -u stock_analysis_api -f` |
| 重启服务 | `sudo systemctl restart stock_analysis_api` |
| 查看服务状态 | `systemctl status stock_analysis_api` |
| 手动跑健康检查 | `./scripts/healthcheck.sh` |
| 手动跑集成冒烟 | `sudo systemctl start stock_analysis_smoke.service && journalctl -u stock_analysis_smoke.service -n 200` |
| 看 smoke timer | `systemctl list-timers stock_analysis_smoke.timer --all` |
| 进入 psql | `psql -h "$PG_HOST" -U "$POSTGRES_USER" -d "$POSTGRES_DB"` |
| 查看 Redis | `redis-cli` |
| CB 手动关闭 | `curl -X POST http://localhost:8000/api/data-sources/adapter/akshare/circuit/close` |

---

## 6. 故障排查

### 6.1 AKShare 403 / 超时
1. `echo $HTTP_PROXY` 在 systemd 环境里应为空(unit 文件里显式清了)
2. `sudo journalctl -u stock_analysis_api | grep -i proxy` 确认 bootstrap 跑过
3. 仍 403 —— 出口 IP 可能被风控,等待或换 IP

### 6.2 TuShare 积分不足
当前仅 120 积分,能拉日 K;`FINANCIAL_DATA` / `INDEX_DAILY` 请求自动降级到 AKShare。

### 6.3 熔断器长期打开
```bash
curl http://localhost:8000/api/data-sources/status | jq '.adapters'
curl -X POST http://localhost:8000/api/data-sources/adapter/<name>/circuit/close
```

### 6.4 盘中快照延迟 / 调度没跑
APScheduler job 嵌在 API 进程里,过滤其日志:
```bash
sudo journalctl -u stock_analysis_api -f | grep -E 'intraday|scheduler'
```
必须:`RUNTIME_ENABLE_INTRADAY=1`(unit 文件里默认开);工作日 09:30–15:00 外无动作是正常的。

---

## 7. 回滚

```bash
ssh jd "cd /root/stock_analysis && \
        git fetch origin && git checkout <tag-or-commit> && \
        bash scripts/deploy.sh"
```

数据库 schema 变更不自动回滚(需手动写反向 SQL;ADR-0001),上线前先在 staging 验证。

---

## 8. 前端

前端工程位于 `frontend/`,技术栈 Vue 3 + Vite + TypeScript + Pinia + Ant Design Vue + AG Grid Community。

P5.1b 起:**FastAPI 自己 serve `<repo>/frontend/dist`**(`backend/api/main.py::_mount_frontend`)。不再使用独立的 nginx 站点,不需要 rsync 到外部目录。

### 8.1 构建
```bash
ssh jd "cd /root/stock_analysis && bash scripts/deploy_frontend.sh"
# 产物: <repo>/frontend/dist —— FastAPI 已就地挂载。
```

### 8.2 本地开发
```bash
# 终端 A: 后端
uvicorn backend.api.main:app --reload --port 8000
# 终端 B: 前端 (vite dev-server 代理 /api 和 /ws 到 localhost:8000)
cd frontend && npm install && npm run dev
# 访问 http://localhost:5173
```

### 8.3 前端自测
```bash
cd frontend
npm run typecheck      # vue-tsc 严格类型
npm run test           # vitest
npm run build          # 生产构建(manualChunks 分包,见 vite.config.ts)
```
