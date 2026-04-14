"""
实时监控服务

运行时段：交易日 9:30 - 15:00
轮询频率：每10秒获取实时行情
存储频率：每5分钟写入一条快照
止损执行：实时检查止损/止盈条件
"""
import sys
import time
import logging
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Optional

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))

from utils.storage import storage
from sqlalchemy import text
from robot.manager import RobotManager
from robot.cash_manager import CashManager
from strategy.loader import load_strategy
from robot.trade_cost import calc_slippage_price, calc_sell_cost
from data.fetcher import get_data_manager

logger = logging.getLogger(__name__)

# 配置参数
POLL_INTERVAL = 10          # 轮询间隔（秒）
SNAPSHOT_INTERVAL = 300     # 快照间隔（秒）
MARKET_OPEN = (9, 36)       # 监控开始（跳过 Phase B）
MARKET_CLOSE = (15, 0)
LUNCH_START = (11, 30)
LUNCH_END = (13, 0)


class RealtimeMonitor:
    """
    实时监控服务
    
    功能：
    1. 每10秒获取所有持仓股票的实时行情
    2. 检查止损/止盈条件，触发时执行卖出
    3. 每5分钟存储行情快照（用于复盘）
    4. 记录所有监控事件
    """
    
    def __init__(self):
        self.engine = storage._get_engine()
        self.rm = RobotManager()
        self.dm = get_data_manager()
        self._last_snapshot_time: Dict[str, datetime] = {}
        self._running = False
    
    def start(self):
        """启动监控主循环"""
        self._running = True
        logger.info("=" * 50)
        logger.info("🟢 实时监控服务启动")
        logger.info("=" * 50)
        
        while self._running:
            now = datetime.now()
            
            # 检查交易时间
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
        """停止监控"""
        self._running = False
    
    def _is_trading_time(self, now: datetime) -> bool:
        """检查是否在交易时间内"""
        t = (now.hour, now.minute)
        
        # 周末不监控
        if now.weekday() >= 5:
            return False
        
        # 上午时段
        if MARKET_OPEN <= t < LUNCH_START:
            return True
        
        # 下午时段
        if LUNCH_END <= t < MARKET_CLOSE:
            return True
        
        return False
    
    def _run_one_cycle(self, now: datetime):
        """执行一轮监控"""
        trade_date = now.strftime('%Y%m%d')
        
        # 获取所有运行中的机器人
        robots = self.rm.list_robots(status='running')
        if not robots:
            return
        
        # 收集所有持仓股票
        holdings_map: Dict[str, List[tuple]] = {}
        with self.engine.connect() as conn:
            for robot in robots:
                holdings = conn.execute(text('''
                    SELECT id, code, name, buy_price, buy_shares, buy_date
                    FROM sim_trades 
                    WHERE robot_id = :rid AND status = 'HOLDING'
                '''), {'rid': robot['robot_id']}).fetchall()
                
                for h in holdings:
                    h = dict(h._mapping)
                    code = h['code']
                    if code not in holdings_map:
                        holdings_map[code] = []
                    holdings_map[code].append((robot, h))
        
        if not holdings_map:
            return
        
        logger.debug(f"监控 {len(holdings_map)} 只股票")
        
        # 逐只股票获取行情并检查信号
        for code, robot_holdings in holdings_map.items():
            try:
                # 获取实时行情（多数据源降级）
                result = self.dm.get_realtime_quote(code)
                if not result.is_valid():
                    logger.warning(f"⚠️ {code}: {result.error}")
                    continue
                
                quote = result.data
                self._process_quote(code, quote, robot_holdings, trade_date, now)
                
            except Exception as e:
                logger.warning(f"监控 {code} 异常: {e}")
    
    def _process_quote(self, code: str, quote: dict, robot_holdings: List[tuple], 
                       trade_date: str, now: datetime):
        """处理单只股票的行情"""
        price = quote.get('price', 0)
        if not price or price <= 0:
            return
        
        # 存储快照（每5分钟一次）
        self._maybe_save_snapshot(code, quote, trade_date, now)
        
        # 检查每个持仓的信号
        for robot, holding in robot_holdings:
            self._check_signals(robot, holding, quote, trade_date, now)
    
    def _check_signals(self, robot: dict, holding: dict, quote: dict, 
                       trade_date: str, now: datetime):
        """检查交易信号（止损/止盈）"""
        price = quote.get('price', 0)
        buy_price = holding.get('buy_price', 0)
        
        if not price or not buy_price:
            return
        
        profit_pct = (price - buy_price) / buy_price if buy_price > 0 else 0
        
        # 加载策略检查是否需要卖出
        try:
            strategy = load_strategy(robot.get('execute_strategy', 'lianban'))
            signal = strategy.should_sell(holding, quote, {'robot': robot})
        except Exception as e:
            logger.warning(f"策略加载失败: {e}")
            # 使用默认止损逻辑
            signal = self._default_should_sell(robot, holding, price)
        
        if signal is None:
            return
        
        # 执行卖出
        logger.warning(f"🚨 触发卖出 | {robot['robot_name']} | {holding['code']} | {signal.reason}")
        self._execute_sell(robot, holding, price, trade_date, now, signal.reason, signal.trigger_pct)
    
    def _default_should_sell(self, robot: dict, holding: dict, current_price: float) -> Optional[object]:
        """默认止损/止盈逻辑"""
        from dataclasses import dataclass
        
        @dataclass
        class SellSignal:
            reason: str
            trigger_pct: float
        
        buy_price = holding.get('buy_price', 0)
        if not buy_price:
            return None
        
        profit_pct = (current_price - buy_price) / buy_price * 100
        
        # 止损 -8%
        if profit_pct <= -8:
            return SellSignal(reason="止损", trigger_pct=-8)
        
        # 止盈 +15%
        if profit_pct >= 15:
            return SellSignal(reason="止盈", trigger_pct=15)
        
        return None
    
    def _execute_sell(self, robot: dict, holding: dict, price: float, 
                      trade_date: str, now: datetime, reason: str, trigger_pct: float = 0):
        """执行卖出操作"""
        robot_id = robot['robot_id']
        code = holding['code']
        name = holding.get('name', '')
        shares = holding.get('buy_shares', 0)
        
        # 计算滑点后价格
        sell_price = calc_slippage_price(price, 'SELL')
        sell_result = calc_sell_cost(sell_price, shares, code)
        
        try:
            with self.engine.begin() as conn:
                # 更新交易记录
                conn.execute(text('''
                    UPDATE sim_trades SET
                        status = 'SOLD',
                        sell_date = :date,
                        sell_price = :sell_price,
                        sell_shares = :shares,
                        sell_amount = :amount,
                        sell_fee = :fee,
                        sell_stamp_duty = :stamp,
                        net_profit = :profit,
                        net_profit_pct = :profit_pct,
                        sell_reason = :reason
                    WHERE id = :id
                '''), {
                    'date': trade_date,
                    'sell_price': round(sell_price, 2),
                    'shares': shares,
                    'amount': round(sell_result['sell_amount'], 2),
                    'fee': round(sell_result['sell_fee'], 2),
                    'stamp': round(sell_result['stamp_duty'], 2),
                    'profit': round(sell_result['net_profit'], 2),
                    'profit_pct': round(sell_result['profit_pct'], 2),
                    'reason': reason,
                    'id': holding['id']
                })
                
                # 更新现金
                cm = CashManager(robot_id)
                cm.credit_sell(sell_result['total_received'], holding['id'], code, name, trade_date)
                
                # 记录监控事件
                conn.execute(text('''
                    INSERT INTO monitor_events
                    (robot_id, stock_code, stock_name, trade_date, event_time, event_type,
                     trigger_price, threshold, current_pct, action_taken, trade_id, memo)
                    VALUES (:rid, :code, :name, :date, :time, :type, :trigger, :threshold, :pct, true, :tid, :memo)
                '''), {
                    'rid': robot_id,
                    'code': code,
                    'name': name,
                    'date': trade_date,
                    'time': now,
                    'type': 'STOP_LOSS' if '止损' in reason else 'STOP_PROFIT',
                    'trigger': round(price, 2),
                    'threshold': trigger_pct,
                    'pct': round((price - holding.get('buy_price', price)) / holding.get('buy_price', 1) * 100, 2),
                    'tid': holding['id'],
                    'memo': f"盘中{reason}触发"
                })
            
            logger.info(f"✅ 卖出成功 | {robot['robot_name']} | {code} | ¥{sell_price:.2f} | {reason}")
            
        except Exception as e:
            logger.error(f"❌ 卖出失败 | {code} | {e}")
    
    def _maybe_save_snapshot(self, code: str, quote: dict, trade_date: str, now: datetime):
        """每5分钟存一条快照"""
        last = self._last_snapshot_time.get(code)
        if last and (now - last).total_seconds() < SNAPSHOT_INTERVAL:
            return
        
        try:
            with self.engine.begin() as conn:
                conn.execute(text('''
                    INSERT INTO market_snapshots
                    (stock_code, stock_name, trade_date, snapshot_time, 
                     price, open, high, low, pre_close, volume, amount, change_pct)
                    VALUES (:code, :name, :date, :time, :price, :open, :high, :low, :pre_close, :volume, :amount, :change_pct)
                '''), {
                    'code': code,
                    'name': quote.get('name', ''),
                    'date': trade_date,
                    'time': now,
                    'price': quote.get('price'),
                    'open': quote.get('open'),
                    'high': quote.get('high'),
                    'low': quote.get('low'),
                    'pre_close': quote.get('pre_close'),
                    'volume': quote.get('volume'),
                    'amount': quote.get('amount'),
                    'change_pct': quote.get('change_pct'),
                })
            
            self._last_snapshot_time[code] = now
            logger.debug(f"📸 快照已保存: {code}")
            
        except Exception as e:
            logger.warning(f"快照保存失败: {code} | {e}")


def start_monitor():
    """启动入口"""
    monitor = RealtimeMonitor()
    try:
        monitor.start()
    except KeyboardInterrupt:
        monitor.stop()
    except Exception as e:
        logger.error(f"监控服务异常退出: {e}")
        monitor.stop()


# === 单元测试 ===
if __name__ == '__main__':
    import os
    os.chdir(ROOT)
    
    print("=== 测试实时监控 ===")
    
    # 测试配置
    print(f"轮询间隔: {POLL_INTERVAL}s")
    print(f"快照间隔: {SNAPSHOT_INTERVAL}s")
    print(f"监控时段: {MARKET_OPEN} - {MARKET_CLOSE}")
    
    # 测试时间判断
    now = datetime.now()
    print(f"当前时间: {now.strftime('%H:%M')}")
    print(f"是否交易时间: {'是' if RealtimeMonitor()._is_trading_time(now) else '否'}")
    
    print("\n✅ 实时监控模块测试完成")
