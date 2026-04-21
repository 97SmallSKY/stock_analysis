# [auto:stock-analysis] Ops Agent 自探测结果

探测时间: 2026-04-22T00:10:00+08:00
探测者: Ops agent (sa-ops worktree, 首轮 bootstrap)

## 核心参数

- **service_name**: `stock_analysis_api.service`
  - 状态: `active (running)`,`ActiveEnterTimestamp=Tue 2026-04-21 09:22:11 CST`(连续运行 14h+,`Result=success`)
  - 描述: *Stock Analysis FastAPI (serves backend API + frontend SPA + embedded APScheduler)*
  - 其他同名但 **inactive/dead** 的 unit(部署时不使用):
    - `stock_analysis_daily.service` — 日度数据更新,oneshot
    - `stock_analysis_smoke.service` — 集成冒烟,oneshot

- **healthcheck_url**: `http://127.0.0.1:8000/healthz`
  - 期望响应: HTTP 200 + body `{"status":"ok","version":"0.3.0"}`(已实测通过)
  - SPA 根 `http://127.0.0.1:8000/` 也返回 200(FastAPI 自 serve `frontend/dist`,P5.1b)
  - 另一个 `/api/health` 会被 SPA fallback 截获返回 HTML,**不要用**

- **startup_marker**:
  - deploy.sh 最后一行: `==> Deploy complete.`
  - healthcheck.sh 最后一行(软警告放过): `==> All checks passed` 或 `==> All hard checks passed (N soft warnings)`
  - 最权威判成功信号: `healthcheck_url` 返回 200 + body 含 `ok`

- **error_markers**(判部署失败的关键词,任一命中即失败):
  - `ERROR`
  - `CRITICAL`
  - `Traceback`
  - `failed to start`
  - `Address already in use`
  - `ModuleNotFoundError`
  - `connection refused`
  - `ImportError`
  - deploy.sh 非 0 退出
  - healthcheck.sh 输出含 `✗` 硬失败(软失败 `⚠` 不阻断)

- **log_sources**(排查失败时依次看):
  1. `ssh jd "journalctl -u stock_analysis_api.service --since '3 min ago' --no-pager"` — 主服务日志(最权威)
  2. `ssh jd "tail -200 ~/stock_analysis/logs/stock_api.log"` — 应用写的文件日志(备份源)
  3. `ssh jd "tail -200 ~/stock_analysis/logs/daily_update.log"` — 日度数据任务日志(仅 `stock_analysis_daily.service` 跑过后有新内容)

## 部署命令契约

- **一键部署**: `ssh jd "cd ~/stock_analysis && git pull && ./scripts/deploy.sh"`
- **deploy.sh 关键步骤**(出自 `scripts/deploy.sh`):
  1. 清代理 env(`HTTP_PROXY` 等 unset,`NO_PROXY=*`)
  2. 自适应 Python(3.11 → 3.10 → python3,含 pyenv shims 兜底)
  3. venv 复用或创建 + `pip install -e .` / `-r requirements.txt`
  4. 前端 `pnpm build` 或 `npm ci && npm run build`(**硬依赖**,失败则整体失败)
  5. Redis 可选启动(docker-compose)
  6. `sudo systemctl restart stock_analysis_api.service`
  7. `scripts/healthcheck.sh` — 6 项检查(见下)
- **healthcheck.sh 检查项**:
  1. `stock_analysis_api.service` active
  2. PostgreSQL `pg_isready`(从 `.env` 读 `PG_HOST` / `POSTGRES_PORT`)
  3. Redis(软失败,缺它不阻断)
  4. `GET /healthz` 200
  5. `GET /` 200(SPA 根)
  6. shell 代理 env 已清
- 重试参数: `HEALTHCHECK_RETRY=3` / `HEALTHCHECK_RETRY_INTERVAL=1`(覆盖 uvicorn + PG 启动窗口)

## 部署判成功流程(Ops 执行 T4)

```bash
# 1. 部署
ssh jd "cd ~/stock_analysis && git pull && ./scripts/deploy.sh" 2>&1 | tee /tmp/ops-deploy-$(date +%s).log
# 记录 exit code

# 2. 稳定窗口
sleep 20

# 3. 查服务日志 error_markers
ssh jd "journalctl -u stock_analysis_api.service --since '3 min ago' --no-pager"

# 4. 命中 startup_marker + 无 error_markers
#    deploy.sh 日志含 '==> Deploy complete.'
#    healthcheck 末行是 '==> All checks passed' 或 '==> All hard checks passed'

# 5. Healthcheck 复核
ssh jd "curl -fsS -m 5 http://127.0.0.1:8000/healthz"
# 期望: {"status":"ok","version":"0.3.0"}
```

全部满足 → Linear state 流转 Done + `@PM deploy ok` comment。
任一失败 → T6 走:state 回 **In Progress** + `@Dev deploy FAILED` + stdout_tail/journalctl error 片段,**不 revert,不重部**。

## 观察到的异常(转交 Dev/PM,Ops 不 fix)

1. **潜在路径遍历(高优先级)**: 2026-04-21 23:59:40 journalctl 记录外部 IP 180.136.85.212 的请求 `GET /static/../../../../../../../../../../../this_path_should_not_exist_claude_probe.txt`,**服务器返回 200 OK**。探测路径本身不存在,但正常 `StaticFiles` mount 应返回 400/403。200 暗示:
   - SPA catch-all fallback 把该路径当成了前端路由返回 index.html(内容非敏感,但 200 掩盖了非法路径)
   - 或静态路径归一化有缺陷
   - 建议 Dev 核实 `backend/api/main.py` 静态挂载,对 `/static/` 前缀做 normalize 后再匹配
   - **不是 Ops 职责**,登记观察后继续部署循环

2. **Tushare 长期 DOWN**: HealthChecker 每 5 分钟一轮连续报 `tushare=DOWN`。其他源(akshare/sina/crawler)健康,数据网关有降级。非部署阻断,PM 可追。

## 版本备注

- 本 probe 基于 sa-ops worktree HEAD;主仓库分支 `new-branch`,当前部署 commit 现问现拿: `ssh jd "cd ~/stock_analysis && git rev-parse HEAD"`
- 下次更新触发点: service_name / healthcheck path / deploy.sh 结构变动时,Ops 自觉更新本文件
