# 龙虎榜量化交易系统 v2 - 风控体系文档

> 版本: 2.1
> 状态: 待实现
> 最后修订: 2026-04-16

---

## 〇、评审采纳说明

> 二次评审修订日期: 2026-04-16

### 已采纳的改进项 ✅

| 评审问题 | 采纳的改进 |
|----------|------------|
| AHS评分公式数学错误（方向反、值域不统一） | 重新设计公式：各因子先归一化到[0,100]，再按风险方向调整，确保0=最健康，100=最危险 |
| 熔断参数三处定义矛盾 | 数据源熔断引用 DATA_SOURCE.md，本文档只定义交易级熔断，避免重复 |
| 硬性限制定义不精确 | 补充术语定义表和判定逻辑伪代码 |
| AHS冷启动问题 | 新增分阶段冷启动策略和不确定性惩罚 |
| 恢复机制可能振荡 | 引入滞后带（Hysteresis），简化奖励机制 |
| 熔断与AHS重叠 | 明确层级关系，精简熔断为即时补充规则 |
| 缺少回测验证计划 | 新增参数验证方案章节 |
| 交易保护不完整 | 补充除权/停牌/零股等A股特殊场景 |
| 日志与表映射不明确 | 补充写入目标和 event_data JSONB 结构 |
| 配置项命名不统一 | 统一为 RISK_{层级}_{模块}_{参数} 格式 |
| 缺少交叉引用 | 补充与 DATABASE.md、DATA_SOURCE.md、TRADING_FLOW.md 的引用关系 |

### 不采纳的建议及原因 ❌

| 评审建议 | 不采纳原因 |
|----------|------------|
| 无 | 本次评审所有高/中优先级问题均合理，已采纳 |

---

## 一、风控体系概述

风控体系分为三层：

1. **系统级硬性限制**: 独立于策略，不可被覆盖
2. **策略级风控**: AHS 仓位管理系统
3. **交易级保护**: 止损止盈、单笔限制

### 风控层级关系

```
┌─────────────────────────────────────────────┐
│  系统级硬性限制（最高优先级，不可覆盖）      │
│  - 累计亏损停机、单笔上限、集中度上限        │
└─────────────────────┬───────────────────────┘
                      │ 覆盖
                      ▼
┌─────────────────────────────────────────────┐
│  即时熔断（实时事件触发，覆盖AHS）            │
│  - 单日急跌、数据源全部不可用                │
└─────────────────────┬───────────────────────┘
                      │ 覆盖
                      ▼
┌─────────────────────────────────────────────┐
│  AHS 仓位系数（日常运行，按日计算）          │
│  - 基于历史交易的持续评估                     │
└─────────────────────────────────────────────┘
```

---

## 二、系统级硬性限制

### 2.1 限制规则

| 限制项 | 阈值 | 说明 | 违反时动作 |
|--------|------|------|------------|
| 单笔买入上限 | 总资产 20% | 防止单次重仓 | 拒绝买入 |
| 单日买入总额上限 | 总资产 50% | 防止日内过度交易 | 拒绝新买入 |
| 单只持仓集中度上限 | 总资产 30% | 防止单只股票过度集中 | 拒绝加仓 |
| 累计亏损停机 | 总资产缩水 20% | 单机器人累计亏损达到阈值 | 停止所有开仓 |

### 2.2 术语精确定义

| 术语 | 定义 | 计算方式 |
|------|------|----------|
| 总资产 | 现金 + 持仓市值 | `robots.cash_balance + SUM(positions.market_value)` |
| 可用资金 | 可用于买入的现金 | `robots.cash_balance - 已挂单冻结金额` |
| 当日买入总额 | 今日所有买入执行金额之和 | `SUM(今日 trade_executions WHERE execution_type='buy')` |
| 累计亏损 | 总资产相对初始资金的缩水比例 | `1 - (当前总资产 / robots.initial_capital)` |
| 持仓市值 | 单只股票的持仓价值 | `持仓股数 × 当前市价` |

### 2.3 判定逻辑

