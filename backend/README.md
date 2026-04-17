# S01 数据层实施文档

> 龙虎榜量化交易系统 v2 - 数据层基础设施

## 📦 已实施功能

### 1. 数据库层 ✅

**迁移脚本** (`backend/db/migrations/`)
- `001_init_schema.sql` - 28张核心表
- `002_init_indexes.sql` - 84个索引
- `003_init_triggers.sql` - 8个触发器 + 4个视图
- `004_init_seed_data.sql` - 基础数据

**ORM 模型** (`backend/db/models/`)
- `base.py` - Base类和Mixin
- `robot.py` - 机器人和交易模型（7个）
- `market.py` - 市场数据模型（8个）
- `config.py` - 配置和辅助模型（9个）

**会话管理** (`backend/db/session.py`)
- 异步 Session 工厂
- 同步 Session（APScheduler JobStore）
- FastAPI 依赖注入

### 2. 数据采集器 ✅

**基类** (`backend/app/collectors/base.py`)
- 自动重试机制（3次，指数退避）
- 错误日志记录
- 演练模式支持
- 数据清洗工具函数

**具体采集器**
- `lhb_collector.py` - 龙虎榜数据（AKShare）
  - 股票汇总：`lhb_stock_daily`
  - 席位明细：`lhb_seats`
  - 机构识别、UPSERT去重
- `market_collector.py` - 市场情绪
  - 涨跌停统计
  - 情绪评分（0-100）
  - 市场阶段判断
- `kline_collector.py` - K线数据（框架）

### 3. 调度器 ✅

**调度系统** (`backend/app/scheduler.py`)
- APScheduler 异步调度
- PostgreSQL 任务持久化
- 交易日检查（chinese_calendar）
- 定时任务：
  - 15:05 - 龙虎榜采集
  - 15:10 - 市场情绪采集
  - 15:30 - 数据完整性检查

### 4. 数据质量监控 ✅

**完整性检查** (`backend/app/integrity.py`)
- 缺失数据检测
- 异常数据识别
- 回溯N天检查

---

## 🚀 快速开始

### 环境准备

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 配置环境变量（已完成，使用生产数据库）
cat .env
# PG_HOST=117.72.184.8
# PG_PORT=5432
# PG_DATABASE=stock_analysis
# PG_USER=stock_user
# PG_PASSWORD=Stock2026!@#
```

### 数据库初始化

```bash
# 方式1: 一键重置（推荐）
python scripts/reset_database.py
# 输入 YES 确认

# 方式2: 手动执行SQL（如果需要）
psql $DATABASE_URL -f backend/db/migrations/001_init_schema.sql
psql $DATABASE_URL -f backend/db/migrations/002_init_indexes.sql
psql $DATABASE_URL -f backend/db/migrations/003_init_triggers.sql
psql $DATABASE_URL -f backend/db/migrations/004_init_seed_data.sql
```

### 手动采集测试

```bash
# 测试龙虎榜采集器（演练模式）
python -c "
import asyncio
from backend.db.session import AsyncSessionLocal
from backend.app.collectors import LhbCollector

async def test():
    async with AsyncSessionLocal() as db:
        collector = LhbCollector(db)
        result = await collector.run(dry_run=True)
        print('Result:', result)

asyncio.run(test())
"
```

### 启动调度器

```bash
# 启动定时采集
python scripts/run_scheduler.py

