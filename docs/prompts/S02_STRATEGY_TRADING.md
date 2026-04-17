# S02 — 策略 & 交易引擎（Strategy & Trading Engine）

> **阶段目标**：基于 S01 落库数据，实现龙虎榜选股策略、模拟交易状态机、持仓管理与盈亏统计，形成可回测、可实盘跟踪的完整交易闭环。

---

## 0. 前置阅读（必须先读）

- `docs/lhb-quant-v2/STRATEGY.md` — 选股逻辑、信号触发条件、参数表
- `docs/lhb-quant-v2/TRADING_FLOW.md` — 买入/卖出/止损的完整流程图
- `docs/lhb-quant-v2/STATE_MACHINE.md` — 持仓状态机转换规则
- `docs/lhb-quant-v2/RISK_CONTROL.md` — 仓位控制、止损线、黑名单规则

---

## 1. 任务目标

构建以下四个子系统：

1. **信号引擎**：从龙虎榜数据中识别买入/卖出信号
2. **策略管理器**：支持多策略并行、参数热更新
3. **模拟交易引擎**：状态机驱动的持仓生命周期管理
4. **风控模块**：实时仓位检查、止损触发、黑名单过滤

---

## 2. 输入依赖

| 依赖 | 说明 |
|------|------|
| S01 产出的 DB 表 | `lhb_records`、`limit_up_pool`、`market_sentiment` 必须有数据 |
| `docs/lhb-quant-v2/STRATEGY.md` | 信号计算的权威参数，不得自行修改默认值 |
| `docs/lhb-quant-v2/RISK_CONTROL.md` | 风控红线，硬编码为常量不可配置覆盖 |

---

## 3. 具体任务

### 3.1 信号引擎（`backend/strategy/signal_engine.py`）

**龙虎榜买入信号**（需同时满足以下所有条件）：

```
条件 1：游资席位净买入
  - 当日龙虎榜中，已知游资席位（seat_whitelist）净买入金额 ≥ 500万
  - 游资净买入占当日成交额比例 ≥ 1.5%

条件 2：连板特征
  - 当前连板天数 ≥ 2（首板不做）
  - 连板晋级率（历史同板高度）≥ 30%

条件 3：市场情绪过滤
  - 当日涨停数 ≥ 50（情绪不冷场）
  - 市场情绪指数（sentiment_index）≥ 60

条件 4：个股排除
  - 不在黑名单（ST、退市风险、近 30 日有财务造假公告）
  - 流通市值 50亿 ≤ market_cap ≤ 500亿
  - 当日换手率 ≥ 5%
```

**卖出信号**（满足任一即触发）：

```
止损：持仓期间最大回撤 ≥ 8%（从买入价计算）
止盈：持仓收益 ≥ 30%
时间止损：持仓超过 5 个交易日且未盈利
形态止损：持仓期间出现"炸板"（涨停后跌停）
游资撤退：持仓股出现在龙虎榜卖方席位（大额净卖出）
```

```python
# 信号引擎接口规范
class SignalEngine:
    async def scan_buy_signals(self, trade_date: date) -> list[BuySignal]:
        """扫描当日所有满足买入条件的股票，返回信号列表（按得分降序）"""
    
    async def check_sell_signal(self, position: Position, current_price: float) -> SellSignal | None:
        """检查持仓是否触发卖出条件，返回 None 表示继续持有"""
    
    async def score_signal(self, signal: BuySignal) -> float:
        """综合评分 0-100，用于多信号时的优先级排序"""
```

### 3.2 策略管理器（`backend/strategy/strategy_manager.py`）

```python
# 支持多策略实例，每个策略有独立参数集
class StrategyManager:
    async def create_strategy(self, name: str, params: StrategyParams) -> Strategy
    async def update_params(self, strategy_id: int, params: dict) -> Strategy  # 热更新
    async def get_active_strategies(self) -> list[Strategy]
    async def run_daily_scan(self, trade_date: date) -> list[TradeSignal]
```

**策略参数表**（`strategy_params` DB 表，对应 `STRATEGY.md` 默认值）：

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `min_net_buy` | NUMERIC | 5000000 | 游资最低净买入（元） |
| `min_net_buy_ratio` | FLOAT | 0.015 | 净买入/成交额最低比 |
| `min_consecutive_days` | INT | 2 | 最低连板天数 |
| `min_promotion_rate` | FLOAT | 0.30 | 最低连板晋级率 |
| `min_sentiment_index` | INT | 60 | 最低情绪指数 |
| `stop_loss_pct` | FLOAT | 0.08 | 止损比例 |
| `take_profit_pct` | FLOAT | 0.30 | 止盈比例 |
| `max_hold_days` | INT | 5 | 最大持仓天数 |
| `max_position_count` | INT | 3 | 最大同时持仓数 |
| `single_position_ratio` | FLOAT | 0.30 | 单只股票仓位上限 |

