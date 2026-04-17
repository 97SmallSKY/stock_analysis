"""
AHS（Adaptive Health Score）仓位管理系统

核心原则：
- AHS_SCORE ∈ [0, 100]，0 = 最健康，100 = 最危险
- 评分越高越危险，符合风险导向设计
- 使用滞后带防止状态频繁切换

参考：docs/lhb-quant-v2/RISK_CONTROL.md §三
"""
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional
from dataclasses import dataclass

from loguru import logger
from sqlalchemy import select, and_, func, desc
from sqlalchemy.ext.asyncio import AsyncSession

from backend import (
    Robot, RobotTrade, AhsHistory, DailyNav, TradeExecution
)


# ============================================================
# AHS 阈值常量
# ============================================================
AHS_THRESHOLD_CAUTIOUS = 21      # 进入谨慎模式阈值
AHS_THRESHOLD_CONSERVATIVE = 41  # 进入保守模式阈值
AHS_THRESHOLD_WARNING = 61       # 进入警戒模式阈值
AHS_THRESHOLD_STOP = 81          # 停止开仓阈值

AHS_HYSTERESIS = 5                # 滞后带宽度（分）
AHS_COLD_START_SCORE = 50        # 冷启动默认分数


# ============================================================
# 仓位映射表
# ============================================================
POSITION_MAPPING = {
    'normal': {'enter': 15, 'exit': 20, 'mult': 1.00},
    'cautious': {'enter': 35, 'exit': 40, 'mult': 0.85},
    'conservative': {'enter': 55, 'exit': 60, 'mult': 0.70},
    'warning': {'enter': 75, 'exit': 80, 'mult': 0.40},
    'stopped': {'enter': 81, 'exit': 60, 'mult': 0.00},  # 特殊：需要降到60才恢复
}


@dataclass
class AHSResult:
    """AHS计算结果"""
    ahs_score: float
    trading_mode: str
    position_mult: float
    win_rate: float
    avg_pnl: float
    consecutive_losses: int
    max_drawdown: float
    sample_count: int
    penalty: float = 0.0


def calculate_ahs(
    win_rate: float,
    avg_pnl: float,
    consecutive_loss: int,
    max_drawdown: float
) -> float:
    """
    AHS评分公式
    
    Args:
        win_rate: 近20笔交易胜率 [0, 1]
        avg_pnl: 近20笔交易平均收益率 [-1, +1]
        consecutive_loss: 连续亏损笔数 [0, 5+]
        max_drawdown: 最大回撤 [0, 1]
    
    Returns:
        AHS_SCORE: [0, 100]，越高越危险
    """
    # 因子1：胜率惩罚（胜率越低 → 越危险 → 分数越高）
    win_penalty = (1 - win_rate) * 100  # 0~100
    
    # 因子2：盈亏比惩罚（亏损越多 → 越危险 → 分数越高）
    # avg_pnl: -1 ~ +1，归一化到 0~100
    pnl_penalty = max(0.0, min(100.0, (1 - avg_pnl) * 50))  # 0~100
    
    # 因子3：连续亏损惩罚（连续亏损越多 → 越危险）
    loss_penalty = min(consecutive_loss / 5, 1.0) * 100  # 0~100
    
    # 因子4：回撤惩罚（回撤越大 → 越危险）
    # 15%回撤 = 满分，低于15%按比例计算
    drawdown_penalty = min(abs(max_drawdown) / 0.15, 1.0) * 100  # 0~100
    
    # 加权求和（总权重 = 1.0）
    ahs = (
        win_penalty * 0.25 +       # 胜率权重 25%
        pnl_penalty * 0.25 +       # 盈亏权重 25%
        loss_penalty * 0.30 +      # 连续亏损权重 30%（最重要的短期信号）
        drawdown_penalty * 0.20    # 回撤权重 20%
    )
    
    return max(0.0, min(100.0, ahs))


def get_cold_start_penalty(trade_count: int) -> float:
    """
    计算冷启动不确定性惩罚
    
    | 样本数 | 置信度 | 处理方式 |
    |--------|--------|----------|
    | 0 次   | 0%     | 固定 50 分 |
    | 1-4 次 | ≤15%   | 最多惩罚20分 |
    | 5-9 次 | 15%-40%| 最多惩罚10分 |
    | ≥10 次 | 100%   | 无惩罚 |
    """
    if trade_count == 0:
        return 50.0  # 返回固定分数，不额外计算
    elif trade_count < 5:
        return (1 - trade_count / 10) * 20  # 最多惩罚20分
    elif trade_count < 10:
        return (1 - trade_count / 20) * 10  # 最多惩罚10分
    else:
        return 0.0


