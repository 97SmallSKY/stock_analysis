# S05 — 基础设施（Infrastructure & Deployment）

> **阶段目标**：完善 Docker 容器编排、Nginx 反向代理、Redis 缓存配置、日志采集、监控告警及生产部署流程，使系统具备生产级可靠性。

---

## 0. 前置阅读（必须先读）

- `docs/lhb-quant-v2/DEPLOYMENT.md` — 部署架构、环境变量清单、上线检查清单
- `docs/lhb-quant-v2/RUNBOOK.md` — 故障处理手册、常见问题排查步骤
- `docs/lhb-quant-v2/ARCHITECTURE.md` — 整体架构，理解各服务依赖关系

---

## 1. 任务目标

1. **容器编排**：Docker Compose 完整配置（开发/生产双环境）
2. **Nginx**：反向代理、SSL 终止、静态资源缓存、WebSocket 代理
3. **Redis**：缓存策略配置、持久化、内存上限
4. **日志**：结构化日志采集、统一格式、日志轮转
5. **监控**：健康检查端点、Prometheus 指标暴露、告警规则
6. **部署脚本**：零停机部署、回滚机制、数据库迁移流程

---

## 2. 输入依赖

| 依赖 | 说明 |
|------|------|
| `docker-compose.yml` | 已有基础定义，本阶段完善生产配置 |
| `.env.example` | 环境变量模板，本阶段补全所有缺失项 |
| S01-S04 所有服务 | 确保所有服务镜像可正常构建 |
| `docs/lhb-quant-v2/DEPLOYMENT.md` | 生产环境规格要求 |

---

## 3. 具体任务

### 3.1 Docker Compose 配置

**服务清单与资源限制**：

```yaml
# docker-compose.prod.yml 关键配置（完整版）
services:
  postgres:
    image: postgres:16-alpine
    deploy:
      resources:
        limits: { memory: 2g, cpus: "1.0" }
    volumes:
      - postgres_data:/var/lib/postgresql/data
      - ./backend/db/migrations:/docker-entrypoint-initdb.d  # 首次启动自动迁移
    environment:
      POSTGRES_PASSWORD: ${DB_PASSWORD}      # 从 .env 读取，不得硬编码
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres"]
      interval: 10s
      timeout: 5s
      retries: 5

  redis:
    image: redis:7-alpine
    command: >
      redis-server
      --maxmemory 512mb
      --maxmemory-policy allkeys-lru
      --save 60 1000          # 60秒内1000次写操作触发持久化
      --requirepass ${REDIS_PASSWORD}
    deploy:
      resources:
        limits: { memory: 600m }

  backend:
    build:
      context: ./backend
      target: production       # 多阶段构建，production 阶段不含开发依赖
    deploy:
      replicas: 2              # 双实例，通过 Nginx upstream 负载均衡
      resources:
        limits: { memory: 1g, cpus: "1.0" }
    depends_on:
      postgres: { condition: service_healthy }
      redis:    { condition: service_started }
    environment:
      - DATABASE_URL=${DATABASE_URL}
      - REDIS_URL=${REDIS_URL}
      - SECRET_KEY=${SECRET_KEY}
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 15s
      timeout: 5s
      retries: 3

  frontend:
    build:
      context: ./frontend
      我先检查已经写了哪些文件、写到哪里了：
<function_calls>
<invoke name="Bash">
<parameter name="command">ls -la ~/Develop/personal/stock_analysis/docs/prompts/      target: production
    deploy:
      resources:
        limits: { memory: 512m }
    depends_on:
      - backend

  nginx:
    image: nginx:1.25-alpine
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./infra/nginx/nginx.conf:/etc/nginx/nginx.conf:ro
      - ./infra/nginx/ssl:/etc/nginx/ssl:ro
      - ./infra/nginx/logs:/var/log/nginx
    depends_on:
      - backend
      - frontend
    deploy:
      resources:
        limits: { memory: 128m }

  prometheus:
    image: prom/prometheus:v2.50.0
    volumes:
      - ./infra/prometheus/prometheus.yml:/etc/prometheus/prometheus.yml:ro
      - prometheus_data:/prometheus
    command:
      - '--config.file=/etc/prometheus/prometheus.yml'
      - '--storage.tsdb.retention.time=30d'
    deploy:
      resources:
        limits: { memory: 512m }

  grafana:
    image: grafana/grafana:10.3.0
    ports:
      - "3001:3000"
    volumes:
      - grafana_data:/var/lib/grafana
      - ./infra/grafana/dashboards:/etc/grafana/provisioning/dashboards:ro
    environment:
      GF_SECURITY_ADMIN_PASSWORD: ${GRAFANA_PASSWORD}
      GF_USERS_ALLOW_SIGN_UP: "false"

volumes:
  postgres_data:
  redis_data:
  prometheus_data:
  grafana_data:
```

