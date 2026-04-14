"""
龙虎榜多因子策略

将现有的选股/买卖逻辑包装为 BaseStrategy 接口
"""
import sys
import logging
from pathlib import Path
from typing import Optional, List
from datetime import datetime

ROOT = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(ROOT))

from ..strategy_base import BaseStrategy, SelectionSignal, TradeSignal

logger = logging.getLogger(__name__)


class LianbanMultiFactorStrategy(BaseStrategy):
    """龙虎榜多因子策略"""
    
    def get_name(self) -> str:
        return "龙虎榜多因子策略"
    
    def get_id(self) -> str:
        return "lianban_multi_factor"
    
    def select_stocks(self, trade_date: str, context: dict) -> List[SelectionSignal]:
        """选股逻辑（复用现有 Phase A 代码）
        
        Args:
            trade_date: 交易日期 (YYYYMMDD)
            context: 包含 robot, cash, holdings 等信息
        """
        robot = context.get('robot', {})
        cash = context.get('cash', 0)
        holdings = context.get('holdings', [])
        max_positions = robot.get('max_positions', 3)
        
        available_slots = max_positions - len(holdings)
        if available_slots <= 0:
            logger.info(f"机器人 {robot.get('robot_id')} 持仓已满，跳过选股")
            return []
        
        position_ratio = robot.get('position_ratio', 0.1)
        initial_capital = robot.get('initial_capital', 100000)
        buy_amount = initial_capital * position_ratio
        
        # 调用现有选股逻辑
        try:
            from ..llm_select import select_stock
            from ..fetch_signal import get_mainboard_signals
            
            # 获取信号
            signals_data = get_mainboard_signals(trade_date)
            if signals_data.empty:
                logger.warning(f"{trade_date} 无龙虎榜信号")
                return []
            
            # LLM 选股
            candidates = select_stock(signals_data, trade_date, self.params)
            
            if not candidates:
                logger.info(f"{trade_date} 无候选股票")
                return []
            
        except Exception as e:
            logger.error(f"选股失败: {e}")
            return []
        
        # 转换为 SelectionSignal
        signals = []
        for c in candidates[:available_slots]:
            # 使用可用现金和计划金额中较小的值
            actual_amount = min(buy_amount, cash)
            if actual_amount <= 0:
                break
            
            signals.append(SelectionSignal(
                stock_code=c.get('code', ''),
                stock_name=c.get('name', ''),
                score=c.get('score', 0),
                buy_amount=actual_amount,
                reason=c.get('reason', ''),
                extra={
                    'lianban_score': c.get('lianban_score', 0),
                    'signal_score': c.get('signal_score', 0),
                },
            ))
        
        logger.info(f"📊 {trade_date} 选出 {len(signals)} 只股票")
        return signals
    
    def should_buy(self, signal: SelectionSignal, realtime: dict, context: dict) -> Optional[TradeSignal]:
        """买入判断 - 默认 T+1 开盘直接买入
        
        Args:
            signal: 选股信号
            realtime: 实时行情数据 (price, open, pre_close 等)
            context: 上下文信息
        """
        price = realtime.get('price') or realtime.get('open')
        if not price or price <= 0:
            logger.warning(f"{signal.stock_code} 无有效价格")
            return None
        
        # 涨停不买
        pre_close = realtime.get('pre_close', 0)
        if pre_close > 0:
            change_pct = (price - pre_close) / pre_close * 100
            if change_pct >= 9.8:
                logger.info(f"{signal.stock_code} 涨停 {change_pct:.1f}%，跳过买入")
                return None
            
            # 高开放弃（AHS 感知）
            abandon_threshold = self._get_abandon_threshold(context)
            if change_pct > abandon_threshold:
                logger.info(f"{signal.stock_code} 高开 {change_pct:.1f}% > 阈值 {abandon_threshold}%，放弃买入")
                return None
        
        return TradeSignal(
            stock_code=signal.stock_code,
            direction='BUY',
            price=price,
            shares=0,  # 由调用方计算股数
            reason=f"T+1买入，评分{signal.score:.1f}，{signal.reason}",
        )
    
    def should_sell(self, position: dict, realtime: dict, context: dict) -> Optional[TradeSignal]:
        """卖出判断 - 止损/止盈/到期
        
        Args:
            position: 持仓信息 (stock_code, buy_price, trade_date 等)
            realtime: 实时行情数据
            context: 上下文信息 (包含 robot 配置)
        """
        robot = context.get('robot', {})
        price = realtime.get('price', 0)
        buy_price = position.get('buy_price', 0)
        
        if not price or not buy_price:
            return None
        
        profit_pct = (price - buy_price) / buy_price
        
        # 1. 止损检查
        stop_loss = robot.get('stop_loss_pct', -0.5)
        if profit_pct <= stop_loss:
            return TradeSignal(
                stock_code=position.get('code', position.get('stock_code', '')),
                direction='SELL',
                price=price,
                shares=position.get('buy_shares', 0),
                reason=f"止损触发: {profit_pct:.1%} <= {stop_loss:.1%}",
                urgency='urgent',
            )
        
        # 2. 到期检查
        trade_date = position.get('trade_date', '')
        if trade_date:
            try:
                buy_dt = datetime.strptime(trade_date, '%Y%m%d')
                days_held = (datetime.now() - buy_dt).days
                max_days = robot.get('max_holding_days', 7)
                
                if days_held >= max_days:
                    return TradeSignal(
                        stock_code=position.get('code', position.get('stock_code', '')),
                        direction='SELL',
                        price=price,
                        shares=position.get('buy_shares', 0),
                        reason=f"持仓到期: {days_held}天",
                    )
            except ValueError:
                pass
        
        # 3. 止盈检查
        stop_profit = robot.get('stop_profit_pct')
        if stop_profit and profit_pct >= stop_profit:
            return TradeSignal(
                stock_code=position.get('code', position.get('stock_code', '')),
                direction='SELL',
                price=price,
                shares=position.get('buy_shares', 0),
                reason=f"止盈触发: {profit_pct:.1%} >= {stop_profit:.1%}",
            )
        
        return None
    
    def _get_abandon_threshold(self, context: dict) -> float:
        """根据 AHS 获取高开放弃阈值"""
        try:
            ahs = context.get('ahs', 50)
            if ahs >= 70:
                return 9.0   # 强势期：可以追高
            elif ahs >= 50:
                return 7.0   # 正常期
            else:
                return 5.0   # 疲弱期：更保守
        except:
            return 7.0


def create_strategy(params: dict) -> BaseStrategy:
    """工厂函数"""
    return LianbanMultiFactorStrategy(params)
