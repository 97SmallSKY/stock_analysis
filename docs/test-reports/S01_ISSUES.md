# S01 数据层待修复问题

> **更新时间**: 2026-04-17 13:15  
> **总问题数**: 7 个（P0: 2, P1: 3, P2: 2）

---

## 🔴 P0 - 致命问题（阻塞发布）

### Issue #1: LhbCollector 字段映射完全错误

**状态**: 🔴 待修复  
**负责人**: TBD  
**预计工时**: 2-4 小时

**问题描述**:

`backend/app/collectors/lhb_collector.py` 中的字段映射与 AKShare API 实际返回不一致，导致采集器无法工作。

**错误代码**:
```python
# Line 107
first_row['交易日期']  # ❌ KeyError: '交易日期'

# 实际字段名应为
first_row['上榜日']  # ✓
```

**AKShare 实际返回的字段**:
```python
['序号', '代码', '名称', '上榜日', '解读', '收盘价', '涨跌幅', 
 '龙虎榜净买额', '龙虎榜买入额', '龙虎榜卖出额', '龙虎榜成交额', 
 '市场总成交额', '净买额占总成交比', '成交额占总成交比', '换手率', 
 '流通市值', '上榜原因', '上榜后1日', '上榜后2日', '上榜后5日', '上榜后10日']
```

**关键发现**:

1. `stock_lhb_detail_em()` 返回的是**股票级别的汇总数据**
2. **不包含席位明细**（买方营业部、卖方营业部等字段不存在）
3. 如果需要席位明细，需调用其他 API：
   - `stock_lhb_hyyyb_em()` - 营业部龙虎榜统计
   - `stock_lhb_jgmmtj_em()` - 机构买卖统计

**修复任务**:

- [ ] 重新调研 AKShare API 文档
- [ ] 确认数据需求（是否需要席位明细？）
- [ ] 重写 `transform()` 方法中的字段映射
- [ ] 如需席位明细，添加额外的 API 调用
- [ ] 更新测试用例中的 mock 数据
- [ ] 添加字段映射文档

**测试验证**:

```bash
# 测试采集器（演练模式）
python -c "
import asyncio
from backend.db.session import AsyncSessionLocal
from backend.app.collectors import LhbCollector
from datetime import date

async def test():
    async with AsyncSessionLocal() as db:
        collector = LhbCollector(db)
        result = await collector.run(dry_run=False, trade_date=date(2026, 4, 16))
        print(result)

asyncio.run(test())
"

# 验证数据入库
psql $DATABASE_URL -c "SELECT * FROM lhb_stock_daily WHERE trade_date = '2026-04-16' LIMIT 5;"
```

