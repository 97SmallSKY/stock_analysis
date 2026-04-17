"""
市场数据相关的 ORM 模型
"""
from datetime import datetime
from sqlalchemy import (
    Column, String, Numeric, Integer, Date, BigInteger,
    CheckConstraint, UniqueConstraint, DateTime
)
from .base import Base, IDMixin, TimestampMixin


class LhbStockDaily(Base, IDMixin, TimestampMixin):
    """龙虎榜每日股票汇总数据"""
    __tablename__ = 'lhb_stock_daily'
    
    trade_date = Column(Date, nullable=False)
    stock_code = Column(String(6), nullable=False)
    stock_name = Column(String(50))
    close_price = Column(Numeric(10, 3))
    change_ratio = Column(Numeric(10, 6))
    turnover_rate = Column(Numeric(10, 6))
    total_buy_amount = Column(Numeric(16, 2))
    total_sell_amount = Column(Numeric(16, 2))
    net_buy_amount = Column(Numeric(16, 2))
    net_buy_ratio = Column(Numeric(10, 6))
    buy_seats = Column(Integer, default=0)
    sell_seats = Column(Integer, default=0)
    institution_buy_amount = Column(Numeric(16, 2), default=0.00)
    institution_sell_amount = Column(Numeric(16, 2), default=0.00)
    institution_net_buy = Column(Numeric(16, 2), default=0.00)
    reason = Column(String(200))
    board_type = Column(String(20))
    
    __table_args__ = (
        UniqueConstraint('trade_date', 'stock_code', name='uk_lhb_stock_daily_date_code'),
        CheckConstraint(
            "stock_code ~ '^[0-9]{6}$'",
            name='chk_lhb_stock_daily_stock_code'
        ),
    )


class LhbSeat(Base, IDMixin):
    """龙虎榜席位明细"""
    __tablename__ = 'lhb_seats'
    
    trade_date = Column(Date, nullable=False)
    stock_code = Column(String(6), nullable=False)
    seat_name = Column(String(100), nullable=False)
    seat_type = Column(String(20), nullable=False)
    direction = Column(String(4), nullable=False)
    rank = Column(Integer, nullable=False)
    amount = Column(Numeric(18, 2), nullable=False, default=0.00)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.now)
    
    __table_args__ = (
        CheckConstraint(
            "seat_type IN ('institution', 'broker', 'individual')",
            name='chk_lhb_seats_seat_type'
        ),
        CheckConstraint(
            "direction IN ('buy', 'sell')",
            name='chk_lhb_seats_direction'
        ),
        CheckConstraint(
            "rank BETWEEN 1 AND 5",
            name='chk_lhb_seats_rank'
        ),
        CheckConstraint(
            "stock_code ~ '^[0-9]{6}$'",
            name='chk_lhb_seats_stock'
        ),
        UniqueConstraint(
            'trade_date', 'stock_code', 'direction', 'rank',
            name='uk_lhb_seats_date_stock_dir_rank'
        ),
    )


class LhbTnPrice(Base, IDMixin):
    """龙虎榜T+N价格纵表"""
    __tablename__ = 'lhb_tn_prices'
    
    trade_date = Column(Date, nullable=False)
    stock_code = Column(String(6), nullable=False)
    n_days = Column(Integer, nullable=False)
    close_price = Column(Numeric(10, 3))
    high_price = Column(Numeric(10, 3))
    low_price = Column(Numeric(10, 3))
    high_ret = Column(Numeric(10, 4))
    close_ret = Column(Numeric(10, 4))
    low_ret = Column(Numeric(10, 4))
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.now)
    
    __table_args__ = (
        UniqueConstraint('trade_date', 'stock_code', 'n_days', name='uk_lhb_tn_prices_date_code_n'),
        CheckConstraint("n_days >= 1", name='chk_lhb_tn_prices_n_days'),
        CheckConstraint("stock_code ~ '^[0-9]{6}$'", name='chk_lhb_tn_prices_stock'),
    )


