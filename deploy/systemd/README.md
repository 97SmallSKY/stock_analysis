# deploy/systemd — systemd 单元

> **P5.2 结论:本项目在 jd 上只需要 1 个 systemd unit。**
>
> 历史的 `stock_analysis_worker.service`(Celery worker) 和
> `stock_analysis_scheduler.service`(独立 APScheduler 进程)**已废弃**:
>
> - Celery 不再使用(详见 `docs/adr` 与 `docs/progress.md`)
> - APScheduler 直接**嵌在 FastAPI 进程**内(由环境变量 `RUNTIME_ENABLE_INTRADAY/WEEKLY/REDIS` 控制)
> - WebSocket 推送也在同一进程,因此 `--workers 1`

## 文件清单

| 文件 | 作用 |
|---|---|
| `stock_analysis_api.service`   | 常驻 uvicorn + FastAPI(内嵌 APScheduler + WebSocket) |
| `stock_analysis_smoke.service` | oneshot:线上集成冒烟(由 timer 触发,不要手动 enable 单跑) |
| `stock_analysis_smoke.timer`   | 每天 16:30 Asia/Shanghai 跑一次 `integration_smoke.sh` |

## 前置条件

1. 代码已 clone 到 `/root/stock_analysis`
2. 已创建 `.venv`(`scripts/deploy.sh` 会自动建)
3. `/root/stock_analysis/.env` 已填好(PG / TuShare token / 等)

## 安装

```bash
sudo bash scripts/install_units.sh
```

等价步骤:

```bash
sudo cp deploy/systemd/stock_analysis_api.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now stock_analysis_api
sudo systemctl status stock_analysis_api --no-pager
```

## 迁移(如果机器上还跑着旧版)

```bash
# 1. 停掉并移除已废弃的 unit(如果存在)
for old in stock_analysis_worker stock_analysis_scheduler; do
    if systemctl list-unit-files | grep -q "^${old}.service"; then
        sudo systemctl disable --now "${old}.service" || true
        sudo rm -f "/etc/systemd/system/${old}.service"
    fi
done

# 2. 覆盖新版 api unit
sudo cp deploy/systemd/stock_analysis_api.service /etc/systemd/system/
sudo systemctl daemon-reload

# 3. 重启
sudo systemctl restart stock_analysis_api

# 4. 验证
curl -fsS http://localhost:8000/healthz && echo OK
bash scripts/healthcheck.sh
```

## 日志

```bash
# 跟随日志
sudo journalctl -u stock_analysis_api -f

# 最近一次启动的所有日志
sudo journalctl -u stock_analysis_api -b

# 只看调度器相关
sudo journalctl -u stock_analysis_api | grep -E 'scheduler|intraday|weekly'
```

## 与 Redis 的关系

本项目的 Redis 是**软依赖**,只作 cache;没有 Redis 应用会降级运行(Redis
相关功能用内存 fallback 或直接跳过)。Redis 在 jd 上通过
`docker-compose.redis.yml` 启动,由 `scripts/deploy.sh` 按需拉起,不以
systemd unit 形式管理。

## 常见问题

**Q: 服务起不来,journalctl 里是 `ModuleNotFoundError`?**
A: `.venv` 没装好。跑一次 `bash scripts/deploy.sh`(会自动 pip install)。

**Q: `EnvironmentFile=/root/stock_analysis/.env` 报 not found?**
A: 先 `cp .env.example .env` 并填好。unit 会直接拒绝启动。

**Q: 想同时跑 N 个 worker 提升吞吐?**
A: 不可以。APScheduler 和 WebSocket 订阅是进程级状态,多 worker 会重复
触发调度、重复 pubsub。真的需要横向扩展时要先把调度/订阅拆出进程。

**Q: 为什么不开机自起 Redis 容器?**
A: `docker-compose.redis.yml` 里已经 `restart: unless-stopped`,机器重启
会自动拉回来。`scripts/deploy.sh` 里还做了"存在 compose 且未在跑则起"
的幂等逻辑,部署流程里也覆盖了。

## 手动跑一次 smoke

```bash
sudo systemctl start stock_analysis_smoke.service
journalctl -u stock_analysis_smoke.service -n 100 --no-pager
```

timer 状态:

```bash
systemctl list-timers stock_analysis_smoke.timer --all
```
