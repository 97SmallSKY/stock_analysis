# P1 阶段：机器人CRUD + 策略注册

> **前置条件**：P0 完成（robot_id 字段 + 交易成本体系）
> **预计时间**：2-3天
> **文档版本**：v1.0

---

## 目标

1. 建立机器人管理表 `sim_robots`
2. 实现机器人 CRUD 接口
3. 建立现金流水表 `cash_ledger`
4. 实现策略可插拔框架

---

## 数据库设计

### 1. sim_robots 机器人管理表

```sql
CREATE TABLE IF NOT EXISTS sim_robots (
    id                SERIAL PRIMARY KEY,
    robot_id          VARCHAR(50) UNIQUE NOT NULL,
    robot_name        VARCHAR(100) NOT NULL,
    
    -- 状态: draft/running/paused/ended/eliminated
    status            VARCHAR(20) DEFAULT 'draft',
    
    -- 来源配置
    stock_source      VARCHAR(50) DEFAULT 'longhubang',
    select_strategy   VARCHAR(50) DEFAULT 'lianban_multi_factor',
    execute_strategy  VARCHAR(50) DEFAULT 'lianban_multi_factor',
    
    -- 资金配置
    initial_capital   FLOAT DEFAULT 100000,
    position_ratio    FLOAT DEFAULT 0.1,
    max_positions     INTEGER DEFAULT 3,
    
    -- 风控配置
    max_holding_days  INTEGER DEFAULT 7,
    stop_loss_pct     FLOAT DEFAULT -0.5,
    stop_profit_pct   FLOAT DEFAULT NULL,
    enable_batch_buy  BOOLEAN DEFAULT false,
    
    -- 交易成本配置（可自定义覆盖默认值）
    cost_config       JSONB DEFAULT '{}',
    
    -- 策略参数
    strategy_params   JSONB DEFAULT '{}',
    
    -- 继承关系
    parent_robot_id   VARCHAR(50) DEFAULT NULL,
    
    -- 淘汰相关
    eliminate_reason  TEXT,
    final_score       FLOAT,
    
    -- 时间
    created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    started_at        TIMESTAMP,
    ended_at          TIMESTAMP,
    updated_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### 2. cash_ledger 现金流水表

```sql
CREATE TABLE IF NOT EXISTS cash_ledger (
    id              SERIAL PRIMARY KEY,
    robot_id        VARCHAR(50) NOT NULL,
    trade_date      VARCHAR(10),
    
    -- 流水类型: INIT/BUY/SELL/DIVIDEND/FEE/TRANSFER_IN/TRANSFER_OUT
    tx_type         VARCHAR(20) NOT NULL,
    
    -- 金额（正数=入账，负数=出账）
    amount          FLOAT NOT NULL,
    
    -- 关联信息
    trade_id        INTEGER,
    stock_code      VARCHAR(10),
    stock_name      VARCHAR(20),
    
    -- 余额快照（流水后的余额）
    balance_after   FLOAT NOT NULL,
    
    -- 说明
    memo            TEXT,
    
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_cash_ledger_robot 
ON cash_ledger(robot_id, created_at);
```

### 3. strategy_registry 策略注册表

```sql
CREATE TABLE IF NOT EXISTS strategy_registry (
    id              SERIAL PRIMARY KEY,
    strategy_id     VARCHAR(50) UNIQUE NOT NULL,
    strategy_name   VARCHAR(100) NOT NULL,
    
    -- 策略类型: select/execute/both
    strategy_type   VARCHAR(20) DEFAULT 'both',
    
    -- Python 模块路径
    module_path     VARCHAR(200) NOT NULL,
    
    -- 策略说明
    description     TEXT,
    
    -- 默认参数
    default_params  JSONB DEFAULT '{}',
    
    -- 是否支持分批建仓
    support_batch   BOOLEAN DEFAULT false,
    
    is_active       BOOLEAN DEFAULT true,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

---

## 核心模块

### 1. 现金管理模块

**文件路径**：`skills/stock_sim_robot/cash_manager.py`

```python
"""
现金余额管理

职责：
- 查询机器人当前余额
- 买入扣款
- 卖出到账
- 分红入账
- 机器人继承时资金转移
"""
import logging
from utils.storage import storage
from sqlalchemy import text

logger = logging.getLogger(__name__)


class CashManager:
    
    def __init__(self, robot_id: str):
        self.robot_id = robot_id
        self.engine = storage._get_engine()
    
    def get_balance(self) -> float:
        """查询当前余额"""
        with self.engine.connect() as conn:
            result = conn.execute(text('''
                SELECT balance_after FROM cash_ledger
                WHERE robot_id = :rid
                ORDER BY id DESC LIMIT 1
            '''), {'rid': self.robot_id}).fetchone()
            
            return float(result[0]) if result else 0.0
    
    def init_capital(self, amount: float, trade_date: str = None):
        """初始化资金"""
        with self.engine.begin() as conn:
            conn.execute(text('''
                INSERT INTO cash_ledger
                (robot_id, tx_type, amount, balance_after, trade_date, memo)
                VALUES (:rid, 'INIT', :amount, :amount, :date, '初始资金')
            '''), {
                'rid': self.robot_id,
                'amount': amount,
                'date': trade_date,
            })
        logger.info(f"💰 {self.robot_id} 初始资金: ¥{amount:.2f}")
    
    def deduct_buy(
        self, total_paid: float, trade_id: int,
        stock_code: str, stock_name: str, trade_date: str,
        detail: str = ''
    ) -> bool:
        """买入扣款（返回是否成功）"""
        with self.engine.begin() as conn:
            balance = self._get_balance_in_tx(conn)
            
            if balance < total_paid:
                logger.warning(f"❌ {self.robot_id} 余额不足: 需要 ¥{total_paid:.2f}, 可用 ¥{balance:.2f}")
                return False
            
            new_balance = balance - total_paid
            
            conn.execute(text('''
                INSERT INTO cash_ledger
                (robot_id, tx_type, amount, balance_after,
                 trade_id, stock_code, stock_name, trade_date, memo)
                VALUES (:rid, 'BUY', :amount, :balance,
                        :tid, :code, :name, :date, :memo)
            '''), {
                'rid': self.robot_id,
                'amount': -total_paid,
                'balance': new_balance,
                'tid': trade_id,
                'code': stock_code,
                'name': stock_name,
                'date': trade_date,
                'memo': detail,
            })
        
        logger.info(f"💸 {self.robot_id} 买入扣款: -¥{total_paid:.2f}, 余额 ¥{new_balance:.2f}")
        return True
    
    def credit_sell(
        self, total_received: float, trade_id: int,
        stock_code: str, stock_name: str, trade_date: str,
        detail: str = ''
    ):
        """卖出到账"""
        with self.engine.begin() as conn:
            balance = self._get_balance_in_tx(conn)
            new_balance = balance + total_received
            
            conn.execute(text('''
                INSERT INTO cash_ledger
                (robot_id, tx_type, amount, balance_after,
                 trade_id, stock_code, stock_name, trade_date, memo)
                VALUES (:rid, 'SELL', :amount, :balance,
                        :tid, :code, :name, :date, :memo)
            '''), {
                'rid': self.robot_id,
                'amount': total_received,
                'balance': new_balance,
                'tid': trade_id,
                'code': stock_code,
                'name': stock_name,
                'date': trade_date,
                'memo': detail,
            })
        
        logger.info(f"💰 {self.robot_id} 卖出到账: +¥{total_received:.2f}, 余额 ¥{new_balance:.2f}")
    
    def credit_dividend(self, amount: float, stock_code: str, stock_name: str, trade_date: str):
        """分红到账"""
        with self.engine.begin() as conn:
            balance = self._get_balance_in_tx(conn)
            new_balance = balance + amount
            
            conn.execute(text('''
                INSERT INTO cash_ledger
                (robot_id, tx_type, amount, balance_after,
                 stock_code, stock_name, trade_date, memo)
                VALUES (:rid, 'DIVIDEND', :amount, :balance,
                        :code, :name, :date, :memo)
            '''), {
                'rid': self.robot_id,
                'amount': amount,
                'balance': new_balance,
                'code': stock_code,
                'name': stock_name,
                'date': trade_date,
                'memo': f'分红到账 {stock_name}',
            })
    
    def transfer(self, target_robot_id: str, amount: float, trade_date: str):
        """资金转移（机器人继承）"""
        with self.engine.begin() as conn:
            # 转出
            balance = self._get_balance_in_tx(conn)
            if balance < amount:
                amount = balance
            
            new_balance = balance - amount
            conn.execute(text('''
                INSERT INTO cash_ledger
                (robot_id, tx_type, amount, balance_after, trade_date, memo)
                VALUES (:rid, 'TRANSFER_OUT', :amount, :balance, :date, :memo)
            '''), {
                'rid': self.robot_id,
                'amount': -amount,
                'balance': new_balance,
                'date': trade_date,
                'memo': f'转出至 {target_robot_id}',
            })
            
            # 转入
            target_balance = self._get_balance_for_robot(conn, target_robot_id)
            target_new_balance = target_balance + amount
            conn.execute(text('''
                INSERT INTO cash_ledger
                (robot_id, tx_type, amount, balance_after, trade_date, memo)
                VALUES (:rid, 'TRANSFER_IN', :amount, :balance, :date, :memo)
            '''), {
                'rid': target_robot_id,
                'amount': amount,
                'balance': target_new_balance,
                'date': trade_date,
                'memo': f'继承自 {self.robot_id}',
            })
        
        logger.info(f"🔄 资金转移: {self.robot_id}(¥{new_balance:.2f}) → {target_robot_id}(¥{target_new_balance:.2f})")
    
    def _get_balance_in_tx(self, conn) -> float:
        """事务内查询余额"""
        result = conn.execute(text('''
            SELECT balance_after FROM cash_ledger
            WHERE robot_id = :rid
            ORDER BY id DESC LIMIT 1 FOR UPDATE
        '''), {'rid': self.robot_id}).fetchone()
        
        return float(result[0]) if result else 0.0
    
    def _get_balance_for_robot(self, conn, robot_id: str) -> float:
        result = conn.execute(text('''
            SELECT balance_after FROM cash_ledger
            WHERE robot_id = :rid ORDER BY id DESC LIMIT 1
        '''), {'rid': robot_id}).fetchone()
        
        return float(result[0]) if result else 0.0
```

### 2. 策略基类

**文件路径**：`skills/stock_sim_robot/strategy_base.py`

```python
"""
策略基类 - 所有策略必须继承此类

新增策略步骤：
1. 在 modules/ 下创建 strategy_xxx.py
2. 继承 BaseStrategy 实现所有抽象方法
3. 在 strategy_registry 表中注册
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


@dataclass
class SelectionSignal:
    """选股信号"""
    stock_code: str
    stock_name: str
    score: float
    buy_amount: float
    reason: str
    extra: dict = None


@dataclass
class TradeSignal:
    """交易信号"""
    stock_code: str
    direction: str           # BUY / SELL
    price: float
    shares: int
    reason: str
    urgency: str = 'normal'  # normal / urgent
    batch_index: int = 0
    batch_total: int = 1


class BaseStrategy(ABC):
    """策略基类"""
    
    def __init__(self, params: dict = None):
        self.params = params or {}
    
    @abstractmethod
    def get_name(self) -> str:
        """策略名称"""
        pass
    
    @abstractmethod
    def get_id(self) -> str:
        """策略ID"""
        pass
    
    @abstractmethod
    def select_stocks(self, trade_date: str, context: dict) -> list[SelectionSignal]:
        """选股（Phase A 调用）"""
        pass
    
    @abstractmethod
    def should_buy(self, signal: SelectionSignal, realtime: dict, context: dict) -> Optional[TradeSignal]:
        """买入判断（Phase B / 实时监控调用）"""
        pass
    
    @abstractmethod
    def should_sell(self, position: dict, realtime: dict, context: dict) -> Optional[TradeSignal]:
        """卖出判断（实时监控调用）"""
        pass
    
    def on_batch_buy(self, position: dict, realtime: dict, context: dict) -> Optional[TradeSignal]:
        """分批建仓判断（默认不支持）"""
        return None
```

### 3. 策略加载器

**文件路径**：`skills/stock_sim_robot/strategy_loader.py`

```python
"""策略加载器 - 根据 strategy_id 动态加载策略类"""
import importlib
import logging
from utils.storage import storage
from sqlalchemy import text
from .strategy_base import BaseStrategy

logger = logging.getLogger(__name__)

_strategy_cache: dict[str, BaseStrategy] = {}


def load_strategy(strategy_id: str, params: dict = None) -> BaseStrategy:
    """
    根据 strategy_id 加载策略实例
    
    流程：
    1. 查 strategy_registry 表获取 module_path
    2. 动态 import 模块
    3. 调用 create_strategy(params) 工厂函数
    """
    cache_key = f"{strategy_id}:{hash(str(params))}"
    if cache_key in _strategy_cache:
        return _strategy_cache[cache_key]
    
    engine = storage._get_engine()
    with engine.connect() as conn:
        row = conn.execute(text('''
            SELECT module_path, default_params
            FROM strategy_registry
            WHERE strategy_id = :sid AND is_active = true
        '''), {'sid': strategy_id}).fetchone()
    
    if not row:
        raise ValueError(f"策略 {strategy_id} 未注册或已禁用")
    
    row = dict(row._mapping)
    module_path = row['module_path']
    default_params = row.get('default_params') or {}
    
    # 合并参数
    merged_params = {**default_params, **(params or {})}
    
    try:
        module = importlib.import_module(module_path)
        strategy = module.create_strategy(merged_params)
        
        if not isinstance(strategy, BaseStrategy):
            raise TypeError(f"{module_path}.create_strategy() 返回类型不是 BaseStrategy")
        
        _strategy_cache[cache_key] = strategy
        logger.info(f"✅ 加载策略: {strategy.get_name()} ({strategy_id})")
        return strategy
        
    except Exception as e:
        logger.error(f"❌ 加载策略 {strategy_id} 失败: {e}")
        raise


def list_strategies() -> list[dict]:
    """列出所有已注册策略"""
    engine = storage._get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text('''
            SELECT strategy_id, strategy_name, strategy_type,
                   description, support_batch, is_active
            FROM strategy_registry
            ORDER BY id
        ''')).fetchall()
    
    return [dict(r._mapping) for r in rows]
```

### 4. 龙虎榜策略实现

**文件路径**：`skills/stock_sim_robot/modules/strategy_lianban.py`

```python
"""
龙虎榜多因子策略

将现有的选股/买卖逻辑包装为 BaseStrategy 接口
"""
import logging
from ..strategy_base import BaseStrategy, SelectionSignal, TradeSignal
from typing import Optional

logger = logging.getLogger(__name__)


class LianbanMultiFactorStrategy(BaseStrategy):
    """龙虎榜多因子策略"""
    
    def get_name(self) -> str:
        return "龙虎榜多因子策略"
    
    def get_id(self) -> str:
        return "lianban_multi_factor"
    
    def select_stocks(self, trade_date: str, context: dict) -> list[SelectionSignal]:
        """选股逻辑（复用现有 Phase A 代码）"""
        robot = context['robot']
        cash = context['cash']
        holdings = context.get('holdings', [])
        max_positions = robot.get('max_positions', 3)
        
        available_slots = max_positions - len(holdings)
        if available_slots <= 0:
            return []
        
        position_ratio = robot.get('position_ratio', 0.1)
        buy_amount = robot['initial_capital'] * position_ratio
        
        # 调用现有选股逻辑
        try:
            from ..llm_select import run_selection
            candidates = run_selection(trade_date, self.params)
        except ImportError:
            logger.warning("llm_select 模块不可用")
            return []
        
        signals = []
        for c in candidates[:available_slots]:
            signals.append(SelectionSignal(
                stock_code=c['code'],
                stock_name=c['name'],
                score=c.get('score', 0),
                buy_amount=min(buy_amount, cash),
                reason=c.get('reason', ''),
                extra={'lianban_score': c.get('lianban_score', 0)},
            ))
        
        return signals
    
    def should_buy(self, signal: SelectionSignal, realtime: dict, context: dict) -> Optional[TradeSignal]:
        """买入判断 - 默认 T+1 开盘直接买入"""
        price = realtime.get('price') or realtime.get('open')
        if not price or price <= 0:
            return None
        
        # 涨停不买
        pre_close = realtime.get('pre_close', 0)
        if pre_close > 0:
            change_pct = (price - pre_close) / pre_close * 100
            if change_pct >= 9.8:
                return None
        
        return TradeSignal(
            stock_code=signal.stock_code,
            direction='BUY',
            price=price,
            shares=0,
            reason=f"T+1买入，评分{signal.score:.1f}",
        )
    
    def should_sell(self, position: dict, realtime: dict, context: dict) -> Optional[TradeSignal]:
        """卖出判断 - 止损/止盈/到期"""
        robot = context['robot']
        price = realtime.get('price', 0)
        buy_price = position.get('buy_price', 0)
        
        if not price or not buy_price:
            return None
        
        profit_pct = (price - buy_price) / buy_price
        
        # 止损检查
        stop_loss = robot.get('stop_loss_pct', -0.5)
        if profit_pct <= stop_loss:
            return TradeSignal(
                stock_code=position['stock_code'],
                direction='SELL',
                price=price,
                shares=0,
                reason=f"止损触发: {profit_pct:.1%} <= {stop_loss:.1%}",
                urgency='urgent',
            )
        
        # 到期检查
        from datetime import datetime
        buy_date = position.get('trade_date', '')
        if buy_date:
            try:
                days_held = (datetime.now() - datetime.strptime(buy_date, '%Y%m%d')).days
                max_days = robot.get('max_holding_days', 7)
                if days_held >= max_days:
                    return TradeSignal(
                        stock_code=position['stock_code'],
                        direction='SELL',
                        price=price,
                        shares=0,
                        reason=f"持仓到期: {days_held}天",
                    )
            except ValueError:
                pass
        
        # 止盈检查
        stop_profit = robot.get('stop_profit_pct')
        if stop_profit and profit_pct >= stop_profit:
            return TradeSignal(
                stock_code=position['stock_code'],
                direction='SELL',
                price=price,
                shares=0,
                reason=f"止盈触发: {profit_pct:.1%}",
            )
        
        return None


def create_strategy(params: dict) -> BaseStrategy:
    """工厂函数"""
    return LianbanMultiFactorStrategy(params)
```

### 5. 机器人管理模块

**文件路径**：`skills/stock_sim_robot/robot_manager.py`

```python
"""
机器人 CRUD 管理

职责：创建、启动、暂停、结束、继承、淘汰
"""
import logging
import uuid
from datetime import datetime
from utils.storage import storage
from sqlalchemy import text
from .cash_manager import CashManager
from .strategy_loader import load_strategy

logger = logging.getLogger(__name__)


class RobotManager:
    
    def __init__(self):
        self.engine = storage._get_engine()
    
    def create_robot(self, config: dict) -> str:
        """创建新机器人"""
        robot_id = f"robot_{datetime.now().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:6]}"
        
        # 验证策略存在
        load_strategy(config.get('select_strategy', 'lianban_multi_factor'))
        
        with self.engine.begin() as conn:
            conn.execute(text('''
                INSERT INTO sim_robots 
                (robot_id, robot_name, status, stock_source,
                 select_strategy, execute_strategy,
                 initial_capital, position_ratio, max_positions,
                 max_holding_days, stop_loss_pct, stop_profit_pct,
                 enable_batch_buy, strategy_params, cost_config)
                VALUES 
                (:rid, :name, 'draft', :source,
                 :sel_strategy, :exe_strategy,
                 :capital, :ratio, :max_pos,
                 :max_days, :stop_loss, :stop_profit,
                 :batch, :params::jsonb, :cost::jsonb)
            '''), {
                'rid': robot_id,
                'name': config.get('robot_name', f'机器人-{robot_id[:8]}'),
                'source': config.get('stock_source', 'longhubang'),
                'sel_strategy': config.get('select_strategy', 'lianban_multi_factor'),
                'exe_strategy': config.get('execute_strategy', 'lianban_multi_factor'),
                'capital': config.get('initial_capital', 100000),
                'ratio': config.get('position_ratio', 0.1),
                'max_pos': config.get('max_positions', 3),
                'max_days': config.get('max_holding_days', 7),
                'stop_loss': config.get('stop_loss_pct', -0.5),
                'stop_profit': config.get('stop_profit_pct'),
                'batch': config.get('enable_batch_buy', False),
                'params': str(config.get('strategy_params', {})).replace("'", '"'),
                'cost': str(config.get('cost_config', {})).replace("'", '"'),
            })
        
        logger.info(f"🤖 机器人已创建: {robot_id}")
        return robot_id
    
    def start_robot(self, robot_id: str, trade_date: str = None):
        """启动机器人（draft → running）"""
        if trade_date is None:
            trade_date = datetime.now().strftime('%Y%m%d')
        
        with self.engine.begin() as conn:
            robot = self._get_robot(conn, robot_id)
            
            if robot['status'] not in ('draft', 'paused'):
                raise ValueError(f"机器人状态为 {robot['status']}，无法启动")
            
            conn.execute(text('''
                UPDATE sim_robots 
                SET status = 'running', started_at = NOW()
                WHERE robot_id = :rid
            '''), {'rid': robot_id})
        
        # 初始化现金
        cm = CashManager(robot_id)
        if cm.get_balance() == 0:
            cm.init_capital(robot['initial_capital'], trade_date)
        
        logger.info(f"🟢 机器人已启动: {robot_id}")
    
    def pause_robot(self, robot_id: str):
        """暂停机器人"""
        with self.engine.begin() as conn:
            conn.execute(text('''
                UPDATE sim_robots SET status = 'paused' WHERE robot_id = :rid
            '''), {'rid': robot_id})
        logger.info(f"⏸️ 机器人已暂停: {robot_id}")
    
    def end_robot(self, robot_id: str, trade_date: str = None):
        """结束机器人（强制清仓）"""
        # 实现清仓逻辑...
        logger.info(f"🔴 机器人已结束: {robot_id}")
    
    def fork_robot(self, source_robot_id: str, new_config: dict) -> str:
        """创建继承机器人"""
        # 实现继承逻辑...
        pass
    
    def list_robots(self, status: str = None) -> list[dict]:
        """列出机器人"""
        with self.engine.connect() as conn:
            if status:
                rows = conn.execute(text('''
                    SELECT r.*,
                           (SELECT COUNT(*) FROM sim_trades WHERE robot_id = r.robot_id AND status = 'HOLDING') as holding_count,
                           (SELECT balance_after FROM cash_ledger WHERE robot_id = r.robot_id ORDER BY id DESC LIMIT 1) as cash_balance
                    FROM sim_robots r WHERE r.status = :status
                    ORDER BY r.created_at DESC
                '''), {'status': status}).fetchall()
            else:
                rows = conn.execute(text('''
                    SELECT r.*,
                           (SELECT COUNT(*) FROM sim_trades WHERE robot_id = r.robot_id AND status = 'HOLDING') as holding_count,
                           (SELECT balance_after FROM cash_ledger WHERE robot_id = r.robot_id ORDER BY id DESC LIMIT 1) as cash_balance
                    FROM sim_robots r ORDER BY r.created_at DESC
                ''')).fetchall()
        
        return [dict(r._mapping) for r in rows]
    
    def _get_robot(self, conn, robot_id: str) -> dict:
        row = conn.execute(text(
            'SELECT * FROM sim_robots WHERE robot_id = :rid'
        ), {'rid': robot_id}).fetchone()
        
        if not row:
            raise ValueError(f"机器人 {robot_id} 不存在")
        
        return dict(row._mapping)
```

---

## 迁移脚本

**文件路径**：`scripts/migrate_p1.py`

```python
"""P1: 机器人管理 + 现金流水 + 策略注册"""
import logging
from utils.storage import storage
from sqlalchemy import text

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
logger = logging.getLogger(__name__)


def run():
    engine = storage._get_engine()
    
    with engine.begin() as conn:
        # 创建表
        logger.info("=== 创建 sim_robots ===")
        conn.execute(text('''...'''))  # 完整 SQL 见上文
        
        logger.info("=== 创建 cash_ledger ===")
        conn.execute(text('''...'''))
        
        logger.info("=== 创建 strategy_registry ===")
        conn.execute(text('''...'''))
        
        # 预置默认策略
        conn.execute(text('''
            INSERT INTO strategy_registry 
            (strategy_id, strategy_name, module_path, description)
            VALUES 
            ('lianban_multi_factor', '龙虎榜多因子策略',
             'skills.stock_sim_robot.modules.strategy_lianban',
             '基于龙虎榜数据，多因子评分选股，T+1买入')
            ON CONFLICT (strategy_id) DO NOTHING
        '''))
        
        # 创建默认机器人
        conn.execute(text('''
            INSERT INTO sim_robots 
            (robot_id, robot_name, status, initial_capital)
            VALUES ('default', '默认机器人（旧数据）', 'running', 100000)
            ON CONFLICT (robot_id) DO NOTHING
        '''))
        
        # 补录现金流水
        # ... 详细实现见完整方案
    
    logger.info("🎉 P1 迁移完成")


if __name__ == '__main__':
    run()
```

---

## trade_runner.py 改造要点

将 Phase A/B 改为遍历所有运行中机器人：

```python
def run_phase_a(trade_date: str):
    rm = RobotManager()
    robots = rm.list_robots(status='running')
    
    for robot in robots:
        strategy = load_strategy(robot['select_strategy'])
        context = {'robot': robot, 'cash': CashManager(robot['robot_id']).get_balance()}
        signals = strategy.select_stocks(trade_date, context)
        # 写入 sim_trades...
```

---

## 文件清单

```
P1 完整文件清单
├── 新增
│   ├── scripts/migrate_p1.py
│   ├── skills/stock_sim_robot/cash_manager.py
│   ├── skills/stock_sim_robot/robot_manager.py
│   ├── skills/stock_sim_robot/strategy_base.py
│   ├── skills/stock_sim_robot/strategy_loader.py
│   └── skills/stock_sim_robot/modules/strategy_lianban.py
│
└── 修改
    └── skills/stock_sim_robot/trade_runner.py
        ├── run_phase_a() 遍历所有机器人
        └── run_phase_b() 遍历 + 现金扣款
```

---

## 执行步骤

```bash
# 1. 执行迁移
python scripts/migrate_p1.py

# 2. 验证
python -c "
from skills.stock_sim_robot.robot_manager import RobotManager
print(RobotManager().list_robots())
"

# 3. 部署
rsync -avz ./ jd:~/stock_analysis/
```

---

## 下一步

P1 完成后，继续执行 [P2: 每日净值 + 净值曲线](./phase-P2-daily-nav.md)
