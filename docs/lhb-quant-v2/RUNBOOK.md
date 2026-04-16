# 龙虎榜量化交易系统 v2 - 运维手册

> 版本: 2.1
> 状态: 待实现
> 最后修订: 2026-04-16

---

## 〇、评审采纳说明

> 二次评审修订日期: 2026-04-16

### 已采纳的改进项 ✅

| 评审问题 | 采纳的改进 |
|----------|------------|
| 紧急操作存在数据安全风险 | 新增管理脚本方案，直接SQL降级为最后手段，增加事务完整性和验证步骤 |
| 巡检完全依赖人工执行 | 拆分为自动巡检（Celery定时任务）和人工巡检两类 |
| 备份脚本存在缺陷 | 重写为健壮版本，增加错误处理、空间检查、验证和过期清理 |
| 故障排查场景不完整 | 补充API不可用、WebSocket断连、OOM、飞书通知失败等场景 |
| 命令与架构文档不一致 | 统一名称（lhb/lhb_quant/kline_daily等），增加对照表 |
| 监控与告警脱节 | 明确监控指标与告警规则的映射关系 |
| 升级流程不安全 | 补充升级前检查清单和回滚验证步骤 |

### 不采纳的建议及原因 ❌

| 评审建议 | 不采纳原因 |
|----------|------------|
| 无 | 本次评审所有高/中优先级问题均合理，已采纳 |

---

## 一、日常巡检

### 1.1 自动巡检（Celery 定时任务执行）

> 设计原则：可自动化的检查由机器执行，异常时才通知人工介入

| 时间 | 检查项 | 自动检查方式 | 异常时动作 |
|------|--------|-------------|-----------|
| 08:30 | 数据库连接 | health_task 执行 `SELECT 1` | 飞书告警 |
| 08:30 | Redis连接 | health_task 执行 `PING` | 飞书告警 |
| 08:30 | 昨日结算完成 | 查 `daily_nav` 表昨日记录 | 自动补算 + 飞书告警 |
| 08:30 | 龙虎榜数据存在 | 查 `lhb_stock_daily` 今日数据 | 自动触发同步 + 告警 |
| 09:40 | Phase B 完成 | 查 `robot_trades` pending→open 转换 | 自动补执行 + 告警 |
| 15:10 | 结算完成 | 查 `daily_nav` 今日记录 | 自动补算 + 告警 |
| 19:00 | Phase A 完成 | 查 `selection_log` 今日记录 | 自动补执行 + 告警 |
| 20:30 | AHS 更新 | 查 `ahs_history` 今日记录 | 自动补算 + 告警 |

**Celery Beat 配置示例：**

```python
# backend/app/tasks/celery_app.py

from celery import Celery
from celery.schedules import crontab

app = Celery('lhb_quant')

app.conf.beat_schedule = {
    # 08:30 早间巡检
    'morning-inspection': {
        'task': 'app.tasks.health_task.morning_inspection',
        'schedule': crontab(hour=8, minute=30, day_of_week='1-5'),
        'options': {'queue': 'default'},
    },
    # 09:40 Phase B 完成检查
    'phase-b-check': {
        'task': 'app.tasks.health_task.check_phase_b_completion',
        'schedule': crontab(hour=9, minute=40, day_of_week='1-5'),
        'options': {'queue': 'trading'},
    },
    # 15:10 结算完成检查
    'settlement-check': {
        'task': 'app.tasks.health_task.check_settlement',
        'schedule': crontab(hour=15, minute=10, day_of_week='1-5'),
        'options': {'queue': 'daily'},
    },
    # 19:00 Phase A 完成检查
    'phase-a-check': {
        'task': 'app.tasks.health_task.check_phase_a_completion',
        'schedule': crontab(hour=19, minute=0, day_of_week='1-5'),
        'options': {'queue': 'trading'},
    },
    # 20:30 AHS 更新检查
    'ahs-check': {
        'task': 'app.tasks.health_task.check_ahs_update',
        'schedule': crontab(hour=20, minute=30, day_of_week='1-5'),
        'options': {'queue': 'daily'},
    },
}
```

### 1.2 人工巡检（需要判断力）

| 时间 | 检查项 | 方法 | 说明 |
|------|--------|------|------|
| 08:30 | 市场特殊事件 | 查看新闻/公告 | 重大政策、黑天鹅事件时考虑暂停交易 |
| 09:20 | 集合竞价异常 | 观察开盘价 | 大面积异常时暂停 Phase B |
| 15:30 | 当日交易回顾 | 查看 Dashboard | 确认交易结果合理，无明显异常 |
| 周五 | 周度汇总 | 查看周报告 | 评估是否需要调整风控参数 |