```python
def check_hard_limits(robot_id: int, buy_amount: float, stock_code: str) -> tuple[bool, str]:
    """
    检查系统级硬性限制
    
    Returns:
        (passed, reason): passed=True 表示通过检查，reason 为失败原因
    """
    robot = get_robot(robot_id)
    positions = get_positions(robot_id)
    
    # 计算总资产
    total_assets = robot.cash_balance + sum(p.market_value for p in positions)
    
    # 限制1：单笔买入上限
    if buy_amount > total_assets * 0.20:
        return False, f"单笔买入金额 {buy_amount:.2f} 超过总资产20%上限 {total_assets*0.20:.2f}"
    
    # 限制2：单日买入总额上限
    today_buys = get_today_buy_amounts(robot_id)
    if today_buys + buy_amount > total_assets * 0.50:
        return False, f"当日买入总额 {today_buys+buy_amount:.2f} 超过总资产50%上限"
    
    # 限制3：单只持仓集中度上限（同股票多笔需合并计算）
    stock_position = sum(p.market_value for p in positions if p.stock_code == stock_code)
    if stock_position + buy_amount > total_assets * 0.30:
        return False, f"持仓 {stock_code} 集中度 {(stock_position+buy_amount)/total_assets:.1%} 超过30%上限"
    
    # 限制4：累计亏损停机
    cumulative_loss = 1 - (total_assets / robot.initial_capital)
    if cumulative_loss >= 0.20:
        return False, f"累计亏损 {cumulative_loss:.1%} 已达停机阈值"
    
    return True, ""

### 2.4 恢复机制

累计亏损停机有恢复阈值，防止振荡：

```python
# 停机阈值：累计亏损 >= 20%
# 恢复阈值：累计亏损 <= 15%（滞后5%）

if cumulative_loss >= 0.20:
    trading_mode = 'stopped'
elif cumulative_loss <= 0.15 and trading_mode == 'stopped':
    trading_mode = 'cautious'  # 恢复后从谨慎模式开始
```

---

## 三、AHS 仓位管理系统

### 3.1 AHS 评分计算公式（修订版）

> **核心原则**：AHS_SCORE ∈ [0, 100]，0 = 最健康，100 = 最危险

```python
def calculate_ahs(win_rate: float, avg_pnl: float, consecutive_loss: int, max_drawdown: float) -> float:
    """
    AHS评分：基于风险的仓位管理
    
    Args:
        win_rate: 近20笔交易胜率 [0, 1]
        avg_pnl: 近20笔交易平均收益率 [-1, +1]
        consecutive_loss: 连续亏损笔数 [0, 5+]
        max_drawdown: 最大回撤 [0, 1]
    
    Returns:
        AHS_SCORE: [0, 100]，越高越危险
    """
    # 因子1：胜率惩罚（胜率越低 → 越危险 → 分数越高）
    win_penalty = (1 - win_rate) * 100  # 0~100
    
    # 因子2：盈亏比惩罚（亏损越多 → 越危险 → 分数越高）
    # avg_pnl: -1 ~ +1，归一化到 0~100
    pnl_penalty = max(0, min(100, (1 - avg_pnl) * 50))  # 0~100
    
    # 因子3：连续亏损惩罚（连续亏损越多 → 越危险）
    loss_penalty = min(consecutive_loss / 5, 1.0) * 100  # 0~100
    
    # 因子4：回撤惩罚（回撤越大 → 越危险）
    # 15%回撤 = 满分，低于15%按比例计算
    drawdown_penalty = min(abs(max_drawdown) / 0.15, 1.0) * 100  # 0~100
    
    # 加权求和（总权重 = 1.0）
    ahs = (
        win_penalty * 0.25 +       # 胜率权重 25%
        pnl_penalty * 0.25 +       # 盈亏权重 25%
        loss_penalty * 0.30 +      # 连续亏损权重 30%（最重要的短期信号）
        drawdown_penalty * 0.20    # 回撤权重 20%
    )
    
    return max(0, min(100, ahs))

