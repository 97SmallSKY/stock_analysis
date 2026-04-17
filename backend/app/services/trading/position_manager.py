"""
持仓管理器

职责：持仓查询、盈亏计算、净值更新

核心功能：
1. 查询当前持仓
2. 计算持仓盈亏
3. 计算持仓市值
4. 更新净值
5. 持仓预警

参考：docs/lhb-quant-v2/TRADING_FLOW.md §四
"""
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional
from dataclasses import dataclass

from loguru import logger
from sqlalchemy import select, and_, func
from sqlalchemy.ext.asyncio import AsyncSession

from backend import (
    Robot, RobotTrade, TradeExecution, DailyNav
)
from backend import KlineDaily, StockInfo


@dataclass
class PositionInfo:
    """持仓信息"""
    trade_id: int
    stock_code: str
    stock_name: str
    buy_date: date
    buy_price: float
    buy_shares: int
    buy_amount: float
    current_price: float
    current_value: float
    unrealized_pnl: float
    unrealized_pnl_ratio: float
    hold_days: int
    max_gain: float  # 持仓期间最大涨幅
    max_loss: float  # 持仓期间最大跌幅


@dataclass
class PortfolioSummary:
    """组合概览"""
    robot_id: int
    total_assets: float
    cash_balance: float
    position_value: float
    position_ratio: float
    daily_pnl: float
    daily_pnl_ratio: float
    total_pnl: float
    total_pnl_ratio: float
    position_count: int
    max_drawdown: float
    sharpe_ratio: float


