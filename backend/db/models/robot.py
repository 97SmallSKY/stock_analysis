"""
机器人和交易相关的 ORM 模型
"""
from datetime import datetime
from sqlalchemy import (
    Column, String, Numeric, Integer, BigInteger, Date, DateTime, Text, ARRAY,
    ForeignKey, CheckConstraint, UniqueConstraint, JSON
)
from sqlalchemy.orm import relationship
from .base import Base, IDMixin, TimestampMixin


class Robot(Base, IDMixin, TimestampMixin):
    """机器人配置表"""
    __tablename__ = 'robots'
    
    name = Column(String(100), nullable=False)
    status = Column(String(20), nullable=False, default='paper_trading')
    mode = Column(String(20), nullable=False, default='paper_trading')
    strategy_id = Column(BigInteger, ForeignKey('strategies.id'), nullable=False)
    initial_capital = Column(Numeric(16, 2), nullable=False)
    current_capital = Column(Numeric(16, 2), nullable=False, default=0.00)
    cash_balance = Column(Numeric(16, 2), nullable=False, default=0.00)
    position_ratio = Column(Numeric(5, 4), nullable=False, default=0.0000)
    version = Column(Integer, nullable=False, default=0)  # 乐观锁
    config = Column(JSON, nullable=False, default={})
    stop_loss_ratio = Column(Numeric(6, 4), default=-0.0500)
    take_profit_tiers = Column(JSON, default=[
        {"ratio": 0.03, "sell_ratio": 0.33},
        {"ratio": 0.05, "sell_ratio": 0.33},
        {"ratio": 0.08, "sell_ratio": 0.34}
    ])
    max_hold_days = Column(Integer, default=5)
    
    __table_args__ = (
        CheckConstraint(
            "status IN ('paper_trading', 'running', 'paused', 'stopped', 'eliminated')",
            name='chk_robots_status'
        ),
        CheckConstraint(
            "mode IN ('paper_trading', 'live')",
            name='chk_robots_mode'
        ),
        CheckConstraint(
            "initial_capital > 0",
            name='chk_robots_initial_capital'
        ),
    )
    
    # 关系
    trades = relationship("RobotTrade", back_populates="robot")
    strategy = relationship("Strategy", back_populates="robots")


class RobotTrade(Base, IDMixin, TimestampMixin):
    """交易主表"""
    __tablename__ = 'robot_trades'
    
    robot_id = Column(BigInteger, ForeignKey('robots.id'), nullable=False)
    stock_code = Column(String(6), nullable=False)
    stock_name = Column(String(50))
    status = Column(String(20), nullable=False, default='pending')
    signal_date = Column(Date, nullable=False)
    signal_score = Column(Numeric(5, 2))
    signal_rank = Column(Integer)
    signal_reasons = Column(ARRAY(Text))
    buy_date = Column(Date)
    buy_price = Column(Numeric(10, 3))
    buy_shares = Column(Integer)
    buy_amount = Column(Numeric(16, 2))
    buy_slippage = Column(Numeric(6, 4), default=0.0000)
    sell_date = Column(Date)
    sell_price = Column(Numeric(10, 3))
    sell_shares = Column(Integer)
    sell_amount = Column(Numeric(16, 2))
    sell_reason = Column(String(50))
    realized_pnl = Column(Numeric(16, 2))
    realized_pnl_ratio = Column(Numeric(8, 4))
    hold_days = Column(Integer)
    stop_loss_price = Column(Numeric(10, 3))
    take_profit_tiers = Column(JSON)
    max_price = Column(Numeric(10, 3))
    min_price = Column(Numeric(10, 3))
    max_drawdown = Column(Numeric(8, 4))
    max_profit = Column(Numeric(8, 4))
    
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'open', 'partial', 'closed', 'abandoned', 'eliminated')",
            name='chk_robot_trades_status'
        ),
        CheckConstraint(
            "stock_code ~ '^[0-9]{6}$'",
            name='chk_robot_trades_stock_code'
        ),
        CheckConstraint(
            "buy_shares IS NULL OR buy_shares % 100 = 0",
            name='chk_robot_trades_buy_shares'
        ),
    )
    
    # 关系
    robot = relationship("Robot", back_populates="trades")
    executions = relationship("TradeExecution", back_populates="trade")


