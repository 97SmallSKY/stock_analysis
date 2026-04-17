# 故障排查手册

> **版本**: v1.0  
> **最后更新**: 2026-04-17  
> **适用范围**: S01 数据层

---

## 🔍 排查流程

### 标准排查步骤

1. **确认问题现象** - 记录错误信息、发生时间、影响范围
2. **查看日志** - 检查相关日志文件
3. **检查系统状态** - 数据库、网络、资源使用
4. **逐步排查** - 从底层到上层逐一验证
5. **记录和解决** - 记录排查过程和解决方案

---

## 🚨 紧急问题

### 1. 数据库连接完全中断

**症状**:
```
sqlalchemy.exc.OperationalError: (psycopg2.OperationalError) could not connect to server
```

**快速诊断**:
```bash
# 1. 检查数据库服务状态
ssh 117.72.184.8 "systemctl status postgresql"

# 2. 检查网络连接
ping -c 3 117.72.184.8
telnet 117.72.184.8 5432

# 3. 检查磁盘空间
ssh 117.72.184.8 "df -h"

# 4. 检查连接数
psql -h 117.72.184.8 -U stock_user -d postgres -c "SELECT count(*) FROM pg_stat_activity"
```

**解决方案**:
```bash
# 如果数据库服务停止
ssh 117.72.184.8 "sudo systemctl restart postgresql"

# 如果磁盘满
ssh 117.72.184.8 "du -sh /var/lib/pgsql/* | sort -rh | head -10"

# 如果连接数过多
psql -h 117.72.184.8 -U stock_user -d postgres -c "SELECT pid, usename, state, query FROM pg_stat_activity LIMIT 20"
```

---

### 2. 采集器完全失败

**症状**:
```
ERROR: 采集失败，所有重试失败
```

**快速诊断**:
```bash
# 1. 检查 AKShare 是否可用
python -c "import akshare as ak; print(ak.__version__)"

# 2. 测试 API 调用
python -c "
import akshare as ak
df = ak.stock_lhb_detail_em(start_date='20240416', end_date='20240416')
print(f'Rows: {len(df)}, Columns: {list(df.columns)}')
"

# 3. 检查采集器代码
python -c "
from backend.app.collectors import LhbCollector
print('Import OK')
"

# 4. 查看采集器日志
tail -100 logs/collector_*.log | grep ERROR
```

**解决方案**:
```bash
# 如果 AKShare 版本过旧
pip install --upgrade akshare

# 如果 API 返回空数据（非交易日）
python -c "
import chinese_calendar as cc
from datetime import date
print(f'Is workday: {cc.is_workday(date.today())}')
"

# 重启采集器
pkill -f collector
python scripts/run_scheduler.py
```

---

### 3. 数据不一致或丢失

**症状**:
```
SELECT COUNT(*) FROM lhb_stock_daily WHERE trade_date = '2024-04-16'
-- 返回 0 或少于预期
```

**快速诊断**:
```bash
# 1. 检查数据是否存在
psql $DATABASE_URL -c "
SELECT trade_date, COUNT(*) 
FROM lhb_stock_daily 
GROUP BY trade_date 
ORDER BY trade_date DESC 
LIMIT 10
"

# 2. 检查采集记录
psql $DATABASE_URL -c "
SELECT created_at, status, message 
FROM collector_error_log 
ORDER BY created_at DESC 
LIMIT 10
"

# 3. 检查调度器任务状态
psql $DATABASE_URL -c "
SELECT id, next_run_time, misfire_count 
FROM apscheduler_jobs
"
```

**解决方案**:
```bash
# 重新采集指定日期
python scripts/backfill_data.py --start 2024-04-16 --end 2024-04-16

# 清理错误数据
psql $DATABASE_URL -c "
DELETE FROM lhb_stock_daily WHERE trade_date = '2024-04-16'
"

# 然后重新采集
python -c "
import asyncio
from backend.db.session import AsyncSessionLocal
from backend.app.collectors import LhbCollector
from datetime import date

async def recollect():
    async with AsyncSessionLocal() as db:
        collector = LhbCollector(db)
        await collector.run(trade_date=date(2024, 4, 16))

asyncio.run(recollect())
"
```

---

## 📋 常见问题

### 数据库相关

#### Q1: 表不存在

**症状**:
```
psycopg2.errors.UndefinedTable: relation "lhb_stock_daily" does not exist
```

**解决**:
```bash
# 执行数据库迁移
python scripts/init_database.py

# 或手动执行
psql $DATABASE_URL -f backend/db/migrations/001_init_schema.sql
```

#### Q2: 索引缺失导致查询慢

**症状**:
```
EXPLAIN ANALYZE SELECT * FROM lhb_stock_daily WHERE trade_date = '2024-04-16'
-- Seq Scan instead of Index Scan
```

**解决**:
```sql
-- 创建缺失的索引
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_lhb_stock_daily_trade_date 
ON lhb_stock_daily(trade_date);

-- 分析表
ANALYZE lhb_stock_daily;
```

#### Q3: 外键约束冲突

**症状**:
```
psycopg2.errors.ForeignKeyViolation: insert or update on table "robot_trades" 
violates foreign key constraint
```