### 1.3 巡检命令汇总

```bash
# 1. 检查所有服务状态
docker compose ps

# 2. 检查数据库连接
docker compose exec postgres pg_isready -U lhb -d lhb_quant

# 3. 检查 Redis
docker compose exec redis redis-cli ping

# 4. 检查 Celery Worker 状态
docker compose exec celery_worker celery -A app.celery_app inspect active

# 5. 检查 Celery Beat 调度
docker compose exec celery_beat celery -A app.celery_app inspect scheduled

# 6. 检查健康端点（API 服务）
curl -s http://localhost:8000/api/v1/system/health | jq .

# 7. 检查数据新鲜度
curl -s http://localhost:8000/api/v1/system/data-health | jq .

# 8. 检查当前运行中的任务
docker compose exec celery_worker celery -A app.celery_app inspect active

# 9. 查看最近的 Celery 日志
docker compose logs celery_worker --tail=50

# 10. 查看应用错误日志
docker compose logs backend --tail=50 | grep -i error
```

---

## 二、常见故障排查

### 2.1 数据库问题

#### 症状: 连接超时

```
sqlalchemy.exc.OperationalError: (psycopg2.OperationalError) connection timeout
```

**排查步骤：**
1. 检查 PostgreSQL 是否运行：`docker compose ps postgres`
2. 检查端口是否通：`nc -zv localhost 5432`
3. 检查认证信息：`.env` 中的 `POSTGRES_USER`、`POSTGRES_PASSWORD`
4. 检查连接数：
   ```sql
   SELECT count(*), state FROM pg_stat_activity GROUP BY state;
   ```

**解决方案：**
```bash
# 重启数据库
docker compose restart postgres

# 等待就绪后重启后端
docker compose restart backend celery_worker

# 如果连接数过多，查看活跃连接
docker compose exec postgres psql -U lhb -d lhb_quant -c "
SELECT pid, usename, application_name, state, query_start 
FROM pg_stat_activity 
WHERE state = 'active' 
ORDER BY query_start;"
```

#### 症状: 磁盘满

```
sqlalchemy.exc.OperationalError: could not write to file "base/xxxxx": No space left on device
```

**排查步骤：**
```bash
# 检查磁盘空间
df -h

# 检查数据目录大小
docker compose exec postgres du -sh /var/lib/postgresql/data

# 检查 WAL 日志大小
docker compose exec postgres du -sh /var/lib/postgresql/data/pg_wal

# 检查各表大小
docker compose exec postgres psql -U lhb -d lhb_quant -c "
SELECT tablename, pg_size_pretty(pg_total_relation_size(tablename::regclass)) as size
FROM pg_tables WHERE schemaname = 'public'
ORDER BY pg_total_relation_size(tablename::regclass) DESC LIMIT 10;"
```

**解决方案：**
```bash
# 清理 WAL 日志（需要先确认没有从库）
docker compose exec postgres pg_archivecleanup /var/lib/postgresql/data/pg_wal $(date -v-3d +%Y%m%d)

# 清理旧数据（先备份）
docker compose exec postgres psql -U lhb -d lhb_quant -c "
DELETE FROM kline_daily WHERE trade_date < CURRENT_DATE - INTERVAL '90 days';"

# VACUUM 回收空间
docker compose exec postgres psql -U lhb -d lhb_quant -c "VACUUM FULL ANALYZE kline_daily;"
```

### 2.2 Redis 问题

#### 症状: 连接拒绝

```
redis.exceptions.ConnectionError: Error -2 connecting to redis:6379
```

**排查步骤：**
```bash
docker compose ps redis
docker compose exec redis redis-cli ping
docker compose exec redis redis-cli info memory
```

**解决方案：**
```bash
# 重启 Redis
docker compose restart redis

# 检查内存是否满
docker compose exec redis redis-cli info memory | grep used_memory_human

# 如果需要清理（谨慎）
docker compose exec redis redis-cli FLUSHDB
```

### 2.3 Celery 问题

#### 症状: 任务不执行

**排查步骤：**
```bash
# 1. 检查 Worker 是否运行
docker compose ps celery_worker

# 2. 查看 Worker 日志
docker compose logs celery_worker --tail=100

# 3. 检查活跃任务
docker compose exec celery_worker celery -A app.celery_app inspect active

# 4. 检查注册的任务
docker compose exec celery_worker celery -A app.celery_app inspect registered
```

