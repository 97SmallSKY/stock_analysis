# P4 阶段：实时监控 + 盘中止损

> **前置条件**：P1 完成（策略框架）
> **预计时间**：3-5天
> **文档版本**：v1.0

---

## 目标

1. 建立行情快照表 `market_snapshots`
2. 实现实时监控主循环（9:30-15:00）
3. 实现盘中止损/止盈执行
4. 每5分钟存储行情快照

---

## 配置参数

| 参数 | 值 | 说明 |
|------|-----|------|
| 行情轮询间隔 | 10秒 | 触发止损用 |
| 快照存储间隔 | 5分钟 | 复盘用 |
| 监控时段 | 9:36-11:30, 13:00-15:00 | 避开 Phase B |

---

## 数据库设计

### 1. market_snapshots 行情快照表

```sql
CREATE TABLE IF NOT EXISTS market_snapshots (
    id              SERIAL PRIMARY KEY,
    stock_code      VARCHAR(10) NOT NULL,
    stock_name      VARCHAR(20),
    trade_date      VARCHAR(10) NOT NULL,
    snapshot_time   TIMESTAMP NOT NULL,
    
    price           FLOAT,
    open            FLOAT,
    high            FLOAT,
    low             FLOAT,
    pre_close       FLOAT,
    volume          BIGINT,
    amount          FLOAT,
    change_pct      FLOAT,
    
    interval_type   VARCHAR(10) DEFAULT '5min',
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_snapshot_code_date
ON market_snapshots(stock_code, trade_date, snapshot_time);
```

### 2. monitor_events 监控事件日志表

```sql
CREATE TABLE IF NOT EXISTS monitor_events (
    id              SERIAL PRIMARY KEY,
    robot_id        VARCHAR(50) NOT NULL,
    stock_code      VARCHAR(10) NOT NULL,
    stock_name      VARCHAR(20),
    trade_date      VARCHAR(10),
    event_time      TIMESTAMP NOT NULL,
    
    -- 事件类型: STOP_LOSS / STOP_PROFIT / SIGNAL
    event_type      VARCHAR(20) NOT NULL,
    
    trigger_price   FLOAT,
    threshold       FLOAT,
    current_pct     FLOAT,
    
    action_taken    BOOLEAN DEFAULT false,
    trade_id        INTEGER,
    
    memo            TEXT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_monitor_events_robot
ON monitor_events(robot_id, trade_date);
```

---

## 核心模块

### 实时监控模块

**文件路径**：`skills/stock_sim_robot/realtime_monitor.py`

