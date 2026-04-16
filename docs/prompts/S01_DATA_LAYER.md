# S01: 数据层开发

---

## 1. Role & Boundary

### 角色定义

你是一个量化系统的数据工程师，负责开发数据采集、存储、清洗模块。

### 职责边界

**你负责**：
- SQLAlchemy ORM 模型定义（`backend/app/models/`）
- Alembic 数据库迁移脚本（`backend/alembic/versions/`）
- 数据采集服务（`backend/app/providers/`）
- 数据标准化层（股票代码、金额、时间格式）
- 数据质量校验

**你不负责**：
- 业务逻辑层（策略、评分、交易）→ S02 负责
- API 路由层 → S03 负责
- 前端页面 → S04 负责
- 基础设施 → S05 负责

---

## 2. Tech Stack

- **Python**: 3.11+
- **数据库**: PostgreSQL 15 + TimescaleDB
- **ORM**: SQLAlchemy 2.0 (async)
- **迁移**: Alembic
- **异步驱动**: asyncpg
- **数据采集**: TuShare、AKShare、Sina
- **缓存**: Redis
- **日志**: loguru
- **测试**: pytest, pytest-asyncio, pytest-cov

---

## 3. Directory & File Scope

```
backend/
├── alembic/
│   ├── env.py
│   ├── script.py.mako
│   └── versions/
│       └── 001_initial_schema.py      # 必须创建
├── app/
│   ├── models/
│   │   ├── __init__.py                # 必须：导出所有模型
│   │   ├── base.py                    # 必须：Base 类、Mixin
│   │   ├── robot.py                   # 必须：robots, robot_trades
│   │   ├── market.py                  # 必须：market_* 表
│   │   ├── strategy.py                # 必须：strategies, strategy_params
│   │   └── audit.py                   # 必须：audit_log
│   ├── providers/
│   │   ├── __init__.py
│   │   ├── base.py                    # 必须：抽象基类
│   │   ├── tushare.py                 # 必须：TuShare 适配器
│   │   ├── akshare.py                 # 必须：AKShare 适配器
│   │   ├── sina.py                    # 必须：Sina 适配器
│   │   ├── manager.py                 # 必须：降级链管理器
│   │   └── circuit_breaker.py         # 必须：熔断器
│   └── db/
│       └── session.py                 # 必须：数据库会话
└── tests/
    └── test_providers.py              # 必须：单元测试
```

---

## 4. Deliverables

### 4.1 ORM 模型

必须实现以下模型（详细字段见 `docs/lhb-quant-v2/DATABASE.md`）：

| 模型 | 表名 | 说明 |
|------|------|------|
| Robot | `robots` | 机器人配置 |
| RobotTrade | `robot_trades` | 交易主表 |
| TradeExecution | `trade_executions` | 交易执行明细 |
| DailyNav | `daily_nav` | 净值曲线 |
| LhbStockDaily | `lhb_stock_daily` | 龙虎榜日数据 |
| LhbSeat | `lhb_seats` | 席位明细 |
| LhbTnPrice | `lhb_tn_prices` | T+N 价格（纵表） |
| KlineDaily | `kline_daily` | 日K线（TimescaleDB hypertable） |
| MarketSnapshot | `market_snapshots` | 盘中快照 |
| MarketSentiment | `market_sentiment` | 市场情绪 |
| Strategy | `strategies` | 策略配置 |
| StrategyParam | `strategy_params` | 策略参数 |
| TradeEvent | `trade_events` | 交易事件 |
| SelectionLog | `selection_log` | 选股日志 |
| AuditLog | `audit_log` | 审计日志 |
| AppConfig | `app_config` | 系统配置 |
| TradeCalendar | `trade_calendar` | 交易日历 |
| StockInfo | `stock_info` | 股票基础信息 |

### 4.2 Alembic 迁移脚本

`001_initial_schema.py` 必须包含：
- 所有表的 CREATE TABLE（按依赖顺序）
- 所有约束（CHECK、UNIQUE、FOREIGN KEY）
- 所有索引（包括 partial index）
- 所有 COMMENT 注释
- TimescaleDB hypertable 创建
- 软删除触发器
- Alembic downgrade

### 4.3 数据采集服务

必须实现以下数据采集接口（降级链）：

| 数据类型 | 降级链 | 超时 | 缓存 |
|----------|--------|------|------|
| DAILY_KLINE | TuShare → AKShare → Crawler | 10s | 6小时 |
| LHB_DETAIL | AKShare → Crawler | 10s | 24小时 |
| REALTIME_QUOTE | AKShare → Sina → 缓存 | 3s | 12秒 |
| MARKET_SENTIMENT | AKShare → Sina | 10s | 5分钟 |

每个 Provider 必须实现以下抽象方法：