class TradeExecution(Base, IDMixin):
    """交易执行明细"""
    __tablename__ = 'trade_executions'
    
    trade_id = Column(BigInteger, ForeignKey('robot_trades.id'), nullable=False)
    execution_type = Column(String(20), nullable=False)
    execution_time = Column(DateTime(timezone=True), nullable=False)
    execution_price = Column(Numeric(10, 3), nullable=False)
    execution_shares = Column(Integer, nullable=False)
    execution_amount = Column(Numeric(16, 2), nullable=False)
    slippage = Column(Numeric(6, 4), default=0.0000)
    commission = Column(Numeric(10, 2), default=0.00)
    stamp_tax = Column(Numeric(10, 2), default=0.00)
    transfer_fee = Column(Numeric(10, 2), default=0.00)
    total_cost = Column(Numeric(16, 2), default=0.00)
    idempotency_key = Column(String(100), unique=True, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.now)
    
    __table_args__ = (
        CheckConstraint(
            "execution_type IN ('buy', 'sell_1', 'sell_2', 'sell_3', 'stop_loss', 'expire', 'manual')",
            name='chk_trade_executions_exec_type'
        ),
    )
    
    # 关系
    trade = relationship("RobotTrade", back_populates="executions")


class CashLedger(Base, IDMixin):
    """现金流水"""
    __tablename__ = 'cash_ledger'
    
    robot_id = Column(BigInteger, ForeignKey('robots.id'), nullable=False)
    trans_type = Column(String(20), nullable=False)
    amount = Column(Numeric(16, 2), nullable=False)
    balance_after = Column(Numeric(16, 2), nullable=False)
    ref_type = Column(String(50))
    ref_id = Column(BigInteger)
    memo = Column(Text)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.now)
    
    __table_args__ = (
        CheckConstraint(
            "trans_type IN ('deposit', 'withdraw', 'buy', 'sell', 'commission', 'stamp_tax', 'transfer_fee', 'dividend', 'adjustment')",
            name='chk_cash_ledger_trans_type'
        ),
    )


class DailyNav(Base, IDMixin):
    """每日净值快照"""
    __tablename__ = 'daily_nav'
    
    robot_id = Column(BigInteger, ForeignKey('robots.id'), nullable=False)
    nav_date = Column(Date, nullable=False)
    open_nav = Column(Numeric(16, 2), nullable=False)
    close_nav = Column(Numeric(16, 2), nullable=False)
    daily_pnl = Column(Numeric(16, 2), nullable=False)
    daily_pnl_ratio = Column(Numeric(10, 6), nullable=False)
    position_value = Column(Numeric(16, 2), nullable=False)
    cash_balance = Column(Numeric(16, 2), nullable=False)
    position_ratio = Column(Numeric(10, 6), nullable=False)
    max_drawdown = Column(Numeric(10, 6))
    sharpe_ratio = Column(Numeric(10, 6))
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.now)
    
    __table_args__ = (
        UniqueConstraint('robot_id', 'nav_date', name='uk_daily_nav_robot_date'),
    )


class WeeklySummary(Base, IDMixin):
    """周度汇总"""
    __tablename__ = 'weekly_summary'
    
    robot_id = Column(BigInteger, ForeignKey('robots.id'), nullable=False)
    week_start_date = Column(Date, nullable=False)
    week_end_date = Column(Date, nullable=False)
    start_nav = Column(Numeric(16, 2), nullable=False)
    end_nav = Column(Numeric(16, 2), nullable=False)
    weekly_pnl = Column(Numeric(16, 2), nullable=False)
    weekly_pnl_ratio = Column(Numeric(10, 6), nullable=False)
    trade_count = Column(Integer, default=0)
    win_count = Column(Integer, default=0)
    lose_count = Column(Integer, default=0)
    max_drawdown = Column(Numeric(8, 4))
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.now)
    
    __table_args__ = (
        UniqueConstraint('robot_id', 'week_start_date', name='uk_weekly_summary_robot_week'),
    )


class AhsHistory(Base, IDMixin):
    """AHS评分历史"""
    __tablename__ = 'ahs_history'
    
    robot_id = Column(BigInteger, ForeignKey('robots.id'), nullable=False)
    score_date = Column(Date, nullable=False)
    ahs_score = Column(Numeric(5, 2), nullable=False)
    consecutive_losses = Column(Integer, default=0)
    weekly_pnl_ratio = Column(Numeric(10, 6))
    win_rate = Column(Numeric(10, 6))
    profit_loss_ratio = Column(Numeric(10, 6))
    loss_penalty = Column(Numeric(5, 2))
    winrate_penalty = Column(Numeric(5, 2))
    plr_penalty = Column(Numeric(5, 2))
    trading_mode = Column(String(20), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.now)
    
    __table_args__ = (
        CheckConstraint(
            "trading_mode IN ('normal', 'cautious', 'warning', 'crisis')",
            name='chk_ahs_history_mode'
        ),
    )


from datetime import datetime  # 需要在顶部导入
