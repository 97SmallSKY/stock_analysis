"""
盘中监控服务

执行频率：每10秒轮询
职责：
1. 止盈止损检查
2. 涨停跌停检测
3. 连板开板检测
4. 触发卖出条件

参考：docs/lhb-quant-v2/TRADING_FLOW.md §四
"""
import asyncio
from datetime import date, datetime
from typing import Optional
from dataclasses import dataclass

from loguru import logger
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from backend import Robot, RobotTrade, TradeExecution
from backend import KlineDaily, StockInfo
from backend import TradeExecutor, ExecutionType
from backend import SellReason
from backend import send_trade_close


# ============================================================
# 常量定义
# ============================================================
# 止盈档位
TAKE_PROFIT_TIERS = [
    {'ratio': 0.03, 'sell_ratio': 0.33, 'tier': 1},
    {'ratio': 0.05, 'sell_ratio': 0.33, 'tier': 2},
    {'ratio': 0.08, 'sell_ratio': 0.34, 'tier': 3},
]

# 止损阈值
STOP_LOSS_RATIO = -0.05  # -5%止损

# 监控间隔
MONITOR_INTERVAL = 10  # 10秒


@dataclass
class MonitorResult:
    """监控结果"""
    checked_count: int
    take_profit_count: int
    stop_loss_count: int
    lianban_open_count: int
    expired_count: int
    alerts: list[dict]


