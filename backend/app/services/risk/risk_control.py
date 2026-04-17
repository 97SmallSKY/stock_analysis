"""
风控模块 - 系统级硬限制和黑名单管理

设计原则：
1. 系统级硬限制是红线，独立于策略，不可被覆盖
2. 所有买入前必须调用 check_before_buy
3. 黑名单股票立即触发持仓平仓

参考：docs/lhb-quant-v2/RISK_CONTROL.md §二
"""
from datetime import datetime
from typing import Optional
from dataclasses import dataclass
from enum import Enum

from loguru import logger
from sqlalchemy import select, and_, func
from sqlalchemy.ext.asyncio import AsyncSession

from backend import Robot, RobotTrade
from backend import StockInfo


# ============================================================
# 常量定义（硬编码红线，不可配置覆盖）
# ============================================================

# 组合层面硬性限制
MAX_TOTAL_POSITIONS = 5          # 最多同时持有5只
MAX_SINGLE_STOCK_RATIO = 0.40    # 单只不超过总资产40%
MAX_DAILY_LOSS_RATIO = 0.05      # 单日组合亏损 >5% 停止新开仓
MAX_DRAWDOWN_HARD_STOP = 0.20    # 组合回撤 >20% 全部平仓

# 单笔交易限制
MIN_BUY_AMOUNT = 1000           # 最低买入金额（元）
MIN_BUY_SHARES = 100            # 最低买入股数
MAX_SLIPPAGE_TOLERANCE = 0.02   # 最大滑点容忍度（2%）


class RiskLevel(str, Enum):
    """风险等级"""
    LOW = 'low'
    MEDIUM = 'medium'
    HIGH = 'high'
    CRITICAL = 'critical'


@dataclass
class RiskCheckResult:
    """风控检查结果"""
    passed: bool
    reason: Optional[str] = None
    level: RiskLevel = RiskLevel.LOW
    details: dict = None
    
    def __post_init__(self):
        if self.details is None:
            self.details = {}


@dataclass
class RiskAlert:
    """风控告警"""
    robot_id: int
    level: RiskLevel
    title: str
    message: str
    timestamp: datetime
    data: dict = None
    
    def __post_init__(self):
        if self.data is None:
            self.data = {}
        self.timestamp = datetime.now()


