# P0 阶段：Bug修复 + 交易成本体系

> **前置条件**：无
> **预计时间**：1-2天
> **文档版本**：v1.0

---

## 目标

修复现有模拟炒股系统的三个核心Bug，并建立完整的交易成本计算体系。

---

## Bug 清单

| Bug | 问题描述 | 影响 |
|-----|----------|------|
| Bug1 | Phase B 使用 `TOTAL_CAPITAL` 而非 `buy_amount` | 仓位计算错误 |
| Bug2 | `sim_selection_log` 缺少 `lianban_score` 字段 | 选股评分丢失 |
| Bug3 | 多机器人无隔离（缺少 `robot_id`） | 数据混乱 |

---

## 交易成本参数

| 类别 | 参数 | 值 |
|------|------|-----|
| 印花税 | 卖出 | 0.05% |
| 佣金 | 买卖双向 | 万3（0.03%），最低5元 |
| 过户费 | 沪市 | 0.001%，深市免收 |
| 滑点 | 模拟 | 买入+0.1%，卖出-0.1% |

---

## 数据库迁移

### 迁移脚本

**文件路径**：`scripts/migrate_p0.py`

```python
"""
P0 数据库迁移脚本

修复内容：
1. sim_selection_log 添加 lianban_score 字段
2. 所有模拟交易表添加 robot_id 字段
3. sim_trades / sim_trade_events 添加交易成本字段
4. 修复仓位溢出的脏数据
"""
import logging
from utils.storage import storage
from sqlalchemy import text, inspect

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
logger = logging.getLogger(__name__)

TOTAL_CAPITAL = 100000


def add_column_if_missing(conn, inspector, table: str, column: str, col_type: str):
    """安全添加字段"""
    if table not in inspector.get_table_names():
        logger.warning(f"  表 {table} 不存在，跳过")
        return False
    
    existing = [c['name'] for c in inspector.get_columns(table)]
    if column in existing:
        logger.info(f"  ✓ {table}.{column} 已存在")
        return False
    
    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"))
    logger.info(f"  ✅ {table}.{column} 已添加")
    return True


def step1_fix_selection_log(conn, inspector):
    """Bug2: 补 lianban_score 字段"""
    logger.info("\n=== Step 1: 修复 sim_selection_log ===")
    add_column_if_missing(conn, inspector, 'sim_selection_log', 'lianban_score', 'FLOAT DEFAULT 0')


def step2_add_robot_id(conn, inspector):
    """Bug3: 所有表添加 robot_id"""
    logger.info("\n=== Step 2: 添加 robot_id ===")
    
    tables = ['sim_trades', 'sim_trade_events', 'sim_selection_log']
    
    for table in tables:
        added = add_column_if_missing(
            conn, inspector, table,
            'robot_id', "VARCHAR(50) DEFAULT 'default'"
        )
        if added:
            conn.execute(text(
                f"UPDATE {table} SET robot_id = 'default' WHERE robot_id IS NULL"
            ))
    
    # 添加索引
    conn.execute(text("""
        CREATE INDEX IF NOT EXISTS idx_sim_trades_robot_status 
        ON sim_trades(robot_id, status)
    """))
    logger.info("  ✅ 索引已创建")


def step3_add_cost_fields(conn, inspector):
    """新增交易成本字段"""
    logger.info("\n=== Step 3: 添加交易成本字段 ===")
    
    # sim_trades 表：买卖两侧的成本
    cost_columns = {
        'buy_commission': 'FLOAT DEFAULT 0',
        'buy_transfer_fee': 'FLOAT DEFAULT 0',
        'buy_slippage_price': 'FLOAT',
        'sell_commission': 'FLOAT DEFAULT 0',
        'sell_transfer_fee': 'FLOAT DEFAULT 0',
        'sell_stamp_tax': 'FLOAT DEFAULT 0',
        'sell_slippage_price': 'FLOAT',
        'total_fees': 'FLOAT DEFAULT 0',
        'net_profit': 'FLOAT',
        'net_profit_pct': 'FLOAT',
    }
    
    for col, col_type in cost_columns.items():
        add_column_if_missing(conn, inspector, 'sim_trades', col, col_type)
    
    # sim_trade_events 表：每笔事件的成本
    event_cost_columns = {
        'commission': 'FLOAT DEFAULT 0',
        'transfer_fee': 'FLOAT DEFAULT 0',
        'stamp_tax': 'FLOAT DEFAULT 0',
        'slippage_price': 'FLOAT',
        'total_cost': 'FLOAT DEFAULT 0',
    }
    
    for col, col_type in event_cost_columns.items():
        add_column_if_missing(conn, inspector, 'sim_trade_events', col, col_type)


def step4_fix_dirty_data(conn):
    """Bug1: 修复仓位溢出的脏数据"""
    logger.info("\n=== Step 4: 修复脏数据 ===")
    
    rows = conn.execute(text('''
        SELECT id, stock_code, stock_name, buy_amount, buy_price, buy_shares
        FROM sim_trades
        WHERE buy_amount > 0 
          AND buy_price > 0
          AND buy_shares > 0
          AND buy_shares * buy_price > buy_amount * 1.5
    ''')).fetchall()
    
    if not rows:
        logger.info("  ✓ 无异常数据")
        return
    
    logger.info(f"  发现 {len(rows)} 条异常记录")
    
    for row in rows:
        r = dict(row._mapping)
        old_shares = r['buy_shares']
        old_amount = old_shares * r['buy_price']
        
        # 重新计算正确股数
        new_shares = int(r['buy_amount'] / r['buy_price'] / 100) * 100
        if new_shares < 100 and r['buy_amount'] >= r['buy_price'] * 100:
            new_shares = 100
        new_amount = new_shares * r['buy_price']
        
        logger.info(
            f"  修复 id={r['id']} {r['stock_code']} {r['stock_name']}: "
            f"shares {old_shares}→{new_shares}, "
            f"amount {old_amount:.0f}→{new_amount:.0f}"
        )
        
        # 更新 sim_trades
        conn.execute(text('''
            UPDATE sim_trades SET buy_shares = :shares WHERE id = :id
        '''), {'shares': new_shares, 'id': r['id']})
        
        # 更新 sim_trade_events
        conn.execute(text('''
            UPDATE sim_trade_events 
            SET shares = :shares, amount = :amount
            WHERE trade_id = :trade_id AND direction = 'BUY'
        '''), {'shares': new_shares, 'amount': new_amount, 'trade_id': r['id']})
    
    logger.info(f"  ✅ 已修复 {len(rows)} 条记录")


def run():
    engine = storage._get_engine()
    inspector = inspect(engine)
    
    logger.info("🚀 P0 数据库迁移开始")
    
    with engine.begin() as conn:
        step1_fix_selection_log(conn, inspector)
        step2_add_robot_id(conn, inspector)
        step3_add_cost_fields(conn, inspector)
        step4_fix_dirty_data(conn)
    
    logger.info("\n🎉 P0 迁移全部完成")


if __name__ == '__main__':
    run()
```