class LhbAggStat(Base, IDMixin):
    """龙虎榜多日汇总统计"""
    __tablename__ = 'lhb_agg_stats'
    
    stock_code = Column(String(6), nullable=False)
    stat_date = Column(Date, nullable=False)
    lookback_days = Column(Integer, nullable=False)
    total_list_count = Column(Integer, default=0)
    total_net_buy = Column(Numeric(16, 2), default=0.00)
    avg_net_buy = Column(Numeric(16, 2), default=0.00)
    win_rate = Column(Numeric(10, 6), default=0.000000)
    avg_high_ret = Column(Numeric(10, 6), default=0.000000)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.now)
    
    __table_args__ = (
        UniqueConstraint('stock_code', 'stat_date', 'lookback_days', name='uk_lhb_agg_stats_code_date_days'),
        CheckConstraint("stock_code ~ '^[0-9]{6}$'", name='chk_lhb_agg_stats_stock'),
    )


class LhbInstitutionStat(Base, IDMixin):
    """机构席位历史胜率统计"""
    __tablename__ = 'lhb_institution_stats'
    
    seat_name = Column(String(100), nullable=False)
    stat_date = Column(Date, nullable=False)
    lookback_days = Column(Integer, nullable=False)
    total_buy_amount = Column(Numeric(16, 2), default=0.00)
    total_sell_amount = Column(Numeric(16, 2), default=0.00)
    trade_count = Column(Integer, default=0)
    win_count = Column(Integer, default=0)
    win_rate = Column(Numeric(10, 6), default=0.000000)
    avg_pnl_ratio = Column(Numeric(10, 6), default=0.000000)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.now)
    
    __table_args__ = (
        UniqueConstraint('seat_name', 'stat_date', 'lookback_days', name='uk_lhb_inst_stats_seat_date_days'),
    )


class KlineDaily(Base):
    """日K线数据（TimescaleDB hypertable）"""
    __tablename__ = 'kline_daily'
    
    trade_date = Column(Date, primary_key=True)
    stock_code = Column(String(6), primary_key=True)
    open_price = Column(Numeric(10, 3))
    high_price = Column(Numeric(10, 3))
    low_price = Column(Numeric(10, 3))
    close_price = Column(Numeric(10, 3))
    volume = Column(BigInteger)
    amount = Column(Numeric(16, 2))
    turnover_rate = Column(Numeric(10, 6))
    change_ratio = Column(Numeric(10, 6))
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.now)
    
    __table_args__ = (
        CheckConstraint("stock_code ~ '^[0-9]{6}$'", name='chk_kline_daily_stock'),
        CheckConstraint(
            """
            high_price >= low_price AND
            high_price >= open_price AND
            high_price >= close_price AND
            low_price <= open_price AND
            low_price <= close_price
            """,
            name='chk_kline_daily_price'
        ),
    )


class MarketSnapshot(Base):
    """盘中实时快照（TimescaleDB hypertable）"""
    __tablename__ = 'market_snapshots'
    
    snapshot_time = Column(DateTime(timezone=True), primary_key=True)
    stock_code = Column(String(6), primary_key=True)
    current_price = Column(Numeric(10, 3))
    bid_price1 = Column(Numeric(10, 3))
    ask_price1 = Column(Numeric(10, 3))
    bid_volume1 = Column(BigInteger)
    ask_volume1 = Column(BigInteger)
    volume = Column(BigInteger)
    amount = Column(Numeric(16, 2))
    high_price = Column(Numeric(10, 3))
    low_price = Column(Numeric(10, 3))
    open_price = Column(Numeric(10, 3))
    prev_close = Column(Numeric(10, 3))
    change_ratio = Column(Numeric(10, 6))
    turnover_rate = Column(Numeric(10, 6))
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.now)
    
    __table_args__ = (
        CheckConstraint("stock_code ~ '^[0-9]{6}$'", name='chk_market_snapshots_stock'),
    )


class MarketSentiment(Base, IDMixin):
    """市场情绪指标"""
    __tablename__ = 'market_sentiment'
    
    trade_date = Column(Date, nullable=False, unique=True)
    limit_up_count = Column(Integer, default=0)
    limit_down_count = Column(Integer, default=0)
    rise_count = Column(Integer, default=0)
    fall_count = Column(Integer, default=0)
    flat_count = Column(Integer, default=0)
    total_amount = Column(Numeric(20, 2), default=0.00)
    limit_up_ratio = Column(Numeric(10, 6), default=0.000000)
    limit_down_ratio = Column(Numeric(10, 6), default=0.000000)
    avg_turnover = Column(Numeric(10, 6), default=0.000000)
    sentiment_score = Column(Numeric(5, 2), default=50.00)
    market_stage = Column(String(20), default='normal')
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.now)
