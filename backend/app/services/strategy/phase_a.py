"""
Phase A 选股引擎

执行时间：交易日 18:50
职责：基于当日龙虎榜数据进行多因子评分选股

核心流程：
Step 1: 数据准备 → 获取当日龙虎榜数据
Step 2: 准入过滤 → ST/退市/北交所/涨跌停/净买入阈值
Step 3: 评分计算 → 技术面40% + 资金面35% + 历史面25%
Step 4: LLM选股（可选）→ 调用万卿 LLM 分析
Step 5: 排序选股 → 取 Top N，排除已持仓和已候选
Step 6: AHS检查 → 若 AHS >= 81 则停止开仓
Step 7: 模拟买入 → 创建 pending 交易，写入 selection_log
Step 8: 发送通知 → 飞书通知选股完成

参考：docs/lhb-quant-v2/TRADING_FLOW.md §二
"""
from datetime import date, datetime
from decimal import Decimal
from typing import Optional
from dataclasses import dataclass

from loguru import logger
from sqlalchemy import select, and_, func
from sqlalchemy.ext.asyncio import AsyncSession

from backend import LhbStockDaily, LhbSeat, LhbTnPrice, StockInfo, MarketSentiment
from backend import Robot, RobotTrade, TradeEvent, SelectionLog
from backend import StrategyParam
from backend import Scorer, ScoreResult, KNOWN_TRADERS
from backend import (
    apply_data_layer_filters,
    apply_strategy_layer_filters,
    get_stock_info_map,
    check_ahs_allows_open,
    check_position_count_limit,
)


@dataclass
class CandidateStock:
    """候选股票"""
    stock_code: str
    stock_name: str
    final_score: float
    tech_score: float
    fund_score: float
    hist_score: float
    net_buy_amount: float
    change_ratio: float
    turnover_rate: float
    signal_reasons: list[str]
    score_details: dict


@dataclass
class PhaseAResult:
    """Phase A 执行结果"""
    success: bool
    robot_id: int
    signal_date: date
    candidates: list[CandidateStock]
    selected_count: int
    rejected_count: int
    ahs_blocked: bool
    error_message: Optional[str] = None