def get_trading_mode(ahs_score: float, current_mode: str = 'normal') -> str:
    """
    获取交易模式（含滞后带逻辑）
    
    Args:
        ahs_score: 当前AHS分数
        current_mode: 当前交易模式
    
    Returns:
        新的交易模式
    """
    # 停止开仓特殊处理：需要降到60才能恢复
    if current_mode == 'stopped':
        if ahs_score <= AHS_THRESHOLD_STOP - AHS_HYSTERESIS * 4:  # 60分
            return 'cautious'  # 恢复后从谨慎模式开始
        return 'stopped'
    
    # 其他模式使用标准滞后带
    if ahs_score <= POSITION_MAPPING['normal']['exit'] and current_mode != 'normal':
        # 从其他模式恢复到正常
        if ahs_score <= 20:
            return 'normal'
    
    # 进入判断（使用 enter 阈值）
    if ahs_score > POSITION_MAPPING['stopped']['enter']:
        return 'stopped'
    elif ahs_score > POSITION_MAPPING['warning']['enter']:
        return 'warning'
    elif ahs_score > POSITION_MAPPING['conservative']['enter']:
        return 'conservative'
    elif ahs_score > POSITION_MAPPING['cautious']['enter']:
        return 'cautious'
    else:
        return 'normal'


def get_position_mult(trading_mode: str) -> float:
    """
    获取仓位系数
    
    Args:
        trading_mode: 交易模式
    
    Returns:
        仓位系数 [0, 1]
    """
    return POSITION_MAPPING.get(trading_mode, POSITION_MAPPING['normal'])['mult']


