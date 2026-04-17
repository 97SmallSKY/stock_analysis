"""
交易执行器

职责：下单/平仓执行，事务处理

核心功能：
1. 创建买入订单
2. 创建卖出订单（止盈/止损/到期/手动）
3. 费用计算（佣金、印花税、过户费）
4. 事务处理（原子性保证）
5. 幂等性保证

参考：docs/lhb-quant-v2/TRADING_FLOW.md §三、§四
"""
from datetime import datetime, date
from decimal import Decimal
from typing import Optional
from dataclasses import dataclass
from enum import Enum

from loguru import logger
from sqlalchemy import select, and_, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend import (
    Robot, RobotTrade, TradeExecution, CashLedger, TradeEvent, AuditLog
)
from backend import (
    TradeStatus, SellReason, AbandonReason, transition_trade_status
)


# ============================================================
# 常量定义
# ============================================================
class ExecutionType(str, Enum):
    """执行类型"""
    BUY = 'buy'
    SELL_1 = 'sell_1'
    SELL_2 = 'sell_2'
    SELL_3 = 'sell_3'
    STOP_LOSS = 'stop_loss'
    EXPIRE = 'expire'
    MANUAL = 'manual'


# 费用率
COMMISSION_RATE = Decimal('0.0003')  # 佣金 万3
COMMISSION_MIN = Decimal('5.0')      # 最低5元
STAMP_TAX_RATE = Decimal('0.001')    # 印花税 千1（仅卖出）
TRANSFER_FEE_RATE = Decimal('0.00001')  # 过户费 万0.1


@dataclass
class ExecutionResult:
    """执行结果"""
    success: bool
    trade_id: int
    execution_id: Optional[int] = None
    error: Optional[str] = None
    details: dict = None
    
    def __post_init__(self):
        if self.details is None:
            self.details = {}