**解决方案：**
```bash
# 重启 Worker
docker compose restart celery_worker

# 清除所有挂起的任务（谨慎）
docker compose exec celery_worker celery -A app.celery_app purge -f

# 如果任务卡死，检查并终止
docker compose exec celery_worker celery -A app.celery_app control revoke <task_id> --terminate
```

#### 症状: Phase A/B 任务错过

**排查步骤：**
```bash
# 查看 Beat 日志确认是否触发
docker compose logs celery_beat --tail=50 | grep -E "(phase_a|phase_b)"

# 检查任务调度
docker compose exec celery_beat celery -A app.celery_app inspect scheduled
```

**解决方案：**
```bash
# 手动触发 Phase A
docker compose exec backend python -c "
from app.tasks.trading_task import run_phase_a
run_phase_a.delay()
"

# 手动触发 Phase B
docker compose exec backend python -c "
from app.tasks.trading_task import run_phase_b
run_phase_b.delay()
"
```

### 2.4 数据问题

#### 症状: 龙虎榜数据缺失

**排查步骤：**
```bash
# 检查最新数据日期
docker compose exec postgres psql -U lhb -d lhb_quant -c "
SELECT MAX(trade_date) as latest FROM lhb_stock_daily;"

# 检查数据源状态
curl -s http://localhost:8000/api/v1/system/health | jq '.providers'
```

**解决方案：**
```bash
# 通过 API 触发同步
curl -X POST http://localhost:8000/api/v1/actions/sync \
  -H "Content-Type: application/json" \
  -d '{"categories": ["lhb_daily"], "trade_date": "2026-04-16"}'

# 或直接执行脚本
docker compose exec backend python -m scripts.sync_lhb --date 2026-04-16
```

#### 症状: 净值计算错误

**排查步骤：**
```bash
# 检查现金和持仓是否一致
docker compose exec postgres psql -U lhb -d lhb_quant -c "
SELECT 
    r.id,
    r.cash_balance,
    COALESCE(SUM(rt.buy_shares * rt.buy_price), 0) as position_cost,
    r.cash_balance + COALESCE(SUM(rt.buy_shares * rt.buy_price), 0) as total
FROM robots r
LEFT JOIN robot_trades rt ON r.id = rt.robot_id AND rt.status IN ('open', 'partial')
GROUP BY r.id;"
```

**解决方案：**
```bash
# 重新计算净值
docker compose exec backend python -m scripts.recalc_nav --robot_id 1

# 重新计算 AHS
docker compose exec backend python -m scripts.recalc_ahs --robot_id 1 --date 2026-04-16
```

### 2.5 后端 API 不可用

#### 症状: curl 返回 connection refused 或 502

**排查步骤：**
```bash
# 1. 检查容器状态
docker compose ps backend

# 2. 查看日志
docker compose logs backend --tail=50

# 3. 检查端口
lsof -i :8000

# 4. 检查健康端点
curl -v http://localhost:8000/api/v1/system/health
```

**常见原因与解决：**

| 原因 | 日志特征 | 解决方案 |
|------|----------|----------|
| 数据库未就绪 | "connection refused :5432" | 等待 PG 启动后重启 backend |
| 端口被占用 | "Address already in use" | `kill $(lsof -t -i:8000)` |
| 依赖缺失 | "ModuleNotFoundError" | `docker compose build --no-cache backend` |
| 配置错误 | "ValidationError" | 检查 `.env` 配置项 |
| OOM | 容器状态 "OOMKilled" | 增加内存限制 |

**恢复命令：**
```bash
# 重启后端
docker compose restart backend

# 如果持续崩溃，前台运行观察
docker compose up backend  # 不加 -d

# 检查依赖服务状态
docker compose exec postgres pg_isready -U lhb
docker compose exec redis redis-cli ping
```

### 2.6 WebSocket 断连

#### 症状: 前端实时数据不更新，但 API 正常

**排查步骤：**
```bash
# 1. 检查 WebSocket 端点
wscat -c ws://localhost:8000/api/v1/ws/realtime

# 2. 检查 Redis Pub/Sub
docker compose exec redis redis-cli SUBSCRIBE quote_updates

# 3. 查看后端日志
docker compose logs backend --tail=50 | grep -i websocket
```

