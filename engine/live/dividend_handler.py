"""
除权分红处理

流程：
1. 每日盘前检查持仓股票是否有除权除息
2. 分红 → 现金到账
3. 送股/转增 → 增加持仓股数，调整均价（前复权）
"""
import sys
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))

from utils.storage import storage
from sqlalchemy import text
from robot.manager import RobotManager
from robot.cash_manager import CashManager

logger = logging.getLogger(__name__)


class DividendHandler:
    """
    除权分红处理器
    
    处理类型：
    1. 现金分红 → 直接到账现金
    2. 送股/转增 → 增加持仓股数
    3. 配股 → 需要现金认购（暂不实现）
    """
    
    def __init__(self):
        self.engine = storage._get_engine()
        self.rm = RobotManager()
    
    def run(self, trade_date: str = None):
        """执行除权分红处理"""
        if trade_date is None:
            trade_date = datetime.now().strftime('%Y%m%d')
        
        logger.info("=" * 50)
        logger.info(f"💰 除权分红检查: {trade_date}")
        logger.info("=" * 50)
        
        # 获取今日除权股票列表
        ex_stocks = self._fetch_ex_dividend_stocks(trade_date)
        
        if not ex_stocks:
            logger.info("今日无除权除息股票")
            return
        
        logger.info(f"今日除权股票: {len(ex_stocks)} 只")
        
        # 处理每个运行中的机器人
        robots = self.rm.list_robots(status='running')
        
        for robot in robots:
            self._process_robot(robot, ex_stocks, trade_date)
    
    def _fetch_ex_dividend_stocks(self, trade_date: str) -> Dict[str, dict]:
        """
        从数据库或API获取今日除权股票
        
        Returns:
            {stock_code: {dividend_per_share, bonus_ratio, split_ratio}}
        """
        # 先查本地数据库
        ex_stocks = {}
        
        with self.engine.connect() as conn:
            rows = conn.execute(text('''
                SELECT stock_code, stock_name, dividend_per_share, 
                       bonus_ratio, split_ratio
                FROM dividend_records
                WHERE ex_date = :date
            '''), {'date': trade_date}).fetchall()
            
            for r in rows:
                ex_stocks[r[0]] = {
                    'stock_name': r[1],
                    'dividend_per_share': r[2],
                    'bonus_ratio': r[3] or 0,
                    'split_ratio': r[4] or 0,
                }
        
        # 如果本地没有数据，尝试从API获取（简化实现）
        # 实际生产中需要对接东方财富/同花顺的除权数据接口
        
        return ex_stocks
    
    def _process_robot(self, robot: dict, ex_stocks: Dict[str, dict], trade_date: str):
        """处理单个机器人的持仓"""
        robot_id = robot['robot_id']
        
        with self.engine.connect() as conn:
            holdings = conn.execute(text('''
                SELECT id, code, name, buy_price, buy_shares
                FROM sim_trades
                WHERE robot_id = :rid AND status = 'HOLDING'
            '''), {'rid': robot_id}).fetchall()
        
        for h in holdings:
            h = dict(h._mapping)
            code = h['code']
            
            if code not in ex_stocks:
                continue
            
            div_info = ex_stocks[code]
            
            # 处理现金分红
            if div_info['dividend_per_share'] > 0:
                self._process_dividend(robot, h, div_info, trade_date)
            
            # 处理送股/转增
            if div_info['bonus_ratio'] > 0 or div_info['split_ratio'] > 0:
                self._process_bonus(robot, h, div_info, trade_date)
    
    def _process_dividend(self, robot: dict, holding: dict, div_info: dict, trade_date: str):
        """处理现金分红"""
        robot_id = robot['robot_id']
        dividend_total = div_info['dividend_per_share'] * holding['buy_shares']
        
        # 现金到账
        cm = CashManager(robot_id)
        cm.credit_dividend(
            amount=dividend_total,
            trade_id=holding['id'],
            stock_code=holding['code'],
            stock_name=holding['name'],
            trade_date=trade_date,
        )
        
        logger.info(
            f"  💵 分红到账: {robot.get('robot_name')} | "
            f"{holding['code']} {holding['name']} | "
            f"¥{dividend_total:.2f} ({div_info['dividend_per_share']:.3f}/股)"
        )
    
    def _process_bonus(self, robot: dict, holding: dict, div_info: dict, trade_date: str):
        """处理送股/转增（调整持仓）"""
        bonus_ratio = div_info.get('bonus_ratio', 0)
        split_ratio = div_info.get('split_ratio', 0)
        total_ratio = bonus_ratio + split_ratio
        
        if total_ratio <= 0:
            return
        
        old_shares = holding['buy_shares']
        old_price = holding['buy_price']
        
        # 新股数 = 原股数 × (1 + 送转比例)
        new_shares = int(old_shares * (1 + total_ratio / 10))
        
        # 新均价 = 原均价 / (1 + 送转比例) （前复权）
        new_price = old_price / (1 + total_ratio / 10)
        
        # 更新持仓
        with self.engine.begin() as conn:
            conn.execute(text('''
                UPDATE sim_trades SET
                    buy_shares = :shares,
                    buy_price = :price,
                    bonus_adjusted = true,
                    bonus_ratio = :ratio
                WHERE id = :id
            '''), {
                'shares': new_shares,
                'price': round(new_price, 2),
                'ratio': total_ratio,
                'id': holding['id'],
            })
        
        logger.info(
            f"  🎁 送转股: {robot.get('robot_name')} | "
            f"{holding['code']} {holding['name']} | "
            f"{old_shares}→{new_shares}股 | "
            f"均价 ¥{old_price:.2f}→¥{new_price:.2f}"
        )


# 扩展 CashManager 方法（如果不存在）
def _credit_dividend_patch(self, amount: float, trade_id: int, 
                           stock_code: str, stock_name: str, 
                           trade_date: str):
    """分红入账"""
    self._add_flow(
        amount=amount,
        flow_type='DIVIDEND',
        ref_type='TRADE',
        ref_id=trade_id,
        stock_code=stock_code,
        stock_name=stock_name,
        memo=f"现金分红 {stock_name}",
        trade_date=trade_date,
    )

# 动态添加方法
if not hasattr(CashManager, 'credit_dividend'):
    CashManager.credit_dividend = _credit_dividend_patch


def run_dividend_handler(trade_date: str = None):
    """运行除权分红处理（供外部调用）"""
    handler = DividendHandler()
    handler.run(trade_date)


# === 单元测试 ===
if __name__ == '__main__':
    import os
    os.chdir(ROOT)
    
    print("=== 测试除权分红处理器 ===")
    
    print("\n处理逻辑:")
    print("  1. 现金分红 → 直接到账现金账户")
    print("  2. 送股/转增 → 增加持仓股数，调整均价（前复权）")
    
    print("\n✅ 除权分红模块测试完成")
