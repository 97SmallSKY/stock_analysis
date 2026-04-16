# 龙虎榜量化交易系统 v2 - 部署指南

> 本文档定义 Docker Compose 部署方案。

---

## 一、环境要求

| 组件 | 版本 | 说明 |
|------|------|------|
| Docker | 24+ | 容器运行时 |
| Docker Compose | 2.20+ | 编排工具 |
| 内存 | ≥ 4GB | 建议配置 |
| 磁盘 | ≥ 50GB | 数据持久化 |

---

## 二、快速部署

### 2.1 克隆代码

```bash
git clone https://github.com/97SmallSKY/stock_analysis.git
cd stock_analysis
git checkout v2-dev
```

### 2.2 配置环境变量

```bash
cp .env.example .env
# 编辑 .env 填写实际配置
```

**必须配置项：**
- `POSTGRES_PASSWORD` - 数据库密码
- `SECRET_KEY` - 应用密钥
- `TUSHARE_TOKEN` - TuShare API Token

### 2.3 启动服务

```bash
make start
```

### 2.4 访问服务

| 服务 | 地址 |
|------|------|
| 前端 | http://localhost:3000 |
| 后端 API | http://localhost:8000 |
| API 文档 | http://localhost:8000/docs |

---

## 三、生产部署

### 3.1 使用生产配置

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

### 3.2 生产配置差异

| 配置项 | 开发环境 | 生产环境 |
|--------|----------|----------|
| restart | unless-stopped | always |
| Redis 密码 | 无 | 必须设置 |
| 日志级别 | DEBUG | WARNING |
| 资源限制 | 无 | CPU/内存限制 |

### 3.3 Nginx 反向代理（可选）

```nginx
server {
    listen 80;
    server_name example.com;

    location /api {
        proxy_pass http://localhost:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }

    location / {
        proxy_pass http://localhost:3000;
    }
}
```

---

## 四、数据持久化

### 4.1 Named Volumes

| 卷名 | 挂载点 | 说明 |
|------|--------|------|
| lhb-pgdata | /var/lib/postgresql/data | PostgreSQL 数据 |
| lhb-redisdata | /data | Redis 数据 |

### 4.2 备份策略

```bash
# 手动备份
make db-backup

# 定时备份（crontab）
0 2 * * * cd /path/to/project && make db-backup
```

---

## 五、监控

### 5.1 Prometheus 配置

```yaml
# monitoring/prometheus.yml
global:
  scrape_interval: 15s

scrape_configs:
  - job_name: 'backend'
    static_configs:
      - targets: ['backend:8000']
```

### 5.2 Grafana 仪表盘

访问 `http://localhost:3001`，默认账号 admin/admin

---

## 六、故障排查

### 6.1 服务无法启动

```bash
# 检查日志
docker compose logs -f backend

# 检查端口占用
lsof -i :8000
```

### 6.2 数据库连接失败

```bash
# 检查数据库状态
docker compose exec postgres pg_isready

# 进入数据库
docker compose exec postgres psql -U postgres
```
