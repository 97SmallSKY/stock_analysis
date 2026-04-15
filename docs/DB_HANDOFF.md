# 数据库交接文档

> 本文档记录 stock_analysis 项目的数据库配置、运维操作和数据管理规范。

---

## 一、数据库架构

### 1.1 容器信息

| 项目 | 值 |
|------|-----|
| **容器名称** | `stock_analysis-db` |
| **镜像** | `postgres:15-alpine` |
| **Docker Compose 文件** | `docker-compose.yml` |
| **数据卷** | `stock_analysis_stock-db-data` (named volume) |
| **端口** | `0.0.0.0:5432` → `5432` (容器内) |
| **状态** | ✅ 运行中 |

### 1.2 数据库信息

| 项目 | 值 |
|------|-----|
| **数据库名** | `stock_analysis` |
| **用户名** | `stock_user` |
| **密码** | `Stock2026!@#` |
| **编码** | UTF8 |
| **PostgreSQL 版本** | 15.17 |

### 1.3 Docker Compose 配置

```yaml
services:
  stock-db:
    image: postgres:15-alpine
    container_name: stock_analysis-db
    restart: unless-stopped
    environment:
      POSTGRES_DB: stock_analysis
      POSTGRES_USER: stock_user
      POSTGRES_PASSWORD: Stock2026!@#
    ports:
      - "0.0.0.0:5432:5432"
    volumes:
      - stock-db-data:/var/lib/postgresql/data

volumes:
  stock-db-data:
```

---

## 二、连接配置

### 2.1 远程服务器（京东云 VPS）

```bash
# 方式一：通过 Docker 容器直接连接
docker exec -it stock_analysis-db psql -U stock_user -d stock_analysis

# 方式二：通过端口连接（需安装 psql 客户端）
psql -h 127.0.0.1 -p 5432 -U stock_user -d stock_analysis
```

### 2.2 本地开发环境

修改 `.env` 文件：

```env
STORAGE_MODE=postgres
PG_HOST=117.72.184.8
PG_PORT=5432
PG_DATABASE=stock_analysis
PG_USER=stock_user
PG_PASSWORD=Stock2026!@#
PG_TABLE_PREFIX=
```

### 2.3 连接测试

```bash
# 本地测试连接
python -c "
from utils.storage import storage
df = storage.read('market_sentiment')
print(f'连接成功！market_sentiment 表有 {len(df)} 行')
"
```

---

## 三、运维命令

### 3.1 Docker Compose 管理

```bash
# 启动服务
docker compose up -d

# 查看状态
docker compose ps

# 查看日志
docker compose logs -f

# 停止服务（数据保留）
docker compose down

# 停止服务并删除数据（⚠️ 谨慎使用！）
docker compose down -v
```

### 3.2 数据库管理

```bash
# 连接数据库
docker exec -it stock_analysis-db psql -U stock_user -d stock_analysis

# 查看所有表
\dt

# 查看表结构和行数
\d+ tablename

# 查看表统计信息
SELECT tablename, n_live_tup FROM pg_stat_user_tables WHERE schemaname='public';

# 退出
\q
```

### 3.3 数据备份与恢复

```bash
# 备份整个数据库
docker exec stock_analysis-db pg_dump -U stock_user -d stock_analysis > backup_$(date +%Y%m%d).sql

# 备份指定表
docker exec stock_analysis-db pg_dump -U stock_user -d stock_analysis -t table_name > table_backup.sql

# 恢复数据库
docker exec -i stock_analysis-db psql -U stock_user -d stock_analysis < backup_20260415.sql

# Docker Volume 备份
docker run --rm -v stock_analysis_stock-db-data:/data -v $(pwd):/backup alpine tar czf /backup/volume_backup.tar.gz /data

# Docker Volume 恢复
docker run --rm -v stock_analysis_stock-db-data:/data -v $(pwd):/backup alpine tar xzf /backup/volume_backup.tar.gz -C /
```

### 3.4 清理和重建

```bash
# 停止并重建容器（保留数据）
docker compose down && docker compose up -d

# 完全重建（⚠️ 删除所有数据！）
docker compose down -v
docker compose up -d
```

