# P5 阶段：淘汰机制 + 分批建仓 + 数据清理

> **前置条件**：P2-P3 完成（净值数据 + 评分体系）
> **预计时间**：3-5天
> **文档版本**：v1.0

---

## 目标

1. 实现机器人淘汰引擎
2. 实现分批建仓逻辑
3. 处理除权分红
4. 历史数据清理策略

---

## 数据库设计

### 1. elimination_records 淘汰记录表

```sql
CREATE TABLE IF NOT EXISTS elimination_records (
    id                  SERIAL PRIMARY KEY,
    robot_id            VARCHAR(50) NOT NULL,
    robot_name          VARCHAR(100),
    eliminate_date      VARCHAR(10) NOT NULL,
    
    final_score         FLOAT,
    final_metrics       JSONB,
    peer_snapshot       JSONB NOT NULL,
    
    reason              TEXT,
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### 2. elimination_schedule 淘汰计划表

```sql
CREATE TABLE IF NOT EXISTS elimination_schedule (
    id              SERIAL PRIMARY KEY,
    schedule_type   VARCHAR(20) NOT NULL,  -- FIXED_PERIOD
    period_days     INTEGER DEFAULT 20,
    eliminate_count INTEGER DEFAULT 1,
    min_robots      INTEGER DEFAULT 3,
    weight_config   VARCHAR(50) DEFAULT 'default',
    is_active       BOOLEAN DEFAULT true,
    last_run_date   VARCHAR(10),
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### 3. dividend_records 分红记录表

```sql
CREATE TABLE IF NOT EXISTS dividend_records (
    id              SERIAL PRIMARY KEY,
    stock_code      VARCHAR(10) NOT NULL,
    stock_name      VARCHAR(20),
    ex_date         VARCHAR(10) NOT NULL,
    
    dividend_per_share  FLOAT NOT NULL,
    bonus_ratio     FLOAT DEFAULT 0,
    split_ratio     FLOAT DEFAULT 0,
    
    source          VARCHAR(50),
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    UNIQUE(stock_code, ex_date)
);
```

---

## 核心模块

### 1. 淘汰引擎

**文件路径**：`skills/stock_sim_robot/eliminator.py`

```python
"""
机器人淘汰引擎

流程：
1. 检查是否到达淘汰日
2. 计算所有运行中机器人评分
3. 淘汰得分最低的N个
4. 强制清仓 + 状态改为 eliminated
5. 记录同期所有机器人快照
"""
import json
import logging
from datetime import datetime
from utils.storage import storage
from sqlalchemy import text
from .robot_manager import RobotManager
from .scorer import rank_robots

logger = logging.getLogger(__name__)


class Eliminator:
    
    def __init__(self):
        self.engine = storage._get_engine()
        self.rm = RobotManager()
    
    def check_and_run(self, trade_date: str = None):
        """检查并执行淘汰"""
        if trade_date is None:
            trade_date = datetime.now().strftime('%Y%m%d')
        
        with self.engine.connect() as conn:
            schedules = conn.execute(text('''
                SELECT * FROM elimination_schedule WHERE is_active = true
            ''')).fetchall()
        
        for schedule in schedules:
            schedule = dict(schedule._mapping)
            
            if self._should_run(schedule, trade_date):
                self._execute_elimination(schedule, trade_date)
    
    def _should_run(self, schedule: dict, trade_date: str) -> bool:
        last_run = schedule.get('last_run_date')
        if not last_run:
            return True
        
        with self.engine.connect() as conn:
            days = conn.execute(text('''
                SELECT COUNT(DISTINCT trade_date) FROM sim_daily_summary
                WHERE trade_date > :last AND trade_date <= :current
            '''), {'last': last_run, 'current': trade_date}).scalar()
        
        return days >= schedule['period_days']
    
    def _execute_elimination(self, schedule: dict, trade_date: str):
        """执行淘汰"""
        logger.info(f"=== 淘汰检查开始: {trade_date} ===")
        
        running = self.rm.list_robots(status='running')
        running_ids = [r['robot_id'] for r in running]
        
        if len(running_ids) <= schedule['min_robots']:
            logger.info(f"运行中机器人 {len(running_ids)} 个，不淘汰")
            return
        
        # 排名
        rankings = rank_robots(running_ids)
        
        if not rankings:
            logger.warning("无法计算排名")
            return
        
        # 记录快照
        peer_snapshot = [{
            'robot_id': r['robot_id'],
            'rank': r['rank'],
            'total_score': r['total_score'],
            'metrics': r['metrics'],
        } for r in rankings]
        
        # 淘汰最低分
        eliminate_count = min(schedule['eliminate_count'], 
                            len(rankings) - schedule['min_robots'])
        to_eliminate = rankings[-eliminate_count:]
        
        for victim in to_eliminate:
            victim_id = victim['robot_id']
            victim_info = next((r for r in running if r['robot_id'] == victim_id), {})
            
            logger.warning(f"🗑️ 淘汰: {victim_info.get('robot_name')} ({victim_id})")
            
            # 强制清仓
            self.rm.end_robot(victim_id, trade_date)
            
            # 更新状态
            with self.engine.begin() as conn:
                conn.execute(text('''
                    UPDATE sim_robots SET
                        status = 'eliminated',
                        eliminate_reason = :reason,
                        final_score = :score
                    WHERE robot_id = :rid
                '''), {
                    'rid': victim_id,
                    'reason': f"排名{victim['rank']}/{len(rankings)}",
                    'score': victim['total_score'],
                })
                
                # 写入淘汰记录
                conn.execute(text('''
                    INSERT INTO elimination_records
                    (robot_id, robot_name, eliminate_date, final_score, 
                     final_metrics, peer_snapshot, reason)
                    VALUES (:rid, :name, :date, :score, :metrics::jsonb, :peers::jsonb, :reason)
                '''), {
                    'rid': victim_id,
                    'name': victim_info.get('robot_name'),
                    'date': trade_date,
                    'score': victim['total_score'],
                    'metrics': json.dumps(victim['metrics']),
                    'peers': json.dumps(peer_snapshot),
                    'reason': f"排名{victim['rank']}/{len(rankings)}",
                })
        
        # 更新计划执行日期
        with self.engine.begin() as conn:
            conn.execute(text('''
                UPDATE elimination_schedule SET last_run_date = :date WHERE id = :sid
            '''), {'date': trade_date, 'sid': schedule['id']})
        
        logger.info(f"=== 淘汰完成: 淘汰 {len(to_eliminate)} 个 ===")
```

### 2. 除权分红处理

**文件路径**：`skills/stock_sim_robot/dividend_handler.py`

```python
"""
除权分红处理

流程：
1. 每日盘前检查持仓股票是否有除权除息
2. 分红 → 现金到账
3. 送股/转增 → 增加持仓股数，调整均价（前复权）
"""
import logging
from datetime import datetime
from utils.storage import storage
from sqlalchemy import text
from .robot_manager import RobotManager
from .cash_manager import CashManager

logger = logging.getLogger(__name__)


class DividendHandler:
    
    def __init__(self):
        self.engine = storage._get_engine()
        self.rm = RobotManager()
    
    def run(self, trade_date: str = None):
        if trade_date is None:
            trade_date = datetime.now().strftime('%Y%m%d')
        
        logger.info(f"=== 除权分红检查: {trade_date} ===")
        
        ex_stocks = self._fetch_ex_dividend_stocks(trade_date)
        
        if not ex_stocks:
            logger.info("今日无除权除息股票")
            return
        
        robots = self.rm.list_robots(status='running')
        
        for robot in robots:
            self._process_robot(robot, ex_stocks, trade_date)
    
    def _fetch_ex_dividend_stocks(self, trade_date: str) -> dict:
        """从东方财富API获取今日除权股票"""
        # 实现见完整方案
        pass
    
    def _process_robot(self, robot, ex_stocks, trade_date):
        """处理单个机器人的持仓"""
        # 实现见完整方案
        pass
```

### 3. 数据清理模块

**文件路径**：`skills/stock_sim_robot/data_cleaner.py`

```python
"""
历史数据清理（节省磁盘空间）

清理策略：
- market_snapshots: 保留最近90天，更早的只保留日级别
- monitor_events: 保留最近180天
"""
import logging
from datetime import datetime, timedelta
from utils.storage import storage
from sqlalchemy import text

logger = logging.getLogger(__name__)


def cleanup(keep_days_snapshots: int = 90, keep_days_events: int = 180):
    engine = storage._get_engine()
    now = datetime.now()
    
    with engine.begin() as conn:
        # 清理行情快照（保留每天最后一条）
        cutoff = (now - timedelta(days=keep_days_snapshots)).strftime('%Y%m%d')
        
        deleted = conn.execute(text('''
            DELETE FROM market_snapshots
            WHERE trade_date < :cutoff
              AND id NOT IN (
                  SELECT DISTINCT ON (stock_code, trade_date) id
                  FROM market_snapshots
                  WHERE trade_date < :cutoff
                  ORDER BY stock_code, trade_date, snapshot_time DESC
              )
        '''), {'cutoff': cutoff})
        
        logger.info(f"📦 行情快照: 清理 {cutoff} 之前, 删除 {deleted.rowcount} 条")
        
        # 清理监控事件
        cutoff_evt = (now - timedelta(days=keep_days_events)).strftime('%Y%m%d')
        
        deleted = conn.execute(text('''
            DELETE FROM monitor_events WHERE trade_date < :cutoff
        '''), {'cutoff': cutoff_evt})
        
        logger.info(f"📦 监控事件: 清理 {cutoff_evt} 之前, 删除 {deleted.rowcount} 条")
```

---

## 每日调度入口

**文件路径**：`scripts/daily_cron.py`

```python
"""
每日调度主入口

crontab 配置：
0  9  * * 1-5  cd ~/stock_analysis && python scripts/daily_cron.py dividend
35 9  * * 1-5  cd ~/stock_analysis && python scripts/daily_cron.py phase_b
45 18 * * 1-5  cd ~/stock_analysis && python scripts/daily_cron.py phase_a
5  15 * * 1-5  cd ~/stock_analysis && python scripts/daily_cron.py settle
0  2  * * *    cd ~/stock_analysis && python scripts/daily_cron.py backup
"""
import sys
import logging
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[
        logging.FileHandler(f'logs/cron_{datetime.now().strftime("%Y%m%d")}.log'),
        logging.StreamHandler(),
    ]
)
logger = logging.getLogger('daily_cron')


def main():
    if len(sys.argv) < 2:
        print("用法: python daily_cron.py <command>")
        print("命令: dividend | phase_b | phase_a | settle | eliminate | backup")
        sys.exit(1)
    
    command = sys.argv[1]
    trade_date = sys.argv[2] if len(sys.argv) > 2 else datetime.now().strftime('%Y%m%d')
    
    logger.info(f"=== daily_cron: {command} | {trade_date} ===")
    
    try:
        if command == 'dividend':
            from skills.stock_sim_robot.dividend_handler import DividendHandler
            DividendHandler().run(trade_date)
        
        elif command == 'phase_b':
            from skills.stock_sim_robot.trade_runner import run_phase_b
            run_phase_b(trade_date)
        
        elif command == 'phase_a':
            from skills.stock_sim_robot.trade_runner import run_phase_a
            run_phase_a(trade_date)
        
        elif command == 'settle':
            from skills.stock_sim_robot.daily_settlement import DailySettlement
            DailySettlement().run(trade_date)
            
            from skills.stock_sim_robot.eliminator import Eliminator
            Eliminator().check_and_run(trade_date)
        
        elif command == 'backup':
            import subprocess
            subprocess.run(['bash', 'scripts/backup_db.sh'], check=True)
        
        else:
            logger.error(f"未知命令: {command}")
            sys.exit(1)
        
        logger.info(f"=== {command} 执行完成 ===")
    
    except Exception as e:
        logger.error(f"❌ {command} 执行失败: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
```

---

## Crontab 完整配置

```bash
# crontab -e
# 模拟炒股 - 每日调度（仅交易日 周一到周五）

# 09:00 除权分红处理
0  9  * * 1-5  cd ~/stock_analysis && /root/miniconda3/bin/python scripts/daily_cron.py dividend

# 09:35 Phase B 开盘买入
35 9  * * 1-5  cd ~/stock_analysis && /root/miniconda3/bin/python scripts/daily_cron.py phase_b

# 15:05 每日结算 + 淘汰检查
5  15 * * 1-5  cd ~/stock_analysis && /root/miniconda3/bin/python scripts/daily_cron.py settle

# 18:45 Phase A 盘后选股
45 18 * * 1-5  cd ~/stock_analysis && /root/miniconda3/bin/python scripts/daily_cron.py phase_a

# 02:00 数据库备份（每天）
0  2  * * *    cd ~/stock_analysis && /root/miniconda3/bin/python scripts/daily_cron.py backup

# 实时监控由 supervisor 管理，不走 cron
```

---

## 文件清单

```
P5 完整文件清单
├── 新增
│   ├── scripts/migrate_p5.py
│   ├── skills/stock_sim_robot/eliminator.py
│   ├── skills/stock_sim_robot/dividend_handler.py
│   ├── skills/stock_sim_robot/data_cleaner.py
│   └── scripts/daily_cron.py
│
└── 配置
    └── crontab 完整配置
```

---

## 完成

至此，策略机器人平台全部开发完成！🎉

返回 [总指导文档](./README.md) 查看完整架构。