**开发环境覆盖**（`docker-compose.override.yml`，git 不跟踪）：
```yaml
# 开发环境：挂载源码热重载，关闭资源限制，暴露调试端口
services:
  backend:
    build: { target: development }
    volumes:
      - ./backend:/app
    environment:
      - DEBUG=true
      - LOG_LEVEL=DEBUG
    ports:
      - "8000:8000"   # 直接暴露，跳过 Nginx
      - "5678:5678"   # debugpy 调试端口

  frontend:
    build: { target: development }
    volumes:
      - ./frontend:/app
      - /app/node_modules   # 匿名卷防止覆盖
    ports:
      - "3000:3000"
    environment:
      - NEXT_PUBLIC_API_BASE=http://localhost:8000
```

---

### 3.2 Nginx 配置（`infra/nginx/nginx.conf`）

```nginx
# 核心要求（完整实现以下配置）

worker_processes auto;
events { worker_connections 1024; }

http {
    # 日志格式（JSON，便于采集）
    log_format json_combined escape=json
        '{"time":"$time_iso8601",'
        '"remote_addr":"$remote_addr",'
        '"method":"$request_method",'
        '"uri":"$request_uri",'
        '"status":$status,'
        '"body_bytes":$body_bytes_sent,'
        '"request_time":$request_time,'
        '"upstream_time":"$upstream_response_time"}';

    access_log /var/log/nginx/access.log json_combined;
    error_log  /var/log/nginx/error.log warn;

    # 安全头
    add_header X-Frame-Options DENY;
    add_header X-Content-Type-Options nosniff;
    add_header X-XSS-Protection "1; mode=block";
    add_header Strict-Transport-Security "max-age=31536000" always;

    # 限流（防爬虫/暴力请求）
    limit_req_zone $binary_remote_addr zone=api:10m rate=30r/m;
    limit_req_zone $binary_remote_addr zone=auth:10m rate=5r/m;

    # Backend 双实例负载均衡
    upstream backend_pool {
        least_conn;
        server backend_1:8000 max_fails=3 fail_timeout=30s;
        server backend_2:8000 max_fails=3 fail_timeout=30s;
        keepalive 32;
    }

    server {
        listen 80;
        return 301 https://$host$request_uri;  # 强制 HTTPS
    }

    server {
        listen 443 ssl http2;
        ssl_certificate     /etc/nginx/ssl/cert.pem;
        ssl_certificate_key /etc/nginx/ssl/key.pem;
        ssl_protocols       TLSv1.2 TLSv1.3;
        ssl_ciphers         HIGH:!aNULL:!MD5;

        # 静态资源（Next.js _next/static）
        location /_next/static/ {
            proxy_pass http://frontend:3000;
            expires 1y;
            add_header Cache-Control "public, immutable";
        }

        # WebSocket（/ws/* 路由）
        location /ws/ {
            proxy_pass http://backend_pool;
            proxy_http_version 1.1;
            proxy_set_header Upgrade $http_upgrade;
            proxy_set_header Connection "upgrade";
            proxy_read_timeout 3600s;  # 长连接不超时
            proxy_send_timeout 3600s;
        }

        # API 路由
        location /api/ {
            limit_req zone=api burst=10 nodelay;
            proxy_pass http://backend_pool;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_connect_timeout 5s;
            proxy_read_timeout 30s;
        }

        # 认证路由（更严格限流）
        location /api/v1/auth/ {
            limit_req zone=auth burst=3 nodelay;
            proxy_pass http://backend_pool;
        }

        # 前端页面
        location / {
            proxy_pass http://frontend:3000;
            proxy_set_header Host $host;
        }
    }
}
```

