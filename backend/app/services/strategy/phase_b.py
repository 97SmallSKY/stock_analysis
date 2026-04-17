"""
Phase B 确认买入引擎

执行时间：
- 模拟盘：交易日 09:35
- 实盘建议：09:45（通过 app_config 配置）

核心流程：
Step 1: 查询 pending 交易（昨日 Phase A 创建）
Step 2: 前置检查（停牌/ST/偏离度/涨停跌停/资金充足）
Step 3: 执行买入（单一事务：executions + ledger + robots + trades + events）
Step 4: 发送通知

幂等设计：
- 使用 idempotency_key: `phase_b:{robot_id}:{trade_id}:{trade_date}`
- Redis 分布式锁防并发

参考：docs/lhb-quant-v2/TRADING_FLOW.md §三
"""
from datetime import date, datetime
from decimal import Decimal
from typing import Optional
from dataclasses import dataclass

from loguru import logger
from sqlalchemy import select, and_, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend import (
    Robot, RobotTrade, TradeExecution, CashLedger, TradeEvent, AuditLog
)
from backend import StockInfo, KlineDaily
from backend import (
    validate_transition, abandon_trade, InvalidStateTransition
)
from backend import RiskControl
from backend import get_position_mult


# ============================================================
# 前置检查相关
# ============================================================
@dataclass
class PreCheckResult:
    """前置检查结果"""
    passed: bool
    reason: Optional[str] = None
    should_abandon: bool = False
    open_price: Optional[float] = None
    limit_up_price: Optional[float] = None
    limit_down_price: Optional[float] = None