---

## 核心模块

### 1. 交易成本计算模块

**文件路径**：`skills/stock_sim_robot/trade_cost.py`

```python
"""
交易成本计算模块

A股交易费用：
- 印花税：卖出 0.05%，买入免收
- 佣金：买卖双向，万3（0.03%），单笔最低5元
- 过户费：沪市 0.001%，深市免收
- 滑点：买入 +0.1%，卖出 -0.1%（模拟市场冲击）
"""
from dataclasses import dataclass, field


@dataclass
class CostConfig:
    """交易费率配置，可按机器人自定义"""
    stamp_tax_rate: float = 0.0005       # 印花税 0.05%
    commission_rate: float = 0.0003      # 佣金 万3
    commission_min: float = 5.0          # 佣金最低5元
    transfer_fee_rate_sh: float = 0.00001  # 沪市过户费 0.001%
    transfer_fee_rate_sz: float = 0.0      # 深市免收
    slippage_rate: float = 0.001         # 滑点 0.1%


DEFAULT_COST_CONFIG = CostConfig()


def is_sh_stock(code: str) -> bool:
    """判断是否沪市股票：6开头"""
    return code.startswith(('6', '688'))


def calc_slippage_price(price: float, direction: str, config: CostConfig = None) -> float:
    """计算滑点后的实际成交价"""
    if config is None:
        config = DEFAULT_COST_CONFIG
    
    if direction == 'BUY':
        return round(price * (1 + config.slippage_rate), 2)
    else:  # SELL
        return round(price * (1 - config.slippage_rate), 2)


def calc_buy_cost(price: float, shares: int, code: str, config: CostConfig = None) -> dict:
    """计算买入总成本"""
    if config is None:
        config = DEFAULT_COST_CONFIG
    
    amount = price * shares
    
    # 佣金：万3，最低5元
    commission = max(amount * config.commission_rate, config.commission_min)
    
    # 过户费：沪市收，深市免
    if is_sh_stock(code):
        transfer_fee = amount * config.transfer_fee_rate_sh
    else:
        transfer_fee = 0.0
    
    stamp_tax = 0.0  # 买入免收
    
    total_cost = round(commission + transfer_fee + stamp_tax, 2)
    total_paid = round(amount + total_cost, 2)
    
    return {
        'amount': round(amount, 2),
        'commission': round(commission, 2),
        'transfer_fee': round(transfer_fee, 2),
        'stamp_tax': stamp_tax,
        'total_cost': total_cost,
        'total_paid': total_paid,
    }


def calc_sell_cost(price: float, shares: int, code: str, config: CostConfig = None) -> dict:
    """计算卖出总成本"""
    if config is None:
        config = DEFAULT_COST_CONFIG
    
    amount = price * shares
    
    commission = max(amount * config.commission_rate, config.commission_min)
    
    if is_sh_stock(code):
        transfer_fee = amount * config.transfer_fee_rate_sh
    else:
        transfer_fee = 0.0
    
    stamp_tax = amount * config.stamp_tax_rate  # 卖出收 0.05%
    
    total_cost = round(commission + transfer_fee + stamp_tax, 2)
    total_received = round(amount - total_cost, 2)
    
    return {
        'amount': round(amount, 2),
        'commission': round(commission, 2),
        'transfer_fee': round(transfer_fee, 2),
        'stamp_tax': round(stamp_tax, 2),
        'total_cost': total_cost,
        'total_received': total_received,
    }


def calc_trade_profit(
    buy_price: float, sell_price: float, 
    shares: int, code: str,
    config: CostConfig = None
) -> dict:
    """计算单笔交易完整盈亏（含所有成本）"""
    if config is None:
        config = DEFAULT_COST_CONFIG
    
    buy = calc_buy_cost(buy_price, shares, code, config)
    sell = calc_sell_cost(sell_price, shares, code, config)
    
    gross_profit = sell['amount'] - buy['amount']
    total_fees = buy['total_cost'] + sell['total_cost']
    net_profit = gross_profit - total_fees
    net_profit_pct = (net_profit / buy['total_paid']) * 100
    
    return {
        'buy_cost': buy,
        'sell_cost': sell,
        'gross_profit': round(gross_profit, 2),
        'total_fees': round(total_fees, 2),
        'net_profit': round(net_profit, 2),
        'net_profit_pct': round(net_profit_pct, 4),
    }
```