**解决方案：**
```bash
# 重启后端（会断开所有连接）
docker compose restart backend

# 检查 Redis Pub/Sub 是否正常
docker compose exec redis redis-cli PUBLISH quote_updates '{"test": true}'
```

### 2.7 内存溢出（OOM）

#### 症状: docker compose ps 显示容器 Exited (137)

**排查步骤：**
```bash
# 1. 确认 OOM
docker inspect <container_id> | jq '.[0].State.OOMKilled'

# 2. 检查内存使用
docker stats --no-stream

# 3. 查看崩溃前日志
docker compose logs --tail=200 <service>
```

**解决方案：**
```bash
# 临时：增加内存限制（修改 docker-compose.yml）
# deploy:
#   resources:
#     limits:
#       memory: 2G

# Celery Worker：设置内存上限自动重启
# backend/app/tasks/celery_app.py
app.conf.worker_max_memory_per_child = 200000  # 200MB

# 回测任务：限制并发数和时间跨度
docker compose exec backend python -c "
from app.tasks.backtest_task import run_backtest
run_backtest.delay(strategy_id=1, start_date='2025-01-01', end_date='2025-03-31')
"
```

### 2.8 数据源全部不可用

#### 症状: 龙虎榜/K线数据停止更新，data-health 显示 stale

**排查步骤：**
```bash
# 1. 检查网络
curl -I https://tushare.pro
curl -I https://quote.eastmoney.com

# 2. 检查熔断状态
curl -s http://localhost:8000/api/v1/system/health | jq '.providers'

# 3. 查看数据源日志
docker compose logs celery_worker --tail=100 | grep -E "(provider|tushare|akshare)"
```

**手动测试数据源：**
```bash
docker compose exec backend python -c "
import akshare as ak
try:
    df = ak.stock_lhb_detail_em(start_date='20260101', end_date='20260101')
    print(f'AKShare OK: {len(df)} rows')
except Exception as e:
    print(f'AKShare FAIL: {e}')

import tushare as ts
try:
    pro = ts.pro_api()
    df = pro.daily(ts_code='000001.SZ', start_date='20260101', end_date='20260101')
    print(f'TuShare OK: {len(df)} rows')
except Exception as e:
    print(f'TuShare FAIL: {e}')
"
```

**解决方案：**

| 原因 | 解决方案 |
|------|----------|
| 网络问题 | 检查 DNS 和防火墙 |
| IP 被封 | 降低请求频率，等待解封 |
| API 变更 | 检查数据源版本，升级适配器 |
| 全部不可用 | 使用缓存数据（标记 stale），暂停自动交易 |

### 2.9 飞书通知失败

#### 症状: 重要告警未收到飞书消息

**排查步骤：**
```bash
# 1. 测试 Webhook
curl -X POST "${FEISHU_WEBHOOK}" \
  -H "Content-Type: application/json" \
  -d '{"msg_type":"text","content":{"text":"测试消息"}}'

# 2. 检查日志
docker compose logs backend --tail=50 | grep -i feishu

# 3. 检查配置
docker compose exec backend python -c "
import os
print(f'Webhook: {os.environ.get(\"FEISHU_WEBHOOK\", \"NOT SET\")}')"
```

**解决方案：**

| 原因 | 解决方案 |
|------|----------|
| Webhook URL 过期 | 重新获取并更新 `.env` |
| 飞书限流 | 降低通知频率 |
| 网络问题 | 检查服务器出站网络 |

---

## 三、紧急操作

### 3.1 紧急平仓

> **重要**：始终优先使用 API 或管理脚本，直接 SQL 仅作为最后手段

#### 方式1：API 调用（推荐）

```bash
# 平仓单个机器人的所有持仓
curl -X POST http://localhost:8000/api/v1/robots/1/actions/close-all \
  -H "Content-Type: application/json" \
  -d '{"reason": "紧急手动平仓 - 系统故障"}'

# 响应示例
# {"success": true, "closed_trades": 3, "total_amount": 45000.00}
```

#### 方式2：管理脚本（API 不可用时）