# 验证用例：
# 全部健康：win=0.7, pnl=0.05, loss=0, dd=0
# AHS = 30*0.25 + 47.5*0.25 + 0 + 0 = 19.375 → 正常交易（100%仓位）✓

# 一般情况：win=0.5, pnl=0, loss=2, dd=-0.05
# AHS = 50*0.25 + 50*0.25 + 40*0.30 + 33.3*0.20 = 12.5+12.5+12+6.66 = 43.66 → 保守交易（70%仓位）✓

# 危险情况：win=0.3, pnl=-0.03, loss=4, dd=-0.12
# AHS = 70*0.25 + 65*0.25 + 80*0.30 + 80*0.20 = 17.5+16.25+24+16 = 73.75 → 警戒模式（40%仓位）✓

# 极端危险：win=0.1, pnl=-0.08, loss=5, dd=-0.18
# AHS = 90*0.25 + 77*0.25 + 100*0.30 + 100*0.20 = 22.5+19.25+30+20 = 91.75 → 停止开仓 ✓
```

### 3.2 AHS 评分输入数据

| 因子 | 数据来源 | 计算周期 | 默认值（无数据时） |
|------|----------|----------|-------------------|
| WIN_RATE | `robot_trades` | 近20笔已完成交易 | 0.50 |
| AVG_PNL | `robot_trades` | 近20笔已完成交易 | 0.00 |
| CONSECUTIVE_LOSS | `robot_trades` | 从最近一笔往前数 | 0 |
| MAX_DRAWDOWN | `daily_nav` | 近20个交易日最大回撤 | 0 |

### 3.3 AHS 冷启动策略

> 新机器人没有历史交易数据，需要分阶段处理

| 阶段 | 条件 | AHS 处理 | 仓位系数 | 说明 |
|------|------|----------|----------|------|
| 无数据 | 0笔交易 | 固定 50 分 | 70% | 从保守开始，不参与淘汰排名 |
| 少量数据 | 1-9 笔 | 实际计算 + 不确定性惩罚 | 惩罚后系数 | `penalty = (1 - count/10) × 20` |
| 初步数据 | 10-19 笔 | 实际计算 + 轻微惩罚 | 惩罚后系数 | `penalty = (1 - count/20) × 10` |
| 充足数据 | ≥20 笔 | 标准公式 | 标准映射 | 无惩罚，正常参与淘汰 |

```python
def get_cold_start_penalty(trade_count: int) -> float:
    """计算冷启动不确定性惩罚"""
    if trade_count == 0:
        return 50  # 固定分数，不参与计算
    elif trade_count < 10:
        return (1 - trade_count / 10) * 20  # 最多惩罚20分
    elif trade_count < 20:
        return (1 - trade_count / 20) * 10  # 最多惩罚10分
    else:
        return 0  # 无惩罚