class AHSCalculator:
    """
    AHS计算器
    
    封装AHS评分计算和仓位映射逻辑
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
    
    async def get_recent_trades(self, limit: int = 20) -> list[RobotTrade]:
        """获取近N笔已完成交易"""
        result = await self.db.execute(
            select(RobotTrade).where(
                and_(
                    RobotTrade.robot_id == self.robot_id,
                    RobotTrade.status == 'closed',
                    RobotTrade.deleted_at.is_(None)
                )
            ).order_by(RobotTrade.sell_date.desc()).limit(limit)
        )
        return result.scalars().all()
    
    async def get_current_consecutive_losses(self) -> int:
        """
        获取当前连续亏损笔数
        从最近一笔往前数，直到遇到盈利交易为止
        """
        result = await self.db.execute(
            select(RobotTrade).where(
                and_(
                    RobotTrade.robot_id == self.robot_id,
                    RobotTrade.status == 'closed',
                    RobotTrade.deleted_at.is_(None)
                )
            ).order_by(RobotTrade.sell_date.desc()).limit(10)
        )
        trades = result.scalars().all()
        
        consecutive = 0
        for trade in trades:
            if trade.realized_pnl_ratio is None:
                continue
            if float(trade.realized_pnl_ratio) < 0:
                consecutive += 1
            else:
                break
        
        return min(consecutive, 5)  # 最多5笔
    
    async def get_max_drawdown(self, lookback_days: int = 20) -> float:
        """获取近N日最大回撤"""
        lookback_date = date.today() - timedelta(days=lookback_days)
        
        result = await self.db.execute(
            select(DailyNav).where(
                and_(
                    DailyNav.robot_id == self.robot_id,
                    DailyNav.nav_date >= lookback_date
                )
            ).order_by(DailyNav.nav_date)
        )
        navs = result.scalars().all()
        
        if len(navs) < 2:
            return 0.0
        
        # 计算最大回撤
        peak = float(navs[0].close_nav)
        max_dd = 0.0
        
        for nav in navs:
            current = float(nav.close_nav)
            if current > peak:
                peak = current
            dd = (peak - current) / peak if peak > 0 else 0
            max_dd = max(max_dd, dd)
        
        return max_dd
    
    async def get_current_mode(self) -> str:
        """获取当前交易模式"""
        result = await self.db.execute(
            select(AhsHistory).where(
                AhsHistory.robot_id == self.robot_id
            ).order_by(AhsHistory.score_date.desc()).limit(1)
        )
        ahs = result.scalar_one_or_none()
        
        if ahs is None:
            return 'normal'
        
        return ahs.trading_mode
    
    async def calculate(self) -> AHSResult:
        """
        计算AHS评分
        
        Returns:
            AHSResult: 计算结果
        """
        # 获取近20笔交易
        trades = await self.get_recent_trades(20)
        sample_count = len(trades)
        
        # 冷启动处理
        if sample_count == 0:
            logger.warning(f"[AHS] 机器人 {self.robot_id} 无历史交易，使用冷启动分数")
            return AHSResult(
                ahs_score=AHS_COLD_START_SCORE,
                trading_mode='cautious',
                position_mult=0.70,
                win_rate=0.5,
                avg_pnl=0.0,
                consecutive_losses=0,
                max_drawdown=0.0,
                sample_count=0,
                penalty=AHS_COLD_START_SCORE
            )
        
        # 计算胜率
        wins = sum(1 for t in trades if t.realized_pnl_ratio and float(t.realized_pnl_ratio) > 0)
        win_rate = wins / sample_count
        
        # 计算平均收益
        returns = [float(t.realized_pnl_ratio or 0) for t in trades]
        avg_pnl = sum(returns) / len(returns) if returns else 0
        
        # 获取连续亏损
        consecutive_losses = await self.get_current_consecutive_losses()
        
        # 获取最大回撤
        max_drawdown = await self.get_max_drawdown(20)
        
        # 计算原始AHS分数
        ahs_score = calculate_ahs(win_rate, avg_pnl, consecutive_losses, max_drawdown)
        
        # 计算冷启动惩罚
        penalty = get_cold_start_penalty(sample_count)
        ahs_final = min(100.0, ahs_score + penalty)
        
        # 获取当前模式
        current_mode = await self.get_current_mode()
        
        # 计算新交易模式
        trading_mode = get_trading_mode(ahs_final, current_mode)
        
        # 获取仓位系数
        position_mult = get_position_mult(trading_mode)
        
        logger.info(
            f"[AHS] 机器人 {self.robot_id}: "
            f"score={ahs_final:.1f}, mode={trading_mode}, mult={position_mult:.0%}, "
            f"win_rate={win_rate:.0%}, avg_pnl={avg_pnl:.2%}, "
            f"consec_loss={consecutive_losses}, max_dd={max_drawdown:.1%}"
        )
        
        return AHSResult(
            ahs_score=ahs_final,
            trading_mode=trading_mode,
            position_mult=position_mult,
            win_rate=win_rate,
            avg_pnl=avg_pnl,
            consecutive_losses=consecutive_losses,
            max_drawdown=max_drawdown,
            sample_count=sample_count,
            penalty=penalty
        )
    
    async def save(self, result: AHSResult, calc_date: date) -> AhsHistory:
        """
        保存AHS计算结果
        
        Args:
            result: 计算结果
            calc_date: 计算日期
        
        Returns:
            AhsHistory: 保存的记录
        """
        ahs = AhsHistory(
            robot_id=self.robot_id,
            score_date=calc_date,
            ahs_score=Decimal(str(result.ahs_score)),
            consecutive_losses=result.consecutive_losses,
            weekly_pnl_ratio=Decimal(str(result.avg_pnl)),
            win_rate=Decimal(str(result.win_rate)),
            profit_loss_ratio=Decimal(str(result.win_rate)),
            loss_penalty=Decimal(str(result.penalty)) if result.penalty > 0 else None,
            winrate_penalty=Decimal(str((1 - result.win_rate) * 25)) if result.win_rate < 1 else None,
            plr_penalty=None,
            trading_mode=result.trading_mode
        )
        
        self.db.add(ahs)
        await self.db.flush()
        
        # 更新机器人表
        robot_result = await self.db.execute(
            select(Robot).where(Robot.id == self.robot_id)
        )
        robot = robot_result.scalar_one_or_none()
        
        if robot:
            # 更新字段（如果有）
            if hasattr(robot, 'ahs_score'):
                robot.ahs_score = result.ahs_score
            if hasattr(robot, 'position_mult'):
                robot.position_mult = result.position_mult
        
        logger.info(f"[AHS] 保存成功: robot_id={self.robot_id}, date={calc_date}")
        
        return ahs


async def calculate_ahs_for_robot(
    db: AsyncSession,
    robot_id: int,
    calc_date: Optional[date] = None
) -> AHSResult:
    """
    为机器人计算并保存AHS评分
    
    Args:
        db: 数据库会话
        robot_id: 机器人ID
        calc_date: 计算日期（默认今天）
    
    Returns:
        AHSResult: 计算结果
    """
    calc_date = calc_date or date.today()
    
    calculator = AHSCalculator(db, robot_id)
    result = await calculator.calculate()
    await calculator.save(result, calc_date)
    
    return result


async def get_position_mult(db: AsyncSession, robot_id: int) -> float:
    """
    获取机器人的仓位系数
    
    Args:
        db: 数据库会话
        robot_id: 机器人ID
    
    Returns:
        仓位系数 [0, 1]
    """
    result = await db.execute(
        select(AhsHistory).where(
            AhsHistory.robot_id == robot_id
        ).order_by(AhsHistory.score_date.desc()).limit(1)
    )
    ahs = result.scalar_one_or_none()
    
    if ahs is None:
        # 冷启动，返回70%仓位
        return 0.70
    
    return get_position_mult(ahs.trading_mode)


async def get_trading_mode(db: AsyncSession, robot_id: int) -> str:
    """
    获取机器人的交易模式
    
    Args:
        db: 数据库会话
        robot_id: 机器人ID
    
    Returns:
        交易模式
    """
    result = await db.execute(
        select(AhsHistory).where(
            AhsHistory.robot_id == robot_id
        ).order_by(AhsHistory.score_date.desc()).limit(1)
    )
    ahs = result.scalar_one_or_none()
    
    if ahs is None:
        return 'cautious'
    
    return ahs.trading_mode