---

### 3.3 Redis 缓存策略（`backend/core/cache.py`）

```python
# 各类数据的 TTL 规范（必须严格遵守，不得随意调整）
CACHE_TTL = {
    # 龙虎榜数据：交易日收盘后固定，次日前不变
    "lhb_daily":          3600 * 12,   # 12 小时
    # 游资画像：周度更新
    "trader_profile":     3600 * 168,  # 7 天
    # 连板梯队：盘中每分钟更新，收盘后固定
    "limit_up_ladder":    60,          # 盘中 60 秒；收盘后改 3600*12
    # 情绪指数：盘中实时计算
    "market_sentiment":   300,         # 5 分钟
    # 信号列表：生成后不变（当日有效）
    "signal_list":        3600 * 8,    # 8 小时
    # 用户 Token（JWT 黑名单，用于登出）
    "token_blacklist":    3600 * 24,   # 24 小时
    # 持仓数据：跟随交易实时变化，不缓存过长
    "position_summary":   30,          # 30 秒
}

# Key 命名规范：{模块}:{资源}:{标识符}
# 示例：
#   lhb:daily:20240115
#   trader:profile:营业部ID_123
#   signal:list:20240115
#   user:token_blacklist:jti_xxx
```

---

### 3.4 日志规范（`backend/core/logging.py`）

```python
# 结构化日志格式（JSON），每条日志必须包含以下字段：
{
    "timestamp": "2024-01-15T14:23:01.123Z",  # ISO 8601 UTC
    "level": "INFO",                            # DEBUG/INFO/WARNING/ERROR/CRITICAL
    "service": "backend",                       # 服务名
    "module": "trading.engine",                 # Python 模块路径
    "trace_id": "abc123",                       # 请求链路 ID（从 X-Trace-ID header 传入）
    "user_id": 42,                              # 可选，有登录上下文时附加
    "msg": "Order submitted",                   # 人类可读消息
    "extra": { ... }                            # 业务扩展字段
}
```

**日志级别规范**：

| 场景 | 级别 |
|------|------|
| 正常交易下单/平仓 | `INFO` |
| 风控触发拒绝信号 | `WARNING` |
| 外部数据获取失败（含重试） | `WARNING` |
| 程序异常、未捕获错误 | `ERROR` |
| 数据库连接断开等严重故障 | `CRITICAL` |

**日志轮转**（`infra/logrotate/stock_analysis`）：
```
/app/logs/*.log {
    daily
    rotate 30       # 保留 30 天
    compress
    delaycompress
    missingok
    notifempty
    postrotate
        docker kill --signal=USR1 $(docker ps -qf name=backend)
    endscript
}
```

---

### 3.5 健康检查与监控

**健康检查端点**（`GET /health`，无需认证）：

```json
// 正常响应 HTTP 200
{
    "status": "ok",
    "version": "2.1.0",
    "checks": {
        "postgres": { "status": "ok", "latency_ms": 2 },
        "redis":    { "status": "ok", "latency_ms": 1 },
        "data_feed":{ "status": "ok", "last_sync": "2024-01-15T14:00:00Z" }
    },
    "uptime_seconds": 86400
}

// 任一依赖不健康时 HTTP 503
{ "status": "degraded", "checks": { "postgres": { "status": "error", "error": "connection refused" } } }
```

**Prometheus 指标**（`GET /metrics`，需内网访问）：

```
# 必须暴露的指标（以下为关键子集）
stock_analysis_http_requests_total{method, path, status}   # 请求计数
stock_analysis_http_duration_seconds{method, path}         # 请求耗时直方图
stock_analysis_trading_orders_total{action, result}        # 下单/平仓计数
stock_analysis_risk_alerts_total{level}                    # 风控告警计数
stock_analysis_data_sync_lag_seconds                       # 数据同步延迟
stock_analysis_ws_connections_active                       # 当前 WebSocket 连接数
```