```bash
# 执行紧急平仓脚本
docker compose exec backend python -m scripts.emergency_close \
  --robot_id 1 \
  --reason "系统故障紧急平仓" \
  --confirm

# 脚本内部流程：
# 1. 获取所有 open/partial 状态的持仓
# 2. 查询最新收盘价（kline_daily）
# 3. 在单一事务内完成：
#    - 更新 robot_trades.status = 'closed'
#    - 设置 sell_date, sell_price, sell_shares, sell_amount
#    - 计算 realized_pnl, realized_pnl_ratio, hold_days
#    - 插入 trade_executions（sell 记录，含费用）
#    - 插入 cash_ledger（sell 收入）
#    - 更新 robots.cash_balance
#    - 插入 trade_events（type='manual_close'）
#    - 插入 audit_log（紧急平仓操作记录）
# 4. 发送飞书通知
```

#### 方式3：直接 SQL（最后手段，仅限 DBA 操作）

> ⚠️ **严重警告**：仅在 API 和脚本都不可用时使用
> ⚠️ 操作前必须备份数据库
> ⚠️ 操作后必须运行对账脚本验证数据一致性

```bash
# 步骤1：备份
docker compose exec -T postgres pg_dump -U lhb -d lhb_quant --format=custom > /backup/before_emergency_$(date +%Y%m%d_%H%M%S).dump

# 步骤2：确认持仓
docker compose exec postgres psql -U lhb -d lhb_quant -c "
SELECT id, stock_code, stock_name, buy_date, buy_price, buy_shares, buy_amount, status
FROM robot_trades 
WHERE robot_id = 1 AND status IN ('open', 'partial') AND deleted_at IS NULL;"

# 步骤3：执行完整事务（需要人工确认后执行）
# 此处应使用管理脚本生成的 SQL，而非手动编写

# 步骤4：验证数据一致性
docker compose exec backend python -m scripts.verify_cash --robot_id 1
docker compose exec backend python -m scripts.verify_positions --robot_id 1
```

### 3.2 暂停所有机器人

```bash
# 暂停指定机器人
curl -X POST http://localhost:8000/api/v1/robots/1/actions/pause \
  -H "Content-Type: application/json" \
  -d '{"reason": "市场异常暂停"}'

# 批量暂停
curl -X POST http://localhost:8000/api/v1/robots/batch \
  -H "Content-Type: application/json" \
  -d '{"action": "pause", "robot_ids": [1, 2, 3], "reason": "全市场暂停"}'
```

### 3.3 强制停止监控

```bash
# 停止 Celery Worker（停止所有任务）
docker compose stop celery_worker

# 停止后手动检查未完成的交易
docker compose exec postgres psql -U lhb -d lhb_quant -c "
SELECT robot_id, stock_code, status, buy_date, hold_days
FROM robot_trades 
WHERE status IN ('open', 'partial') AND deleted_at IS NULL;"
```

### 3.4 数据修复脚本

```bash
# 修复持仓状态不一致
docker compose exec backend python -m scripts.fix_positions --robot_id 1 --dry-run
docker compose exec backend python -m scripts.fix_positions --robot_id 1 --execute

# 重新计算 AHS 评分
docker compose exec backend python -m scripts.recalc_ahs --robot_id 1 --date 2026-04-16

# 重新计算净值
docker compose exec backend python -m scripts.recalc_nav --robot_id 1 --start_date 2026-01-01

# 导入交易日历
docker compose exec backend python -m scripts.import_calendar --year 2026
```

---

## 四、备份与恢复

### 4.1 备份策略

| 数据类型 | 备份频率 | 保留时间 | 备份方式 |
|----------|----------|----------|----------|
| 数据库全量 | 每日 03:00 | 30 天 | pg_dump --format=custom |
| 数据库 WAL | 实时 | 7 天 | 连续归档 |
| Redis 数据 | 每小时 | 24 小时 | BGSAVE |
| 配置文件 | 每次变更 | 90 天 | git |
| 日志文件 | 每日归档 | 7 天 | logrotate |

### 4.2 备份脚本（健壮版本）