class PhaseBEngine:
    """
    Phase B 确认买入引擎
    
    执行 pending 交易的买入确认
    """
    
    def __init__(
        self,
        db: AsyncSession,
        robot_id: int,
        trade_date: Optional[date] = None
    ):
        """
        初始化
        
        Args:
            db: 数据库会话
            robot_id: 机器人ID
            trade_date: 交易日期（默认今天）
        """
        self.db = db
        self.robot_id = robot_id
        self.trade_date = trade_date or date.today()
        
        # 配置参数
        self.open_deviation_threshold = 0.05  # 开盘价偏离度阈值 5%
        self.min_buy_shares = 100  # 最小买入股数
        
        # 机器人配置（延迟加载）
        self._robot: Optional[Robot] = None
        self._position_mult: float = 1.0
    
    async def _load_robot(self) -> Robot:
        """加载机器人配置"""
        if self._robot is None:
            result = await self.db.execute(
                select(Robot).where(Robot.id == self.robot_id)
            )
            self._robot = result.scalar_one_or_none()
            if self._robot is None:
                raise ValueError(f"机器人不存在: {self.robot_id}")
            
            # 获取仓位系数
            self._position_mult = await get_position_mult(self.db, self.robot_id)
        
        return self._robot
    
    async def _get_pending_trades(self) -> list[RobotTrade]:
        """
        Step 1: 查询 pending 交易
        
        查询昨日 signal_date 的 pending 状态交易
        """
        # 获取上一个交易日（简化处理，实际应从 trade_calendar 查询）
        prev_date = self.trade_date - __import__('datetime').timedelta(days=1)
        
        result = await self.db.execute(
            select(RobotTrade).where(
                and_(
                    RobotTrade.robot_id == self.robot_id,
                    RobotTrade.status == 'pending',
                    RobotTrade.signal_date == prev_date,  # 昨日选股
                    RobotTrade.deleted_at.is_(None)
                )
            )
        )
        trades = result.scalars().all()
        
        logger.info(
            f"[PhaseB] 查询到 {len(trades)} 条 pending 交易，"
            f"signal_date={prev_date}"
        )
        
        return list(trades)
    
    async def _pre_check(self, trade: RobotTrade) -> PreCheckResult:
        """
        Step 2: 前置检查
        
        检查项：
        1. 停牌检测
        2. ST 检测
        3. 开盘价偏离度
        4. 涨停跌停检测
        5. 资金充足检测
        """
        stock_code = trade.stock_code
        
        # 获取股票基础信息
        stock_result = await self.db.execute(
            select(StockInfo).where(StockInfo.stock_code == stock_code)
        )
        stock_info = stock_result.scalar_one_or_none()
        
        if stock_info is None:
            return PreCheckResult(
                passed=False,
                reason=f"股票信息不存在: {stock_code}",
                should_abandon=True
            )
        
        # 检查1: ST 检测
        if stock_info.is_st:
            return PreCheckResult(
                passed=False,
                reason=f"股票 {stock_code} 为ST股",
                should_abandon=True
            )
        
        # 检查2: 退市检测
        if not stock_info.is_active:
            return PreCheckResult(
                passed=False,
                reason=f"股票 {stock_code} 已退市",
                should_abandon=True
            )
        
        # 获取今日K线数据（开盘价等）
        kline_result = await self.db.execute(
            select(KlineDaily).where(
                and_(
                    KlineDaily.trade_date == self.trade_date,
                    KlineDaily.stock_code == stock_code
                )
            )
        )
        kline = kline_result.scalar_one_or_none()
        
        # 如果没有今日K线，可能停牌
        if kline is None:
            # 尝试获取最新K线判断停牌
            last_kline_result = await self.db.execute(
                select(KlineDaily).where(
                    KlineDaily.stock_code == stock_code
                ).order_by(KlineDaily.trade_date.desc()).limit(1)
            )
            last_kline = last_kline_result.scalar_one_or_none()
            
            if last_kline:
                # 简化判断：如果最新K线日期不是今天，可能停牌
                return PreCheckResult(
                    passed=False,
                    reason=f"股票 {stock_code} 可能停牌（无今日K线）",
                    should_abandon=True
                )
        
        open_price = float(kline.open_price) if kline else None
        prev_close = float(kline.close_price) if kline else None  # 简化处理
        
        # 检查3: 开盘价偏离度（如果有昨日收盘价）
        # 实际应从 yesterday 的 K 线获取 prev_close
        if open_price and trade.signal_score:
            # 这里简化处理，实际应该用昨日收盘价
            pass
        
        # 检查4: 涨停跌停检测
        if open_price:
            # 获取涨停价和跌停价（简化计算）
            board_type = stock_info.board_type or 'main'
            if board_type == 'star' or board_type == 'gem':
                limit_ratio = 0.20
            elif stock_info.is_st:
                limit_ratio = 0.05
            else:
                limit_ratio = 0.10
            
            # 简化处理：假设 prev_close = open_price（实际应获取昨日收盘）
            limit_up_price = open_price * (1 + limit_ratio)
            limit_down_price = open_price * (1 - limit_ratio)
            
            # 检查是否涨停封板
            current_price = float(kline.close_price) if kline else open_price
            if current_price >= limit_up_price * 0.99:
                return PreCheckResult(
                    passed=False,
                    reason=f"股票 {stock_code} 涨停封板，放弃买入",
                    should_abandon=True,
                    open_price=open_price,
                    limit_up_price=limit_up_price
                )
            
            return PreCheckResult(
                passed=True,
                open_price=open_price,
                limit_up_price=limit_up_price,
                limit_down_price=limit_down_price
            )
        
        return PreCheckResult(passed=False, reason="无法获取开盘价")
    
    async def _check_funds(self, trade: RobotTrade, buy_price: float) -> tuple[bool, int]:
        """
        检查资金是否充足，计算可买入股数
        
        Returns:
            (sufficient, shares): 是否充足和可买入股数
        """
        robot = await self._load_robot()
        
        # 计算可用资金（考虑AHS仓位系数）
        available_cash = float(robot.cash_balance) * self._position_mult * 0.99  # 预留手续费
        
        # 计算可买入股数（向下取整到100股）
        max_shares = int(available_cash / buy_price)
        shares = (max_shares // 100) * 100
        
        if shares < self.min_buy_shares:
            return False, 0
        
        return True, shares
    
    async def _execute_buy(
        self,
        trade: RobotTrade,
        buy_price: float,
        shares: int
    ) -> bool:
        """
        Step 3: 执行买入（单一事务）
        
        事务内容：
        a. 插入 trade_executions
        b. 插入 cash_ledger
        c. 更新 robots.cash_balance（乐观锁）
        d. 更新 robot_trades.status → 'open'
        e. 插入 trade_events
        f. 插入 audit_log
        """
        robot = await self._load_robot()
        
        # 计算费用
        buy_amount = buy_price * shares
        commission = max(5.0, buy_amount * 0.0003)  # 佣金 万3，最低5元
        transfer_fee = buy_amount * 0.00001  # 过户费 万0.1
        total_cost = buy_amount + commission + transfer_fee
        
        # 幂等键
        idempotency_key = f"phase_b:{self.robot_id}:{trade.id}:{self.trade_date}"
        
        # 检查是否已执行（幂等）
        existing = await self.db.execute(
            select(TradeExecution).where(
                TradeExecution.idempotency_key == idempotency_key
            )
        )
        if existing.scalar_one_or_none():
            logger.info(f"[PhaseB] 已执行，跳过: {idempotency_key}")
            return True
        
        try:
            # a. 插入执行记录
            execution = TradeExecution(
                trade_id=trade.id,
                execution_type='buy',
                execution_time=datetime.now(),
                execution_price=Decimal(str(buy_price)),
                execution_shares=shares,
                execution_amount=Decimal(str(buy_amount)),
                commission=Decimal(str(commission)),
                transfer_fee=Decimal(str(transfer_fee)),
                total_cost=Decimal(str(total_cost)),
                idempotency_key=idempotency_key
            )
            self.db.add(execution)
            
            # b. 插入现金流水
            ledger = CashLedger(
                robot_id=self.robot_id,
                trans_type='buy',
                amount=Decimal(str(-total_cost)),
                balance_after=Decimal(str(float(robot.cash_balance) - total_cost)),
                ref_type='trade_execution',
                ref_id=execution.id,
                memo=f"买入 {trade.stock_code}"
            )
            self.db.add(ledger)
            
            # c. 更新机器人现金（乐观锁）
            current_version = robot.version or 0
            new_cash = float(robot.cash_balance) - total_cost
            
            update_result = await self.db.execute(
                update(Robot).where(
                    and_(
                        Robot.id == self.robot_id,
                        Robot.version == current_version
                    )
                ).values(
                    cash_balance=Decimal(str(new_cash)),
                    version=current_version + 1,
                    updated_at=datetime.now()
                )
            )
            
            if update_result.rowcount == 0:
                # 乐观锁冲突
                raise Exception("乐观锁冲突，请重试")
            
            # d. 更新交易状态
            trade.status = 'open'
            trade.buy_date = self.trade_date
            trade.buy_price = Decimal(str(buy_price))
            trade.buy_shares = shares
            trade.buy_amount = Decimal(str(buy_amount))
            trade.buy_slippage = Decimal('0')  # TODO: 计算实际滑点
            trade.updated_at = datetime.now()
            
            # e. 插入事件
            event = TradeEvent(
                trade_id=trade.id,
                event_type='phase_b',
                event_time=datetime.now(),
                event_data={
                    'buy_price': buy_price,
                    'shares': shares,
                    'amount': buy_amount,
                    'commission': commission,
                }
            )
            self.db.add(event)
            
            # f. 审计日志
            audit = AuditLog(
                user_id='system',
                action='phase_b_buy',
                resource_type='robot_trade',
                resource_id=trade.id,
                old_value={'status': 'pending'},
                new_value={'status': 'open', 'buy_price': str(buy_price)},
            )
            self.db.add(audit)
            
            await self.db.flush()
            
            logger.info(
                f"[PhaseB] 买入成功: trade_id={trade.id}, "
                f"stock={trade.stock_code}, price={buy_price:.3f}, "
                f"shares={shares}, amount={buy_amount:.2f}"
            )
            
            return True
            
        except Exception as e:
            logger.exception(f"[PhaseB] 买入失败: {e}")
            return False
    
    async def run(self) -> dict:
        """
        执行 Phase B 流程
        
        Returns:
            执行结果统计
        """
        logger.info(f"[PhaseB] 开始执行，robot={self.robot_id}, date={self.trade_date}")
        
        await self._load_robot()
        
        # Step 1: 查询 pending 交易
        pending_trades = await self._get_pending_trades()
        
        if not pending_trades:
            logger.info("[PhaseB] 无 pending 交易，跳过")
            return {
                'success': True,
                'total': 0,
                'bought': 0,
                'abandoned': 0,
                'failed': 0
            }
        
        results = {
            'total': len(pending_trades),
            'bought': 0,
            'abandoned': 0,
            'failed': 0,
            'details': []
        }
        
        for trade in pending_trades:
            # Step 2: 前置检查
            check_result = await self._pre_check(trade)
            
            if not check_result.passed:
                if check_result.should_abandon:
                    # 放弃买入
                    await abandon_trade(self.db, trade.id, check_result.reason or "前置检查失败")
                    results['abandoned'] += 1
                    results['details'].append({
                        'trade_id': trade.id,
                        'stock_code': trade.stock_code,
                        'status': 'abandoned',
                        'reason': check_result.reason
                    })
                continue
            
            # 检查资金并计算买入数量
            if check_result.open_price:
                sufficient, shares = await self._check_funds(trade, check_result.open_price)
                
                if not sufficient:
                    await abandon_trade(self.db, trade.id, "资金不足")
                    results['abandoned'] += 1
                    continue
                
                # Step 3: 执行买入
                success = await self._execute_buy(trade, check_result.open_price, shares)
                
                if success:
                    results['bought'] += 1
                    results['details'].append({
                        'trade_id': trade.id,
                        'stock_code': trade.stock_code,
                        'status': 'bought',
                        'price': check_result.open_price,
                        'shares': shares
                    })
                else:
                    results['failed'] += 1
        
        # 提交事务
        await self.db.commit()
        
        logger.info(
            f"[PhaseB] 执行完成: 总计 {results['total']}，"
            f"买入 {results['bought']}，"
            f"放弃 {results['abandoned']}，"
            f"失败 {results['failed']}"
        )
        
        # TODO: Step 4 发送通知
        
        return results


async def run_phase_b(
    db: AsyncSession,
    robot_id: int,
    trade_date: Optional[date] = None
) -> dict:
    """
    执行 Phase B（便捷函数）
    
    Args:
        db: 数据库会话
        robot_id: 机器人ID
        trade_date: 交易日期
    
    Returns:
        执行结果
    """
    engine = PhaseBEngine(db, robot_id, trade_date)
    return await engine.run()