# 查看日志
tail -f logs/scheduler_*.log
```

---

## 📊 数据库状态

### 表结构统计

```
✓ 表数量: 28张
✓ 索引数量: 84个
✓ 触发器数量: 8个
✓ 视图数量: 4个
```

### 核心表

**交易相关**
- `robots` - 机器人配置
- `robot_trades` - 交易记录
- `trade_executions` - 执行明细
- `cash_ledger` - 现金流水
- `daily_nav` - 净值曲线
- `weekly_summary` - 周汇总
- `ahs_history` - AHS评分历史

**市场数据**
- `lhb_stock_daily` - 龙虎榜日汇总
- `lhb_seats` - 席位明细
- `lhb_tn_prices` - T+N价格
- `lhb_agg_stats` - 多日汇总统计
- `lhb_institution_stats` - 机构胜率统计
- `kline_daily` - 日K线
- `market_snapshots` - 盘中快照
- `market_sentiment` - 市场情绪

**配置表**
- `strategies` - 策略配置
- `strategy_params` - 策略参数
- `trade_events` - 交易事件日志
- `selection_log` - 选股日志
- `audit_log` - 审计日志
- `app_config` - 应用配置
- `trade_calendar` - 交易日历
- `stock_info` - 股票信息
- `collector_error_log` - 采集错误日志

---

## 🔧 开发指南

### 添加新采集器

1. 继承 `BaseCollector`
2. 实现三个方法：`fetch()`, `transform()`, `save()`
3. 在 `scheduler.py` 注册定时任务

```python
from backend import BaseCollector


class MyCollector(BaseCollector):
    async def fetch(self, trade_date=None):
        # 从 API 获取数据
        return df

    async def transform(self, df):
        # 清洗转换
        return records

    async def save(self, records):
        # 入库
        return count
```

### 数据库迁移

```bash
# 添加新的迁移文件
backend/db/migrations/005_add_feature.sql

# 执行迁移
psql $DATABASE_URL -f backend/db/migrations/005_add_feature.sql
```

---

## 📝 注意事项

### 生产环境

- ✅ 使用京东云生产数据库 (117.72.184.8:5432)
- ✅ 本地和远程环境统一配置
- ⚠️ 无 TimescaleDB 扩展（已兼容处理）
- ⚠️ 建议启用SSL连接（TODO）

### 数据采集

- 龙虎榜数据每日15:05后可获取
- 市场情绪建议在收盘后采集
- AKShare API 有频率限制，注意控制速率
- 采集失败会自动重试3次

### 监控告警

- 日志存储在 `logs/` 目录
- 采集错误会记录到 `collector_error_log` 表
- TODO: 接入钉钉/邮件告警

---

## 🎯 后续任务

### 待实施功能

1. **K线采集器完善** - 批量采集所有A股K线
2. **T+N价格计算** - 自动计算龙虎榜后续收益
3. **LHB聚合统计** - 多日汇总、机构胜率
4. **告警系统** - Redis Pub/Sub + 钉钉通知
5. **单元测试** - Mock AKShare，测试覆盖率
6. **性能优化** - 批量插入、索引优化

### 验收标准

- [x] 数据库完整创建
- [x] 至少一个采集器可用
- [x] 调度器可正常启动
- [ ] 采集器单元测试通过
- [ ] 手动执行采集成功
- [ ] 定时任务稳定运行24小时

---

## 📚 参考文档

- [DATABASE.md](../../docs/lhb-quant-v2/DATABASE.md) - 数据库设计
- [DATA_SOURCE.md](../../docs/lhb-quant-v2/DATA_SOURCE.md) - 数据源规范
- [ARCHITECTURE.md](../../docs/lhb-quant-v2/ARCHITECTURE.md) - 系统架构
- [S01_DATA_LAYER.md](../../docs/prompts/S01_DATA_LAYER.md) - 实施指南

---

## 🆘 故障排查

### 数据库连接失败

```bash
# 检查网络
ping 117.72.184.8

# 测试连接
python -c "
import psycopg2
conn = psycopg2.connect(
    host='117.72.184.8',
    port=5432,
    database='stock_analysis',
    user='stock_user',
    password='Stock2026!@#'
)
print('连接成功')
"
```

### AKShare 调用失败

- 检查网络代理配置
- 查看 AKShare 官方文档确认 API 是否变更
- 降低请求频率

### 调度器无法启动

```bash
# 查看日志
tail -f logs/scheduler_*.log

# 检查 APScheduler 表
psql $DATABASE_URL -c "\dt apscheduler_*"
```

---

**最后更新**: 2026-04-17  
**维护者**: Zhang Xiaotian
