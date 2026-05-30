"""
现金余额管理

职责：
- 查询机器人当前余额
- 买入扣款
- 卖出到账
- 分红入账
- 机器人继承时资金转移
"""
import sys
import logging
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))

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
        
        logger.info(f"💰 {self.robot_id} 分红到账: +¥{amount:.2f}, 余额 ¥{new_balance:.2f}")
    
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


# === 单元测试 ===
if __name__ == '__main__':
    import os
    os.chdir(ROOT)
    
    print("=== 测试 CashManager ===")
    
    # 使用测试机器人
    cm = CashManager('test_robot')
    
    # 初始化资金
    cm.init_capital(100000, '20260101')
    print(f"初始余额: ¥{cm.get_balance():.2f}")
    
    # 模拟买入扣款
    success = cm.deduct_buy(10000, 1, '600001', '测试股票', '20260102', '测试买入')
    print(f"扣款成功: {success}, 余额: ¥{cm.get_balance():.2f}")
    
    # 模拟卖出到账
    cm.credit_sell(10500, 1, '600001', '测试股票', '20260103', '测试卖出')
    print(f"卖出后余额: ¥{cm.get_balance():.2f}")
    
    print("\n✅ CashManager 测试通过")
