# S06 — 测试（Testing & Quality Assurance）

> **阶段目标**：为系统建立完整测试体系，覆盖单元测试、集成测试、API 测试、E2E 测试和 CI 流水线。

---

## 0. 前置阅读

- S01~S05 所有产出代码
- `docs/lhb-quant-v2/remade.md` 附录（算法边界条件）
- `docs/lhb-quant-v2/功能清单.md`

---

## 1. 测试框架

| 工具 | 用途 |
|------|------|
| pytest + pytest-asyncio | 后端测试运行器 |
| pytest-cov | 覆盖率统计 |
| httpx | FastAPI 异步测试客户端 |
| factory-boy | 测试数据工厂 |
| freezegun | 冻结时间 |
| respx | mock AkShare HTTP |
| Playwright | E2E 浏览器自动化 |
| Vitest | 前端单元测试 |

---

## 2. 目录结构

```
backend/tests/
├── conftest.py
├── factories.py
├── unit/
│   ├── test_ahs_calculator.py
│   ├── test_scorer.py
│   ├── test_phase_a.py
│   ├── test_phase_b.py
│   ├── test_settlement.py
│   ├── test_feishu.py
│   └── test_ahs_constraint.py
├── integration/
│   ├── test_trading_flow.py
│   └── test_settlement_flow.py
└── api/
    ├── test_auth.py
    ├── test_robots.py
    ├── test_trades.py
    └── test_market.py

frontend/tests/
├── unit/format.test.ts
├── unit/ws.test.ts
└── e2e/
    ├── test_login.py
    ├── test_dashboard.py
    ├── test_robot_crud.py
    └── test_trade_flow.py
```

---

## 3. S01 数据层测试报告（2026-04-17）

### 3.1 测试环境

- **Python 版本**: 3.13.9
- **数据库**: PostgreSQL 16 @ 117.72.184.8:5432
- **测试框架**: pytest 8.4.2 + pytest-asyncio 1.3.0

### 3.2 测试执行结果

#### ✅ 通过的测试（11/12）

| 测试类别 | 测试名称 | 状态 | 说明 |
|---------|---------|------|------|
| BaseCollector | test_validate_stock_code | ✓ | 股票代码验证正确 |
| BaseCollector | test_clean_amount | ✓ | 金额清洗功能正常 |
| LhbCollector | test_fetch_success | ✓ | 成功抓取数据（Mock） |
| LhbCollector | test_fetch_empty | ✓ | 空数据处理正确 |
| LhbCollector | test_transform | ✓ | 数据转换逻辑正确 |
| LhbCollector | test_run_dry_mode | ✓ | 演练模式工作正常 |
| MarketCollector | test_transform | ✓ | 市场情绪计算正确 |
| MarketCollector | test_sentiment_score_hot_market | ✓ | 热市评分正确 |
| MarketCollector | test_sentiment_score_crisis_market | ✓ | 危机市场评分正确 |
| Integration | test_retry_mechanism | ✓ | 重试机制正常 |
| Integration | test_max_retries_exceeded | ✓ | 最大重试限制正常 |

#### ❌ 失败的测试（1/12）

| 测试类别 | 测试名称 | 错误类型 | 详细说明 |
|---------|---------|---------|---------|
| BaseCollector | test_clean_ratio | AssertionError | 浮点数精度问题：期望 0.052，实际 0.052000000000000005 |

### 3.3 🔴 严重问题（需立即修复）

#### 问题 1: ORM 模型导入不完整 ✅ **已修复**

**位置**: `backend/db/models/robot.py`, `config.py`

**错误**:
```python
NameError: name 'BigInteger' is not defined
NameError: name 'DateTime' is not defined
NameError: name 'Numeric' is not defined
NameError: name 'ARRAY' is not defined
NameError: name 'CheckConstraint' is not defined
```

**修复方案**: ✅ 已在测试中修复
- robot.py: 添加 `from datetime import datetime` 和 SQLAlchemy 类型导入
- config.py: 补全所有缺失的 SQLAlchemy 类型导入

**修复代码**:
```python
# backend/db/models/robot.py
from datetime import datetime
from sqlalchemy import (
    Column, String, Numeric, Integer, BigInteger, Date, DateTime, Text, ARRAY,
    ForeignKey, CheckConstraint, UniqueConstraint, JSON
)

# backend/db/models/config.py
from sqlalchemy import (
    Column, String, Text, Boolean, Integer, Date, BigInteger, Numeric, ARRAY,
    ForeignKey, UniqueConstraint, CheckConstraint, JSON, DateTime
)
```

#### 问题 2: 缺少 asyncpg 依赖 ⚠️ **需补充**

**错误**: `ModuleNotFoundError: No module named 'asyncpg'`

**原因**: 项目使用 SQLAlchemy 异步引擎，但未安装 asyncpg 驱动

**修复方案**: 
1. 添加到依赖文件（推荐创建 `backend/requirements.txt` 或使用 `pyproject.toml`）
2. 临时修复: `pip install asyncpg`

