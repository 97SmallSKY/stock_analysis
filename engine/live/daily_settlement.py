"""
每日结算任务（15:05 执行）

职责：
1. 获取所有持仓股票的收盘价
2. 计算每个机器人的持仓市值和净值
3. 检查超期持仓强制卖出
4. 写入 sim_daily_summary
5. 拉取基准指数收盘数据
"""
import sys
import logging
import json
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))

from utils.storage import storage
from sqlalchemy import text
from robot.manager import RobotManager
from robot.cash_manager import CashManager
from data.fetcher import get_data_manager

logger = logging.getLogger(__name__)


class DailySettlement:
    
    def __init__(self):
        self.engine = storage._get_engine()
        self.rm = RobotManager()
        self.dm = get_data_manager()
    
    def run(self, trade_date: str = None):
        """执行每日结算"""
        if trade_date is None:
            trade_date = datetime.now().strftime('%Y%m%d')
        
        logger.info(f"{'='*50}")
        logger.info(f"📊 每日结算开始: {trade_date}")
        logger.info(f"{'='*50}")
        
        # 1. 获取所有运行中的机器人
        robots = self.rm.list_robots(status='running')
        if not robots:
            logger.warning("⚠️ 无运行中的机器人")
            return
        
        logger.info(f"📋 待结算机器人: {len(robots)} 个")
        
        # 2. 收集所有持仓股票代码
        all_codes = self._collect_holding_codes(robots)
        
        # 3. 批量获取收盘价（使用多数据源降级）
        close_prices = self._fetch_close_prices(all_codes)
        
        # 4. 逐个机器人结算
        for robot in robots:
            try:
                self._settle_one_robot(robot, trade_date, close_prices)
            except Exception as e:
                logger.error(f"❌ {robot['robot_id']} 结算异常: {e}")
        
        # 5. 拉取基准指数数据
        self._fetch_benchmarks(trade_date)
        
        logger.info(f"{'='*50}")
        logger.info(f"✅ 每日结算完成")
        logger.info(f"{'='*50}")
    
    def _collect_holding_codes(self, robots: list) -> set:
        """收集所有持仓股票代码"""
        all_codes = set()
        with self.engine.connect() as conn:
            for robot in robots:
                holdings = conn.execute(text('''
                    SELECT code FROM sim_trades
                    WHERE robot_id = :rid AND status = 'HOLDING'
                '''), {'rid': robot['robot_id']}).fetchall()
                for h in holdings:
                    all_codes.add(h[0])
        return all_codes
    
    def _fetch_close_prices(self, codes: set) -> dict:
        """批量获取收盘价（使用多数据源降级）"""
        prices = {}
        
        for code in codes:
            try:
                # 使用实时行情获取当前价格（盘中和盘后都可用）
                result = self.dm.get_realtime_quote(code)
                if result.is_valid():
                    quote = result.data
                    prices[code] = {
                        'price': quote.get('price', 0),
                        'pre_close': quote.get('pre_close', 0),
                    }
                    logger.debug(f"✓ {code}: ¥{quote.get('price', 0):.2f}")
                else:
                    logger.warning(f"⚠️ {code}: {result.error}")
            except Exception as e:
                logger.warning(f"⚠️ {code}: 获取失败 {e}")
        
        logger.info(f"📊 获取到 {len(prices)}/{len(codes)} 只股票收盘价")
        return prices
    
    def _settle_one_robot(self, robot: dict, trade_date: str, close_prices: dict):
        """结算单个机器人"""
        robot_id = robot['robot_id']
        robot_name = robot.get('robot_name', robot_id)
        initial_capital = robot.get('initial_capital', 100000)
        
        # 获取现金余额
        cm = CashManager(robot_id)
        cash = cm.get_balance()
        
        with self.engine.begin() as conn:
            # 获取持仓
            holdings = conn.execute(text('''
                SELECT id, code, name, buy_price, buy_shares, trade_date
                FROM sim_trades 
                WHERE robot_id = :rid AND status = 'HOLDING'
            '''), {'rid': robot_id}).fetchall()
            
            holding_value = 0.0
            holdings_snapshot = []
            
            for h in holdings:
                h = dict(h._mapping)
                code = h['code']
                current_price = close_prices.get(code, {}).get('price') or h['buy_price']
                
                market_value = current_price * h['buy_shares']
                holding_value += market_value
                
                profit_pct = 0
                if h['buy_price'] > 0:
                    profit_pct = (current_price - h['buy_price']) / h['buy_price'] * 100
                
                holdings_snapshot.append({
                    'code': code,
                    'name': h.get('name', ''),
                    'shares': h['buy_shares'],
                    'buy_price': h['buy_price'],
                    'current_price': round(current_price, 2),
                    'market_value': round(market_value, 2),
                    'profit_pct': round(profit_pct, 2),
                    'buy_date': h.get('trade_date', ''),
                })
            
            total_value = cash + holding_value
            nav = total_value / initial_capital
            
            # 计算当日收益率（需要前一天的数据）
            daily_return_pct = self._calc_daily_return(conn, robot_id, trade_date, nav)
            
            # 写入快照
            conn.execute(text('''
                INSERT INTO sim_daily_summary
                (robot_id, trade_date, cash, holding_value, total_value, nav,
                 daily_return_pct, holdings_snapshot)
                VALUES (:rid, :date, :cash, :hv, :tv, :nav, :drp, :snap::jsonb)
                ON CONFLICT (robot_id, trade_date) DO UPDATE SET
                    cash = EXCLUDED.cash,
                    holding_value = EXCLUDED.holding_value,
                    total_value = EXCLUDED.total_value,
                    nav = EXCLUDED.nav,
                    daily_return_pct = EXCLUDED.daily_return_pct,
                    holdings_snapshot = EXCLUDED.holdings_snapshot
            '''), {
                'rid': robot_id,
                'date': trade_date,
                'cash': round(cash, 2),
                'hv': round(holding_value, 2),
                'tv': round(total_value, 2),
                'nav': round(nav, 4),
                'drp': round(daily_return_pct, 4),
                'snap': json.dumps(holdings_snapshot),
            })
        
        logger.info(f"  📊 {robot_name}: 净值={nav:.4f}, 现金=¥{cash:.0f}, 持仓=¥{holding_value:.0f}, 总值=¥{total_value:.0f}")
    
    def _calc_daily_return(self, conn, robot_id: str, trade_date: str, current_nav: float) -> float:
        """计算日收益率"""
        # 获取前一个交易日的净值
        prev = conn.execute(text('''
            SELECT nav FROM sim_daily_summary
            WHERE robot_id = :rid AND trade_date < :date
            ORDER BY trade_date DESC LIMIT 1
        '''), {'rid': robot_id, 'date': trade_date}).fetchone()
        
        if prev and prev[0] > 0:
            prev_nav = prev[0]
            return (current_nav - prev_nav) / prev_nav * 100
        
        return 0.0
    
    def _fetch_benchmarks(self, trade_date: str):
        """拉取基准指数数据"""
        # 主要基准指数
        benchmarks = [
            ('000001', '上证指数'),
            ('399001', '深证成指'),
            ('000300', '沪深300'),
            ('000905', '中证500'),
            ('399006', '创业板指'),
        ]
        
        logger.info(f"📈 拉取基准指数数据...")
        
        for code, name in benchmarks:
            try:
                # 使用实时行情获取指数数据
                result = self.dm.get_realtime_quote(code)
                if result.is_valid():
                    quote = result.data
                    close_price = quote.get('price', 0)
                    pre_close = quote.get('pre_close', 0)
                    change_pct = 0
                    if pre_close > 0:
                        change_pct = (close_price - pre_close) / pre_close * 100
                    nav = self._calc_benchmark_nav(code, trade_date, close_price, pre_close)
                    
                    with self.engine.begin() as conn:
                        conn.execute(text('''
                            INSERT INTO benchmark_daily
                            (index_code, index_name, trade_date, close_price, change_pct, nav)
                            VALUES (:code, :name, :date, :price, :pct, :nav)
                            ON CONFLICT (index_code, trade_date) DO UPDATE SET
                                close_price = EXCLUDED.close_price,
                                change_pct = EXCLUDED.change_pct,
                                nav = EXCLUDED.nav
                        '''), {
                            'code': code,
                            'name': name,
                            'date': trade_date,
                            'price': close_price,
                            'pct': round(change_pct, 4),
                            'nav': round(nav, 6),
                        })
                    logger.debug(f"  ✓ {name}({code}): {close_price:.2f} ({change_pct:+.2f}%)")
            except Exception as e:
                logger.warning(f"  ⚠️ {name}({code}): {e}")
        
        logger.info(f"✅ 基准指数数据拉取完成")

    def _calc_benchmark_nav(self, index_code: str, trade_date: str, close_price: float, pre_close: float) -> float:
        """
        计算基准净值（从1起算的累计净值）。
        优先沿用上一交易日 nav，按当日涨跌幅滚动。
        """
        if close_price <= 0:
            return 1.0

        with self.engine.connect() as conn:
            prev = conn.execute(text('''
                SELECT nav
                FROM benchmark_daily
                WHERE index_code = :code AND trade_date < :date
                ORDER BY trade_date DESC
                LIMIT 1
            '''), {'code': index_code, 'date': trade_date}).fetchone()

        prev_nav = float(prev[0]) if prev and prev[0] else 1.0
        if pre_close and pre_close > 0:
            return prev_nav * (close_price / pre_close)
        return prev_nav


# === 单元测试 ===
if __name__ == '__main__':
    import os
    os.chdir(ROOT)
    
    print("=== 测试 DailySettlement ===")
    
    ds = DailySettlement()
    
    # 手动指定日期测试（避免周末）
    ds.run('20240412')  # 使用一个交易日
    
    print("\n✅ 测试完成")