**Grafana 面板**（`infra/grafana/dashboards/overview.json`）：
- 面板 1：QPS + P95/P99 延迟（过去 1 小时）
- 面板 2：错误率（5xx / 4xx 分开展示）
- 面板 3：数据库连接池使用率
- 面板 4：Redis 内存使用率 + 命中率
- 面板 5：WebSocket 在线连接数
- 面板 6：风控告警历史时间轴

**告警规则**（`infra/prometheus/alerts.yml`）：

```yaml
groups:
  - name: stock_analysis_critical
    rules:
      - alert: HighErrorRate
        expr: rate(stock_analysis_http_requests_total{status=~"5.."}[5m]) > 0.05
        for: 2m
        annotations:
          summary: "错误率超过 5%，持续 2 分钟"

      - alert: DataSyncLag
        expr: stock_analysis_data_sync_lag_seconds > 300
        for: 5m
        annotations:
          summary: "数据同步延迟超过 5 分钟，龙虎榜数据可能过时"

      - alert: DatabaseDown
        expr: up{job="postgres"} == 0
        for: 1m
        annotations:
          summary: "PostgreSQL 数据库不可达"
```

---

### 3.6 部署流程（`scripts/deploy.sh`）

```bash
#!/bin/bash
# 零停机部署流程（蓝绿切换）
set -euo pipefail

IMAGE_TAG=${1:-latest}
COMPOSE_FILE="docker-compose.prod.yml"

echo "=== [1/5] 拉取新镜像 ==="
docker compose -f $COMPOSE_FILE pull backend frontend

echo "=== [2/5] 执行数据库迁移 ==="
# 迁移在独立容器中执行，失败则中止部署
docker compose -f $COMPOSE_FILE run --rm backend alembic upgrade head

echo "=== [3/5] 滚动重启 backend（先起新实例再停旧实例）==="
docker compose -f $COMPOSE_FILE up -d --no-deps --scale backend=4 backend
sleep 15   # 等待新实例健康检查通过
docker compose -f $COMPOSE_FILE up -d --no-deps --scale backend=2 backend

echo "=== [4/5] 重启 frontend ==="
docker compose -f $COMPOSE_FILE up -d --no-deps frontend

echo "=== [5/5] 验证部署 ==="
# 连续 3 次 /health 检查通过才算成功
for i in 1 2 3; do
    sleep 5
    STATUS
<function_calls>
<invoke name="Bash">
<parameter name="command">cat >> ~/Develop/personal/stock_analysis/docs/prompts/S05_INFRASTRUCTURE.md << 'PROMPT_EOF'
    STATUS=$(curl -sf http://localhost/health | python3 -c "import sys,json; print(json.load(sys.stdin)['status'])")
    echo "  健康检查 #$i: $STATUS"
    [ "$STATUS" != "ok" ] && echo "❌ 健康检查失败，请手动排查" && exit 1
done

echo "✅ 部署成功 — 镜像版本: $IMAGE_TAG"
```

**回滚流程**（`scripts/rollback.sh`）：
```bash
#!/bin/bash
# 快速回滚到上一个版本
set -euo pipefail

echo "=== 回滚数据库迁移（降级一个版本）==="
docker compose -f docker-compose.prod.yml run --rm backend alembic downgrade -1

echo "=== 回滚容器镜像 ==="
# 约定：previous 标签指向上一个稳定版本（CI 流水线维护）
docker compose -f docker-compose.prod.yml up -d --no-deps backend frontend

echo "✅ 回滚完成，请确认 /health 状态"
```

---

## 4. 环境变量清单（`.env.example` 完整模板）