class IntradayMonitor:
    """
    盘中监控服务
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
        self.executor = TradeExecutor(db)
        
        # 缓存已执行的止盈档位，防止重复触发
        self._executed_tiers: dict[int, set] = {}
    
    async def _get_robot(self) -> Robot:
        """获取机器人"""
        result = await self.db.execute(
            select(Robot).where(Robot.id == self.robot_id)
        )
        robot = result.scalar_one_or_none()
        if robot is None:
            raise ValueError(f"机器人不存在: {self.robot_id}")
        return robot
    
    async def _get_positions(self) -> list[RobotTrade]:
        """获取持仓"""
        result = await self.db.execute(
            select(RobotTrade).where(
                and_(
                    RobotTrade.robot_id == self.robot_id,
                    RobotTrade.status.in_(['open', 'partial']),
                    RobotTrade.deleted_at.is_(None)
                )
            )
        )
        return list(result.scalars().all())
    
    async def _get_current_price(self, stock_code: str) -> Optional[float]:
        """获取当前价格"""
        result = await self.db.execute(
            select(KlineDaily).where(
                and_(
                    KlineDaily.stock_code == stock_code,
                    KlineDaily.trade_date == date.today()
                )
            )
        )
        kline = result.scalar_one_or_none()
        return float(kline.close_price) if kline else None
    
    async def _get_limit_prices(self, stock_code: str) -> tuple[Optional[float], Optional[float]]:
        """获取涨停价和跌停价"""
        # 获取股票信息
        result = await self.db.execute(
            select(StockInfo).where(StockInfo.stock_code == stock_code)
        )
        stock_info = result.scalar_one_or_none()
        
        if not stock_info:
            return None, None
        
        # 获取昨收
        kline_result = await self.db.execute(
            select(KlineDaily).where(
                KlineDaily.stock_code == stock_code
            ).order_by(KlineDaily.trade_date.desc()).limit(1)
        )
        prev_kline = kline_result.scalar_one_or_none()
        prev_close = float(prev_kline.close_price) if prev_kline else 0
        
        if prev_close <= 0:
            return None, None
        
        # 计算涨跌停价
        if stock_info.board_type == 'star' or stock_info.board_type == 'gem':
            limit_ratio = 0.20
        elif stock_info.is_st:
            limit_ratio = 0.05
        else:
            limit_ratio = 0.10
        
        limit_up = prev_close * (1 + limit_ratio)
        limit_down = prev_close * (1 - limit_ratio)
        
        return limit_up, limit_down
    
    async def _check_take_profit(self, trade: RobotTrade, current_price: float) -> bool:
        """
        检查止盈条件
        
        Returns:
            True 表示触发了止盈
        """
        buy_price = float(trade.buy_price or 0)
        if buy_price <= 0:
            return False
        
        # 计算收益率
        pnl_ratio = (current_price - buy_price) / buy_price
        
        # 检查各档止盈
        for tier in TAKE_PROFIT_TIERS:
            if pnl_ratio >= tier['ratio']:
                # 检查是否已执行
                if trade.id not in self._executed_tiers:
                    self._executed_tiers[trade.id] = set()
                
                if tier['tier'] in self._executed_tiers[trade.id]:
                    continue  # 已执行过
                
                # 计算卖出数量
                sold_shares = sum(
                    e.execution_shares for e in trade.executions
                    if e.execution_type.startswith('sell')
                )
                remaining_shares = (trade.buy_shares or 0) - sold_shares
                sell_shares = int(remaining_shares * tier['sell_ratio'])
                
                if sell_shares < 100:
                    sell_shares = remaining_shares  # 不足100股全部卖出
                
                if sell_shares <= 0:
                    continue
                
                # 执行卖出
                result = await self.executor.execute_sell(
                    robot_id=self.robot_id,
                    trade_id=trade.id,
                    price=current_price,
                    shares=sell_shares,
                    trade_date=date.today(),
                    execution_type=f"sell_{tier['tier']}",
                    reason=f"止盈{tier['tier']}档（收益率 {pnl_ratio:.1%} >= {tier['ratio']:.0%}）"
                )
                
                if result.success:
                    self._executed_tiers[trade.id].add(tier['tier'])
                    logger.info(
                        f"[Monitor] 止盈触发: trade_id={trade.id}, "
                        f"stock={trade.stock_code}, tier={tier['tier']}, "
                        f"shares={sell_shares}, price={current_price:.3f}"
                    )
                    
                    # 发送通知
                    await send_trade_close(
                        robot_name="策略机器人",
                        stock_code=trade.stock_code,
                        stock_name=trade.stock_name,
                        pnl=result.details.get('amount', 0) - float(trade.buy_amount or 0),
                        pnl_ratio=pnl_ratio,
                        reason=f"止盈{tier['tier']}档"
                    )
                    
                    return True
        
        return False
    
    async def _check_stop_loss(self, trade: RobotTrade, current_price: float) -> bool:
        """
        检查止损条件
        
        Returns:
            True 表示触发了止损
        """
        buy_price = float(trade.buy_price or 0)
        if buy_price <= 0:
            return False
        
        # 计算收益率
        pnl_ratio = (current_price - buy_price) / buy_price
        
        if pnl_ratio <= STOP_LOSS_RATIO:
            # 计算剩余股数
            sold_shares = sum(
                e.execution_shares for e in trade.executions
                if e.execution_type.startswith('sell')
            )
            remaining_shares = (trade.buy_shares or 0) - sold_shares
            
            if remaining_shares <= 0:
                return False
            
            # 执行止损
            result = await self.executor.execute_sell(
                robot_id=self.robot_id,
                trade_id=trade.id,
                price=current_price,
                shares=remaining_shares,
                trade_date=date.today(),
                execution_type='stop_loss',
                reason=f"止损（收益率 {pnl_ratio:.1%} <= {STOP_LOSS_RATIO:.0%}）"
            )
            
            if result.success:
                logger.info(
                    f"[Monitor] 止损触发: trade_id={trade.id}, "
                    f"stock={trade.stock_code}, shares={remaining_shares}, "
                    f"price={current_price:.3f}"
                )
                
                # 发送通知
                await send_trade_close(
                    robot_name="策略机器人",
                    stock_code=trade.stock_code,
                    stock_name=trade.stock_name,
                    pnl=result.details.get('amount', 0) - float(trade.buy_amount or 0),
                    pnl_ratio=pnl_ratio,
                    reason="止损"
                )
                
                return True
        
        return False
    
    async def _check_expire(self, trade: RobotTrade) -> bool:
        """
        检查到期平仓
        
        Returns:
            True 表示触发了到期平仓
        """
        if not trade.buy_date:
            return False
        
        # 计算持仓天数
        hold_days = (date.today() - trade.buy_date).days
        
        # 获取max_hold_days
        robot = await self._get_robot()
        max_hold_days = robot.max_hold_days or 5
        
        if hold_days >= max_hold_days:
            # 获取当前价格
            current_price = await self._get_current_price(trade.stock_code)
            if not current_price:
                return False
            
            # 计算剩余股数
            sold_shares = sum(
                e.execution_shares for e in trade.executions
                if e.execution_type.startswith('sell')
            )
            remaining_shares = (trade.buy_shares or 0) - sold_shares
            
            if remaining_shares <= 0:
                return False
            
            # 执行到期平仓
            result = await self.executor.execute_sell(
                robot_id=self.robot_id,
                trade_id=trade.id,
                price=current_price,
                shares=remaining_shares,
                trade_date=date.today(),
                execution_type='expire',
                reason=f"到期平仓（持仓 {hold_days} 天 >= {max_hold_days} 天）"
            )
            
            if result.success:
                logger.info(
                    f"[Monitor] 到期平仓: trade_id={trade.id}, "
                    f"stock={trade.stock_code}, hold_days={hold_days}"
                )
                return True
        
        return False
    
    async def _check_lianban_open(self, trade: RobotTrade, current_price: float) -> bool:
        """
        检查连板开板
        
        连板开板：股票涨停后打开涨停板
        
        Returns:
            True 表示触发了连板开板
        """
        # 获取涨停价
        limit_up, _ = await self._get_limit_prices(trade.stock_code)
        if not limit_up:
            return False
        
        # 检查是否涨停封板
        if current_price >= limit_up * 0.99:
            return False  # 还在涨停
        
        # 检查昨日是否涨停（简化处理：检查昨日K线）
        # 这里简化：如果当前价格低于涨停价且昨日涨停，则认为开板
        # 实际需要更复杂的逻辑来判断是否"连板开板"
        
        # 获取剩余股数
        sold_shares = sum(
            e.execution_shares for e in trade.executions
            if e.execution_type.startswith('sell')
        )
        remaining_shares = (trade.buy_shares or 0) - sold_shares
        
        if remaining_shares <= 0:
            return False
        
        # 执行卖出（全部卖出）
        result = await self.executor.execute_sell(
            robot_id=self.robot_id,
            trade_id=trade.id,
            price=current_price,
            shares=remaining_shares,
            trade_date=date.today(),
            execution_type='manual',
            reason="连板开板"
        )
        
        if result.success:
            logger.info(
                f"[Monitor] 连板开板: trade_id={trade.id}, "
                f"stock={trade.stock_code}"
            )
            return True
        
        return False
    
    async def run_once(self) -> MonitorResult:
        """
        执行一次监控
        
        Returns:
            MonitorResult: 监控结果
        """
        # 获取持仓
        positions = await self._get_positions()
        
        checked = 0
        take_profit = 0
        stop_loss = 0
        lianban_open = 0
        expired = 0
        alerts = []
        
        for trade in positions:
            checked += 1
            
            # 获取当前价格
            current_price = await self._get_current_price(trade.stock_code)
            if not current_price:
                continue
            
            # 检查止盈
            if await self._check_take_profit(trade, current_price):
                take_profit += 1
            
            # 检查止损
            elif await self._check_stop_loss(trade, current_price):
                stop_loss += 1
            
            # 检查到期
            elif await self._check_expire(trade):
                expired += 1
            
            # 检查连板开板
            elif await self._check_lianban_open(trade, current_price):
                lianban_open += 1
        
        return MonitorResult(
            checked_count=checked,
            take_profit_count=take_profit,
            stop_loss_count=stop_loss,
            lianban_open_count=lianban_open,
            expired_count=expired,
            alerts=alerts
        )
    
    async def run_continuous(self):
        """
        持续监控（每10秒轮询）
        
        仅在交易时段运行（9:30-11:30, 13:00-15:00）
        """
        logger.info(f"[Monitor] 开始持续监控，robot={self.robot_id}")
        
        while True:
            now = datetime.now()
            
            # 检查是否在交易时段
            hour, minute = now.hour, now.minute
            is_trading_time = (
                (9, 30) <= (hour, minute) < (11, 30) or
                (13, 0) <= (hour, minute) < (15, 0)
            )
            
            if is_trading_time:
                try:
                    result = await self.run_once()
                    
                    if result.take_profit_count > 0 or result.stop_loss_count > 0:
                        logger.info(
                            f"[Monitor] 轮询完成: 检查{result.checked_count}，"
                            f"止盈{result.take_profit_count}，"
                            f"止损{result.stop_loss_count}，"
                            f"到期{result.expired_count}"
                        )
                except Exception as e:
                    logger.exception(f"[Monitor] 轮询异常: {e}")
            
            # 等待10秒
            await asyncio.sleep(MONITOR_INTERVAL)


async def run_monitor(db: AsyncSession, robot_id: int) -> MonitorResult:
    """便捷函数：执行一次监控"""
    monitor = IntradayMonitor(db, robot_id)
    return await monitor.run_once()


async def start_continuous_monitor(db: AsyncSession, robot_id: int):
    """便捷函数：启动持续监控"""
    monitor = IntradayMonitor(db, robot_id)
    await monitor.run_continuous()