class PhaseAEngine:
    """
    Phase A 选股引擎
    
    基于龙虎榜数据进行多因子评分选股
    """
    
    def __init__(
        self,
        db: AsyncSession,
        robot_id: int,
        signal_date: Optional[date] = None
    ):
        """
        初始化
        
        Args:
            db: 数据库会话
            robot_id: 机器人ID
            signal_date: 信号日期（默认今天）
        """
        self.db = db
        self.robot_id = robot_id
        self.signal_date = signal_date or date.today()
        
        # 机器人配置（延迟加载）
        self._robot: Optional[Robot] = None
        self._strategy_params: dict = {}
        
        # 评分器
        self._scorer: Optional[Scorer] = None
        
        # 结果统计
        self._candidates: list[CandidateStock] = []
        self._rejected_count = 0
        self._ahs_blocked = False
    
    async def _load_robot(self) -> Robot:
        """加载机器人配置"""
        if self._robot is None:
            result = await self.db.execute(
                select(Robot).where(Robot.id == self.robot_id)
            )
            self._robot = result.scalar_one_or_none()
            if self._robot is None:
                raise ValueError(f"机器人不存在: {self.robot_id}")
        return self._robot
    
    async def _load_strategy_params(self) -> dict:
        """加载策略参数"""
        if not self._strategy_params:
            robot = await self._load_robot()
            
            # 从 strategy_params 表加载参数
            result = await self.db.execute(
                select(StrategyParam).where(
                    StrategyParam.strategy_id == robot.strategy_id
                )
            )
            params = result.scalars().all()
            
            self._strategy_params = {
                p.param_name: self._parse_param_value(p.param_value, p.param_type)
                for p in params
            }
            
            # 设置默认值
            defaults = {
                'min_net_buy': 50_000_000,  # 5000万
                'min_tech_score': 50.0,
                'top_n': 3,
                'max_positions': 3,
                'llm_enabled': False,
                'tech_weight': 0.40,
                'fund_weight': 0.35,
                'hist_weight': 0.25,
            }
            
            for key, value in defaults.items():
                if key not in self._strategy_params:
                    self._strategy_params[key] = value
        
        return self._strategy_params
    
    def _parse_param_value(self, value: str, param_type: str):
        """解析参数值"""
        if param_type == 'int':
            return int(value)
        elif param_type == 'float':
            return float(value)
        elif param_type == 'bool':
            return value.lower() == 'true'
        else:
            return value
    
    async def _get_scorer(self) -> Scorer:
        """获取评分器"""
        if self._scorer is None:
            params = await self._load_strategy_params()
            self._scorer = Scorer(
                tech_weight=params.get('tech_weight', 0.40),
                fund_weight=params.get('fund_weight', 0.35),
                hist_weight=params.get('hist_weight', 0.25)
            )
        return self._scorer
    
    async def _fetch_lhb_data(self) -> list[dict]:
        """
        Step 1: 数据准备
        
        获取当日龙虎榜数据及相关数据
        """
        logger.info(f"[PhaseA] 开始获取 {self.signal_date} 龙虎榜数据")
        
        # 查询龙虎榜股票数据
        lhb_result = await self.db.execute(
            select(LhbStockDaily).where(
                LhbStockDaily.trade_date == self.signal_date
            )
        )
        lhb_stocks = lhb_result.scalars().all()
        
        if not lhb_stocks:
            logger.warning(f"[PhaseA] {self.signal_date} 无龙虎榜数据")
            return []
        
        logger.info(f"[PhaseA] 获取到 {len(lhb_stocks)} 条龙虎榜记录")
        
        # 获取股票代码列表
        stock_codes = [s.stock_code for s in lhb_stocks]
        
        # 批量获取席位数据
        seats_result = await self.db.execute(
            select(LhbSeat).where(
                and_(
                    LhbSeat.trade_date == self.signal_date,
                    LhbSeat.stock_code.in_(stock_codes)
                )
            )
        )
        seats = seats_result.scalars().all()
        
        # 按股票分组席位
        seats_by_stock: dict[str, list[LhbSeat]] = {}
        for seat in seats:
            if seat.stock_code not in seats_by_stock:
                seats_by_stock[seat.stock_code] = []
            seats_by_stock[seat.stock_code].append(seat)
        
        # 获取股票基础信息
        stock_info_map = await get_stock_info_map(self.db, stock_codes)
        
        # 获取历史面数据（近20次上榜的T+N表现）
        # 这里简化处理，实际需要更复杂的查询
        hist_result = await self.db.execute(
            select(LhbTnPrice).where(
                LhbTnPrice.stock_code.in_(stock_codes)
            ).order_by(LhbTnPrice.trade_date.desc()).limit(1000)
        )
        hist_prices = hist_result.scalars().all()
        
        # 按股票分组历史数据
        hist_by_stock: dict[str, list[LhbTnPrice]] = {}
        for hp in hist_prices:
            if hp.stock_code not in hist_by_stock:
                hist_by_stock[hp.stock_code] = []
            hist_by_stock[hp.stock_code].append(hp)
        
        # 组装数据
        data = []
        for lhb in lhb_stocks:
            stock_info = stock_info_map.get(lhb.stock_code)
            if stock_info is None:
                continue
            
            stock_seats = seats_by_stock.get(lhb.stock_code, [])
            stock_hist = hist_by_stock.get(lhb.stock_code, [])
            
            # 计算买入席位
            buy_seats = [s for s in stock_seats if s.direction == 'buy']
            buy_seat_names = [s.seat_name for s in buy_seats]
            
            # 判断是否有知名游资
            has_known_trader = any(name in KNOWN_TRADERS for name in buy_seat_names)
            
            # 计算机构买入占比
            institution_buy = sum(float(s.amount or 0) for s in buy_seats if s.seat_type == 'institution')
            total_buy = sum(float(s.amount or 0) for s in buy_seats)
            institution_ratio = institution_buy / total_buy if total_buy > 0 else 0
            
            # 计算第一大席位占比
            buy_amounts = sorted([float(s.amount or 0) for s in buy_seats], reverse=True)
            top1_ratio = buy_amounts[0] / total_buy if buy_amounts and total_buy > 0 else 0
            
            # 计算历史面数据
            t1_win_rate, t1_avg_return, max_gain, sample_count = self._calc_hist_metrics(stock_hist)
            
            data.append({
                'stock_code': lhb.stock_code,
                'stock_name': stock_info.stock_name,
                'change_ratio': float(lhb.change_ratio or 0),
                'turnover_rate': float(lhb.turnover_rate or 0),
                'net_buy_amount': float(lhb.net_buy_amount or 0),
                'total_buy_amount': total_buy,
                'institution_ratio': institution_ratio,
                'top1_ratio': top1_ratio,
                'lhb_count': self._calc_lhb_count(lhb.stock_code),  # 近30日上榜次数
                'buy_seat_names': buy_seat_names,
                'has_known_trader': has_known_trader,
                'board_type': stock_info.board_type,
                'is_st': stock_info.is_st,
                # 历史面
                't1_win_rate': t1_win_rate,
                't1_avg_return': t1_avg_return,
                'max_gain': max_gain,
                'sample_count': sample_count,
            })
        
        logger.info(f"[PhaseA] 组装数据完成，共 {len(data)} 条")
        return data
    
    def _calc_hist_metrics(self, hist_prices: list[LhbTnPrice]) -> tuple[float, float, float, int]:
        """计算历史面指标"""
        if not hist_prices:
            return 0.5, 0.0, 0.0, 0
        
        # 只取 T+1 数据
        t1_data = [hp for hp in hist_prices if hp.n_days == 1][:20]
        
        if not t1_data:
            return 0.5, 0.0, 0.0, 0
        
        # 胜率
        wins = sum(1 for hp in t1_data if hp.close_ret and float(hp.close_ret) > 0)
        win_rate = wins / len(t1_data)
        
        # 平均收益
        returns = [float(hp.close_ret or 0) for hp in t1_data]
        avg_return = sum(returns) / len(returns)
        
        # 最大涨幅
        gains = [float(hp.high_ret or 0) for hp in t1_data]
        max_gain = max(gains) if gains else 0
        
        return win_rate, avg_return, max_gain, len(t1_data)
    
    def _calc_lhb_count(self, stock_code: str) -> int:
        """计算近30日上榜次数（简化版本，需要单独查询）"""
        # 这里返回一个默认值，实际应该查询数据库
        return 1
    
    async def _apply_filters(self, data: list[dict]) -> list[dict]:
        """
        Step 2: 准入过滤
        
        应用数据层和策略层过滤
        """
        if not data:
            return []
        
        params = await self._load_strategy_params()
        
        # 获取股票代码列表
        stock_codes = [d['stock_code'] for d in data]
        
        # 数据层过滤
        valid_codes = await apply_data_layer_filters(self.db, stock_codes, exclude_bse=True)
        
        # 策略层过滤
        valid_codes = await apply_strategy_layer_filters(
            self.db, self.robot_id, valid_codes, self.signal_date
        )
        
        # 业务层过滤（净买入阈值、涨跌幅）
        min_net_buy = params.get('min_net_buy', 50_000_000)
        
        filtered_data = []
        for item in data:
            if item['stock_code'] not in valid_codes:
                continue
            
            # 净买入阈值
            if item['net_buy_amount'] < min_net_buy:
                logger.debug(f"[PhaseA] {item['stock_code']} 净买入不足，跳过")
                self._rejected_count += 1
                continue
            
            # 涨跌幅必须为正
            if item['change_ratio'] <= 0:
                logger.debug(f"[PhaseA] {item['stock_code']} 涨跌幅为负，跳过")
                self._rejected_count += 1
                continue
            
            filtered_data.append(item)
        
        logger.info(
            f"[PhaseA] 过滤完成: {len(data)} -> {len(filtered_data)}，"
            f"拒绝 {self._rejected_count} 条"
        )
        
        return filtered_data
    
    async def _calculate_scores(self, data: list[dict]) -> list[CandidateStock]:
        """
        Step 3: 评分计算
        """
        if not data:
            return []
        
        scorer = await self._get_scorer()
        params = await self._load_strategy_params()
        min_tech_score = params.get('min_tech_score', 50.0)
        
        candidates = []
        
        for item in data:
            # 计算评分
            result = scorer.score(
                change_pct=item['change_ratio'],
                turnover_rate=item['turnover_rate'],
                lhb_count=item.get('lhb_count', 1),
                top1_ratio=item['top1_ratio'],
                net_buy=item['net_buy_amount'],
                institution_ratio=item['institution_ratio'],
                seat_names=item['buy_seat_names'],
                t1_win_rate=item['t1_win_rate'],
                t1_avg_return=item['t1_avg_return'],
                max_gain=item['max_gain'],
                sample_count=item['sample_count']
            )
            
            # 检查技术面最低分
            if result.tech_score < min_tech_score:
                logger.debug(
                    f"[PhaseA] {item['stock_code']} 技术面得分 "
                    f"{result.tech_score:.1f} < {min_tech_score}，跳过"
                )
                self._rejected_count += 1
                continue
            
            # 生成信号原因
            reasons = self._generate_reasons(item, result)
            
            candidate = CandidateStock(
                stock_code=item['stock_code'],
                stock_name=item['stock_name'],
                final_score=result.final_score,
                tech_score=result.tech_score,
                fund_score=result.fund_score,
                hist_score=result.hist_score,
                net_buy_amount=item['net_buy_amount'],
                change_ratio=item['change_ratio'],
                turnover_rate=item['turnover_rate'],
                signal_reasons=reasons,
                score_details={
                    'tech_details': result.tech_details,
                    'fund_details': result.fund_details,
                    'hist_details': result.hist_details,
                }
            )
            
            candidates.append(candidate)
        
        logger.info(f"[PhaseA] 评分完成，共 {len(candidates)} 只候选")
        return candidates
    
    def _generate_reasons(self, item: dict, result: ScoreResult) -> list[str]:
        """生成选股原因"""
        reasons = []
        
        # 净买入
        if item['net_buy_amount'] >= 100_000_000:
            reasons.append(f"净买入超1亿")
        elif item['net_buy_amount'] >= 50_000_000:
            reasons.append(f"净买入超5千万")
        
        # 知名游资
        if item['has_known_trader']:
            reasons.append("知名游资买入")
        
        # 机构买入
        if item['institution_ratio'] >= 0.3:
            reasons.append(f"机构买入{item['institution_ratio']:.0%}")
        
        # 历史胜率
        if item['t1_win_rate'] >= 0.6 and item['sample_count'] >= 5:
            reasons.append(f"T+1胜率{item['t1_win_rate']:.0%}")
        
        # 涨幅适中
        if 0.03 <= item['change_ratio'] <= 0.07:
            reasons.append("涨幅适中(3%-7%)")
        
        return reasons
    
    async def _apply_llm_filter(self, candidates: list[CandidateStock]) -> list[CandidateStock]:
        """
        Step 4: LLM 选股（可选）
        
        TODO: 实现调用万卿 LLM 分析
        """
        params = await self._load_strategy_params()
        
        if not params.get('llm_enabled', False):
            return candidates
        
        # TODO: 实现 LLM 筛选逻辑
        logger.info("[PhaseA] LLM 筛选暂未实现，跳过")
        return candidates
    
    async def _select_top_n(self, candidates: list[CandidateStock]) -> list[CandidateStock]:
        """
        Step 5: 排序选股
        
        取 Top N，检查 AHS 限制
        """
        params = await self._load_strategy_params()
        robot = await self._load_robot()
        top_n = params.get('top_n', 3)
        
        # 按评分降序排序
        sorted_candidates = sorted(
            candidates, key=lambda c: c.final_score, reverse=True
        )
        
        # 检查 AHS 是否允许开仓
        ahs_allowed, ahs_reason = await check_ahs_allows_open(self.db, self.robot_id)
        
        if not ahs_allowed:
            self._ahs_blocked = True
            logger.warning(f"[PhaseA] AHS 阻止开仓: {ahs_reason}")
            return []
        
        # 检查剩余可开仓位数
        position_allowed, position_reason = await check_position_count_limit(
            self.db, self.robot_id, robot.max_hold_days or params.get('max_positions', 3)
        )
        
        if not position_allowed:
            logger.warning(f"[PhaseA] 持仓数量限制: {position_reason}")
            return []
        
        # 计算剩余可开仓数
        result = await self.db.execute(
            select(func.count()).select_from(RobotTrade).where(
                and_(
                    RobotTrade.robot_id == self.robot_id,
                    RobotTrade.status.in_(['open', 'partial', 'pending']),
                    RobotTrade.deleted_at.is_(None)
                )
            )
        )
        current_count = result.scalar() or 0
        remaining_slots = params.get('max_positions', 3) - current_count
        
        selected = sorted_candidates[:min(top_n, remaining_slots)]
        
        logger.info(
            f"[PhaseA] 选股完成: Top {len(selected)} 只，"
            f"当前持仓 {current_count}，剩余可开仓 {remaining_slots}"
        )
        
        return selected
    
    async def _create_pending_trades(self, candidates: list[CandidateStock]) -> list[RobotTrade]:
        """
        Step 7: 模拟买入
        
        创建 pending 状态的交易记录
        """
        if not candidates:
            return []
        
        robot = await self._load_robot()
        params = await self._load_strategy_params()
        
        # 计算可用资金
        available_cash = float(robot.cash_balance)
        
        # 计算每只股票的买入金额（等分）
        # 考虑 AHS 仓位系数
        from backend import get_position_mult
        position_mult = await get_position_mult(self.db, self.robot_id)
        
        # 预留手续费（约 1%）
        usable_cash = available_cash * 0.99 * position_mult
        amount_per_stock = usable_cash / max(len(candidates), 1)
        
        trades = []
        
        for i, candidate in enumerate(candidates):
            # 创建 pending 交易
            trade = RobotTrade(
                robot_id=self.robot_id,
                stock_code=candidate.stock_code,
                stock_name=candidate.stock_name,
                status='pending',
                signal_date=self.signal_date,
                signal_score=Decimal(str(candidate.final_score)),
                signal_rank=i + 1,
                signal_reasons=candidate.signal_reasons,
                # 买入信息在 Phase B 填充
                take_profit_tiers=[
                    {"ratio": 0.03, "sell_ratio": 0.33, "executed": False},
                    {"ratio": 0.05, "sell_ratio": 0.33, "executed": False},
                    {"ratio": 0.08, "sell_ratio": 0.34, "executed": False},
                ],
            )
            
            self.db.add(trade)
            
            # 创建选股日志
            log = SelectionLog(
                robot_id=self.robot_id,
                signal_date=self.signal_date,
                stock_code=candidate.stock_code,
                phase='A',
                final_score=Decimal(str(candidate.final_score)),
                rank=i + 1,
                factor_scores=candidate.score_details,
                reasons=candidate.signal_reasons,
                action_taken='pending',
            )
            
            self.db.add(log)
            
            trades.append(trade)
        
        await self.db.flush()
        
        # 创建交易事件
        for trade in trades:
            event = TradeEvent(
                trade_id=trade.id,
                event_type='phase_a',
                event_time=datetime.now(),
                event_data={
                    'signal_date': str(self.signal_date),
                    'score': float(trade.signal_score),
                    'rank': trade.signal_rank,
                }
            )
            self.db.add(event)
        
        logger.info(f"[PhaseA] 创建 {len(trades)} 条 pending 交易")
        return trades
    
    async def run(self) -> PhaseAResult:
        """
        执行 Phase A 选股流程
        
        Returns:
            PhaseAResult: 执行结果
        """
        try:
            logger.info(f"[PhaseA] 开始执行，机器人={self.robot_id}，日期={self.signal_date}")
            
            # 加载配置
            await self._load_robot()
            await self._load_strategy_params()
            
            # Step 1: 数据准备
            data = await self._fetch_lhb_data()
            if not data:
                return PhaseAResult(
                    success=True,
                    robot_id=self.robot_id,
                    signal_date=self.signal_date,
                    candidates=[],
                    selected_count=0,
                    rejected_count=0,
                    ahs_blocked=False,
                    error_message="当日无龙虎榜数据",
                )
            
            # Step 2: 准入过滤
            filtered_data = await self._apply_filters(data)
            
            # Step 3: 评分计算
            candidates = await self._calculate_scores(filtered_data)
            
            # Step 4: LLM 选股（可选）
            candidates = await self._apply_llm_filter(candidates)
            
            # Step 5-6: 选股和 AHS 检查
            selected = await self._select_top_n(candidates)
            
            # Step 7: 模拟买入
            trades = await self._create_pending_trades(selected)
            
            # Step 8: 发送通知
            await self._send_notification(trades)
            
            # 提交事务
            await self.db.commit()
            
            self._candidates = selected
            
            return PhaseAResult(
                success=True,
                robot_id=self.robot_id,
                signal_date=self.signal_date,
                candidates=selected,
                selected_count=len(trades),
                rejected_count=self._rejected_count,
                ahs_blocked=self._ahs_blocked,
            )
            
        except Exception as e:
            await self.db.rollback()
            logger.exception(f"[PhaseA] 执行失败: {e}")
            return PhaseAResult(
                success=False,
                robot_id=self.robot_id,
                signal_date=self.signal_date,
                candidates=[],
                selected_count=0,
                rejected_count=0,
                ahs_blocked=False,
                error_message=str(e),
            )
    
    async def _send_notification(self, trades: list[RobotTrade]):
        """
        Step 8: 发送通知
        
        TODO: 实现飞书通知
        """
        if not trades:
            logger.info("[PhaseA] 无选中股票，不发送通知")
            return
        
        # TODO: 调用飞书通知服务
        message = (
            f"📊 Phase A 选股完成\n"
            f"日期: {self.signal_date}\n"
            f"选中: {len(trades)} 只\n"
            + "\n".join([
                f"- {t.stock_code} {t.stock_name} (评分: {t.signal_score:.1f})"
                for t in trades
            ])
        )
        logger.info(f"[PhaseA] 通知内容:\n{message}")


async def run_phase_a(
    db: AsyncSession,
    robot_id: int,
    signal_date: Optional[date] = None
) -> PhaseAResult:
    """
    执行 Phase A 选股（便捷函数）
    
    Args:
        db: 数据库会话
        robot_id: 机器人ID
        signal_date: 信号日期（默认今天）
    
    Returns:
        PhaseAResult: 执行结果
    """
    engine = PhaseAEngine(db, robot_id, signal_date)
    return await engine.run()