### 3.3 模拟交易引擎（`backend/trading/`）

```
backend/trading/
├── engine.py          # 交易引擎主入口
├── state_machine.py   # 持仓状态机（严格按 STATE_MACHINE.md）
├── order_manager.py   # 订单创建、成交模拟、撤单
├── position_manager.py # 持仓查询、盈亏计算、净值更新
└── portfolio.py       # 组合层面：总资产、收益率、最大回撤
```

**持仓状态机**（严格按 `STATE_MACHINE.md` 实现，不得新增状态）：

```
PENDING_BUY  →  HOLDING  →  PENDING_SELL  →  CLOSED
     ↓              ↓              ↓
  CANCELLED     STOP_LOSS      CANCELLED
```

状态转换规则：
- `PENDING_BUY → HOLDING`：次日以**开盘价**成交（T+1 交易制度）
- `HOLDING → PENDING_SELL`
<function_calls>
<invoke name="Bash">
<parameter name="command">cat ~/Develop/personal/stock_analysis/docs/lhb-quant-v2/TRADING_FLOW.md：触发卖出信号后挂单，次日开盘价成交
- `HOLDING → STOP_LOSS`：当日收盘价跌破止损线，**当日收盘价**直接成交（不等次日）
- `PENDING_BUY → CANCELLED`：买入当日涨停无法成交（开盘一字板）则取消
- `CLOSED`：终态，不可逆

**成交价格模拟规则**（对应 `TRADING_FLOW.md`）：
```python
# 买入：T+1 开盘价，若开盘涨停则以涨停价成交（极端情况标记 slippage=HIGH）
# 卖出：T+1 开盘价，若开盘跌停则以跌停价成交
# 手续费：买入 0.03%，卖出 0.03% + 印花税 0.1%
# 最小手续费：5 元/笔
def calc_commission(amount: float, side: str) -> float:
    rate = 0.0003 if side == 'BUY' else 0.0013
    return max(amount * rate, 5.0)
```

### 3.4 风控模块（`backend/trading/risk_control.py`）

以下为**硬编码红线**，不得通过配置覆盖：

```python
class RiskControl:
    # 组合层面硬性限制
    MAX_TOTAL_POSITIONS = 5          # 最多同时持有 5 只
    MAX_SINGLE_STOCK_RATIO = 0.40    # 单只不超过总资产 40%
    MAX_DAILY_LOSS_RATIO = 0.05      # 单日组合亏损 >5% 停止新开仓
    MAX_DRAWDOWN_HARD_STOP = 0.20    # 组合回撤 >20% 全部平仓

    async def check_before_buy(self, signal: BuySignal, portfolio: Portfolio) -> RiskCheckResult:
        """买入前检查，返回 PASS / REJECT（含拒绝原因）"""

    async def check_blacklist(self, stock_code: str) -> bool:
        """检查是否在黑名单（ST、退市、财务造假）"""

    async def monitor_portfolio(self, portfolio: Portfolio) -> list[RiskAlert]:
        """实时监控组合风险，返回需要处理的告警列表"""
```

