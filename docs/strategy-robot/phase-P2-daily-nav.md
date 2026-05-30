# P2 阶段：每日净值 + 净值曲线

> **前置条件**：P1 完成（机器人管理 + 现金流水）
> **预计时间**：2天
> **文档版本**：v1.0

---

## 目标

1. 建立每日净值快照表 `sim_daily_summary`
2. 实现每日结算任务（15:05 执行）
3. 建立基准指数行情表 `benchmark_daily`
4. 实现数据库备份

---

## 数据库设计

### 1. sim_daily_summary 每日快照表

```sql
CREATE TABLE IF NOT EXISTS sim_daily_summary (
    id              SERIAL PRIMARY KEY,
    robot_id        VARCHAR(50) NOT NULL,
    trade_date      VARCHAR(10) NOT NULL,
    
    -- 资产快照
    cash            FLOAT NOT NULL,
    holding_value   FLOAT NOT NULL,
    total_value     FLOAT NOT NULL,
    nav             FLOAT NOT NULL,
    
    -- 当日变动
    daily_return    FLOAT DEFAULT 0,
    daily_return_pct FLOAT DEFAULT 0,
    
    -- 累计指标
    cumulative_return_pct FLOAT DEFAULT 0,
    max_drawdown_pct      FLOAT DEFAULT 0,
    
    -- 持仓明细快照（JSON）
    holdings_snapshot JSONB DEFAULT '[]',
    
    -- 交易成本累计
    total_fees_cumulative FLOAT DEFAULT 0,
    
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    UNIQUE(robot_id, trade_date)
);
```

### 2. benchmark_daily 基准指数行情表

```sql
CREATE TABLE IF NOT EXISTS benchmark_daily (
    id              SERIAL PRIMARY KEY,
    index_code      VARCHAR(20) NOT NULL,
    index_name      VARCHAR(50),
    trade_date      VARCHAR(10) NOT NULL,
    close_price     FLOAT NOT NULL,
    change_pct      FLOAT,
    nav             FLOAT,
    
    UNIQUE(index_code, trade_date)
);
```

---

## 核心模块

### 每日结算模块

**文件路径**：`skills/stock_sim_robot/daily_settlement.py`