```bash
#!/bin/bash
# scripts/backup.sh
set -euo pipefail

# ==================== 配置 ====================
BACKUP_DIR="${BACKUP_DIR:-./backup/db}"
RETENTION_DAYS=30
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
DB_USER="${POSTGRES_USER:-lhb}"
DB_NAME="${POSTGRES_DB:-lhb_quant}"
MIN_BACKUP_SIZE_KB=100

# ==================== 初始化 ====================
mkdir -p "$BACKUP_DIR"
LOG_FILE="${BACKUP_DIR}/backup.log"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"
}

# ==================== 前置检查 ====================
log "[INFO] 开始备份流程"

# 检查磁盘空间（至少保留 2GB）
AVAILABLE_KB=$(df "$BACKUP_DIR" | tail -1 | awk '{print $4}')
if [ "$AVAILABLE_KB" -lt 2097152 ]; then
    log "[ERROR] 磁盘空间不足: ${AVAILABLE_KB}KB 可用，至少需要 2GB"
    exit 1
fi

# ==================== 数据库备份 ====================
log "[INFO] 开始数据库备份..."
DB_BACKUP="${BACKUP_DIR}/db_${TIMESTAMP}.dump"

if ! docker compose exec -T postgres pg_dump \
    -U "$DB_USER" \
    -d "$DB_NAME" \
    --format=custom \
    --compress=6 \
    > "$DB_BACKUP" 2>/dev/null; then
    log "[ERROR] pg_dump 执行失败"
    rm -f "$DB_BACKUP"
    exit 1
fi

# 验证备份文件
FILESIZE_KB=$(du -k "$DB_BACKUP" | cut -f1)
if [ "$FILESIZE_KB" -lt "$MIN_BACKUP_SIZE_KB" ]; then
    log "[ERROR] 备份文件异常: ${FILESIZE_KB}KB < ${MIN_BACKUP_SIZE_KB}KB"
    rm -f "$DB_BACKUP"
    exit 1
fi

log "[INFO] 数据库备份完成: $DB_BACKUP (${FILESIZE_KB}KB)"

# ==================== Redis 备份 ====================
log "[INFO] 开始 Redis 备份..."
REDIS_BACKUP="${BACKUP_DIR}/redis_${TIMESTAMP}.rdb"

# 触发 BGSAVE
docker compose exec -T redis redis-cli BGSAVE > /dev/null 2>&1

# 等待 BGSAVE 完成（最多30秒）
for i in $(seq 1 30); do
    SAVE_STATUS=$(docker compose exec -T redis redis-cli LASTSAVE 2>/dev/null)
    sleep 1
    NEW_STATUS=$(docker compose exec -T redis redis-cli LASTSAVE 2>/dev/null)
    if [ "$SAVE_STATUS" != "$NEW_STATUS" ] || [ "$i" -eq 1 ]; then
        break
    fi
    if [ "$i" -eq 30 ]; then
        log "[WARN] Redis BGSAVE 超时，使用当前 RDB"
    fi
done

# 复制 RDB 文件
docker compose cp redis:/data/dump.rdb "$REDIS_BACKUP" 2>/dev/null || {
    log "[WARN] Redis RDB 复制失败，可能数据为空"
    touch "$REDIS_BACKUP"
}

log "[INFO] Redis 备份完成"

# ==================== 清理过期备份 ====================
log "[INFO] 清理过期备份..."
DELETED_DB=$(find "$BACKUP_DIR" -name "db_*.dump" -mtime +${RETENTION_DAYS} -delete -print 2>/dev/null | wc -l || echo 0)
DELETED_REDIS=$(find "$BACKUP_DIR" -name "redis_*.rdb" -mtime +${RETENTION_DAYS} -delete -print 2>/dev/null | wc -l || echo 0)
log "[INFO] 清理完成: DB=${DELETED_DB}个, Redis=${DELETED_REDIS}个"

# ==================== 记录备份日志 ====================
echo "${TIMESTAMP},${DB_BACKUP},${FILESIZE_KB}KB,success" >> "${BACKUP_DIR}/backup.csv"

log "[INFO] 备份流程完成"
```

### 4.3 恢复命令

```bash
# 恢复数据库（custom 格式）
docker compose exec -T postgres pg_restore \
    -U lhb \
    -d lhb_quant \
    --clean \
    --if-exists \
    < /backup/db_20260416_030000.dump

# 恢复 Redis
docker compose stop redis
cp /backup/redis_20260416_030000.rdb ./data/redis/dump.rdb
docker compose start redis

# 验证数据完整性
docker compose exec backend python -m scripts.verify_cash --all-robots
docker compose exec backend python -m scripts.verify_positions --all-robots
```

---

## 五、日志管理

### 5.1 日志目录结构

```
logs/
├── app/                  # 应用日志
│   ├── app.log          # 主应用日志
│   ├── error.log        # 错误日志（独立）
│   └── trade.log        # 交易专用日志（保留更久）
├── celery/              # Celery 日志
│   ├── worker.log
│   └── beat.log
└── archive/             # 归档日志
```

### 5.2 日志查看命令