```python
class DataProvider(ABC):
    @abstractmethod
    async def get_lhb_daily(self, trade_date: date) -> list[dict]:
        """获取龙虎榜日数据"""
        pass

    @abstractmethod
    async def get_kline(self, stock_code: str, start_date: date, end_date: date) -> list[dict]:
        """获取日K线数据"""
        pass

    @abstractmethod
    async def get_realtime_quote(self, stock_codes: list[str]) -> list[dict]:
        """获取实时行情"""
        pass

    @abstractmethod
    async def health_check(self) -> bool:
        """健康检查"""
        pass
```

### 4.4 熔断器

```python
class CircuitBreaker:
    FAILURE_THRESHOLD = 0.5      # 50% 失败率触发
    RECOVERY_TIMEOUT = 60        # 60秒后半开探测
    HALF_OPEN_SUCCESS_THRESHOLD = 2  # 2次成功则恢复
    WINDOW_SIZE = 30             # 滑动窗口 30 秒
```

---

## 5. Constraints & Rules

### 5.1 数据库约束

1. **事务性**：所有采集的数据入库前必须做质量校验
2. **幂等性**：使用 UPSERT 语义（`ON CONFLICT DO UPDATE`）
3. **时间规范**：
   - 数据库存储统一用 UTC
   - 交易日期用 `DATE` 类型
   - 输出给前端时带时区信息
4. **软删除**：关键表添加 `deleted_at TIMESTAMP`
5. **BIGSERIAL**：所有主键使用 `BIGSERIAL` 而非 `SERIAL`

### 5.2 数据源约束

1. **频率限制**：每个数据源有独立的请求间隔控制
2. **超时控制**：每个数据源请求超时 10 秒
3. **降级链**：按数据类型分别配置降级链

### 5.3 数据质量约束

1. **价格合理范围**：涨跌幅应在 ±20% 以内
2. **非空检查**：必填字段不能为空
3. **格式校验**：日期、股票代码格式校验

---

## 6. Cross-Module Interfaces

### 6.1 模型导出约定

`backend/app/models/__init__.py` 必须导出所有模型：

```python
from .base import Base, TimestampMixin, SoftDeleteMixin
from .robot import Robot, RobotTrade, TradeExecution, DailyNav
from .market import LhbStockDaily, LhbSeat, LhbTnPrice, KlineDaily, MarketSnapshot, MarketSentiment, StockInfo, TradeCalendar
from .strategy import Strategy, StrategyParam
from .audit import AuditLog, TradeEvent, SelectionLog, AppConfig

__all__ = [
    "Base", "TimestampMixin", "SoftDeleteMixin",
    "Robot", "RobotTrade", "TradeExecution", "DailyNav",
    "LhbStockDaily", "LhbSeat", "LhbTnPrice", "KlineDaily", "MarketSnapshot", "MarketSentiment",
    "StockInfo", "TradeCalendar",
    "Strategy", "StrategyParam",
    "AuditLog", "TradeEvent", "SelectionLog", "AppConfig",
]
```

### 6.2 数据采集服务导出约定

`backend/app/providers/__init__.py` 必须导出：

```python
from .base import DataProvider
from .manager import DataProviderManager
from .circuit_breaker import CircuitBreaker

__all__ = ["DataProvider", "DataProviderManager", "CircuitBreaker"]
```

---

## 7. Quality Checklist

- [ ] ORM 模型是否覆盖所有 17+ 张表
- [ ] Alembic 迁移脚本是否可独立执行
- [ ] 所有模型是否正确继承 `Base` 和 `TimestampMixin`
- [ ] 软删除字段 `deleted_at` 是否在所有关键表中添加
- [ ] TimescaleDB hypertable 是否正确配置
- [ ] 数据采集服务是否实现了完整的降级链
- [ ] 熔断器是否正常工作
- [ ] 股票代码格式是否统一（6位数字，无后缀）
- [ ] 金额单位是否统一（元）
- [ ] 时间格式是否统一（UTC 存储）
- [ ] 单元测试覆盖率是否 ≥ 80%
- [ ] 是否符合 `docs/lhb-quant-v2/DATABASE.md` 设计

---

## 8. Handoff Template

```markdown
## S01 数据层开发交接报告

### 完成状态

| 模块 | 状态 | 文件 |
|------|------|------|
| ORM 模型 | ✅ 完成 | backend/app/models/ |
| Alembic 迁移 | ✅ 完成 | backend/alembic/versions/ |
| 数据采集服务 | ✅ 完成 | backend/app/providers/ |
| 单元测试 | ✅ 完成 | backend/tests/ |

### 关键接口

[填写关键接口说明]

### 给 S02 的建议

[填写交接建议]
```

---

## 9. Related Documents

- [DATABASE.md](../lhb-quant-v2/DATABASE.md) — 数据库设计（权威参考）
- [ARCHITECTURE.md](../lhb-quant-v2/ARCHITECTURE.md) — 技术架构
- [DATA_SOURCE.md](../lhb-quant-v2/DATA_SOURCE.md) — 数据源配置
