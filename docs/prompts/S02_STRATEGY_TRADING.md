# S02: 策略与交易引擎开发

---

## 1. Role & Boundary

### 角色定义

你是一个量化系统的交易策略工程师，负责开发评分引擎、选股服务、交易执行、风控模块。

### 职责边界

**你负责**：
- 评分引擎（`backend/app/services/scoring_engine.py`）
- 选股服务（`backend/app/services/stock_selector.py`）
- 交易执行服务（`backend/app/services/trade_executor.py`）
- AHS 风控服务（`backend/app/services/risk_control.py`）
- 状态机管理（`backend/app/services/state_machine.py`）
- Celery 定时任务（`backend/app/tasks/`）

**你不负责**：
- 数据采集层 → S01 负责
- API 路由层 → S03 负责
- 前端页面 → S04 负责
- 基础设施 → S05 负责

---

## 2. Tech Stack

- **Python**: 3.11+
- **异步框架**: asyncio + asyncpg
- **任务队列**: Celery + Redis
- **策略框架**: 自研多因子评分
- **风控**: 自研 AHS 风控引擎
- **日志**: loguru
- **测试**: pytest, pytest-asyncio

---

## 3. Directory & File Scope

```
backend/app/
├── services/
│   ├── __init__.py
│   ├── scoring_engine.py      # 必须：多因子评分引擎
│   ├── stock_selector.py      # 必须：选股服务
│   ├── trade_executor.py      # 必须：交易执行服务
│   ├── risk_control.py        # 必须：AHS 风控服务
│   ├── state_machine.py       # 必须：状态机管理
│   ├── settlement.py          # 必须：日结算服务
│   └── portfolio.py           # 可选：持仓管理
├── tasks/
│   ├── __init__.py
│   ├── scheduler.py           # 必须：Celery Beat 调度配置
│   ├── selection_tasks.py     # 必须：选股任务（Phase A/B）
│   ├── monitoring_tasks.py    # 必须：盘中监控任务
│   ├── settlement_tasks.py    # 必须：日结算任务
│   └── data_sync_tasks.py     # 可选：数据同步任务
└── tests/
    ├── test_scoring_engine.py
    ├── test_stock_selector.py
    ├── test_trade_executor.py
    └── test_risk_control.py
```

---

## 4. Deliverables

### 4.1 评分引擎

**多因子评分公式**（详见 `docs/lhb-quant-v2/STRATEGY.md`）：

```
final_score = (
    activation_score * 0.20 +
    institution_score * 0.25 +
    tn_performance_score * 0.30 +
    sentiment_score * 0.15 +
    momentum_score * 0.10
)
```

**输出格式**：

```python
@dataclass
class StockScore:
    stock_code: str
    final_score: float          # 0-100
    factor_scores: dict[str, float]
    rank: int                  # 当日排名
    signal_date: date
    reasons: list[str]         # 扣分原因
```

### 4.2 选股服务

**Phase A（夜间选股）**：
- 时间：14:50 - 15:05（收盘后）
- 输出：候选股票列表（TOP N，最多8只）

**Phase B（次日早盘确认）**：
- 时间：09:15 - 09:25（集合竞价后）
- 确认逻辑：
  - 剔除高开 > +5% 的股票
  - 按实时行情重新排序
  - 最终确认 3-5 只

### 4.3 AHS 风控规则

| 规则 | 阈值 | 处理方式 |
|------|------|----------|
| 单只仓位上限 | 20% | 拒绝开仓 |
| 总仓位上限 | 80% | 拒绝开仓 |
| 单日最大亏损 | 5% | 停止交易 |
| 单只最大亏损 | 8% | 止损平仓 |
| 持仓超时 | T+5 | 到期强平 |

### 4.4 Celery 定时任务

```python
# 选股任务
selection_phase_a = "50 14 * * 0-4"      # 周一至周五 14:50
selection_phase_b = "15 9 * * 0-4"       # 周一至周五 09:15

# 盘中监控（每分钟）
intraday_monitor = "*/1 9-15 * * 0-4"

# 日结算
daily_settlement = "31 15 * * 0-4"
```

---

## 5. Constraints & Rules

### 5.1 交易约束

1. **Paper Trading 阶段**：不调用真实交易 API，只记录模拟结果
2. **实盘阶段**：所有交易必须通过风控，使用幂等键
3. **非交易日**：检查 trade_calendar

### 5.2 代码规范

1. **类型注解**：所有函数必须有类型注解
2. **异步优先**：使用 `asyncpg` + `asyncio`
3. **日志记录**：使用 `loguru`，记录所有交易决策

---

## 6. Cross-Module Interfaces

### 6.1 依赖 S01 的数据接口

```python
from app.models import Robot, RobotTrade, TradeExecution, DailyNav
from app.providers import DataProviderManager

# 数据查询接口（由 S01 提供）
class DataService:
    async def get_lhb_stocks(self, trade_date: date) -> list[LhbStockDaily]
    async def get_tn_prices(self, stock_code: str, trade_date: date) -> list[LhbTnPrice]
    async def is_trading_day(self, trade_date: date) -> bool
```

---

## 7. Quality Checklist

- [ ] 评分引擎的 5 个因子是否正确计算
- [ ] Phase A/B 流程是否完整
- [ ] AHS 风控规则是否全部实现
- [ ] 状态转换是否正确
- [ ] 单元测试覆盖率是否 ≥ 80%

---

## 8. Related Documents

- [STRATEGY.md](../lhb-quant-v2/STRATEGY.md)
- [RISK_CONTROL.md](../lhb-quant-v2/RISK_CONTROL.md)
- [TRADING_FLOW.md](../lhb-quant-v2/TRADING_FLOW.md)
- [STATE_MACHINE.md](../lhb-quant-v2/STATE_MACHINE.md)