### 2. 仓位计算模块

**文件路径**：`skills/stock_sim_robot/position_calc.py`

```python
"""
仓位计算模块

职责：根据 Phase A 的决策金额计算买入股数
替代原来 trade_runner.py 中的硬编码 BUY_AMOUNT
"""
import logging
from .trade_cost import calc_slippage_price, calc_buy_cost, DEFAULT_COST_CONFIG

logger = logging.getLogger(__name__)

DEFAULT_POSITION_RATIO = 0.1   # 默认仓位 10%
MAX_SINGLE_TRADE_RATIO = 0.3   # 单笔最大 30%


def calculate_buy_shares(
    planned_amount: float,
    raw_price: float,
    stock_code: str,
    total_capital: float = 100000,
) -> dict:
    """
    计算买入股数（含滑点和费用）
    
    Returns:
        {
            'shares': 买入股数,
            'slippage_price': 滑点后成交价,
            'planned_amount': 计划金额,
            'actual_amount': 实际成交金额,
            'cost_detail': 成本明细,
            'valid': 是否有效,
            'reason': 无效原因,
        }
    """
    result = {
        'shares': 0,
        'slippage_price': 0,
        'planned_amount': planned_amount,
        'actual_amount': 0,
        'cost_detail': {},
        'valid': False,
        'reason': '',
    }
    
    # Step 1: 校验 planned_amount
    if planned_amount is None or planned_amount <= 0:
        planned_amount = total_capital * DEFAULT_POSITION_RATIO
        logger.warning(f"buy_amount 无效，使用默认仓位: {planned_amount:.0f}")
        result['planned_amount'] = planned_amount
    
    # Step 2: 单笔上限
    max_amount = total_capital * MAX_SINGLE_TRADE_RATIO
    if planned_amount > max_amount:
        logger.warning(f"buy_amount {planned_amount:.0f} 超过单笔上限 {max_amount:.0f}，截断")
        planned_amount = max_amount
        result['planned_amount'] = planned_amount
    
    # Step 3: 计算滑点后价格
    slippage_price = calc_slippage_price(raw_price, 'BUY')
    result['slippage_price'] = slippage_price
    
    # Step 4: 预估费用，倒推可用于买股票的金额
    fee_ratio = DEFAULT_COST_CONFIG.commission_rate
    if stock_code.startswith(('6', '688')):
        fee_ratio += DEFAULT_COST_CONFIG.transfer_fee_rate_sh
    available_for_stock = planned_amount / (1 + fee_ratio)
    
    # Step 5: 计算股数（向下取整到100）
    raw_shares = available_for_stock / slippage_price
    shares = int(raw_shares / 100) * 100
    
    # Step 6: 至少100股
    if shares < 100:
        min_cost = calc_buy_cost(slippage_price, 100, stock_code)
        if planned_amount >= min_cost['total_paid']:
            shares = 100
        else:
            result['reason'] = f"资金不足: 需要{min_cost['total_paid']:.0f}, 可用{planned_amount:.0f}"
            logger.warning(f"{stock_code} {result['reason']}")
            return result
    
    # Step 7: 计算实际成本
    cost = calc_buy_cost(slippage_price, shares, stock_code)
    
    # Step 8: 如果总支出超过计划金额，减100股
    while cost['total_paid'] > planned_amount and shares >= 200:
        shares -= 100
        cost = calc_buy_cost(slippage_price, shares, stock_code)
    
    if cost['total_paid'] > planned_amount and shares < 200:
        result['reason'] = f"调整后仍超出预算"
        logger.warning(f"{stock_code} {result['reason']}")
        return result
    
    # Step 9: 填充结果
    result['shares'] = shares
    result['actual_amount'] = cost['amount']
    result['cost_detail'] = cost
    result['valid'] = True
    
    logger.info(
        f"📊 {stock_code} 仓位计算: "
        f"计划={planned_amount:.0f}, 价格={raw_price}→{slippage_price}, "
        f"股数={shares}, 实际={cost['total_paid']:.0f}"
    )
    
    return result
```