class PositionManager:
    """
    持仓管理器
    """
    
    def __init__(self, db: AsyncSession, robot_id: int):
        """
        初始化
        
        Args:
            db: 数据库会话
            robot_id: 机器人ID
        """
        self.db = db
        self.robot_id = robot_id
    
    async def get_positions(self) -> list[PositionInfo]:
        """
        获取当前持仓列表
        
        Returns:
            持仓信息列表
        """
        # 查询持仓
        result = await self.db.execute(
            select(RobotTrade).where(
                and_(
                    RobotTrade.robot_id == self.robot_id,
                    RobotTrade.status.in_(['open', 'partial']),
                    RobotTrade.deleted_at.is_(None)
                )
            ).order_by(RobotTrade.buy_date.desc())
        )
        trades = result.scalars().all()
        
        positions = []
        for trade in trades:
            # 获取当前价格
            kline_result = await self.db.execute(
                select(KlineDaily).where(
                    KlineDaily.stock_code == trade.stock_code
                ).order_by(KlineDaily.trade_date.desc()).limit(1)
            )
            kline = kline_result.scalar_one_or_none()
            current_price = float(kline.close_price) if kline else float(trade.buy_price or 0)
            
            # 计算已卖出股数
            sold_shares = await self._get_sold_shares(trade.id)
            remaining_shares = (trade.buy_shares or 0) - sold_shares
            
            if remaining_shares <= 0:
                continue
            
            # 计算当前市值
            current_value = remaining_shares * current_price
            buy_cost = float(trade.buy_amount or 0)
            
            # 计算未实现盈亏
            unrealized_pnl = current_value - buy_cost
            unrealized_pnl_ratio = unrealized_pnl / buy_cost if buy_cost > 0 else 0
            
            # 计算持仓天数
            hold_days = (date.today() - trade.buy_date).days if trade.buy_date else 0
            
            # 获取股票名称
            stock_result = await self.db.execute(
                select(StockInfo.stock_name).where(
                    StockInfo.stock_code == trade.stock_code
                )
            )
            stock_info = stock_result.scalar_one_or_none()
            stock_name = stock_info[0] if stock_info else trade.stock_name
            
            positions.append(PositionInfo(
                trade_id=trade.id,
                stock_code=trade.stock_code,
                stock_name=stock_name,
                buy_date=trade.buy_date,
                buy_price=float(trade.buy_price or 0),
                buy_shares=trade.buy_shares,
                buy_amount=buy_cost,
                current_price=current_price,
                current_value=current_value,
                unrealized_pnl=unrealized_pnl,
                unrealized_pnl_ratio=unrealized_pnl_ratio,
                hold_days=hold_days,
                max_gain=0.0,  # TODO: 计算持仓期间最高价
                max_loss=0.0,  # TODO: 计算持仓期间最低价
            ))
        
        return positions
    
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
    
    async def get_portfolio_summary(self) -> PortfolioSummary:
        """
        获取组合概览
        
        Returns:
            PortfolioSummary: 组合概览
        """
        # 获取机器人
        result = await self.db.execute(
            select(Robot).where(Robot.id == self.robot_id)
        )
        robot = result.scalar_one_or_none()
        
        if robot is None:
            raise ValueError(f"机器人不存在: {self.robot_id}")
        
        # 获取持仓
        positions = await self.get_positions()
        position_value = sum(p.current_value for p in positions)
        
        # 计算总资产
        cash_balance = float(robot.cash_balance)
        total_assets = cash_balance + position_value
        
        # 计算初始资金
        initial_capital = float(robot.initial_capital)
        total_pnl = total_assets - initial_capital
        total_pnl_ratio = total_pnl / initial_capital if initial_capital > 0 else 0
        
        # 获取昨日净值
        prev_nav_result = await self.db.execute(
            select(DailyNav).where(
                DailyNav.robot_id == self.robot_id
            ).order_by(DailyNav.nav_date.desc()).limit(1)
        )
        prev_nav = prev_nav_result.scalar_one_or_none()
        
        if prev_nav:
            daily_pnl = total_assets - float(prev_nav.close_nav)
            daily_pnl_ratio = daily_pnl / float(prev_nav.close_nav) if float(prev_nav.close_nav) > 0 else 0
        else:
            daily_pnl = total_pnl
            daily_pnl_ratio = total_pnl_ratio
        
        # 计算最大回撤
        max_dd = await self._calc_max_drawdown(total_assets)
        
        # 计算夏普比率（简化）
        sharpe = await self._calc_sharpe_ratio()
        
        return PortfolioSummary(
            robot_id=self.robot_id,
            total_assets=total_assets,
            cash_balance=cash_balance,
            position_value=position_value,
            position_ratio=position_value / total_assets if total_assets > 0 else 0,
            daily_pnl=daily_pnl,
            daily_pnl_ratio=daily_pnl_ratio,
            total_pnl=total_pnl,
            total_pnl_ratio=total_pnl_ratio,
            position_count=len(positions),
            max_drawdown=max_dd,
            sharpe_ratio=sharpe,
        )
    
    async def _calc_max_drawdown(self, current_nav: float) -> float:
        """计算最大回撤"""
        lookback_days = 20
        start_date = date.today() - timedelta(days=lookback_days)
        
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
            # 无历史数据，返回0
            return 0.0
        
        peak = max(float(n.close_nav) for n in navs)
        peak = max(peak, current_nav)
        
        return (peak - current_nav) / peak if peak > 0 else 0
    
    async def _calc_sharpe_ratio(self, risk_free_rate: float = 0.03) -> float:
        """计算夏普比率"""
        lookback_days = 60
        start_date = date.today() - timedelta(days=lookback_days)
        
        result = await self.db.execute(
            select(DailyNav).where(
                and_(
                    DailyNav.robot_id == self.robot_id,
                    DailyNav.nav_date >= start_date
                )
            ).order_by(DailyNav.nav_date)
        )
        navs = result.scalars().all()
        
        if len(navs) < 10:
            return 0.0
        
        # 计算日收益率
        daily_returns = []
        for i in range(1, len(navs)):
            prev = float(navs[i-1].close_nav)
            curr = float(navs[i].close_nav)
            if prev > 0:
                daily_returns.append((curr - prev) / prev)
        
        if not daily_returns:
            return 0.0
        
        # 计算日均收益
        avg_return = sum(daily_returns) / len(daily_returns)
        
        # 计算年化收益
        annual_return = (1 + avg_return) ** 252 - 1
        
        # 计算年化波动率
        variance = sum((r - avg_return) ** 2 for r in daily_returns) / (len(daily_returns) - 1)
        volatility = (variance ** 0.5) * (252 ** 0.5)
        
        # 夏普比率
        if volatility > 0:
            return (annual_return - risk_free_rate) / volatility
        
        return 0.0
    
    async def update_nav(self, nav_date: Optional[date] = None) -> float:
        """
        更新净值（手动触发）
        
        Args:
            nav_date: 净值日期（默认今天）
        
        Returns:
            当前净值
        """
        nav_date = nav_date or date.today()
        
        # 检查是否已存在
        existing = await self.db.execute(
            select(DailyNav).where(
                and_(
                    DailyNav.robot_id == self.robot_id,
                    DailyNav.nav_date == nav_date
                )
            )
        )
        if existing.scalar_one_or_none():
            logger.warning(f"[Position] 净值已存在: robot={self.robot_id}, date={nav_date}")
            return 0.0
        
        # 获取组合概览
        summary = await self.get_portfolio_summary()
        
        # 获取昨日净值
        prev_nav_result = await self.db.execute(
            select(DailyNav).where(
                DailyNav.robot_id == self.robot_id
            ).order_by(DailyNav.nav_date.desc()).limit(1)
        )
        prev_nav = prev_nav_result.scalar_one_or_none()
        open_nav = float(prev_nav.close_nav) if prev_nav else summary.total_assets
        
        # 创建净值记录
        nav_record = DailyNav(
            robot_id=self.robot_id,
            nav_date=nav_date,
            open_nav=Decimal(str(open_nav)),
            close_nav=Decimal(str(summary.total_assets)),
            daily_pnl=Decimal(str(summary.daily_pnl)),
            daily_pnl_ratio=Decimal(str(summary.daily_pnl_ratio)),
            position_value=Decimal(str(summary.position_value)),
            cash_balance=Decimal(str(summary.cash_balance)),
            position_ratio=Decimal(str(summary.position_ratio)),
            max_drawdown=Decimal(str(summary.max_drawdown))
        )
        
        self.db.add(nav_record)
        await self.db.flush()
        
        logger.info(
            f"[Position] 净值更新: robot={self.robot_id}, "
            f"date={nav_date}, nav={summary.total_assets:.2f}"
        )
        
        return summary.total_assets
    
    async def get_position_alerts(self) -> list[dict]:
        """
        获取持仓预警
        
        Returns:
            预警列表
        """
        alerts = []
        positions = await self.get_positions()
        
        for pos in positions:
            # 预警1：亏损超过5%
            if pos.unrealized_pnl_ratio <= -0.05:
                alerts.append({
                    'type': 'loss_warning',
                    'trade_id': pos.trade_id,
                    'stock_code': pos.stock_code,
                    'loss_ratio': pos.unrealized_pnl_ratio,
                    'message': f"{pos.stock_code} 亏损 {pos.unrealized_pnl_ratio:.1%}"
                })
            
            # 预警2：持仓天数超过max_hold_days
            if pos.hold_days >= 5:  # 默认5天
                alerts.append({
                    'type': 'hold_days_warning',
                    'trade_id': pos.trade_id,
                    'stock_code': pos.stock_code,
                    'hold_days': pos.hold_days,
                    'message': f"{pos.stock_code} 持仓 {pos.hold_days} 天，接近到期"
                })
            
            # 预警3：涨停/跌停
            # TODO: 检查当前价格是否涨停/跌停
        
        return alerts


async def get_positions(db: AsyncSession, robot_id: int) -> list[PositionInfo]:
    """便捷函数：获取持仓"""
    manager = PositionManager(db, robot_id)
    return await manager.get_positions()


async def get_portfolio_summary(db: AsyncSession, robot_id: int) -> PortfolioSummary:
    """便捷函数：获取组合概览"""
    manager = PositionManager(db, robot_id)
    return await manager.get_portfolio_summary()


async def update_nav(db: AsyncSession, robot_id: int, nav_date: Optional[date] = None) -> float:
    """便捷函数：更新净值"""
    manager = PositionManager(db, robot_id)
    return await manager.update_nav(nav_date)