class TradeExecutor:
    """
    交易执行器
    
    封装所有交易执行逻辑，确保事务一致性
    """
    
    def __init__(self, db: AsyncSession):
        """
        初始化
        
        Args:
            db: 数据库会话
        """
        self.db = db
    
    async def _get_robot(self, robot_id: int) -> Robot:
        """获取机器人"""
        result = await self.db.execute(
            select(Robot).where(Robot.id == robot_id)
        )
        robot = result.scalar_one_or_none()
        if robot is None:
            raise ValueError(f"机器人不存在: {robot_id}")
        return robot
    
    async def _get_trade(self, trade_id: int) -> RobotTrade:
        """获取交易"""
        result = await self.db.execute(
            select(RobotTrade).where(RobotTrade.id == trade_id)
        )
        trade = result.scalar_one_or_none()
        if trade is None:
            raise ValueError(f"交易不存在: {trade_id}")
        return trade
    
    def _calc_fees(
        self,
        execution_type: str,
        amount: Decimal
    ) -> dict:
        """
        计算交易费用
        
        Returns:
            {commission, stamp_tax, transfer_fee, total_cost}
        """
        # 佣金（双向收取，最低5元）
        commission = max(COMMISSION_MIN, amount * COMMISSION_RATE)
        
        # 印花税（仅卖出收取）
        stamp_tax = Decimal('0')
        if execution_type != 'buy':
            stamp_tax = amount * STAMP_TAX_RATE
        
        # 过户费（双向收取）
        transfer_fee = amount * TRANSFER_FEE_RATE
        
        # 总费用
        total_cost = commission + stamp_tax + transfer_fee
        
        return {
            'commission': commission,
            'stamp_tax': stamp_tax,
            'transfer_fee': transfer_fee,
            'total_cost': total_cost,
        }
    
    def _generate_idempotency_key(
        self,
        robot_id: int,
        trade_id: int,
        execution_type: str,
        trade_date: date
    ) -> str:
        """生成幂等键"""
        return f"{execution_type}:{robot_id}:{trade_id}:{trade_date}"
    
    async def execute_buy(
        self,
        robot_id: int,
        trade_id: int,
        price: float,
        shares: int,
        trade_date: date,
        signal_score: Optional[float] = None
    ) -> ExecutionResult:
        """
        执行买入
        
        Args:
            robot_id: 机器人ID
            trade_id: 交易ID（pending状态）
            price: 买入价格
            shares: 买入股数
            trade_date: 交易日期
            signal_score: 信号评分
        
        Returns:
            ExecutionResult: 执行结果
        """
        try:
            # 获取交易和机器人
            trade = await self._get_trade(trade_id)
            robot = await self._get_robot(robot_id)
            
            # 验证状态
            if trade.status != 'pending':
                return ExecutionResult(
                    success=False,
                    trade_id=trade_id,
                    error=f"交易状态错误: {trade.status}，应为 pending"
                )
            
            # 计算买入金额和费用
            buy_amount = Decimal(str(price)) * shares
            fees = self._calc_fees('buy', buy_amount)
            total_cost = buy_amount + fees['total_cost']
            
            # 检查资金是否充足
            if total_cost > robot.cash_balance:
                return ExecutionResult(
                    success=False,
                    trade_id=trade_id,
                    error=f"资金不足: 需要 {total_cost}，可用 {robot.cash_balance}"
                )
            
            # 幂等键
            idempotency_key = self._generate_idempotency_key(
                robot_id, trade_id, 'buy', trade_date
            )
            
            # 检查是否已执行
            existing = await self.db.execute(
                select(TradeExecution).where(
                    TradeExecution.idempotency_key == idempotency_key
                )
            )
            if existing.scalar_one_or_none():
                logger.info(f"[Executor] 买入已执行，跳过: {idempotency_key}")
                return ExecutionResult(
                    success=True,
                    trade_id=trade_id,
                    error="已执行（幂等）"
                )
            
            # 创建执行记录
            execution = TradeExecution(
                trade_id=trade_id,
                execution_type='buy',
                execution_time=datetime.now(),
                execution_price=Decimal(str(price)),
                execution_shares=shares,
                execution_amount=buy_amount,
                commission=fees['commission'],
                stamp_tax=fees['stamp_tax'],
                transfer_fee=fees['transfer_fee'],
                total_cost=total_cost,
                idempotency_key=idempotency_key
            )
            self.db.add(execution)
            await self.db.flush()
            
            # 更新现金
            new_cash = float(robot.cash_balance) - float(total_cost)
            await self.db.execute(
                update(Robot).where(Robot.id == robot_id).values(
                    cash_balance=Decimal(str(new_cash)),
                    version=robot.version + 1,
                    updated_at=datetime.now()
                )
            )
            
            # 更新交易状态
            trade.status = 'open'
            trade.buy_date = trade_date
            trade.buy_price = Decimal(str(price))
            trade.buy_shares = shares
            trade.buy_amount = buy_amount
            trade.updated_at = datetime.now()
            
            # 创建现金流水
            ledger = CashLedger(
                robot_id=robot_id,
                trans_type='buy',
                amount=-total_cost,
                balance_after=Decimal(str(new_cash)),
                ref_type='trade_execution',
                ref_id=execution.id,
                memo=f"买入 {trade.stock_code}"
            )
            self.db.add(ledger)
            
            # 创建事件
            event = TradeEvent(
                trade_id=trade_id,
                event_type='phase_b',
                event_time=datetime.now(),
                event_data={
                    'price': float(price),
                    'shares': shares,
                    'amount': float(buy_amount),
                    'commission': float(fees['commission']),
                }
            )
            self.db.add(event)
            
            # 审计日志
            audit = AuditLog(
                user_id='system',
                action='execute_buy',
                resource_type='robot_trade',
                resource_id=trade_id,
                old_value={'status': 'pending'},
                new_value={'status': 'open', 'buy_price': str(price)},
            )
            self.db.add(audit)
            
            await self.db.flush()
            
            logger.info(
                f"[Executor] 买入成功: trade_id={trade_id}, "
                f"stock={trade.stock_code}, price={price:.3f}, "
                f"shares={shares}, amount={buy_amount:.2f}"
            )
            
            return ExecutionResult(
                success=True,
                trade_id=trade_id,
                execution_id=execution.id,
                details={
                    'price': float(price),
                    'shares': shares,
                    'amount': float(buy_amount),
                    'fees': {k: float(v) for k, v in fees.items()},
                }
            )
            
        except Exception as e:
            logger.exception(f"[Executor] 买入失败: {e}")
            return ExecutionResult(
                success=False,
                trade_id=trade_id,
                error=str(e)
            )
    
    async def execute_sell(
        self,
        robot_id: int,
        trade_id: int,
        price: float,
        shares: int,
        trade_date: date,
        execution_type: str,
        reason: str
    ) -> ExecutionResult:
        """
        执行卖出
        
        Args:
            robot_id: 机器人ID
            trade_id: 交易ID
            price: 卖出价格
            shares: 卖出股数
            trade_date: 交易日期
            execution_type: 执行类型（sell_1/sell_2/sell_3/stop_loss/expire/manual）
            reason: 卖出原因
        
        Returns:
            ExecutionResult: 执行结果
        """
        try:
            # 获取交易和机器人
            trade = await self._get_trade(trade_id)
            robot = await self._get_robot(robot_id)
            
            # 验证状态
            if trade.status not in ['open', 'partial']:
                return ExecutionResult(
                    success=False,
                    trade_id=trade_id,
                    error=f"交易状态错误: {trade.status}，应为 open/partial"
                )
            
            # 计算卖出金额和费用
            sell_amount = Decimal(str(price)) * shares
            fees = self._calc_fees(execution_type, sell_amount)
            net_amount = sell_amount - fees['total_cost']
            
            # 幂等键
            idempotency_key = self._generate_idempotency_key(
                robot_id, trade_id, execution_type, trade_date
            )
            
            # 检查是否已执行
            existing = await self.db.execute(
                select(TradeExecution).where(
                    TradeExecution.idempotency_key == idempotency_key
                )
            )
            if existing.scalar_one_or_none():
                logger.info(f"[Executor] 卖出已执行，跳过: {idempotency_key}")
                return ExecutionResult(
                    success=True,
                    trade_id=trade_id,
                    error="已执行（幂等）"
                )
            
            # 创建执行记录
            execution = TradeExecution(
                trade_id=trade_id,
                execution_type=execution_type,
                execution_time=datetime.now(),
                execution_price=Decimal(str(price)),
                execution_shares=shares,
                execution_amount=sell_amount,
                commission=fees['commission'],
                stamp_tax=fees['stamp_tax'],
                transfer_fee=fees['transfer_fee'],
                total_cost=net_amount,
                idempotency_key=idempotency_key
            )
            self.db.add(execution)
            await self.db.flush()
            
            # 更新现金
            new_cash = float(robot.cash_balance) + float(net_amount)
            await self.db.execute(
                update(Robot).where(Robot.id == robot_id).values(
                    cash_balance=Decimal(str(new_cash)),
                    version=robot.version + 1,
                    updated_at=datetime.now()
                )
            )
            
            # 更新交易状态
            trade.sell_price = Decimal(str(price))
            trade.sell_shares = (trade.sell_shares or 0) + shares
            trade.sell_amount = (trade.sell_amount or 0) + sell_amount
            trade.sell_reason = reason
            trade.updated_at = datetime.now()
            
            # 检查是否全部卖出
            remaining_shares = (trade.buy_shares or 0) - (trade.sell_shares or 0)
            if remaining_shares <= 0:
                trade.status = 'closed'
                trade.sell_date = trade_date
                
                # 计算已实现盈亏
                buy_cost = float(trade.buy_amount or 0)
                pnl = float(sell_amount) - buy_cost - float(fees['total_cost'])
                trade.realized_pnl = Decimal(str(pnl))
                pnl_ratio = pnl / buy_cost if buy_cost > 0 else 0
                trade.realized_pnl_ratio = Decimal(str(pnl_ratio))
            else:
                trade.status = 'partial'
            
            # 创建现金流水
            ledger = CashLedger(
                robot_id=robot_id,
                trans_type='sell',
                amount=Decimal(str(net_amount)),
                balance_after=Decimal(str(new_cash)),
                ref_type='trade_execution',
                ref_id=execution.id,
                memo=f"卖出 {trade.stock_code}"
            )
            self.db.add(ledger)
            
            # 创建事件
            event = TradeEvent(
                trade_id=trade_id,
                event_type=execution_type,
                event_time=datetime.now(),
                event_data={
                    'price': float(price),
                    'shares': shares,
                    'amount': float(sell_amount),
                    'commission': float(fees['commission']),
                    'reason': reason,
                }
            )
            self.db.add(event)
            
            # 审计日志
            audit = AuditLog(
                user_id='system',
                action='execute_sell',
                resource_type='robot_trade',
                resource_id=trade_id,
                old_value={'status': trade.status},
                new_value={'status': 'closed' if remaining_shares <= 0 else 'partial'},
            )
            self.db.add(audit)
            
            await self.db.flush()
            
            logger.info(
                f"[Executor] 卖出成功: trade_id={trade_id}, "
                f"stock={trade.stock_code}, price={price:.3f}, "
                f"shares={shares}, type={execution_type}"
            )
            
            return ExecutionResult(
                success=True,
                trade_id=trade_id,
                execution_id=execution.id,
                details={
                    'price': float(price),
                    'shares': shares,
                    'amount': float(sell_amount),
                    'fees': {k: float(v) for k, v in fees.items()},
                    'remaining_shares': remaining_shares,
                }
            )
            
        except Exception as e:
            logger.exception(f"[Executor] 卖出失败: {e}")
            return ExecutionResult(
                success=False,
                trade_id=trade_id,
                error=str(e)
            )


async def execute_buy(
    db: AsyncSession,
    robot_id: int,
    trade_id: int,
    price: float,
    shares: int,
    trade_date: date
) -> ExecutionResult:
    """便捷函数：执行买入"""
    executor = TradeExecutor(db)
    return await executor.execute_buy(robot_id, trade_id, price, shares, trade_date)


async def execute_sell(
    db: AsyncSession,
    robot_id: int,
    trade_id: int,
    price: float,
    shares: int,
    trade_date: date,
    execution_type: str,
    reason: str
) -> ExecutionResult:
    """便捷函数：执行卖出"""
    executor = TradeExecutor(db)
    return await executor.execute_sell(
        robot_id, trade_id, price, shares, trade_date, execution_type, reason
    )
