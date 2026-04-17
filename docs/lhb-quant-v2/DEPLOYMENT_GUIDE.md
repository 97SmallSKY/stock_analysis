# S01 数据层部署指南

> **版本**: v1.0  
> **最后更新**: 2026-04-17  
> **维护者**: Zhang Xiaotian

---

## 📋 概述

本指南详细说明如何部署和配置龙虎榜量化交易系统 v2 的数据层（S01）。

---

## 🏗️ 系统架构

```
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│   采集器        │    │   调度器        │    │   数据库        │
│  Collectors     │───▶│   Scheduler     │───▶│  PostgreSQL     │
│                 │    │                 │    │                 │
│ - LhbCollector  │    │ - APScheduler   │    │ - 28张表        │
│ - MarketColl    │    │ - 定时任务      │    │ - 84个索引      │
│ - KlineColl     │    │ - 重试机制      │    │ - 8个触发器     │
└─────────────────┘    └─────────────────┘    └─────────────────┘
         │                       │                       │
         ▼                       ▼                       ▼
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│   AKShare API   │    │   监控告警      │    │   备份恢复      │
│                 │    │                 │    │                 │
│ - 龙虎榜        │    │ - 日志记录      │    │ - 定时备份      │
│ - 市场情绪      │    │ - 错误告警      │    │ - 数据恢复      │
└─────────────────┘    └─────────────────┘    └─────────────────┘
```

---

## 🚀 快速开始

### 1. 环境要求

- **Python**: 3.10+
- **PostgreSQL**: 14+
- **内存**: 最少 2GB
- **磁盘**: 最少 10GB

### 2. 安装依赖

```bash
# 克隆仓库
git clone https://github.com/97SmallSKY/stock_analysis.git
cd stock_analysis

# 创建虚拟环境
python -m venv .venv
source .venv/bin/activate  # Linux/macOS
# .venv\Scripts\activate   # Windows

# 安装依赖
pip install -r backend/requirements.txt
```

### 3. 配置环境变量

```bash
# 复制环境变量模板
cp .env.example .env

# 编辑配置
vim .env
```

**必需配置**:
```bash
# 数据库配置
PG_HOST=117.72.184.8
PG_PORT=5432
PG_DATABASE=stock_analysis
PG_USER=stock_user
PG_PASSWORD=Stock2026!@#
PG_TABLE_PREFIX=

# 采集器配置
AKSHARE_TIMEOUT=30
AKSHARE_RETRY_COUNT=3

# 调度器配置
SCHEDULER_ENABLED=true
SCHEDULER_TIMEZONE=Asia/Shanghai
```

### 4. 数据库初始化

```bash
# 方式1: 使用初始化脚本
python scripts/init_database.py

# 方式2: 手动执行 SQL
psql -h $PG_HOST -U $PG_USER -d $PG_DATABASE -f backend/db/migrations/001_init_schema.sql
psql -h $PG_HOST -U $PG_USER -d $PG_DATABASE -f backend/db/migrations/002_init_indexes.sql
psql -h $PG_HOST -U $PG_USER -d $PG_DATABASE -f backend/db/migrations/003_init_triggers.sql
psql -h $PG_HOST -U $PG_USER -d $PG_DATABASE -f backend/db/migrations/004_init_seed_data.sql
```

### 5. 验证安装

```bash
# 运行快速验收测试
python scripts/quick_acceptance_test.py

# 预期输出:
# ============================================================
#   S01 数据层快速验收测试
# ============================================================
# 
# [测试1] 数据库连接            ✓ 通过
# [测试2] 核心表存在性           ✓ 通过（9张核心表）
# [测试3] 数据统计              ✓ 通过
# [测试4] 索引检查              ✓ 通过（84个）
# [测试5] 视图检查              ✓ 通过（4个）
# 
# 通过: 5 个
# 失败: 0 个
# 成功率: 100.0%
# 
# 🎉 所有测试通过！
```

---

## 📅 日常运行

### 1. 启动调度器

```bash
# 启动定时采集任务
python scripts/run_scheduler.py

# 后台运行（使用 nohup）
nohup python scripts/run_scheduler.py > logs/scheduler.log 2>&1 &

# 查看日志
tail -f logs/scheduler.log
```

### 2. 手动采集

```bash
# 采集指定日期的数据
python -c "
import asyncio
from backend.db.session import AsyncSessionLocal
from backend.app.collectors import LhbCollector
from datetime import date

async def manual_collect():
    async with AsyncSessionLocal() as db:
        collector = LhbCollector(db)
        result = await collector.run(trade_date=date(2024, 4, 16))
        print(result)

asyncio.run(manual_collect())
"
```

### 3. 数据回填

```bash
# 回填最近 N 天的数据
python scripts/backfill_data.py --days 60

# 回填指定日期范围
python scripts/backfill_data.py --start 2024-01-01 --end 2024-04-16
```

---

## 🔧 监控和维护

### 1. 健康检查