```bash
# ── 数据库 ──────────────────────────────────────
DATABASE_URL=postgresql+asyncpg://postgres:${DB_PASSWORD}@postgres:5432/stock_analysis
DB_PASSWORD=change_me_in_production          # 生产环境必须修改

# ── Redis ───────────────────────────────────────
REDIS_URL=redis://:${REDIS_PASSWORD}@redis:6379/0
REDIS_PASSWORD=change_me_in_production

# ── 认证 ────────────────────────────────────────
SECRET_KEY=至少64位随机字符串_生产环境必须修改
ACCESS_TOKEN_EXPIRE_MINUTES=30
REFRESH_TOKEN_EXPIRE_DAYS=7

# ── 数据源 ──────────────────────────────────────
TUSHARE_TOKEN=                               # 申请地址：tushare.pro
AKSHARE_PROXY=                               # 可选，国内访问代理

# ── 交易风控 ────────────────────────────────────
MAX_POSITION_RATIO=0.20                      # 单股最大仓位 20%
MAX_DAILY_LOSS_RATIO=0.05                    # 单日最大亏损 5%
MAX_TOTAL_POSITIONS=10                       # 最多同时持有 10 只

# ── 服务配置 ────────────────────────────────────
LOG_LEVEL=INFO                               # DEBUG/INFO/WARNING/ERROR
ENVIRONMENT=production                       # development/staging/production
ALLOWED_ORIGINS=https://yourdomain.com       # CORS 白名单，多个用逗号分隔

# ── 监控 ────────────────────────────────────────
GRAFANA_PASSWORD=change_me_in_production
PROMETHEUS_RETENTION_DAYS=30
```

---

## 5. 验收标准

```bash
# 5.1 所有服务正常启动
docker compose -f docker-compose.prod.yml up -d
docker compose -f docker-compose.prod.yml ps
# 期望：所有服务 Status = Up (healthy)

# 5.2 健康检查通过
curl -s http://localhost/health | python3 -m json.tool
# 期望：{"status": "ok", ...}

# 5.3 HTTPS 重定向生效
curl -I http://localhost
# 期望：301 → https://

# 5.4 WebSocket 可连接
wscat -c wss://localhost/ws/realtime
# 期望：Connected

# 5.5 限流生效（超过 30req/min 返回 429）
for i in $(seq 1 35); do curl -so /dev/null -w "%{http_code}\n" http://localhost/api/v1/lhb/daily; done
# 期望：前 30 次 200，后 5 次 429

# 5.6 Prometheus 指标可抓取
curl -s http://localhost:9090/metrics | grep stock_analysis_http_requests_total

# 5.7 Grafana 可登录
curl -s -u admin:${GRAFANA_PASSWORD} http://localhost:3001/api/health
# 期望：{"database": "ok"}

# 5.8 模拟部署脚本
bash scripts/deploy.sh latest
# 期望：5 个步骤全部 ✅，/health 返回 ok

# 5.9 日志格式验证（JSON 结构）
docker compose logs backend | head -5 | python3 -c "import sys,json; [json.loads(l) for l in sys.stdin]"
# 期望：无解析报错（所有行均为合法 JSON）
```

---

## 6. 禁止事项

- ❌ 不得在镜像中硬编码任何密码、Token、密钥
- ❌ 不得将 `.env` 文件提交到 git（已在 `.gitignore` 中排除）
- ❌ 不得直接暴露 PostgreSQL（5432）和 Redis（6379）端口到公网
- ❌ 不得在 `docker-compose.prod.yml` 中使用 `latest` 标签（必须使用具体版本号或 git SHA）
- ❌ 部署脚本执行前不得跳过数据库迁移步骤
- ❌ 不得删除 Prometheus 数据保留策略（最少保留 30 天）

---

## 7. 输出产物

```
docker-compose.yml                    ← 基础定义
docker-compose.prod.yml               ← 生产完整配置
docker-compose.override.yml           ← 开发环境覆盖（gitignore）
.env.example                          ← 环境变量完整模板
infra/nginx/nginx.conf                ← Nginx 完整配置
infra/prometheus/prometheus.yml       ← Prometheus 抓取配置
infra/prometheus/alerts.yml           ← 告警规则
infra/grafana/dashboards/overview.json ← Grafana 面板定义
backend/core/cache.py                 ← Redis 缓存策略
backend/core/logging.py               ← 结构化日志配置
scripts/deploy.sh                     ← 零停机部署脚本
scripts/rollback.sh                   ← 快速回滚脚本
```