#### 问题 3: LhbCollector 字段映射错误 🔴 **致命错误**

**位置**: `backend/app/collectors/lhb_collector.py:107`

**错误**: `KeyError: '交易日期'`

**根本原因**: 代码中使用的字段名与 AKShare API 实际返回的字段名不匹配

**AKShare 实际返回的字段**:
```python
['序号', '代码', '名称', '上榜日', '解读', '收盘价', '涨跌幅', 
 '龙虎榜净买额', '龙虎榜买入额', '龙虎榜卖出额', '龙虎榜成交额', 
 '市场总成交额', '净买额占总成交比', '成交额占总成交比', '换手率', 
 '流通市值', '上榜原因', '上榜后1日', '上榜后2日', '上榜后5日', '上榜后10日']
```

**代码中错误使用的字段**:
```python
first_row['交易日期']  # ❌ 应为 '上榜日'
row.get('买方营业部名称')  # ❌ 字段不存在
row.get('卖方营业部名称')  # ❌ 字段不存在
row.get('买入金额')  # ❌ 字段不存在
row.get('卖出金额')  # ❌ 字段不存在
```

**修复方案**: 需要完全重写 `LhbCollector.transform()` 方法

**注意**: 当前的 AKShare API `stock_lhb_detail_em()` 返回的是**股票级别的汇总数据**，不包含席位明细。如果需要席位明细，需要调用不同的 API：
- `stock_lhb_hyyyb_em()` - 按营业部统计
- `stock_lhb_jgmmtj_em()` - 机构买卖统计

**建议**: 
1. 重新审查 `docs/lhb-quant-v2/DATA_SOURCE.md` 中的 API 映射
2. 可能需要调用多个 AKShare API 来获取完整数据
3. 更新测试数据以匹配实际 API 返回格式

### 3.4 ⚠️ 中等问题

#### 问题 4: 浮点数精度测试失败

**位置**: `backend/tests/test_collectors.py:110`

**修复方案**: 使用 `pytest.approx()` 或 `math.isclose()`

```python
# 修改前
assert collector._clean_ratio('5.2%', as_decimal=True) == 0.052

# 修复建议
import pytest
assert collector._clean_ratio('5.2%', as_decimal=True) == pytest.approx(0.052)
```

### 3.5 ✅ 验证通过的功能

1. **数据库连接** ✓
   - 生产数据库连接正常（117.72.184.8:5432）
   - 24 张表已创建
   - 表结构符合设计文档

2. **基础工具函数** ✓
   - 股票代码验证
   - 金额单位转换
   - 比率清洗（除精度问题外）

3. **重试机制** ✓
   - 自动重试 3 次
   - 指数退避策略正常

4. **市场情绪计算** ✓
   - 涨跌停统计准确
   - 情绪评分逻辑正确
   - 市场阶段判断准确

### 3.6 📋 待测试功能

- [ ] 调度器启动和任务执行
- [ ] 数据完整性检查（`integrity.py`）
- [ ] K线采集器（当前仅框架）
- [ ] 数据库迁移脚本验证
- [ ] 并发采集性能测试
- [ ] 错误日志写入测试

### 3.7 🎯 下一步行动

#### 高优先级（P0 - 阻塞）

1. **修复 LhbCollector 字段映射** 🔴
   - 重新调研 AKShare API 文档
   - 更新字段映射表
   - 重写 `transform()` 方法
   - 如需席位明细，调用额外 API

2. **补充依赖文件** ⚠️
   - 创建 `backend/requirements.txt`
   - 添加 asyncpg、akshare 等依赖

#### 中优先级（P1）

3. **修复测试精度问题**
   - 使用 `pytest.approx()`

4. **数据库迁移验证**
   - 测试迁移脚本在干净数据库上的执行
   - 验证索引创建

#### 低优先级（P2）

5. **补充集成测试**
   - 真实 API 调用测试（使用 VCR.py 录制）
   - 调度器端到端测试

---

## 4. 测试执行命令参考

```bash
# 运行所有测试
pytest backend/tests/ -v

# 运行特定测试文件
pytest backend/tests/test_collectors.py -v

# 运行特定测试类
pytest backend/tests/test_collectors.py::TestLhbCollector -v

# 生成覆盖率报告
pytest backend/tests/ --cov=backend/app --cov-report=html

# 测试数据库连接
python -c "from backend.db.session import AsyncSessionLocal; print('OK')"

# 测试采集器（演练模式）
python -c "
import asyncio
from backend.db.session import AsyncSessionLocal
from backend.app.collectors import LhbCollector
from datetime import date

async def test():
    async with AsyncSessionLocal() as db:
        collector = LhbCollector(db)
        result = await collector.run(dry_run=True, trade_date=date(2026, 4, 16))
        print(result)

asyncio.run(test())
"
```

---

**测试报告生成时间**: 2026-04-17 13:13  
**测试人员**: Zhang Xiaotian  
**测试环境**: macOS 15.7.3 / Python 3.13.9