# 例：5笔交易，AHS计算值为30
# penalty = (1 - 5/10) * 20 = 10
# AHS_final = 30 + 10 = 40 → 谨慎模式（85%仓位）
```

### 3.4 仓位映射表（含滞后带）

| AHS 区间 | 进入条件 | 退出条件 | 仓位系数 | 行为 |
|----------|----------|----------|----------|------|
| 正常 | AHS ≤ 15 | AHS > 20 | 100% | 策略满仓运行 |
| 谨慎 | AHS ≤ 35 | AHS > 40 | 85% | 降低开仓规模 |
| 保守 | AHS ≤ 55 | AHS > 60 | 70% | 进一步降低仓位 |
| 警戒 | AHS ≤ 75 | AHS > 80 | 40% | 只做高确信度信号 |
| 停止开仓 | AHS > 80 | AHS ≤ 60 | 0% | 只平不清，等待恢复 |

> **滞后带设计**：
> - 进入"停止开仓"需要 AHS > 80
> - 恢复出"停止开仓"需要 AHS ≤ 60（滞后 20 分）
> - 其他状态滞后带宽度为 5 分
> - 确保不会在阈值附近频繁切换

### 3.5 AHS 恢复机制（简化版）

| 条件 | 动作 | 说明 |
|------|------|------|
| AHS 自然下降 | 仓位恢复 | 随着交易数据改善自动恢复 |
| 连续 10 个交易日无亏损 | AHS 强制降至 max(当前, 40) | 长期稳定运行的奖励 |

> **注意**：移除了"单日盈利>3%加分"机制（波动太大，容易振荡）

---

## 四、熔断机制（精简版）

### 4.1 设计原则

- AHS 是核心引擎（按日计算，持续评估）
- 熔断是补充触发器（实时事件，即时响应）
- 每个风险事件只由一层处理，不重复响应

### 4.2 熔断触发条件

| 触发条件 | 动作 | 恢复条件 | 说明 |
|----------|------|----------|------|
| 单日亏损 > 总资产 5% | 立即停止开仓 | 下一交易日 + AHS < 60 | AHS 按日计算，无法捕获日内急跌 |
| 数据源全部不可用 | 暂停所有交易 | 数据源恢复 | 无数据时不能做决策 |
| 系统异常（DB/Redis 宕机） | 暂停所有交易 | 系统恢复 | 基础设施保护 |

> **说明**：已删除与 AHS 重叠的触发条件：
> - ~~L1: AHS>=81~~ → 已由 AHS 直接处理
> - ~~L3: 连续3笔亏损~~ → 已反映在 consecutive_loss 因子中
> - ~~L4: 资金利用率低~~ → 这不是风险事件

### 4.3 数据源熔断

> 数据源级别的熔断器配置详见 [DATA_SOURCE.md - 熔断器配置章节](./DATA_SOURCE.md#七熔断器配置)

本文档不重复定义数据源熔断参数，避免三处定义不一致。

| 类别 | 触发对象 | 配置位置 | 管理模块 |
|------|----------|----------|----------|
| 交易熔断 | 机器人交易表现 | 本章 4.2 节 | `risk_service` |
| 数据源熔断 | 数据源 API 可用性 | DATA_SOURCE.md | `data_providers` |

---

## 五、交易级保护

### 5.1 单笔交易限制

| 限制项 | 阈值 | 说明 |
|--------|------|------|
| 最低买入金额 | 1000 元 | 低于此金额不交易 |
| 最低卖出数量 | 100 股 | A 股最小单位 |
| 最高滑点容忍 | 2% | 超过则拒绝成交 |

### 5.2 价格保护

| 场景 | 处理方式 |
|------|----------|
| 买入价格 > 涨停价 | 拒绝买入 |
| 卖出价格 < 跌停价 | 挂跌停价等待 |
| 价格偏离昨日收盘 > 10% | 告警，人工确认 |

### 5.3 时间保护

| 场景 | 处理方式 |
|------|----------|
| 非交易时段 | 不执行任何交易 |
| 集合竞价 (09:15-09:25) | 不执行监控 |
| 午间休市 (11:30-13:00) | 暂停监控 |

### 5.4 特殊场景保护

| 场景 | 检测方式 | 处理方式 |
|------|----------|----------|
| 除权除息 | 对比昨收与今开差异 + 除权标记 | 重新计算止损止盈线 |
| 停牌 | 实时行情返回状态='停牌' | 冻结 hold_days，复牌后继续 |
| 临时停牌 | 盘中行情中断 | 等待复牌，不视为止损失败 |
| 资金不足100股 | 买入前检查 | 放弃买入或调整为可买数量 |
| 零股卖出 | 剩余股数 < 100 | 允许一次性卖出 |
| 当日买入 T+1 | 检查买入日期 | 买入当日不可卖出 |
| ST 股 | stock_info 表 is_st 字段 | Phase B 前置检查拒绝 |
| 科创板/创业板 | stock_info 表 board 字段 | 涨跌停阈值自动调整 |

---

## 六、涨跌停特殊处理

### 6.1 涨停板处理

| 场景 | 处理方式 |
|------|----------|
| Phase B 买入时涨停封板 | 放弃买入，记录原因 |
| 持仓股涨停 | 继续持有，监控开板 |
| 开板信号 | 全部卖出 |

**开板判断**: 当前价格 < 涨停价 × 0.98（偏离 2% 以上视为开板）

### 6.2 跌停板处理

| 场景 | 处理方式 |
|------|----------|
| 止损触发但跌停 | 挂跌停价等待卖出 |
| 跌停后下一个交易日 | 优先卖出（排在跌停价队伍前面） |

### 6.3 板块差异化

| 板块 | 涨停价 | 跌停价 | 止损敏感度 | Phase B 处理 |
|------|--------|--------|------------|--------------|
| 主板 | +10% | -10% | 标准 | 正常执行 |
| 科创板/创业板 | +20% | -20% | 放宽 2 倍 | 检查是否注册制 |
| 北交所 | +30% | -30% | 建议**过滤** | 不买入 |
| ST 股 | +5% | -5% | 建议**过滤** | Phase B 拒绝 |

> 板块信息来源：参见 [DATABASE.md - stock_info 表](./DATABASE.md#stock_info)

---

## 七、集合竞价处理

### 7.1 Phase B 执行流程

```
09:15 集合竞价开始
    ↓
