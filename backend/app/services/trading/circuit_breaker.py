"""
熔断器服务

职责：
1. 单日亏损熔断
2. 数据源熔断
3. 恢复机制

参考：docs/lhb-quant-v2/RISK_CONTROL.md §四
"""
from datetime import date, datetime, timedelta
from typing import Optional
from dataclasses import dataclass
from enum import Enum

from loguru import logger
from sqlalchemy import select, and_, func
from sqlalchemy.ext.asyncio import AsyncSession

from backend import Robot, DailyNav, RobotTrade


# ============================================================
# 常量定义
# ============================================================
class CircuitState(str, Enum):
    """熔断状态"""
    CLOSED = 'closed'          # 正常（未熔断）
    OPEN = 'open'             # 熔断中
    HALF_OPEN = 'half_open'   # 半开（恢复观察）


# 熔断阈值
DAILY_LOSS_THRESHOLD = -0.05      # 单日亏损 >5% 熔断
CONTINUOUS_LOSS_DAYS = 3          # 连续亏损天数
MAX_DRAWDOWN_THRESHOLD = -0.20    # 最大回撤 >20% 熔断

# 熔断时长
CIRCUIT_BREAK_DURATION = timedelta(days=1)  # 熔断持续时间


@dataclass
class CircuitStatus:
    """熔断状态"""
    state: CircuitState
    reason: Optional[str] = None
    triggered_at: Optional[datetime] = None
    will_resume_at: Optional[datetime] = None
    details: dict = None
    
    def __post_init__(self):
        if self.details is None:
            self.details = {}
    
    @property
    def is_active(self) -> bool:
        """是否处于熔断状态"""
        return self.state != CircuitState.CLOSED