```python
"""
实时监控服务

运行时段：交易日 9:30 - 15:00
轮询频率：每10秒获取实时行情
存储频率：每5分钟写入一条快照
"""
import time
import logging
from datetime import datetime
from utils.storage import storage
from sqlalchemy import text
from .robot_manager import RobotManager
from .cash_manager import CashManager
from .strategy_loader import load_strategy
from .trade_cost import calc_slippage_price, calc_sell_cost
from .trade_runner import execute_sell

logger = logging.getLogger(__name__)

POLL_INTERVAL = 10          # 轮询间隔（秒）
SNAPSHOT_INTERVAL = 300     # 快照间隔（秒）
MARKET_OPEN = (9, 36)
MARKET_CLOSE = (15, 0)
LUNCH_START = (11, 30)
LUNCH_END = (13, 0)


class RealtimeMonitor:
    
    def __init__(self):
        self.engine = storage._get_engine()
        self.rm = RobotManager()
        self._last_snapshot_time = {}
        self._running = False
    
    def start(self):
        """启动监控主循环"""
        self._running = True
        logger.info("🟢 实时监控启动")
        
        while self._running:
            now = datetime.now()
            
            if not self._is_trading_time(now):
                time.sleep(30)
                continue
            
            try:
                self._run_one_cycle(now)
            except Exception as e:
                logger.error(f"监控循环异常: {e}")
            
            time.sleep(POLL_INTERVAL)
        
        logger.info("🔴 实时监控已停止")
    
    def stop(self):
        self._running = False
    
    def _is_trading_time(self, now: datetime) -> bool:
        t = (now.hour, now.minute)
        
        if now.weekday() >= 5:
            return False
        
        if MARKET_OPEN <= t < LUNCH_START:
            return True
        if LUNCH_END <= t < MARKET_CLOSE:
            return True
        
        return False
    
    def _run_one_cycle(self, now: datetime):
        """执行一轮监控"""
        trade_date = now.strftime('%Y%m%d')
        robots = self.rm.list_robots(status='running')
        
        # 收集所有持仓
        holdings_map = {}
        with self.engine.connect() as conn:
            for robot in robots:
                holdings = conn.execute(text('''
                    SELECT id, stock_code, stock_name, buy_price, buy_shares
                    FROM sim_trades WHERE robot_id = :rid AND status = 'HOLDING'
                '''), {'rid': robot['robot_id']}).fetchall()
                
                for h in holdings:
                    h = dict(h._mapping)
                    code = h['stock_code']
                    if code not in holdings_map:
                        holdings_map[code] = []
                    holdings_map[code].append((robot, h))
        
        if not holdings_map:
            return
        
        # 批量获取行情
        from skills.eastmoney_api import EastMoneyAPI
        api = EastMoneyAPI()
        
        for code, robot_holdings in holdings_map.items():
            try:
                quote = api.realtime(code)
                if not quote or not quote.get('price'):
                    continue
                
                # 存储快照
                self._maybe_save_snapshot(code, quote, trade_date, now)
                
                # 检查信号
                for robot, holding in robot_holdings:
                    self._check_signals(robot, holding, quote, trade_date, now)
                    
            except Exception as e:
                logger.warning(f"监控 {code} 异常: {e}")
    
    def _check_signals(self, robot, holding, quote, trade_date, now):
        """检查交易信号"""
        price = quote.get('price', 0)
        buy_price = holding.get('buy_price', 0)
        
        if not price or not buy_price:
            return
        
        profit_pct = (price - buy_price) / buy_price
        
        # 加载策略判断
        strategy = load_strategy(robot['execute_strategy'])
        signal = strategy.should_sell(holding, quote, {'robot': robot})
        
        if signal is None:
            return
        
        # 执行卖出
        logger.warning(f"🚨 {robot['robot_name']} | {holding['stock_code']} | {signal.reason}")
        
        with self.engine.begin() as conn:
            execute_sell(holding, price, trade_date, conn, signal.reason)
            
            # 卖出到账
            sell_price = calc_slippage_price(price, 'SELL')
            sell_cost = calc_sell_cost(sell_price, holding['buy_shares'], holding['stock_code'])
            
            cm = CashManager(robot['robot_id'])
            cm.credit_sell(sell_cost['total_received'], holding['id'],
                          holding['stock_code'], holding['stock_name'], trade_date)
            
            # 记录事件
            conn.execute(text('''
                INSERT INTO monitor_events
                (robot_id, stock_code, trade_date, event_time, event_type,
                 trigger_price, current_pct, action_taken, trade_id, memo)
                VALUES (:rid, :code, :date, :time, :type, :trigger, :pct, true, :tid, :memo)
            '''), {...})
    
    def _maybe_save_snapshot(self, code, quote, trade_date, now):
        """每5分钟存一条快照"""
        last = self._last_snapshot_time.get(code)
        if last and (now - last).total_seconds() < SNAPSHOT_INTERVAL:
            return
        
        with self.engine.begin() as conn:
            conn.execute(text('''
                INSERT INTO market_snapshots
                (stock_code, stock_name, trade_date, snapshot_time, price, open, high, low, pre_close, volume, amount, change_pct)
                VALUES (:code, :name, :date, :time, :price, :open, :high, :low, :pre_close, :volume, :amount, :change_pct)
            '''), {...})
        
        self._last_snapshot_time[code] = now


def start_monitor():
    """启动入口"""
    monitor = RealtimeMonitor()
    try:
        monitor.start()
    except KeyboardInterrupt:
        monitor.stop()
```

---

## 进程管理

**文件路径**：`/etc/supervisor/conf.d/sim_monitor.conf`

```ini
[program:sim_monitor]
command=/root/miniconda3/bin/python -c "from skills.stock_sim_robot.realtime_monitor import start_monitor; start_monitor()"
directory=/root/stock_analysis
autostart=true
autorestart=true
startsecs=10
stdout_logfile=/root/stock_analysis/logs/monitor.log
stderr_logfile=/root/stock_analysis/logs/monitor_err.log
```

---

## 文件清单

```
P4 完整文件清单
├── 新增
│   ├── scripts/migrate_p4.py
│   ├── skills/stock_sim_robot/realtime_monitor.py
│   └── /etc/supervisor/conf.d/sim_monitor.conf
│
└── 配置
    └── supervisor: sim_monitor 进程
```

---

## 下一步

P4 完成后，继续执行 [P5: 淘汰机制 + 分批建仓](./phase-P5-elimination.md)