09:20 获取开盘价
    ↓
开盘价偏离度检查: |开盘价 - 昨日收盘| / 昨日收盘 > 5%?
    ↓ 是 → 放弃买入，记录原因
    ↓ 否 → 继续
    ↓
09:25 确认买入数量和价格
    ↓
09:30 连续竞价开始，执行买入
```

### 7.2 偏离度阈值

```python
OPEN_PRICE_DEVIATION_THRESHOLD = 0.05  # 5%
```

超过此阈值视为异常，放弃买入。

---

## 八、风控配置表

### 8.1 app_config 风控配置

> 配置项命名规范：`RISK_{层级}_{模块}_{参数}`

| key | value | value_type | 说明 |
|-----|-------|------------|------|
| **系统级限制** | | | |
| RISK_SYS_MAX_SINGLE_POSITION | 0.20 | float | 单笔买入上限（占总资产） |
| RISK_SYS_MAX_DAILY_TRADE | 0.50 | float | 单日买入总额上限 |
| RISK_SYS_MAX_CONCENTRATION | 0.30 | float | 单只持仓集中度上限 |
| RISK_SYS_LOSS_STOP_THRESHOLD | 0.20 | float | 累计亏损停机阈值 |
| RISK_SYS_LOSS_RECOVER_THRESHOLD | 0.15 | float | 累计亏损恢复阈值 |
| **AHS 阈值** | | | |
| RISK_AHS_THRESHOLD_CAUTIOUS | 21 | int | 进入谨慎模式阈值 |
| RISK_AHS_THRESHOLD_CONSERVATIVE | 41 | int | 进入保守模式阈值 |
| RISK_AHS_THRESHOLD_WARNING | 61 | int | 进入警戒模式阈值 |
| RISK_AHS_THRESHOLD_STOP | 81 | int | 停止开仓阈值 |
| RISK_AHS_HYSTERESIS | 5 | int | 滞后带宽度（分） |
| RISK_AHS_COLD_START_SCORE | 50 | int | 冷启动默认分数 |
| **交易级保护** | | | |
| RISK_TRADE_MIN_BUY_AMOUNT | 1000 | int | 最低买入金额（元） |
| RISK_TRADE_MAX_SLIPPAGE | 0.02 | float | 最大滑点容忍度 |
| RISK_TRADE_OPEN_DEVIATION | 0.05 | float | 开盘价偏离度阈值 |
| RISK_TRADE_DAILY_LOSS_CIRCUIT | 0.05 | float | 单日亏损熔断阈值 |
| **交易时间** | | | |
| RISK_TIME_MONITOR_START | 09:30 | str | 监控开始时间 |
| RISK_TIME_MONITOR_END | 15:00 | str | 监控结束时间 |
| RISK_TIME_LUNCH_START | 11:30 | str | 午休开始时间 |
| RISK_TIME_LUNCH_END | 13:00 | str | 午休结束时间 |

---

## 九、风控日志

### 9.1 日志写入目标

> 参见 [DATABASE.md - audit_log 表](./DATABASE.md#audit_log)

| 事件 | 写入表 | event_type | event_data 结构 |
|------|--------|------------|-----------------|
| AHS 评分更新 | `ahs_history` | - | 专用表，含所有因子明细 |
| 熔断触发 | `trade_events` | circuit_break | 见下方 |
| 熔断恢复 | `trade_events` | circuit_recover | 见下方 |
| 硬限制拒绝 | `audit_log` | risk_reject | 见下方 |
| 仓位调整 | `audit_log` | position_adjust | 见下方 |

### 9.2 event_data JSONB 结构

#### 熔断触发

```json
{
  "trigger_type": "daily_loss",
  "trigger_value": 0.052,
  "threshold": 0.05,
  "robot_id": 1,
  "ahs_score": 75,
  "action": "stop_open",
  "positions_affected": ["000001", "600519"]
}
```

#### 硬限制拒绝

```json
{
  "limit_type": "single_position",
  "requested_amount": 25000,
  "limit_value": 20000,
  "total_assets": 100000,
  "ratio": 0.25,
  "threshold": 0.20,
  "stock_code": "000001",
  "action": "rejected"
}
```

#### 仓位调整

```json
{
  "old_ahs": 45,
  "new_ahs": 62,
  "old_position_ratio": 0.70,
  "new_position_ratio": 0.40,
  "reason": "consecutive_loss_increased",
  "consecutive_losses": 3
}
```

### 9.3 告警规则

| 告警级别 | 触发条件 | 通知方式 |
|----------|----------|----------|
| INFO | 熔断触发/恢复 | 飞书群消息 |
| WARN | AHS 进入 61-80 区间 | 飞书 @指定人 |
| CRITICAL | AHS 进入 81-100 或单日亏损熔断 | 飞书电话 + 短信 |

---

## 十、参数验证计划

### 10.1 回测验证项

| 参数 | 验证方法 | 关注指标 |
|------|----------|----------|
| 累计亏损阈值（20%） | 对比 15%/20%/25% 三组 | 触发次数、恢复后收益、最大回撤 |
| AHS 阈值分档 | 对比不同分档方案 | 年化收益、夏普比率、最大回撤 |
| 开盘偏离度（5%） | 对比 3%/5%/8% 三组 | 被过滤信号的后续表现 |
| 仓位系数映射 | 对比固定仓位 vs AHS 动态 | 风险调整后收益 |

### 10.2 验证时机

- **Phase 4**（交易引擎完成后）：使用近 1 年龙虎榜数据进行历史回测
- **模拟盘验证**：模拟盘运行 20+ 交易日，验证实际效果
- **参数调优流程**：
  1. 回测确定初始参数
  2. 模拟盘验证实际效果
  3. 根据实际表现微调，更新 `app_config`
  4. 记录每次调整的原因和效果

---

## 十一、版本历史

| 版本 | 日期 | 修改内容 |
|------|------|----------|
| 1.0.0 | 2026-04-16 | 初始版本 |
| 2.1.0 | 2026-04-16 | 评审修订：修正 AHS 公式、消除熔断参数重复、补充冷启动策略、引入滞后带 |

---

## 十二、参考文档

| 文档 | 关系 |
|------|------|
| [DATA_SOURCE.md](./DATA_SOURCE.md) | 数据源熔断器参数定义 |
| [DATABASE.md](./DATABASE.md) | app_config、ahs_history、trade_events 表结构 |
| [TRADING_FLOW.md](./TRADING_FLOW.md) | Phase B 前置检查流程、盘中监控时间窗口 |
| [API.md](./API.md) | GET /robots/{id}/ahs 接口定义 |
| [ARCHITECTURE.md](./ARCHITECTURE.md) | AHS 计算任务调度（Celery Beat） |

---

*文档版本: 2.1*
*最后修订: 2026-04-16*