**解决**:
```sql
-- 检查外键关系
SELECT 
    tc.table_name, 
    kcu.column_name, 
    ccu.table_name AS foreign_table_name,
    ccu.column_name AS foreign_column_name 
FROM information_schema.table_constraints AS tc 
JOIN information_schema.key_column_usage AS kcu
  ON tc.constraint_name = kcu.constraint_name
JOIN information_schema.constraint_column_usage AS ccu
  ON ccu.constraint_name = tc.constraint_name
WHERE tc.constraint_type = 'FOREIGN KEY';

-- 按顺序删除数据（先删除子表）
DELETE FROM robot_trades WHERE ...;
DELETE FROM robots WHERE ...;
```

---

### 采集器相关

#### Q4: 字段映射错误

**症状**:
```
KeyError: '上榜日'
```

**解决**:
```bash
# 检查 AKShare 实际返回的字段
python -c "
import akshare as ak
df = ak.stock_lhb_detail_em(start_date='20240416', end_date='20240416')
print('Actual columns:', df.columns.tolist())
"

# 更新字段映射
# 编辑 backend/app/collectors/lhb_collector.py
# 将 '交易日期' 改为 '上榜日'
```

#### Q5: 数据清洗失败

**症状**:
```
ValueError: could not convert string to float: '10.5%'
```

**解决**:
```python
# 检查 _clean_ratio 方法
def _clean_ratio(self, value, as_decimal=False):
    if isinstance(value, str):
        value = value.strip('%')
        value = float(value) / 100
    # ...
```

#### Q6: 采集超时

**症状**:
```
asyncio.exceptions.TimeoutError
```

**解决**:
```bash
# 增加超时设置
export AKSHARE_TIMEOUT=60

# 或在代码中设置
import akshare as ak
ak.settings.set("timeout", 60)
```

---

### 调度器相关

#### Q7: 任务不执行

**症状**:
```
# 调度器运行但任务不触发
```

**解决**:
```bash
# 检查调度器配置
python -c "
from backend.app.scheduler import app_scheduler
print('Jobs:', app_scheduler.get_jobs())
"

# 检查交易日
python -c "
import chinese_calendar as cc
from datetime import date
print('Is workday:', cc.is_workday(date.today()))
print('Is holiday:', cc.is_holiday(date.today()))
"

# 重启调度器
pkill -f run_scheduler.py
python scripts/run_scheduler.py
```

#### Q8: 任务重复执行

**症状**:
```
# 同一天数据被多次采集
```

**解决**:
```sql
-- 检查调度器任务状态
SELECT id, next_run_time, misfire_count 
FROM apscheduler_jobs 
WHERE id LIKE '%lhb%'

-- 清理重复任务
DELETE FROM apscheduler_jobs WHERE id = 'lhb_collector_duplicate'

-- 重启调度器
pkill -f run_scheduler.py
python scripts/run_scheduler.py
```

---

## 🔧 工具脚本

### 健康检查脚本

```bash
# 运行完整健康检查
python scripts/health_check.py

# 输出示例:
# ============================================================
#   系统健康检查
# ============================================================
# 
# [✓] 数据库连接          正常 (延迟: 12ms)
# [✓] 调度器状态          运行中 (PID: 12345)
# [✓] 最近采集            2024-04-16 15:10:23
# [✓] 错误日志            无错误
# [✓] 磁盘使用            45% / 100GB
# [✓] 内存使用            2.1GB / 8GB
# 
# 健康状态: ✓ 正常
```

### 数据验证脚本

```bash
# 验证数据完整性
python scripts/validate_data.py --date 2024-04-16

# 输出示例:
# ============================================================
#   数据完整性验证
# ============================================================
# 
# [✓] lhb_stock_daily     71条记录
# [✓] market_sentiment    1条记录
# [✓] 数据质量            无异常
# [✓] 索引使用            正常
# 
# 验证结果: ✓ 通过
```

### 性能分析脚本

```bash
# 分析查询性能
python scripts/analyze_performance.py

# 输出示例:
# ============================================================
#   性能分析报告
# ============================================================
# 
# 慢查询统计:
# - avg_time: 125ms
# - max_time: 2.3s
# - 总查询数: 1234
# 
# 索引使用情况:
# - idx_used: 95%
# - seq_scan: 5%
# 
# 建议优化:
# 1. 为 lhb_stock_daily(trade_date) 添加索引
# 2. 增加 shared_buffers 到 2GB
```

---

## 📞 获取帮助

### 内部资源

- **项目仓库**: https://github.com/97SmallSKY/stock_analysis.git
- **文档目录**: `docs/lhb-quant-v2/`
- **测试报告**: `docs/test-reports/`

### 外部资源

- **AKShare 文档**: https://akshare.akfamily.xyz/
- **SQLAlchemy 文档**: https://docs.sqlalchemy.org/
- **PostgreSQL 文档**: https://www.postgresql.org/docs/

### 联系方式

- **维护者**: Zhang Xiaotian
- **邮箱**: wb_zhangxiaotian03@example.com

---

**最后更新**: 2026-04-17  
**维护者**: Zhang Xiaotian
