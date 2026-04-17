"""
每日结算服务

执行时间：交易日 15:05
职责：
1. 到期持仓确认（兜底）
2. 计算净值快照
3. 写入 daily_nav
4. 触发 AHS 更新
5. 发送飞书日报

参考：docs/lhb-quant-v2/TRADING_FLOW.md §五
"""
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Optional
from dataclasses import dataclass

from loguru import logger
from sqlalchemy import select, and_, func, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend import (
    Robot, RobotTrade, DailyNav, TradeExecution, CashLedger
)
from backend import KlineDaily
from backend import calculate_ahs_for_robot


@dataclass
class SettlementResult:
    """结算结果"""
    robot_id: int
    settle_date: date
    total_nav: float
    cash_balance: float
    position_value: float
    daily_pnl: float
    daily_pnl_ratio: float
    drawdown: float
    max_drawdown: float
    position_count: int
    expired_closed: int
    ahs_triggered: bool


class DailySettlement:
    """
    每日结算服务
    """
    
    def __init__(self, db: AsyncSession, robot_id: int, settle_date: Optional[date] = None):
        """
        初始化
        
        Args:
            db: 数据库会话
            robot_id: 机器人ID
            settle_date: 结算日期（默认今天）
        """
        self.db = db
        self.robot_id = robot_id
        self.settle_date = settle_date or date.today()
        
        self._robot: Optional[Robot] = None
    
    async def _load_robot(self) -> Robot:
        """加载机器人"""
        if self._robot is None:
            result = await self.db.execute(
                select(Robot).where(Robot.id == self.robot_id)
            )
            self._robot = result.scalar_one_or_none()
            if self._robot is None:
                raise ValueError(f"机器人不存在: {self.robot_id}")
        return self._robot
    
    async def _check_expired_positions(self) -> int:
        """
        Step 1: 到期持仓确认（兜底）
        
        检查 hold_days >= max_hold_days 的持仓，以收盘价平仓
        """
        robot = await self._load_robot()
        max_hold_days = robot.max_hold_days or 5
        
        # 查询持仓
        result = await self.db.execute(
            select(RobotTrade).where(
                and_(
                    RobotTrade.robot_id == self.robot_id,
                    RobotTrade.status.in_(['open', 'partial']),
                    RobotTrade.buy_date.isnot(None),
                    RobotTrade.deleted_at.is_(None)
                )
            )
        )
        positions = result.scalars().all()
        
        expired_count = 0
        
        for pos in positions:
            # 计算 hold_days
            trading_days = await self._count_trading_days(
                pos.buy_date, self.settle_date
            )
            hold_days = trading_days
            
            if hold_days >= max_hold_days:
                # 到期，以收盘价平仓
                close_price = await self._get_close_price(pos.stock_code, self.settle_date)
                
                if close_price:
                    # 计算 remaining_shares
                    sold_shares = await self._get_sold_shares(pos.id)
                    remaining_shares = (pos.buy_shares or 0) - sold_shares
                    
                    if remaining_shares > 0:
                        # 创建卖出执行记录
                        sell_amount = remaining_shares * close_price
                        commission = max(5.0, sell_amount * 0.0003)
                        stamp_tax = sell_amount * 0.001  # 印花税千1
                        transfer_fee = sell_amount * 0.00001
                        
                        execution = TradeExecution(
                            trade_id=pos.id,
                            execution_type='expire',
                            execution_time=datetime.now(),
                            execution_price=Decimal(str(close_price)),
                            execution_shares=remaining_shares,
                            execution_amount=Decimal(str(sell_amount)),
                            commission=Decimal(str(commission)),
                            stamp_tax=Decimal(str(stamp_tax)),
                            transfer_fee=Decimal(str(transfer_fee)),
                            total_cost=Decimal(str(sell_amount - commission - stamp_tax - transfer_fee)),
                            idempotency_key=f"expire:{pos.id}:{self.settle_date}"
                        )
                        self.db.add(execution)
                        
                        # 更新持仓状态
                        pos.status = 'closed'
                        pos.sell_date = self.settle_date
                        pos.sell_price = Decimal(str(close_price))
                        pos.sell_shares = (pos.sell_shares or 0) + remaining_shares
                        pos.sell_reason = 'expire'
                        pos.hold_days = hold_days
                        pos.updated_at = datetime.now()
                        
                        # 计算已实现盈亏
                        buy_cost = float(pos.buy_amount or 0)
                        pnl = sell_amount - buy_cost - commission - stamp_tax - transfer_fee
                        pos.realized_pnl = Decimal(str(pnl))
                        pnl_ratio = pnl / buy_cost if buy_cost > 0 else 0
                        pos.realized_pnl_ratio = Decimal(str(pnl_ratio))
                        
                        # 更新现金
                        cash_result = await self.db.execute(
                            select(Robot).where(Robot.id == self.robot_id)
                        )
                        robot = cash_result.scalar_one()
                        new_cash = float(robot.cash_balance) + sell_amount - commission - stamp_tax - transfer_fee
                        robot.cash_balance = Decimal(str(new_cash))
                        
                        expired_count += 1
                        
                        logger.info(
                            f"[Settlement] 到期平仓: trade_id={pos.id}, "
                            f"stock={pos.stock_code}, price={close_price:.3f}, "
                            f"shares={remaining_shares}, hold_days={hold_days}"
                        )
        
        return expired_count
    
    async def _count_trading_days(self, start_date: date, end_date: date) -> int:
        """计算交易日数量"""
        # 简化实现：使用自然日减去周末
        # 实际应查询 trade_calendar 表
        days = (end_date - start_date).days
        # 粗略估计：一周5个交易日
        trading_days = (days // 7) * 5 + (days % 7)
        return max(0, trading_days)
    
    async def _get_close_price(self, stock_code: str, trade_date: date) -> Optional[float]:
        """获取收盘价"""
        result = await self.db.execute(
            select(KlineDaily).where(
                and_(
                    KlineDaily.trade_date == trade_date,
                    KlineDaily.stock_code == stock_code
                )
            )
        )
        kline = result.scalar_one_or_none()
        return float(kline.close_price) if kline else None
    
    async def _get_sold_shares(self, trade_id: int) -> int:
        """获取已卖出股数"""
        result = await self.db.execute(
            select(func.sum(TradeExecution.execution_shares)).where(
                and_(
                    TradeExecution.trade_id == trade_id,
                    TradeExecution.execution_type.in_(['sell_1', 'sell_2', 'sell_3', 'stop_loss', 'expire', 'manual'])
                )
            )
        )
        return int(result.scalar() or 0)
    
    async def _calculate_position_value(self) -> float:
        """计算持仓市值"""
        result = await self.db.execute(
            select(RobotTrade).where(
                and_(
                    RobotTrade.robot_id == self.robot_id,
                    RobotTrade.status.in_(['open', 'partial']),
                    RobotTrade.deleted_at.is_(None)
                )
            )
        )
        positions = result.scalars().all()
        
        total_value = 0.0
        
        for pos in positions:
            # 计算 remaining_shares
            sold_shares = await self._get_sold_shares(pos.id)
            remaining_shares = (pos.buy_shares or 0) - sold_shares
            
            if remaining_shares > 0:
                # 获取当前收盘价
                close_price = await self._get_close_price(pos.stock_code, self.settle_date)
                
                if close_price:
                    total_value += remaining_shares * close_price
                else:
                    # 使用买入价作为兜底
                    total_value += remaining_shares * float(pos.buy_price or 0)
        
        return total_value
    
    async def _get_prev_nav(self) -> float:
        """获取昨日净值"""
        prev_date = self.settle_date - timedelta(days=1)
        
        result = await self.db.execute(
            select(DailyNav).where(
                and_(
                    DailyNav.robot_id == self.robot_id,
                    DailyNav.nav_date == prev_date
                )
            )
        )
        prev_nav = result.scalar_one_or_none()
        
        if prev_nav:
            return float(prev_nav.close_nav)
        else:
            # 使用初始资金
            robot = await self._load_robot()
            return float(robot.initial_capital)
    
    async def _calc_max_drawdown(self, current_nav: float) -> tuple[float, float]:
        """
        计算最大回撤
        
        Returns:
            (当前回撤, 历史最大回撤)
        """
        # 获取历史净值
        lookback_days = 20
        start_date = self.settle_date - timedelta(days=lookback_days)
        
        result = await self.db.execute(
            select(DailyNav).where(
                and_(
                    DailyNav.robot_id == self.robot_id,
                    DailyNav.nav_date >= start_date
                )
            ).order_by(DailyNav.nav_date)
        )
        navs = result.scalars().all()
        
        if not navs:
            robot = await self._load_robot()
            historical_max = float(robot.initial_capital)
        else:
            historical_max = max(float(n.close_nav) for n in navs)
        
        historical_max = max(historical_max, current_nav)
        
        current_drawdown = (historical_max - current_nav) / historical_max if historical_max > 0 else 0
        
        # 计算历史最大回撤
        if navs:
            peak = float(navs[0].close_nav)
            max_dd = 0.0
            
            for nav in navs:
                current = float(nav.close_nav)
                if current > peak:
                    peak = current
                dd = (peak - current) / peak if peak > 0 else 0
                max_dd = max(max_dd, dd)
            
            # 加上今天
            dd_today = (historical_max - current_nav) / historical_max if historical_max > 0 else 0
            max_dd = max(max_dd, dd_today)
        else:
            max_dd = current_drawdown
        
        return current_drawdown, max_dd
    
    async def run(self) -> SettlementResult:
        """
        执行结算流程
        """
        logger.info(f"[Settlement] 开始结算，robot={self.robot_id}, date={self.settle_date}")
        
        robot = await self._load_robot()
        
        # Step 1: 到期持仓确认（兜底）
        expired_closed = await self._check_expired_positions()
        
        # 刷新以获取更新后的数据
        await self.db.flush()
        
        # Step 2: 计算持仓市值
        position_value = await self._calculate_position_value()
        
        # Step 3: 计算总资产
        cash_balance = float(robot.cash_balance)
        total_nav = cash_balance + position_value
        initial_capital = float(robot.initial_capital)
        
        # Step 4: 计算净值比
        nav_ratio = total_nav / initial_capital if initial_capital > 0 else 1.0
        
        # Step 5: 计算今日盈亏
        prev_nav = await self._get_prev_nav()
        daily_pnl = total_nav - prev_nav
        daily_pnl_ratio = daily_pnl / prev_nav if prev_nav > 0 else 0
        
        # Step 6: 计算最大回撤
        drawdown, max_drawdown = await self._calc_max_drawdown(total_nav)
        
        # Step 7: 查询持仓数量
        result = await self.db.execute(
            select(func.count()).select_from(RobotTrade).where(
                and_(
                    RobotTrade.robot_id == self.robot_id,
                    RobotTrade.status.in_(['open', 'partial']),
                    RobotTrade.deleted_at.is_(None)
                )
            )
        )
        position_count = result.scalar() or 0
        
        # Step 8: 写入净值快照（幂等）
        existing_nav = await self.db.execute(
            select(DailyNav).where(
                and_(
                    DailyNav.robot_id == self.robot_id,
                    DailyNav.nav_date == self.settle_date
                )
            )
        )
        
        if existing_nav.scalar_one_or_none():
            # 更新
            await self.db.execute(
                update(DailyNav).where(
                    and_(
                        DailyNav.robot_id == self.robot_id,
                        DailyNav.nav_date == self.settle_date
                    )
                ).values(
                    close_nav=Decimal(str(total_nav)),
                    daily_pnl=Decimal(str(daily_pnl)),
                    daily_pnl_ratio=Decimal(str(daily_pnl_ratio)),
                    position_value=Decimal(str(position_value)),
                    cash_balance=Decimal(str(cash_balance)),
                    position_ratio=Decimal(str(position_value / total_nav)) if total_nav > 0 else Decimal('0'),
                    max_drawdown=Decimal(str(max_drawdown)),
                    updated_at=datetime.now()
                )
            )
        else:
            # 新增
            nav_record = DailyNav(
                robot_id=self.robot_id,
                nav_date=self.settle_date,
                open_nav=Decimal(str(prev_nav)),
                close_nav=Decimal(str(total_nav)),
                daily_pnl=Decimal(str(daily_pnl)),
                daily_pnl_ratio=Decimal(str(daily_pnl_ratio)),
                position_value=Decimal(str(position_value)),
                cash_balance=Decimal(str(cash_balance)),
                position_ratio=Decimal(str(position_value / total_nav)) if total_nav > 0 else Decimal('0'),
                max_drawdown=Decimal(str(max_drawdown))
            )
            self.db.add(nav_record)
        
        # Step 9: 触发 AHS 更新
        ahs_result = await calculate_ahs_for_robot(self.db, self.robot_id, self.settle_date)
        ahs_triggered = ahs_result is not None
        
        # 提交事务
        await self.db.commit()
        
        logger.info(
            f"[Settlement] 结算完成: total_nav={total_nav:.2f}, "
            f"daily_pnl={daily_pnl_ratio:.2%}, position_count={position_count}"
        )
        
        # TODO: Step 10 发送飞书日报
        
        return SettlementResult(
            robot_id=self.robot_id,
            settle_date=self.settle_date,
            total_nav=total_nav,
            cash_balance=cash_balance,
            position_value=position_value,
            daily_pnl=daily_pnl,
            daily_pnl_ratio=daily_pnl_ratio,
            drawdown=drawdown,
            max_drawdown=max_drawdown,
            position_count=position_count,
            expired_closed=expired_closed,
            ahs_triggered=ahs_triggered
        )


async def run_daily_settlement(
    db: AsyncSession,
    robot_id: int,
    settle_date: Optional[date] = None
) -> SettlementResult:
    """
    执行每日结算（便捷函数）
    """
    settlement = DailySettlement(db, robot_id, settle_date)
    return await settlement.run()