---

## 验证脚本

**文件路径**：`scripts/verify_p0.py`

```python
"""P0 修复验证：三个bug + 交易成本"""
import sys
import logging
from utils.storage import storage
from sqlalchemy import text, inspect

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
logger = logging.getLogger(__name__)


def verify():
    engine = storage._get_engine()
    inspector = inspect(engine)
    errors = []
    
    # 验证1: lianban_score 字段
    logger.info("验证1: sim_selection_log.lianban_score")
    cols = [c['name'] for c in inspector.get_columns('sim_selection_log')]
    if 'lianban_score' in cols:
        logger.info("  ✅ 通过")
    else:
        errors.append("sim_selection_log 缺少 lianban_score")
    
    # 验证2: robot_id 字段
    logger.info("验证2: robot_id 字段")
    for table in ['sim_trades', 'sim_trade_events', 'sim_selection_log']:
        cols = [c['name'] for c in inspector.get_columns(table)]
        if 'robot_id' in cols:
            logger.info(f"  ✅ {table}.robot_id 存在")
        else:
            errors.append(f"{table} 缺少 robot_id")
    
    # 验证3: 交易成本字段
    logger.info("验证3: 交易成本字段")
    cost_cols = ['buy_commission', 'sell_commission', 'sell_stamp_tax', 'total_fees', 'net_profit']
    cols = [c['name'] for c in inspector.get_columns('sim_trades')]
    for col in cost_cols:
        if col in cols:
            logger.info(f"  ✅ sim_trades.{col} 存在")
        else:
            errors.append(f"sim_trades 缺少 {col}")
    
    # 验证4: 无仓位溢出
    logger.info("验证4: 仓位溢出检查")
    with engine.connect() as conn:
        overflow = conn.execute(text('''
            SELECT id, stock_code, buy_amount, buy_shares, buy_price
            FROM sim_trades
            WHERE buy_amount > 0 AND buy_price > 0 AND buy_shares > 0
              AND buy_shares * buy_price > buy_amount * 1.5
        ''')).fetchall()
        
        if overflow:
            errors.append(f"仓位溢出: {len(overflow)} 条记录")
        else:
            logger.info("  ✅ 无溢出记录")
    
    # 验证5: 交易成本计算模块
    logger.info("验证5: 交易成本计算")
    try:
        from skills.stock_sim_robot.trade_cost import calc_buy_cost, calc_sell_cost
        
        buy = calc_buy_cost(10.0, 1000, '600001')
        assert buy['commission'] == 5.0  # 最低5元
        assert buy['transfer_fee'] == 0.1  # 沪市过户费
        logger.info(f"  ✅ 沪市买入测试通过")
        
        sell = calc_sell_cost(10.0, 1000, '000001')
        assert sell['stamp_tax'] == 5.0  # 印花税
        assert sell['transfer_fee'] == 0.0  # 深市免收
        logger.info(f"  ✅ 深市卖出测试通过")
        
    except Exception as e:
        errors.append(f"交易成本模块异常: {e}")
    
    # 汇总
    logger.info("\n" + "=" * 50)
    if errors:
        logger.error(f"🔴 {len(errors)} 个问题:")
        for e in errors:
            logger.error(f"  ❌ {e}")
    else:
        logger.info("🟢 P0 全部验证通过!")
    
    return len(errors) == 0


if __name__ == '__main__':
    sys.exit(0 if verify() else 1)
```