**黑名单维护**（`stock_blacklist` 表）：
- ST/*ST 股票：每日从 Akshare `stock_zh_a_st_em` 自动同步
- 退市风险：近 60 日有退市公告的股票手动维护
- 黑名单添加后立即触发持仓中该股票的平仓流程

---

## 4. 回测框架（`backend/backtest/`）

```
backend/backtest/
├── backtest_engine.py   # 历史数据回放，调用 SignalEngine + TradingEngine
├── metrics.py           # 年化收益、夏普比率、最大回撤、胜率计算
└── report.py            # 生成回测报告（JSON + HTML）
```

**回测必须支持的指标**：

| 指标 | 计算方式 |
|------|---------|
| 年化收益率 | `(期末净值/期初净值)^(252/交易天数) - 1` |
| 夏普比率 | `(年化收益 - 无风险利率3%) / 年化波动率` |
| 最大回撤 | 净值序列的最大峰谷差 |
| 胜率 | 盈利交易数 / 总交易数 |
| 盈亏比 | 平均盈利金额 / 平均亏损金额 |
| 平均持仓天数 | 所有已平仓交易的持仓天数均值 |

---

## 5. 验收标准

```bash
# 5.1 策略单测：信号引擎输出可重现（固定种子数据）
pytest backend/tests/test_signal_engine.py -v

# 5.2 状态机单测：所有状态转换路径覆盖率 100%
pytest backend/tests/test_state_machine.py -v --cov=backend/trading/state_machine

# 5.3 风控单测：红线不可被绕过
pytest backend/tests/test_risk_control.py -v

# 5.4 回测验证：用 2023 年数据回测，年化收益应为正（基准验证）
python -m backend.backtest.backtest_engine --start 2023-01-01 --end 2023-12-31 --output /tmp/backtest.json

# 5.5 回测报告包含所有必要指标，无 NaN 字段
python -c "import json; r=json.load(open('/tmp/backtest.json')); assert all(v is not None for v in r['metrics'].values())"
```

---

## 6. 禁止事项

- ❌ 不得在信号引擎里使用未来数据（look-ahead bias）：当日信号只能用当日 15:00 前可获得的数据
- ❌ 不得跳过风控检查直接下单，`check_before_buy` 必须在每次买入前调用
- ❌ 不得修改 `MAX_DRAWDOWN_HARD_STOP` 等硬编码红线的值
- ❌ 不得在回测中使用收盘价买入（现实中无法做到），必须用次日开盘价
- ❌ 止损计算不得包含手续费前的价格，必须用**含手续费的实际成本价**

---

## 7. 输出产物

```
backend/strategy/signal_engine.py
backend/strategy/strategy_manager.py
backend/trading/engine.py
backend/trading/state_machine.py
backend/trading/position_manager.py
backend/trading/risk_control.py
backend/backtest/backtest_engine.py
backend/backtest/metrics.py
PostgreSQL:
  strategies          ← 策略配置
  trade_signals       ← 每日信号记录
  orders              ← 订单记录
  positions           ← 持仓记录
  portfolio_nav       ← 每日净值快照
  stock_blacklist     ← 风控黑名单
```
        用 check_and_close_by_rules 执行规则平仓
        2. 查询所有 status='open' 持仓的当日收盘价（从 kline_daily）
        3. 计算当日持仓市值：Σ(shares × close_price)
        4. 计算总资产：可用现金 + 持仓市值
        5. 计算单位净值：total_assets / initial_capital
        6. 计算 daily_return：(今日净值 - 昨日净值) / 昨日净值
        7. 计算历史最大回撤（rolling max drawdown）
        8. 写入 daily_nav（ON CONFLICT DO UPDATE）
        9. 触发 AHS 更新（调用 AHSCalculator.calculate）
        10. 发飞书日报通知（净值、持仓数、今日盈亏）
        """

    def _calc_max_drawdown(self, robot_id: int, up_to_date: date) -> float:
        """
        从 daily_nav 历史记录中计算截至 up_to_date 的最大回撤：
        max_drawdown = max((peak_nav - trough_nav) / peak_nav)
        使用滑动窗口，O(n) 实现。
        """
```

---

## 6. 飞书通知

```python
# backend/app/services/notify/feishu.py

class FeishuNotifier:
    """
    封装飞书 Webhook 消息发送，所有业务通知统一走此类。
    FEISHU_WEBHOOK_URL 从环境变量读取，为空时静默跳过（不报错）。
    """

    async def send_trade_open(self, robot_name: str, stock_code: str,
                               stock_name: str, shares: int,
                               price: float, score: float) -> None:
        """
        开仓通知模板：
        📈 开仓通知
        机器人：{robot_name}
        股票：{stock_code} {stock_name}
        数量：{shares} 股 @ ¥{price}
        评分：{score:.1f} 分
        时间：{now}
        """

    async def send_trade_close(self, robot_name: str, stock_code: str,
                                stock_name: str, pnl: float,
                                pnl_ratio: float, reason: CloseReason) -> None:
        """
        平仓通知模板（盈利用 📈，亏损用 📉）：
        📉 平仓通知
        机器人：{robot_name}
        股票：{stock_code} {stock_name}
        盈亏：{+pnl:.2f} 元 ({+pnl_ratio:.2%})
        原因：{reason.value}
        """

    async def send_daily_report(self, robot_name: str, nav: float,
                                 daily_return: float, open_count: int,
                                 ahs_score: float) -> None:
        """
        每日结算报告（15:00 后发送）：
        📊 每日结算报告 {date}
        机器人：{robot_name}
        净值：{nav:.4f} (今日 {+daily_return:.2%})
        持仓：{open_count} 只
        AHS：{ahs_score:.1f} ({risk_level})
        """

    async def send_alert(self, title: str, content: str, level: str = "warning") -> None:
        """通用告警，level: 'info' | 'warning' | 'error'"""
```

---

## 7. Celery Task 入口

```python
# backend/app/tasks/strategy_tasks.py

@celery_app.task(bind=True, max_retries=2, queue='trading')
def run_phase_a(self, robot_id: int, trade_date: str):
    """09:15 触发，执行 Phase A 选股，结果写入 selection_log。"""

@celery_app.task(bind=True, max_retries=2, queue='trading')
def run_phase_b(self, robot_id: int, trade_date: str):
    """
    09:40 触发，执行 Phase B 确认 + 下单。
    依赖：phase_a 已完成（从 selection_log 读取 phase='A' 结果）。
    """

@celery_app.task(bind=True, max_retries=2, queue='trading')
def run_daily_settlement(self, robot_id: int, settle_date: str):
    """15:00 触发，执行每日结算。"""

# Beat 调度补充（追加到 S01 的 celery_app.py）
beat_schedule_additions = {
    'phase-a-all-robots': {
        'task': 'app.tasks.strategy_tasks.run_phase_a_all',
        'schedule': crontab(hour=9, minute=15, day_of_week='1-5'),
        'options': {'queue': 'trading'},
    },
    'phase-b-all-robots': {
        'task': 'app.tasks.strategy_tasks.run_phase_b_all',
        'schedule': crontab(hour=9, minute=40, day_of_week='1-5'),
        'options': {'queue': 'trading'},
    },
    'daily-settlement-all': {
        'task': 'app.tasks.strategy_tasks.run_settlement_all',
        'schedule': crontab(hour=15, minute=5, day_of_week='1-5'),
        'options': {'queue': 'trading'},
    },
}
```

---

## 8. 验收标准

```bash
# 8.1 单元测试全部通过
pytest backend/tests/unit/test_scorer.py -v
# 必须覆盖：
#   - 净买入强度各档位边界值
#   - 席位质量：有/无知名游资、纯机构席位
#   - 价格动量：正/负/涨停板特殊处理
#   - 综合评分加权求和精度

pytest backend/tests/unit/test_phase_a.py -v
# 必须覆盖：
#   - ST 股票强制排除
#   - AHS=stop 时返回空列表
#   - 硬性过滤条件各边界

pytest backend/tests/unit/test_settlement.py -v
# 必须覆盖：
#   - 止损触发（亏损 >= stop_loss_ratio）
#   - 持仓天数超限触发
#   - 净值计算精度（与手工计算结果误差 < 0.01%）
#   - 最大回撤计算正确性

# 8.2 集成测试（使用测试数据库）
pytest backend/tests/integration/test_trading_flow.py -v
# 覆盖完整流程：Phase A → Phase B → 开仓 → 日结 → 规则平仓

# 8.3 飞书通知测试（需配置 FEISHU_WEBHOOK_URL）
pytest backend/tests/unit/test_feishu.py -v -k "test_send_mock"
# 使用 mock，验证消息模板格式正确

# 8.4 AHS 约束验证
# 手动创建一个 ahs_history 记录 ahs_score=85
# 执行 Phase B，验证返回空列表（停止开仓）
pytest backend/tests/unit/test_ahs_constraint.py -v
```

---

## 9. 禁止事项

- ❌ Phase A/B 评分权重不得在代码中硬编码，必须支持从 `system_config` 表动态读取
- ❌ 不得在 `open_position` 中跳过风控二次校验（即使 Phase B 已校验过）
- ❌ 模拟盘和实盘共用 `robot_trades` 表，`mode` 字段区分，不得建两张表
- ❌ 飞书通知失败不得阻塞主流程（try/except 捕获后只记录日志）
- ❌ 结算 Task 必须幂等（同日重复执行结果相同，不得产生重复净值记录）
- ❌ `check_and_close_by_rules` 中止损规则优先级最高，不得被其他条件覆盖

---

## 10. 输出产物

```
backend/app/services/
├── strategy/
│   ├── phase_a.py             # Phase A 引擎
│   ├── phase_b.py             # Phase B 引擎
│   ├── scorer.py              # 综合评分器（含 KNOWN_TRADERS 常量）
│   └── filters.py             # 过滤条件函数集合
├── trading/
│   └── executor.py            # 下单/平仓执行器
├── settlement.py              # 每日结算服务
├── ahs_calculator.py          # （S01 已建，本阶段补充 batch_update）
└── notify/
    └── feishu.py              # 飞书通知封装

backend/app/tasks/
└── strategy_tasks.py          # Celery Task 入口（Phase A/B/结算）

backend/tests/unit/
├── test_scorer.py             # 评分器单元测试（≥ 12 个用例）
├── test_phase_a.py            # Phase A 单元测试（≥ 8 个用例）
├── test_settlement.py         # 结算单元测试（≥ 8 个用例）
├── test_feishu.py             # 飞书通知测试（mock）
└── test_ahs_constraint.py     # AHS 约束测试

backend/tests/integration/
└── test_trading_flow.py       # 端到端交易流程集成测试
```