**参考文档**:
- [AKShare 龙虎榜文档](https://akshare.akfamily.xyz/data/stock/stock_lhb.html)
- `docs/lhb-quant-v2/DATA_SOURCE.md`

---

### Issue #2: 缺少依赖文件

**状态**: 🔴 待修复  
**负责人**: TBD  
**预计工时**: 0.5 小时

**问题描述**:

项目缺少明确的依赖声明文件，导致部署时缺少必需的包：
- `asyncpg` - SQLAlchemy 异步引擎驱动
- `chinese-calendar` - 交易日判断

**修复任务**:

- [ ] 创建 `backend/requirements.txt`
- [ ] 添加所有必需包及版本号
- [ ] 更新 README 安装说明

**修复代码**:

```bash
# 生成依赖文件
cd /Users/xiaotian/Develop/personal/stock_analysis
pip freeze > backend/requirements.txt

# 或手动创建
cat > backend/requirements.txt << 'EOF'
# Database
asyncpg>=0.29.0
psycopg2-binary>=2.9.9
sqlalchemy>=2.0.0

# Data & Analysis
akshare>=1.14.0
pandas>=2.0.0
numpy>=1.24.0

# Scheduling
apscheduler>=3.10.0
chinese-calendar>=1.9.0

# Logging
loguru>=0.7.2

# Testing
pytest>=8.0.0
pytest-asyncio>=0.23.0
pytest-cov>=4.1.0
EOF
```

**验证**:

```bash
# 在干净环境测试
python -m venv test_env
source test_env/bin/activate
pip install -r backend/requirements.txt
python -c "from backend.db.session import AsyncSessionLocal; print('OK')"
```

---

## ⚠️ P1 - 中等问题（功能完善）

### Issue #3: 测试浮点数精度问题

**状态**: 🟡 待修复  
**负责人**: TBD  
**预计工时**: 0.25 小时

**问题描述**:

```python
# backend/tests/test_collectors.py:110
assert collector._clean_ratio('5.2%', as_decimal=True) == 0.052
# AssertionError: assert 0.052000000000000005 == 0.052
```

**修复任务**:

- [ ] 使用 `pytest.approx()` 修复精度问题
- [ ] 确认是否需要在代码中使用 `Decimal` 类型

**修复代码**:

```python
# 方案 1: 使用 pytest.approx (推荐)
import pytest
assert collector._clean_ratio('5.2%', as_decimal=True) == pytest.approx(0.052)

# 方案 2: 使用 math.isclose
import math
assert math.isclose(
    collector._clean_ratio('5.2%', as_decimal=True), 
    0.052, 
    rel_tol=1e-9
)

# 方案 3: 在实现中使用 Decimal（如果涉及金融计算）
from decimal import Decimal
def _clean_ratio(self, value, as_decimal=False):
    # ... 现有逻辑 ...
    return Decimal(str(result))
```

---

### Issue #4: ORM 模型 SQLAlchemy 2.0 警告

**状态**: 🟡 待修复  
**负责人**: TBD  
**预计工时**: 0.5 小时

**问题描述**:

```
MovedIn20Warning: The ``declarative_base()`` function is now available 
as sqlalchemy.orm.declarative_base(). (deprecated since: 2.0)
```

**修复任务**:

- [ ] 更新 `backend/db/models/base.py` 使用新 API
- [ ] 验证所有 ORM 功能正常

**修复代码**:

```python
# backend/db/models/base.py

# 修改前
from sqlalchemy.ext.declarative import declarative_base
Base = declarative_base()

# 修改后
from sqlalchemy.orm import declarative_base
Base = declarative_base()
```

---

### Issue #5: 缺少数据库迁移验证测试

**状态**: 🟡 待修复  
**负责人**: TBD  
**预计工时**: 1 小时

**问题描述**:

未验证数据库迁移脚本在干净数据库上的执行情况。

**修复任务**:

- [ ] 在测试数据库执行完整迁移
- [ ] 验证所有表、索引、触发器创建成功
- [ ] 添加迁移回滚测试

**测试脚本**:

```bash
# 创建测试数据库
createdb stock_analysis_test

# 执行迁移
psql stock_analysis_test -f backend/db/migrations/001_init_schema.sql
psql stock_analysis_test -f backend/db/migrations/002_init_indexes.sql
psql stock_analysis_test -f backend/db/migrations/003_init_triggers.sql
psql stock_analysis_test -f backend/db/migrations/004_init_seed_data.sql

# 验证
psql stock_analysis_test -c "\dt"
psql stock_analysis_test -c "\di"
psql stock_analysis_test -c "\df"

# 清理
dropdb stock_analysis_test
```

---

### Issue #6: 缺少真实 API 集成测试

**状态**: 🟡 待修复  
**负责人**: TBD  
**预计工时**: 2 小时

**问题描述**:

当前测试全部使用 Mock 数据，未验证与真实 AKShare API 的集成。

**修复任务**:

- [ ] 添加真实 API 测试（使用 VCR.py 录制）
- [ ] 验证 API 限流处理
- [ ] 验证错误重试机制

**测试框架**:

```python
# backend/tests/integration/test_akshare_api.py
import pytest
import vcr

my_vcr = vcr.VCR(
    cassette_library_dir='fixtures/vcr_cassettes',
    record_mode='once',
    match_on=['uri', 'method'],
)

@pytest.mark.integration
@my_vcr.use_cassette('lhb_detail.yaml')
async def test_lhb_collector_real_api(db_session):
    collector = LhbCollector(db_session)
    result = await collector.run(trade_date=date(2026, 4, 16))
    
    assert result['success'] == True
    assert result['records_count'] > 0
```

---

## 📝 P2 - 低优先级（代码质量）

### Issue #7: 测试覆盖率不足

**状态**: ⚪ 待实施  
**负责人**: TBD  
**预计工时**: 4 小时

**问题描述**:

当前测试覆盖率未统计，需要提高到 80% 以上。

**修复任务**:

- [ ] 生成覆盖率报告
- [ ] 补充缺失的测试用例
- [ ] 添加边界条件测试

**命令**:

```bash
# 生成覆盖率报告
pytest backend/tests/ --cov=backend/app --cov-report=html --cov-report=term

# 查看报告
open htmlcov/index.html
```

---

### Issue #8: 缺少性能测试

**状态**: ⚪ 待实施  
**负责人**: TBD  
**预计工时**: 3 小时

**问题描述**:

未验证大数据量下的性能表现。

**修复任务**:

- [ ] 测试批量插入性能（10000 条记录）
- [ ] 测试并发采集性能
- [ ] 优化慢查询

**测试脚本**:

```python
import time
import asyncio

async def benchmark_bulk_insert(records_count=10000):
    start = time.time()
    
    # 生成测试数据
    records = generate_test_records(records_count)
    
    # 批量插入
    async with AsyncSessionLocal() as db:
        await bulk_insert_lhb_records(db, records)
    
    duration = time.time() - start
    print(f"Inserted {records_count} records in {duration:.2f}s")
    print(f"Throughput: {records_count/duration:.0f} records/s")

asyncio.run(benchmark_bulk_insert())
```

---

## 📊 问题统计

| 优先级 | 总数 | 待修复 | 进行中 | 已完成 |
|--------|------|--------|--------|--------|
| P0     | 2    | 2      | 0      | 0      |
| P1     | 3    | 3      | 0      | 0      |
| P2     | 2    | 2      | 0      | 0      |
| **总计** | **7** | **7** | **0** | **0** |

---

## 🎯 修复优先级建议

### 本周必须完成（P0）

1. Issue #1: LhbCollector 字段映射 🔴
2. Issue #2: 补充依赖文件 🔴

### 本周建议完成（P1）

3. Issue #3: 测试精度问题 🟡
4. Issue #4: SQLAlchemy 2.0 警告 🟡

### 下周完成（P1+P2）

5. Issue #5: 迁移验证测试 🟡
6. Issue #6: 真实 API 测试 🟡
7. Issue #7: 测试覆盖率 ⚪
8. Issue #8: 性能测试 ⚪

---

**维护者**: Zhang Xiaotian  
**更新频率**: 每日更新  
**问题提交**: 在此文件底部添加新问题