---

## trade_runner.py 修改要点

### 修改1：删除硬编码常量

```python
# --- 删除 ---
# BUY_AMOUNT = TOTAL_CAPITAL

# --- 替换为 ---
from .position_calc import calculate_buy_shares
from .trade_cost import calc_slippage_price, calc_buy_cost, calc_sell_cost, calc_trade_profit
```

### 修改2：买入逻辑改造

将原来的：
```python
buy_shares = int(BUY_AMOUNT / buy_price / 100) * 100
```

改为使用 `calculate_buy_shares()` 函数，详见完整代码。

---

## 文件清单

```
P0 完整文件清单
├── 新增
│   ├── skills/stock_sim_robot/trade_cost.py      # 交易成本计算
│   ├── skills/stock_sim_robot/position_calc.py   # 仓位计算
│   ├── scripts/migrate_p0.py                     # 数据库迁移
│   └── scripts/verify_p0.py                      # 验证脚本
│
└── 修改
    └── skills/stock_sim_robot/trade_runner.py
        ├── 删除 BUY_AMOUNT = TOTAL_CAPITAL
        ├── 引入 trade_cost + position_calc
        └── 替换买入/卖出逻辑
```

---

## 执行步骤

```bash
# 1. 执行数据库迁移
python scripts/migrate_p0.py

# 2. 验证修复结果
python scripts/verify_p0.py

# 3. 部署到远程服务器
rsync -avz --exclude='__pycache__' ./ jd:~/stock_analysis/
```

---

## 下一步

P0 完成后，继续执行 [P1: 机器人CRUD + 策略注册](./phase-P1-robot-crud.md)
