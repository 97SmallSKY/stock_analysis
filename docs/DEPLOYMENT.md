# 部署与运维手册

> 目标环境：jd 服务器 (117.72.184.8)。本地 macOS **不部署运行时服务**，只做代码编辑与 git push。

---

## 1. 首次部署 (jd 服务器)

### 1.1 系统依赖
```bash
# 在 jd 上，以具备 sudo 的账户执行
sudo apt update
sudo apt install -y python3.11 python3.11-venv python3-pip \
    postgresql-15 postgresql-15-timescaledb redis-server \
    nginx git curl
```

### 1.2 初始化 PostgreSQL + TimescaleDB
```bash
sudo -u postgres psql <<'SQL'
CREATE DATABASE lhb_quant;
CREATE USER lhb_admin WITH PASSWORD 'lhb_quant_2024';
GRANT ALL ON DATABASE lhb_quant TO lhb_admin;
\c lhb_quant
CREATE EXTENSION IF NOT EXISTS timescaledb;
SQL
```
然后导入初始 schema：
```bash
psql -U lhb_admin -d lhb_quant -f db/init/00_init.sql
psql -U lhb_admin -d lhb_quant -f db/init/01_schema.sql
```

### 1.3 拉代码
```bash
cd ~
git clone <repo-url> stock_analysis
cd stock_analysis
cp .env.example .env   # 编辑 .env 填入真实 token / 密码
```

### 1.4 安装 Python 依赖
```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r requirements.txt     # W1 后可用 poetry
```

### 1.5 systemd 注册
```bash
sudo cp deploy/systemd/*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now stock_analysis_api
sudo systemctl enable --now stock_analysis_worker
sudo systemctl enable --now stock_analysis_scheduler
```

### 1.6 Nginx
```bash
sudo cp deploy/nginx/stock_analysis.conf /etc/nginx/sites-available/
sudo ln -s /etc/nginx/sites-available/stock_analysis.conf /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

### 1.7 验证
```bash
./scripts/healthcheck.sh
```

---

## 2. 日常部署流程 (本地 → jd)

每次代码更新后，在本地执行：
```bash
git push origin <branch>
ssh jd "cd ~/stock_analysis && git pull && ./scripts/deploy.sh"
```

`deploy.sh` 做的事：
1. 清环境代理
2. `pip install -r requirements.txt` (或 poetry sync)
3. 前端 build (W7 后)
4. `alembic upgrade head`
5. `systemctl restart` 三个服务
6. `nginx -s reload`
7. 跑 healthcheck

部署失败时 `deploy.sh` 非 0 退出，不会破坏正在跑的旧进程（systemd `restart` 的行为是先起新进程再杀旧进程）。

---

## 3. 代理与网络

jd 服务器默认注入了 SG 代理 (`HTTP_PROXY=...`, `HTTPS_PROXY=...`)，用于访问境外服务。

**本项目所有出站都是国内源** (TuShare / AKShare / EastMoney / Sina)，**必须绕过 SG 代理**，否则会 403 / 超时。

实现：
- `backend/core/startup.py::bootstrap()` 在进程启动时 `os.environ.pop("HTTP_PROXY"...)` 并设 `NO_PROXY=*`。
- 所有适配器的 `requests.Session` 都显式 `trust_env=False` + `proxies={}` 双保险。
- FastAPI / Celery / APScheduler 所有入口脚本**第一行**必须调用 `bootstrap()`。

验证：
```bash
ssh jd "cd ~/stock_analysis && source .venv/bin/activate && \
        python -c 'from backend.core.startup import bootstrap; bootstrap(); \
                   import akshare as ak; print(ak.stock_zh_a_spot_em().head())'"
```
应 2s 内返回数据，无代理错误。

---

## 4. 数据库备份

每日 03:00 自动备份（写 crontab）：
```
0 3 * * * pg_dump -U lhb_admin lhb_quant | gzip > /var/backups/lhb_$(date +\%Y\%m\%d).sql.gz && \
          find /var/backups -name 'lhb_*.sql.gz' -mtime +30 -delete
```

恢复：
```bash
gunzip -c /var/backups/lhb_20260417.sql.gz | psql -U lhb_admin lhb_quant
```

---

## 5. 服务管理速查

| 操作 | 命令 |
|---|---|
| 查看 API 日志 | `sudo journalctl -u stock_analysis_api -f` |
| 重启单个服务 | `sudo systemctl restart stock_analysis_api` |
| 查看所有状态 | `systemctl status stock_analysis_*` |
| 手动跑健康检查 | `./scripts/healthcheck.sh` |
| 进入 psql | `psql -U lhb_admin -d lhb_quant` |
| 查看 Redis | `redis-cli` |
| CB 手动关闭 | `curl -X POST http://localhost:8000/api/data-sources/adapter/akshare/circuit/close` |

---

## 6. 故障排查

### 6.1 AKShare 403 / 超时
1. 检查 `echo $HTTP_PROXY` 在 systemd 环境中是否为空（应为空）
2. `sudo journalctl -u stock_analysis_api | grep "已清除代理环境变量"` 确认 startup 跑过
3. 若仍然有问题，IP 可能被封 — 换出口 IP 或等待

### 6.2 TuShare 积分不足
当前仅 120 积分仅能拉日 K；`FINANCIAL_DATA` / `INDEX_DAILY` 等请求会自动降级到 AKShare。升级到 2000 积分后参考 [重写方案_v2.md §6](重写方案_v2.md)。

### 6.3 熔断器长期打开
```bash
# 查看状态
curl http://localhost:8000/api/data-sources/status | jq '.adapters'
# 手动关闭
curl -X POST http://localhost:8000/api/data-sources/adapter/<name>/circuit/close
```

### 6.4 盘中快照延迟
APScheduler job 监控：
```bash
sudo journalctl -u stock_analysis_scheduler -f | grep intraday_snapshot
```
若 CB 打开会自动降级到 30s。爬虫失败率 > 50% 时考虑更换 crawler 实现。

---

## 7. 回滚

```bash
ssh jd "cd ~/stock_analysis && \
        git fetch origin && git checkout <tag-or-commit> && \
        ./scripts/deploy.sh"
```

数据库 migration 不自动回滚（需手动 `alembic downgrade -1`），上线前先在 staging 验证。