class CircuitBreaker:
    """
    熔断器
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
        
        # 熔断状态缓存
        self._state: Optional[CircuitStatus] = None
        self._last_check: Optional[datetime] = None
    
    async def _get_robot(self) -> Robot:
        """获取机器人"""
        result = await self.db.execute(
            select(Robot).where(Robot.id == self.robot_id)
        )
        robot = result.scalar_one_or_none()
        if robot is None:
            raise ValueError(f"机器人不存在: {self.robot_id}")
        return robot
    
    async def _check_daily_loss(self) -> tuple[bool, Optional[str]]:
        """
        检查单日亏损熔断
        
        Returns:
            (triggered, reason)
        """
        today = date.today()
        
        # 获取今日和昨日的净值
        result = await self.db.execute(
            select(DailyNav).where(
                and_(
                    DailyNav.robot_id == self.robot_id,
                    DailyNav.nav_date.in_([today, today - timedelta(days=1)])
                )
            ).order_by(DailyNav.nav_date.desc())
        )
        navs = result.scalars().all()
        
        if len(navs) < 2:
            return False, None
        
        # 计算日收益率
        today_nav = float(navs[0].close_nav) if navs[0].nav_date == today else float(navs[1].close_nav)
        yesterday_nav = float(navs[1].close_nav) if navs[0].nav_date == today else float(navs[0].close_nav)
        
        if yesterday_nav <= 0:
            return False, None
        
        daily_return = (today_nav - yesterday_nav) / yesterday_nav
        
        if daily_return < DAILY_LOSS_THRESHOLD:
            return True, f"单日亏损 {daily_return:.1%} 超过阈值 {DAILY_LOSS_THRESHOLD:.0%}"
        
        return False, None
    
    async def _check_continuous_loss(self) -> tuple[bool, Optional[str]]:
        """
        检查连续亏损熔断
        
        Returns:
            (triggered, reason)
        """
        # 获取近N天的净值
        start_date = date.today() - timedelta(days=CONTINUOUS_LOSS_DAYS + 5)
        
        result = await self.db.execute(
            select(DailyNav).where(
                and_(
                    DailyNav.robot_id == self.robot_id,
                    DailyNav.nav_date >= start_date
                )
            ).order_by(DailyNav.nav_date.desc())
        )
        navs = result.scalars().all()
        
        if len(navs) < CONTINUOUS_LOSS_DAYS + 1:
            return False, None
        
        # 检查连续亏损
        consecutive_losses = 0
        for i in range(len(navs) - 1):
            today_nav = float(navs[i].close_nav)
            yesterday_nav = float(navs[i + 1].close_nav)
            
            if yesterday_nav > 0:
                daily_return = (today_nav - yesterday_nav) / yesterday_nav
                if daily_return < 0:
                    consecutive_losses += 1
                else:
                    break
        
        if consecutive_losses >= CONTINUOUS_LOSS_DAYS:
            return True, f"连续亏损 {consecutive_losses} 天"
        
        return False, None
    
    async def _check_max_drawdown(self) -> tuple[bool, Optional[str]]:
        """
        检查最大回撤熔断
        
        Returns:
            (triggered, reason)
        """
        # 获取当前净值
        result = await self.db.execute(
            select(DailyNav).where(
                DailyNav.robot_id == self.robot_id
            ).order_by(DailyNav.nav_date.desc()).limit(1)
        )
        nav = result.scalar_one_or_none()
        
        if not nav:
            return False, None
        
        current_nav = float(nav.close_nav)
        
        # 获取历史最高净值
        result = await self.db.execute(
            select(func.max(DailyNav.close_nav)).where(
                DailyNav.robot_id == self.robot_id
            )
        )
        max_nav = result.scalar()
        
        if max_nav and max_nav > 0:
            drawdown = (max_nav - current_nav) / max_nav
            if drawdown > abs(MAX_DRAWDOWN_THRESHOLD):
                return True, f"最大回撤 {drawdown:.1%} 超过阈值 {abs(MAX_DRAWDOWN_THRESHOLD):.0%}"
        
        return False, None
    
    async def check(self) -> CircuitStatus:
        """
        检查熔断状态
        
        Returns:
            CircuitStatus: 熔断状态
        """
        # 如果已有熔断状态，检查是否需要恢复
        if self._state and self._state.is_active:
            # 检查是否已过熔断期
            if self._state.will_resume_at and datetime.now() >= self._state.will_resume_at:
                logger.info(f"[Circuit] 熔断期结束，进入半开状态: robot={self.robot_id}")
                self._state.state = CircuitState.HALF_OPEN
            else:
                return self._state
        
        # 检查各项熔断条件
        triggered, reason = await self._check_daily_loss()
        if not triggered:
            triggered, reason = await self._check_continuous_loss()
        if not triggered:
            triggered, reason = await self._check_max_drawdown()
        
        if triggered:
            self._state = CircuitStatus(
                state=CircuitState.OPEN,
                reason=reason,
                triggered_at=datetime.now(),
                will_resume_at=datetime.now() + CIRCUIT_BREAK_DURATION
            )
            
            logger.warning(f"[Circuit] 熔断触发: robot={self.robot_id}, reason={reason}")
            
            return self._state
        
        # 半开状态检查
        if self._state and self._state.state == CircuitState.HALF_OPEN:
            # 在半开状态，如果检查通过，恢复正常
            self._state = CircuitStatus(state=CircuitState.CLOSED)
            logger.info(f"[Circuit] 熔断恢复: robot={self.robot_id}")
        
        # 正常状态
        self._state = CircuitStatus(state=CircuitState.CLOSED)
        self._last_check = datetime.now()
        
        return self._state
    
    async def is_allowed(self) -> tuple[bool, Optional[str]]:
        """
        检查是否允许交易
        
        Returns:
            (allowed, reason): allowed=True 表示允许交易
        """
        status = await self.check()
        
        if status.state == CircuitState.CLOSED:
            return True, None
        elif status.state == CircuitState.HALF_OPEN:
            # 半开状态允许观察性交易（减半仓位）
            return True, "半开状态，建议减半仓位"
        else:
            return False, status.reason or "熔断中"
    
    async def get_status_summary(self) -> dict:
        """
        获取熔断状态摘要
        
        Returns:
            状态摘要字典
        """
        status = await self.check()
        
        return {
            'state': status.state.value,
            'is_active': status.is_active,
            'reason': status.reason,
            'triggered_at': str(status.triggered_at) if status.triggered_at else None,
            'will_resume_at': str(status.will_resume_at) if status.will_resume_at else None,
        }


async def check_circuit_breaker(
    db: AsyncSession,
    robot_id: int
) -> tuple[bool, Optional[str]]:
    """
    便捷函数：检查熔断
    
    Returns:
        (allowed, reason): allowed=True 表示允许交易
    """
    breaker = CircuitBreaker(db, robot_id)
    return await breaker.is_allowed()


async def get_circuit_status(
    db: AsyncSession,
    robot_id: int
) -> CircuitStatus:
    """
    便捷函数：获取熔断状态
    
    Returns:
        CircuitStatus: 熔断状态
    """
    breaker = CircuitBreaker(db, robot_id)
    return await breaker.check()