```python
"""
每日结算任务（15:05 执行）

职责：
1. 获取所有持仓股票的收盘价
2. 计算每个机器人的持仓市值和净值
3. 检查超期持仓强制卖出
4. 写入 sim_daily_summary
5. 拉取基准指数收盘数据
"""
import logging
from datetime import datetime
from utils.storage import storage
from sqlalchemy import text
from .robot_manager import RobotManager
from .cash_manager import CashManager

logger = logging.getLogger(__name__)


class DailySettlement:
    
    def __init__(self):
        self.engine = storage._get_engine()
        self.rm = RobotManager()
    
    def run(self, trade_date: str = None):
        if trade_date is None:
            trade_date = datetime.now().strftime('%Y%m%d')
        
        logger.info(f"=== 每日结算开始: {trade_date} ===")
        
        robots = self.rm.list_robots(status='running')
        close_prices = self._fetch_close_prices(robots)
        
        for robot in robots:
            try:
                self._settle_one_robot(robot, trade_date, close_prices)
            except Exception as e:
                logger.error(f"❌ {robot['robot_id']} 结算异常: {e}")
        
        self._fetch_benchmarks(trade_date)
        
        logger.info(f"=== 每日结算完成 ===")
    
    def _fetch_close_prices(self, robots: list) -> dict:
        """批量获取收盘价"""
        from skills.eastmoney_api import EastMoneyAPI
        api = EastMoneyAPI()
        
        all_codes = set()
        with self.engine.connect() as conn:
            for robot in robots:
                holdings = conn.execute(text('''
                    SELECT stock_code FROM sim_trades
                    WHERE robot_id = :rid AND status = 'HOLDING'
                '''), {'rid': robot['robot_id']}).fetchall()
                for h in holdings:
                    all_codes.add(h[0])
        
        prices = {}
        for code in all_codes:
            try:
                quote = api.realtime(code)
                prices[code] = {
                    'price': quote.get('price', 0),
                    'pre_close': quote.get('pre_close', 0),
                }
            except Exception as e:
                logger.warning(f"获取 {code} 行情失败: {e}")
        
        return prices
    
    def _settle_one_robot(self, robot: dict, trade_date: str, close_prices: dict):
        """结算单个机器人"""
        robot_id = robot['robot_id']
        cm = CashManager(robot_id)
        cash = cm.get_balance()
        
        with self.engine.begin() as conn:
            holdings = conn.execute(text('''
                SELECT id, stock_code, stock_name, buy_price, buy_shares, trade_date
                FROM sim_trades WHERE robot_id = :rid AND status = 'HOLDING'
            '''), {'rid': robot_id}).fetchall()
            
            holding_value = 0.0
            holdings_snapshot = []
            
            for h in holdings:
                h = dict(h._mapping)
                code = h['stock_code']
                current_price = close_prices.get(code, {}).get('price') or h['buy_price']
                
                market_value = current_price * h['buy_shares']
                holding_value += market_value
                
                profit_pct = (current_price - h['buy_price']) / h['buy_price'] * 100
                holdings_snapshot.append({
                    'code': code, 'name': h['stock_name'],
                    'shares': h['buy_shares'], 'buy_price': h['buy_price'],
                    'current_price': current_price, 'market_value': round(market_value, 2),
                    'profit_pct': round(profit_pct, 2),
                })
            
            total_value = cash + holding_value
            nav = total_value / robot['initial_capital']
            
            # 写入快照
            conn.execute(text('''
                INSERT INTO sim_daily_summary
                (robot_id, trade_date, cash, holding_value, total_value, nav, holdings_snapshot)
                VALUES (:rid, :date, :cash, :hv, :tv, :nav, :snap::jsonb)
                ON CONFLICT (robot_id, trade_date) DO UPDATE SET
                    cash = EXCLUDED.cash, holding_value = EXCLUDED.holding_value,
                    total_value = EXCLUDED.total_value, nav = EXCLUDED.nav
            '''), {...})
        
        logger.info(f"📊 {robot['robot_name']}: 净值={nav:.4f}, 总值=¥{total_value:.0f}")
    
    def _fetch_benchmarks(self, trade_date: str):
        """拉取基准指数数据"""
        # 沪深300, 中证500, 上证指数, 创业板指
        pass
```

---

## 备份脚本

**文件路径**：`scripts/backup_db.sh`

```bash
#!/bin/bash
# PostgreSQL 定期备份脚本
# crontab: 0 2 * * * /root/stock_analysis/scripts/backup_db.sh

BACKUP_DIR="/root/stock_analysis/backups"
DB_NAME="stock_analysis"
DB_USER="stock_user"
DATE=$(date +%Y%m%d_%H%M%S)
KEEP_DAYS=30

mkdir -p $BACKUP_DIR

BACKUP_FILE="$BACKUP_DIR/${DB_NAME}_${DATE}.sql.gz"
pg_dump -U $DB_USER $DB_NAME | gzip > $BACKUP_FILE

if [ $? -eq 0 ]; then
    SIZE=$(du -h $BACKUP_FILE | cut -f1)
    echo "$(date) ✅ 备份成功: $BACKUP_FILE ($SIZE)"
else
    echo "$(date) ❌ 备份失败"
    exit 1
fi

# 清理旧备份
find $BACKUP_DIR -name "*.sql.gz" -mtime +$KEEP_DAYS -delete
```

---

## Crontab 配置

```bash
# 每日结算（15:05）
5  15 * * 1-5  cd ~/stock_analysis && /root/miniconda3/bin/python -c "from skills.stock_sim_robot.daily_settlement import DailySettlement; DailySettlement().run()"

# 数据库备份（02:00）
0  2  * * *    /root/stock_analysis/scripts/backup_db.sh
```

---

## 文件清单

```
P2 完整文件清单
├── 新增
│   ├── scripts/migrate_p2.py
│   ├── skills/stock_sim_robot/daily_settlement.py
│   └── scripts/backup_db.sh
│
└── 配置
    └── crontab 新增结算和备份任务
```

---

## 下一步

P2 完成后，继续执行 [P3: 对比看板 + 评分体系](./phase-P3-comparison-dashboard.md)
