"""
策略配置和辅助表的 ORM 模型
"""
from datetime import datetime
from sqlalchemy import (
    Column, String, Text, Boolean, Integer, Date, BigInteger, Numeric, ARRAY,
    ForeignKey, UniqueConstraint, CheckConstraint, JSON, DateTime
)
from sqlalchemy.orm import relationship
from .base import Base, IDMixin, TimestampMixin


class Strategy(Base, IDMixin, TimestampMixin):
    """策略配置表"""
    __tablename__ = 'strategies'
    
    name = Column(String(100), nullable=False, unique=True)
    type = Column(String(50), nullable=False)
    version = Column(String(20), nullable=False, default='1.0.0')
    description = Column(Text)
    config = Column(JSON, nullable=False, default={})
    is_active = Column(Boolean, nullable=False, default=True)
    
    # 关系
    robots = relationship("Robot", back_populates="strategy")
    params = relationship("StrategyParam", back_populates="strategy")


class StrategyParam(Base, IDMixin):
    """策略参数表"""
    __tablename__ = 'strategy_params'
    
    strategy_id = Column(BigInteger, ForeignKey('strategies.id'), nullable=False)
    param_name = Column(String(50), nullable=False)
    param_value = Column(Text, nullable=False)
    param_type = Column(String(20), nullable=False, default='string')
    description = Column(Text)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.now)
    
    __table_args__ = (
        UniqueConstraint('strategy_id', 'param_name', name='uk_strategy_params_strategy_param'),
    )
    
    # 关系
    strategy = relationship("Strategy", back_populates="params")


class TradeEvent(Base, IDMixin):
    """交易事件日志"""
    __tablename__ = 'trade_events'
    
    trade_id = Column(BigInteger, ForeignKey('robot_trades.id'), nullable=False)
    event_type = Column(String(50), nullable=False)
    event_time = Column(DateTime(timezone=True), nullable=False)
    event_data = Column(JSON, nullable=False, default={})
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.now)


class SelectionLog(Base, IDMixin):
    """选股日志"""
    __tablename__ = 'selection_log'
    
    robot_id = Column(BigInteger, ForeignKey('robots.id'), nullable=False)
    signal_date = Column(Date, nullable=False)
    stock_code = Column(String(6), nullable=False)
    phase = Column(String(10), nullable=False)
    final_score = Column(Numeric(5, 2))
    rank = Column(Integer)
    factor_scores = Column(JSON)
    reasons = Column(ARRAY(Text))
    action_taken = Column(String(20))
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.now)
    
    __table_args__ = (
        CheckConstraint("phase IN ('A', 'B')", name='chk_selection_log_phase'),
        CheckConstraint(
            "action_taken IN ('confirmed', 'rejected', 'pending', 'expired')",
            name='chk_selection_log_action'
        ),
    )


class AuditLog(Base, IDMixin):
    """审计日志"""
    __tablename__ = 'audit_log'
    
    user_id = Column(String(100), default='system')
    action = Column(String(100), nullable=False)
    resource_type = Column(String(50), nullable=False)
    resource_id = Column(BigInteger)
    old_value = Column(JSON)
    new_value = Column(JSON)
    ip_address = Column(String(45))
    user_agent = Column(Text)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.now)


class AppConfig(Base):
    """应用配置表"""
    __tablename__ = 'app_config'
    
    key = Column(String(100), primary_key=True)
    value = Column(Text, nullable=False)
    description = Column(Text)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=datetime.now)


class TradeCalendar(Base):
    """交易日历表"""
    __tablename__ = 'trade_calendar'
    
    trade_date = Column(Date, primary_key=True)
    is_open = Column(Boolean, nullable=False, default=True)
    market = Column(String(20), default='A股')
    prev_trading_day = Column(Date)
    next_trading_day = Column(Date)


class StockInfo(Base):
    """股票基础信息表"""
    __tablename__ = 'stock_info'
    
    stock_code = Column(String(6), primary_key=True)
    stock_name = Column(String(50), nullable=False)
    industry = Column(String(50))
    board_type = Column(String(20))
    is_st = Column(Boolean, default=False)
    list_date = Column(Date)
    is_active = Column(Boolean, default=True)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=datetime.now)


class CollectorErrorLog(Base, IDMixin):
    """数据采集错误日志表"""
    __tablename__ = 'collector_error_log'
    
    collector_name = Column(String(100), nullable=False)
    error_type = Column(String(50), nullable=False)
    error_message = Column(Text, nullable=False)
    trade_date = Column(Date)
    retry_count = Column(Integer, default=0)
    resolved = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.now)


from sqlalchemy import Numeric, ARRAY, CheckConstraint  # 需要导入