class RiskControl:
    """
    风控模块主入口
    
    实现系统级硬限制检查和黑名单管理
    """
    
    def __init__(self, db: AsyncSession):
        """
        初始化
        
        Args:
            db: 数据库会话
        """
        self.db = db
    
    async def get_robot_assets(self, robot_id: int) -> dict:
        """
        获取机器人资产概况
        
        Returns:
            {
                'cash_balance': 现金余额,
                'position_value': 持仓市值,
                'total_assets': 总资产,
                'cumulative_loss': 累计亏损比例,
                'initial_capital': 初始资金
            }
        """
        # 查询机器人
        result = await self.db.execute(
            select(Robot).where(Robot.id == robot_id)
        )
        robot = result.scalar_one_or_none()
        
        if robot is None:
            raise ValueError(f"机器人不存在: {robot_id}")
        
        # 计算持仓市值
        position_result = await self.db.execute(
            select(RobotTrade).where(
                and_(
                    RobotTrade.robot_id == robot_id,
                    RobotTrade.status.in_(['open', 'partial']),
                    RobotTrade.deleted_at.is_(None)
                )
            )
        )
        positions = position_result.scalars().all()
        
        # 简化计算：使用买入金额作为持仓市值（实际应使用当前市价）
        position_value = sum(
            float(p.buy_amount or 0) for p in positions
        )
        
        cash_balance = float(robot.cash_balance)
        total_assets = cash_balance + position_value
        initial_capital = float(robot.initial_capital)
        cumulative_loss = 1 - (total_assets / initial_capital) if initial_capital > 0 else 0
        
        return {
            'cash_balance': cash_balance,
            'position_value': position_value,
            'total_assets': total_assets,
            'cumulative_loss': cumulative_loss,
            'initial_capital': initial_capital,
            'position_count': len(positions)
        }
    
    async def check_before_buy(
        self,
        robot_id: int,
        stock_code: str,
        buy_amount: float
    ) -> RiskCheckResult:
        """
        买入前风控检查
        
        Args:
            robot_id: 机器人ID
            stock_code: 股票代码
            buy_amount: 买入金额
        
        Returns:
            RiskCheckResult: 检查结果
        """
        logger.debug(
            f"[RiskControl] 检查买入: robot={robot_id}, "
            f"stock={stock_code}, amount={buy_amount}"
        )
        
        # 获取资产信息
        assets = await self.get_robot_assets(robot_id)
        total_assets = assets['total_assets']
        
        # 检查1：最低买入金额
        if buy_amount < MIN_BUY_AMOUNT:
            return RiskCheckResult(
                passed=False,
                reason=f"买入金额 {buy_amount:.2f} 低于最低限制 {MIN_BUY_AMOUNT} 元",
                level=RiskLevel.LOW,
                details={'buy_amount': buy_amount, 'min_amount': MIN_BUY_AMOUNT}
            )
        
        # 检查2：单笔买入上限（总资产20%）
        single_limit = total_assets * MAX_SINGLE_STOCK_RATIO / 2  # 20%是单日总上限，单笔用一半
        if buy_amount > single_limit:
            return RiskCheckResult(
                passed=False,
                reason=f"单笔买入 {buy_amount:.2f} 超过上限 {single_limit:.2f}",
                level=RiskLevel.MEDIUM,
                details={'buy_amount': buy_amount, 'limit': single_limit}
            )
        
        # 检查3：现金充足
        if buy_amount > assets['cash_balance']:
            return RiskCheckResult(
                passed=False,
                reason=f"可用现金 {assets['cash_balance']:.2f} 不足",
                level=RiskLevel.MEDIUM,
                details={'buy_amount': buy_amount, 'cash': assets['cash_balance']}
            )
        
        # 检查4：持仓数量限制
        if assets['position_count'] >= MAX_TOTAL_POSITIONS:
            return RiskCheckResult(
                passed=False,
                reason=f"持仓数量已达上限 {MAX_TOTAL_POSITIONS}",
                level=RiskLevel.MEDIUM,
                details={'position_count': assets['position_count']}
            )
        
        # 检查5：累计亏损停机
        if assets['cumulative_loss'] >= MAX_DRAWDOWN_HARD_STOP:
            return RiskCheckResult(
                passed=False,
                reason=f"累计亏损 {assets['cumulative_loss']:.1%} 已达停机阈值",
                level=RiskLevel.CRITICAL,
                details={'cumulative_loss': assets['cumulative_loss']}
            )
        
        # 检查6：黑名单检查
        is_blacklisted = await self.check_blacklist(stock_code)
        if is_blacklisted:
            return RiskCheckResult(
                passed=False,
                reason=f"股票 {stock_code} 在黑名单中",
                level=RiskLevel.HIGH,
                details={'stock_code': stock_code, 'blacklisted': True}
            )
        
        # 检查7：集中度上限（同股票多笔需合并计算）
        concentration_ok, concentration_reason = await self._check_concentration(
            robot_id, stock_code, buy_amount, total_assets
        )
        if not concentration_ok:
            return RiskCheckResult(
                passed=False,
                reason=concentration_reason,
                level=RiskLevel.HIGH,
                details={'stock_code': stock_code}
            )
        
        logger.debug(f"[RiskControl] 检查通过")
        return RiskCheckResult(passed=True)
    
    async def _check_concentration(
        self,
        robot_id: int,
        stock_code: str,
        new_buy_amount: float,
        total_assets: float
    ) -> tuple[bool, Optional[str]]:
        """检查单只股票集中度"""
        result = await self.db.execute(
            select(func.sum(RobotTrade.buy_amount)).where(
                and_(
                    RobotTrade.robot_id == robot_id,
                    RobotTrade.stock_code == stock_code,
                    RobotTrade.status.in_(['open', 'partial']),
                    RobotTrade.deleted_at.is_(None)
                )
            )
        )
        existing_amount = float(result.scalar() or 0)
        
        total_position = existing_amount + new_buy_amount
        concentration_ratio = total_position / total_assets if total_assets > 0 else 0
        
        if concentration_ratio > MAX_SINGLE_STOCK_RATIO:
            return False, (
                f"持仓 {stock_code} 集中度 {concentration_ratio:.1%} "
                f"超过上限 {MAX_SINGLE_STOCK_RATIO:.0%}"
            )
        
        return True, None
    
    async def check_blacklist(self, stock_code: str) -> bool:
        """
        检查股票是否在黑名单
        
        Args:
            stock_code: 股票代码
        
        Returns:
            True 表示在黑名单
        """
        # 检查 ST 状态
        stock_result = await self.db.execute(
            select(StockInfo).where(StockInfo.stock_code == stock_code)
        )
        stock_info = stock_result.scalar_one_or_none()
        
        if stock_info and stock_info.is_st:
            return True
        
        # 检查是否退市
        if stock_info and not stock_info.is_active:
            return True
        
        # TODO: 检查自定义黑名单表（stock_blacklist）
        
        return False
    
    async def monitor_portfolio(self, robot_id: int) -> list[RiskAlert]:
        """
        实时监控组合风险
        
        Returns:
            风险告警列表
        """
        alerts = []
        assets = await self.get_robot_assets(robot_id)
        
        # 告警1：累计亏损接近停机阈值
        if assets['cumulative_loss'] >= MAX_DRAWDOWN_HARD_STOP * 0.75:
            alerts.append(RiskAlert(
                robot_id=robot_id,
                level=RiskLevel.HIGH,
                title='累计亏损预警',
                message=f"累计亏损 {assets['cumulative_loss']:.1%}，接近停机阈值",
                data={'cumulative_loss': assets['cumulative_loss']}
            ))
        
        # 告警2：持仓数量接近上限
        if assets['position_count'] >= MAX_TOTAL_POSITIONS - 1:
            alerts.append(RiskAlert(
                robot_id=robot_id,
                level=RiskLevel.MEDIUM,
                title='持仓数量预警',
                message=f"持仓 {assets['position_count']} 只，接近上限",
                data={'position_count': assets['position_count']}
            ))
        
        # 告警3：现金不足
        min_cash_for_new_position = 10000  # 至少需要1万才能开新仓
        if assets['cash_balance'] < min_cash_for_new_position:
            alerts.append(RiskAlert(
                robot_id=robot_id,
                level=RiskLevel.MEDIUM,
                title='现金不足',
                message=f"可用现金 {assets['cash_balance']:.2f} 不足",
                data={'cash_balance': assets['cash_balance']}
            ))
        
        if alerts:
            logger.warning(f"[RiskControl] 机器人 {robot_id} 有 {len(alerts)} 条告警")
        
        return alerts
    
    async def get_hard_limits_summary(self, robot_id: int) -> dict:
        """
        获取硬限制状态摘要
        
        Returns:
            各项限制的当前值和阈值
        """
        assets = await self.get_robot_assets(robot_id)
        
        return {
            'position_count': {
                'current': assets['position_count'],
                'limit': MAX_TOTAL_POSITIONS,
                'usage': assets['position_count'] / MAX_TOTAL_POSITIONS
            },
            'cumulative_loss': {
                'current': assets['cumulative_loss'],
                'limit': MAX_DRAWDOWN_HARD_STOP,
                'usage': assets['cumulative_loss'] / MAX_DRAWDOWN_HARD_STOP
            },
            'cash_balance': {
                'current': assets['cash_balance'],
                'min_required': MIN_BUY_AMOUNT,
                'sufficient': assets['cash_balance'] >= MIN_BUY_AMOUNT
            }
        }


# ============================================================
# 便捷函数
# ============================================================
async def check_hard_limits(
    db: AsyncSession,
    robot_id: int,
    stock_code: str,
    buy_amount: float
) -> tuple[bool, str]:
    """
    快速检查硬限制
    
    Returns:
        (passed, reason): passed=True 表示通过检查
    """
    rc = RiskControl(db)
    result = await rc.check_before_buy(robot_id, stock_code, buy_amount)
    return result.passed, result.reason or ""