```bash
# 实时查看应用日志
docker compose logs -f backend

# 查看最近50行错误日志
docker compose logs backend --tail=50 | grep -i error

# 查看特定机器人的交易日志
docker compose logs backend --tail=100 | grep "robot_id=1"

# 查看 Celery Worker 日志
docker compose logs celery_worker --tail=100

# 查看特定时间段的日志
docker compose logs backend --since="2026-04-16T09:00:00" --until="2026-04-16T10:00:00"
```

### 5.3 日志轮转配置

> 参见 [ARCHITECTURE.md - 日志架构](./ARCHITECTURE.md#八日志架构)

```python
# backend/app/core/logging.py
from loguru import logger
import sys

# 移除默认处理器
logger.remove()

# 控制台输出（开发环境）
logger.add(
    sys.stdout,
    level="DEBUG" if DEBUG else "INFO",
    format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | {message}",
)

# 主应用日志
logger.add(
    "logs/app/app.log",
    rotation="00:00",          # 每日轮转
    retention="14 days",      # 保留14天
    compression="gz",         # 压缩
    level="INFO",
    format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} | {message}",
    enqueue=True,             # 异步写入
)

# 错误日志（独立文件，保留更久）
logger.add(
    "logs/app/error.log",
    rotation="00:00",
    retention="90 days",
    compression="gz",
    level="ERROR",
    format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} | {message}\n{exception}",
    enqueue=True,
)

# 交易专用日志（保留更久）
logger.add(
    "logs/app/trade.log",
    rotation="00:00",
    retention="90 days",
    compression="gz",
    level="INFO",
    format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {message}",
    filter=lambda record: "trade" in record["extra"],
    enqueue=True,
)
```

---

## 六、性能监控

### 6.1 监控指标与告警

| 指标 | 正常范围 | 告警阈值 | 告警级别 | 通知方式 |
|------|----------|----------|----------|----------|
| 数据库连接数 | < 50 | > 80 | WARN | 飞书群 |
| API 响应时间 | < 200ms | > 1000ms | WARN | 飞书群 |
| Celery 任务队列 | < 100 | > 500 | WARN | 飞书群 |
| 磁盘使用率 | < 70% | > 85% | WARN | 飞书群 |
| 内存使用率 | < 80% | > 90% | CRITICAL | 飞书@人 |
| CPU 使用率 | < 70% | > 90% 持续5分钟 | CRITICAL | 飞书电话 |
| 数据新鲜度 | < 1天 | > 1天 | CRITICAL | 飞书电话 |
| 数据源熔断 | 无 | 任意数据源熔断 | WARN | 飞书群 |

### 6.2 监控命令

```bash
# 数据库连接数
docker compose exec postgres psql -U lhb -d lhb_quant -c "
SELECT count(*) as connections, state 
FROM pg_stat_activity 
GROUP BY state;"

# 表大小排行
docker compose exec postgres psql -U lhb -d lhb_quant -c "
SELECT 
    tablename,
    pg_size_pretty(pg_total_relation_size(tablename::regclass)) as total_size,
    pg_size_pretty(pg_relation_size(tablename::regclass)) as table_size,
    pg_size_pretty(pg_indexes_size(tablename::regclass)) as index_size
FROM pg_tables 
WHERE schemaname = 'public'
ORDER BY pg_total_relation_size(tablename::regclass) DESC 
LIMIT 10;"

# API 响应时间
curl -w "\n响应时间: %{time_total}s\nHTTP状态: %{http_code}\n" \
    -o /dev/null -s http://localhost:8000/api/v1/robots

# Celery 队列状态
docker compose exec celery_worker celery -A app.celery_app inspect stats

# Docker 资源使用
docker stats --no-stream --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.NetIO}}"
```

---

## 七、版本升级

### 7.1 升级前检查清单

```bash
# 1. 确认当前版本
docker compose exec backend python -c "from app import __version__; print(__version__)"

# 2. 检查数据库迁移状态
docker compose exec backend alembic current

# 3. 检查是否有正在运行的任务
docker compose exec celery_worker celery -A app.celery_app inspect active

# 4. 检查持仓状态（确保没有 pending 状态的异常持仓）
docker compose exec postgres psql -U lhb -d lhb_quant -c "
SELECT robot_id, COUNT(*) as open_positions
FROM robot_trades 
WHERE status IN ('open', 'partial') AND deleted_at IS NULL
GROUP BY robot_id;"

# 5. 备份数据库
./scripts/backup.sh

# 6. 通知相关方（如使用飞书）
curl -X POST "${FEISHU_WEBHOOK}" \
  -H "Content-Type: application/json" \
  -d '{"msg_type":"text","content":{"text":"系统将在 10 分钟后进行升级维护"}}'
```

### 7.2 升级步骤

```bash
# 1. 停止服务（保留数据库和 Redis）
docker compose stop backend celery_worker celery_beat

# 2. 更新代码
git pull origin main

# 3. 检查变更内容
git log --oneline HEAD~5..HEAD
git diff HEAD~1 --name-only

# 4. 重新构建镜像
docker compose build --no-cache backend celery_worker

# 5. 执行数据库迁移（如有）
docker compose run --rm backend alembic upgrade head

# 6. 启动服务
docker compose up -d

# 7. 等待服务就绪
sleep 10

# 8. 验证健康状态
curl -s http://localhost:8000/api/v1/system/health | jq .

# 9. 验证 Celery Worker
docker compose exec celery_worker celery -A app.celery_app inspect ping

# 10. 通知升级完成
curl -X POST "${FEISHU_WEBHOOK}" \
  -H "Content-Type: application/json" \
  -d '{"msg_type":"text","content":{"text":"系统升级完成，已恢复正常运行"}}'
```

### 7.3 回滚步骤

```bash
# 1. 停止服务
docker compose stop backend celery_worker celery_beat

# 2. 回滚代码
git log --oneline -5  # 找到上一个稳定版本的 commit
git checkout <previous_commit_hash>

# 3. 回滚数据库迁移（如需要）
docker compose run --rm backend alembic downgrade -1

# 4. 重新构建并启动
docker compose build --no-cache backend celery_worker
docker compose up -d

# 5. 验证
curl -s http://localhost:8000/api/v1/system/health | jq .
docker compose exec celery_worker celery -A app.celery_app inspect ping

# 6. 恢复主分支（如回滚成功）
git checkout main
```

---

## 八、名称统一对照表

> 所有文档和命令应使用右列统一名称

| 项目 | 应统一为 | 来源 |
|------|----------|------|
| 数据库用户 | `lhb` | docker-compose.yml |
| 数据库名 | `lhb_quant` | docker-compose.yml |
| K线表 | `kline_daily` | DATABASE.md |
| 龙虎榜日表 | `lhb_stock_daily` | DATABASE.md |
| Celery App 路径 | `app.celery_app` | ARCHITECTURE.md |
| 错误类型前缀 | `sqlalchemy.exc.` 或 `psycopg2.` | 技术栈为 FastAPI+SQLAlchemy |

---

## 九、运维脚本清单

> 参见 [PLAN.md - 目录结构](./PLAN.md#四目录结构)

```
scripts/
├── init_db.sql              # 数据库初始化 DDL
├── migrate_data.py          # 数据迁移（从旧系统）
├── seed_data.py             # 测试数据生成
├── backup.sh                # 数据库备份（本文档 §4.2）
├── restore.sh               # 数据库恢复（本文档 §4.3）
├── emergency_close.py       # 紧急平仓（本文档 §3.1）
├── verify_cash.py           # 资金对账验证
├── verify_positions.py      # 持仓状态验证
├── recalc_ahs.py            # AHS 评分重算
├── recalc_nav.py            # 净值重算
├── import_calendar.py       # 交易日历导入
└── sync_lhb.py              # 龙虎榜数据同步
```

---

## 十、联系信息

| 角色 | 职责 | 联系方式 |
|------|------|----------|
| 开发者 | 技术问题、Bug 修复 | - |
| 运维 | 服务故障、基础设施 | - |
| 数据源 | 数据质量问题 | TuShare/AKShare 官方渠道 |

---

## 十一、版本历史

| 版本 | 日期 | 修改内容 |
|------|------|----------|
| 1.0.0 | 2026-04-16 | 初始版本 |
| 2.1.0 | 2026-04-16 | 评审修订：增加自动巡检、健壮备份脚本、补充故障场景、统一名称对照表 |

---

## 十二、参考文档

| 文档 | 关系 |
|------|------|
| [ARCHITECTURE.md](./ARCHITECTURE.md) | 系统架构和日志配置定义 |
| [DATABASE.md](./DATABASE.md) | 表结构定义 |
| [RISK_CONTROL.md](./RISK_CONTROL.md) | 风控参数配置 |
| [PLAN.md](./PLAN.md) | 运维脚本目录结构 |

---

*文档版本: 2.1*
*最后修订: 2026-04-16*