---

## 四、数据表说明

> 详细字段说明请参考 `docs/database.md`

### 4.1 核心业务表

| 表名 | 说明 | 备注 |
|------|------|------|
| `lhb_top10` | 龙虎榜净买入 Top10 | 策略分析基础数据 |
| `lhb_top10_prices` | T+N 价格与收益 | 回测核心数据 |
| `lhb_detail` | 龙虎榜明细 | 每日上榜股票 |
| `lhb_seat_detail` | 席位明细 | 营业部买卖数据 |
| `lhb_jigou` | 机构汇总 | 机构席位统计 |
| `lhb_ggtj` | 个股上榜统计 | 累计上榜数据 |
| `market_sentiment` | 市场情绪 | 涨跌停统计 |

### 4.2 模拟交易表

| 表名 | 说明 | 备注 |
|------|------|------|
| `sim_trades` | 模拟交易记录 | 核心交易表 |
| `sim_trade_events` | 交易事件日志 | 事件追踪 |
| `sim_audit_log` | 审计日志 | 不可删除 |
| `sim_selection_log` | 选股日志 | 选股理由记录 |
| `sim_summary` | 交易汇总 | 周报数据 |
| `sim_price_log` | 价格日志 | 盘中监控 |
| `sim_config` | 配置表 | AHS 配置 |

---

## 五、数据管理规范

### 5.1 重要提示

> ⚠️ **数据已丢失**：由于 Docker 容器和 volume 被意外清理，原有数据（2026-04-14 及之前）已无法恢复。

### 5.2 备份策略

| 备份类型 | 频率 | 保留时间 | 存储位置 |
|----------|------|----------|----------|
| 每日自动备份 | 每天 23:00 | 7 天 | `/root/stock_analysis/backups/` |
| 每周全量备份 | 每周日 22:00 | 4 周 | `/root/stock_analysis/backups/weekly/` |
| 手动备份 | 按需 | 自定义 | 本地/云存储 |

### 5.3 备份脚本

```bash
#!/bin/bash
# /root/stock_analysis/scripts/backup_db.sh

BACKUP_DIR="/root/stock_analysis/backups"
DATE=$(date +%Y%m%d)
CONTAINER="stock_analysis-db"
DB_USER="stock_user"
DB_NAME="stock_analysis"

mkdir -p $BACKUP_DIR

# 备份数据库
docker exec $CONTAINER pg_dump -U $DB_USER -d $DB_NAME > "$BACKUP_DIR/backup_$DATE.sql"

# 保留最近 7 天的备份
find $BACKUP_DIR -name "backup_*.sql" -mtime +7 -delete

echo "备份完成: $BACKUP_DIR/backup_$DATE.sql"
```

### 5.4 数据恢复流程

1. 停止应用服务
2. 确认备份文件存在
3. 执行数据恢复
4. 验证数据完整性
5. 重启应用服务

---

## 六、故障排查

### 6.1 常见问题

| 问题 | 原因 | 解决方案 |
|------|------|----------|
| 连接失败 | 端口被占用 | `docker compose down` 后重新启动 |
| 连接失败 | 密码错误 | 检查 `.env` 中的密码 |
| 数据丢失 | volume 被删除 | 使用备份恢复 |
| 启动失败 | 端口冲突 | 检查 `lsof -i :5432` |

### 6.2 诊断命令

```bash
# 检查容器状态
docker ps -a | grep stock

# 检查端口占用
lsof -i :5432

# 检查 volume
docker volume ls | grep stock

# 检查日志
docker compose logs stock-db
```

---

## 七、安全建议

1. **定期更换密码**：建议每 3 个月更换一次数据库密码
2. **限制访问**：确保防火墙只开放必要端口
3. **备份验证**：定期测试备份文件的可恢复性
4. **监控告警**：配置数据库连接失败告警

---

## 八、联系信息

如有问题，请检查：
1. `AGENTS.md` - 开发规范
2. `docs/database.md` - 数据库详细文档
3. `docker-compose.yml` - 容器配置