```bash
# 运行健康检查脚本
python scripts/health_check.py

# 输出示例:
# 数据库连接: ✓ 正常
# 调度器状态: ✓ 运行中
# 最近采集: ✓ 2024-04-16 15:10:23
# 错误日志: ✓ 无错误
```

### 2. 日志查看

```bash
# 查看调度器日志
tail -f logs/scheduler.log

# 查看采集器日志
tail -f logs/collector.log

# 查看错误日志
grep ERROR logs/*.log
```

### 3. 数据库维护

```bash
# 检查表大小
psql $DATABASE_URL -c "
SELECT 
    schemaname,
    tablename,
    pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename)) AS size
FROM pg_tables 
WHERE schemaname = 'public'
ORDER BY pg_total_relation_size(schemaname||'.'||tablename) DESC;
"

# 清理过期数据（谨慎操作）
python scripts/cleanup_old_data.py --days 365
```

---

## 🔄 更新和升级

### 1. 代码更新

```bash
# 拉取最新代码
git pull origin v2-dev

# 安装新依赖
pip install -r backend/requirements.txt --upgrade

# 执行数据库迁移（如果有）
psql $DATABASE_URL -f backend/db/migrations/005_new_feature.sql

# 重启调度器
pkill -f run_scheduler.py
nohup python scripts/run_scheduler.py > logs/scheduler.log 2>&1 &
```

### 2. 依赖升级

```bash
# 检查依赖更新
pip list --outdated

# 升级特定包
pip install --upgrade akshare sqlalchemy

# 更新依赖文件
pip freeze > backend/requirements.txt
```

---

## 🛡️ 故障排除

### 常见问题

#### 1. 数据库连接失败

**症状**:
```
psycopg2.OperationalError: could not connect to server
```

**排查**:
```bash
# 检查网络连通性
ping 117.72.184.8

# 检查端口
telnet 117.72.184.8 5432

# 检查数据库状态
psql -h 117.72.184.8 -U stock_user -d stock_analysis -c "SELECT 1"
```

**解决**:
- 确认数据库服务正在运行
- 检查防火墙规则
- 验证连接参数

#### 2. 采集器执行失败

**症状**:
```
KeyError: '上榜日'
```

**排查**:
```bash
# 检查 AKShare 版本
pip show akshare

# 测试 API 调用
python -c "
import akshare as ak
df = ak.stock_lhb_detail_em(start_date='20240416', end_date='20240416')
print(df.columns.tolist())
"
```

**解决**:
- 更新 AKShare 到最新版本
- 检查字段映射是否正确
- 查看采集器日志

#### 3. 调度器无法启动

**症状**:
```
ModuleNotFoundError: No module named 'apscheduler'
```

**解决**:
```bash
# 重新安装依赖
pip install -r backend/requirements.txt

# 检查 Python 路径
which python
python -c "import sys; print(sys.path)"
```

---

## 📊 性能优化

### 1. 数据库优化

```sql
-- 添加缺失的索引
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_lhb_stock_daily_date 
ON lhb_stock_daily(trade_date);

-- 分析表统计信息
ANALYZE lhb_stock_daily;
ANALYZE market_sentiment;

-- 检查慢查询
SELECT query, calls, total_time, mean_time
FROM pg_stat_statements 
WHERE query LIKE '%lhb_stock_daily%'
ORDER BY total_time DESC
LIMIT 10;
```

### 2. 采集器优化

```python
# 使用连接池
from sqlalchemy.pool import QueuePool

engine = create_engine(
    DATABASE_URL,
    poolclass=QueuePool,
    pool_size=20,
    max_overflow=40,
    pool_recycle=3600
)

# 批量插入
def bulk_insert_records(records, batch_size=1000):
    for i in range(0, len(records), batch_size):
        batch = records[i:i+batch_size]
        # 执行插入
```

### 3. 调度器优化

```python
# 使用进程池
from apscheduler.executors.pool import ProcessPoolExecutor

scheduler.add_executor(ProcessPoolExecutor(4))

# 分布式调度
# 考虑使用 Redis 作为 jobstore
from apscheduler.jobstores.redis import RedisJobStore

jobstores = {
    'default': RedisJobStore(host='localhost', port=6379)
}
```

---

## 📚 参考文档

- [S01 实施文档](../backend/README.md) - 数据层详细说明
- [DATABASE.md](lhb-quant-v2/DATABASE.md) - 数据库设计
- [DATA_SOURCE_API_GUIDE.md](lhb-quant-v2/DATA_SOURCE_API_GUIDE.md) - API 使用指南
- [S01_BUGFIX_REPORT.md](test-reports/S01_BUGFIX_REPORT.md) - Bug 修复报告
- [S01_ISSUES.md](test-reports/S01_ISSUES.md) - 待修复问题

---

**部署日期**: 2026-04-17  
**维护者**: Zhang Xiaotian  
**联系方式**: wb_zhangxiaotian03
