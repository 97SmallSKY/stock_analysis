"""
过滤条件函数集合

过滤职责分层：
1. 数据层过滤：ST股票、退市股票、北交所股票
2. 策略层过滤：净买入阈值、涨跌幅、技术面最低分、已持仓、已候选
3. 风控层过滤：AHS允许开仓、单笔上限、单日上限、仓位已满

参考: docs/lhb-quant-v2/STRATEGY.md §三
"""
from datetime import date
from typing import Optional
from loguru import logger

from sqlalchemy import select, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession

from backend import StockInfo, MarketSentiment
from backend import RobotTrade, Robot
from backend import StrategyParam


# ============================================================
# 数据层过滤（在 data_providers 层获取数据时排除）
# ============================================================
async def filter_st_stocks(
    db: AsyncSession,
    stock_codes: list[str]
) -> list[str]:
    """
    过滤ST股票
    
    Args:
        db: 数据库会话
        stock_codes: 待检查股票代码列表
    
    Returns:
        过滤后的股票代码列表（排除ST股）
    """
    if not stock_codes:
        return []
    
    result = await db.execute(
        select(StockInfo.stock_code).where(
            and_(
                StockInfo.stock_code.in_(stock_codes),
                StockInfo.is_st == False,
                StockInfo.is_active == True
            )
        )
    )
    return [row[0] for row in result.all()]


async def get_st_stocks(
    db: AsyncSession,
    stock_codes: list[str]
) -> list[str]:
    """
    获取ST股票列表
    
    Args:
        db: 数据库会话
        stock_codes: 待检查股票代码列表
    
    Returns:
        ST股票代码列表
    """
    if not stock_codes:
        return []
    
    result = await db.execute(
        select(StockInfo.stock_code).where(
            and_(
                StockInfo.stock_code.in_(stock_codes),
                StockInfo.is_st == True
            )
        )
    )
    return [row[0] for row in result.all()]


async def filter_delisted_stocks(
    db: AsyncSession,
    stock_codes: list[str]
) -> list[str]:
    """
    过滤退市股票
    
    Args:
        db: 数据库会话
        stock_codes: 待检查股票代码列表
    
    Returns:
        过滤后的股票代码列表（排除退市股）
    """
    if not stock_codes:
        return []
    
    result = await db.execute(
        select(StockInfo.stock_code).where(
            and_(
                StockInfo.stock_code.in_(stock_codes),
                StockInfo.is_active == True
            )
        )
    )
    return [row[0] for row in result.all()]


async def filter_bse_stocks(
    db: AsyncSession,
    stock_codes: list[str],
    enabled: bool = True
) -> list[str]:
    """
    过滤北交所股票
    
    Args:
        db: 数据库会话
        stock_codes: 待检查股票代码列表
        enabled: 是否启用北交所过滤（默认启用）
    
    Returns:
        过滤后的股票代码列表（排除北交所）
    
    Note:
        北交所股票代码特征：
        - 8开头：82/83/87/88开头（创新层/基础层）
        - 4开头：43/83开头
    """
    if not stock_codes:
        return []
    
    if not enabled:
        return stock_codes
    
    # 北交所代码前缀
    bse_prefixes = ('82', '83', '87', '88', '43')
    
    # 先从数据库过滤
    result = await db.execute(
        select(StockInfo.stock_code).where(
            and_(
                StockInfo.stock_code.in_(stock_codes),
                StockInfo.board_type != 'bse'
            )
        )
    )
    db_filtered = [row[0] for row in result.all()]
    
    # 二次过滤（代码前缀）
    return [code for code in db_filtered if not code.startswith(bse_prefixes)]


async def get_stock_info_map(
    db: AsyncSession,
    stock_codes: list[str]
) -> dict:
    """
    批量获取股票基础信息
    
    Args:
        db: 数据库会话
        stock_codes: 股票代码列表
    
    Returns:
        {stock_code: StockInfo} 字典
    """
    if not stock_codes:
        return {}
    
    result = await db.execute(
        select(StockInfo).where(StockInfo.stock_code.in_(stock_codes))
    )
    return {stock.stock_code: stock for stock in result.scalars().all()}


# ============================================================
# 策略层过滤（在 scorer.py 评分前过滤）
# ============================================================
def filter_net_buy_threshold(
    net_buy_amount: float,
    min_net_buy: float = 50_000_000.0  # 默认5000万
) -> bool:
    """
    净买入阈值过滤
    
    Args:
        net_buy_amount: 净买入额（元）
        min_net_buy: 最低净买入阈值
    
    Returns:
        True 表示通过过滤
    """
    return net_buy_amount >= min_net_buy


def filter_positive_change(
    change_ratio: float
) -> bool:
    """
    涨跌幅过滤（必须为正）
    
    Args:
        change_ratio: 涨跌幅
    
    Returns:
        True 表示通过过滤
    """
    return change_ratio > 0


def filter_min_tech_score(
    tech_score: float,
    min_tech_score: float = 50.0
) -> bool:
    """
    技术面最低分过滤
    
    Args:
        tech_score: 技术面得分
        min_tech_score: 最低技术面得分
    
    Returns:
        True 表示通过过滤
    """
    return tech_score >= min_tech_score


async def filter_existing_positions(
    db: AsyncSession,
    robot_id: int,
    stock_codes: list[str]
) -> list[str]:
    """
    过滤已持仓股票
    
    Args:
        db: 数据库会话
        robot_id: 机器人ID
        stock_codes: 待检查股票代码列表
    
    Returns:
        过滤后的股票代码列表（排除已持仓）
    
    Note:
        持仓状态: open, partial
    """
    if not stock_codes:
        return []
    
    # 查询当前持仓
    result = await db.execute(
        select(RobotTrade.stock_code).where(
            and_(
                RobotTrade.robot_id == robot_id,
                RobotTrade.stock_code.in_(stock_codes),
                RobotTrade.status.in_(['open', 'partial']),
                RobotTrade.deleted_at.is_(None)
            )
        )
    )
    held_codes = {row[0] for row in result.all()}
    
    return [code for code in stock_codes if code not in held_codes]


async def filter_pending_candidates(
    db: AsyncSession,
    robot_id: int,
    stock_codes: list[str],
    signal_date: date
) -> list[str]:
    """
    过滤已候选股票
    
    Args:
        db: 数据库会话
        robot_id: 机器人ID
        stock_codes: 待检查股票代码列表
        signal_date: 信号日期
    
    Returns:
        过滤后的股票代码列表（排除已候选）
    
    Note:
        候选状态: pending
    """
    if not stock_codes:
        return []
    
    # 查询pending状态的交易
    result = await db.execute(
        select(RobotTrade.stock_code).where(
            and_(
                RobotTrade.robot_id == robot_id,
                RobotTrade.stock_code.in_(stock_codes),
                RobotTrade.status == 'pending',
                RobotTrade.signal_date >= signal_date,  # 当日或之后
                RobotTrade.deleted_at.is_(None)
            )
        )
    )
    pending_codes = {row[0] for row in result.all()}
    
    return [code for code in stock_codes if code not in pending_codes]


async def get_existing_position_codes(
    db: AsyncSession,
    robot_id: int
) -> set[str]:
    """
    获取机器人当前持仓股票代码集合
    
    Args:
        db: 数据库会话
        robot_id: 机器人ID
    
    Returns:
        持仓股票代码集合
    """
    result = await db.execute(
        select(RobotTrade.stock_code).where(
            and_(
                RobotTrade.robot_id == robot_id,
                RobotTrade.status.in_(['open', 'partial']),
                RobotTrade.deleted_at.is_(None)
            )
        )
    )
    return {row[0] for row in result.all()}


async def get_pending_candidate_codes(
    db: AsyncSession,
    robot_id: int,
    signal_date: date
) -> set[str]:
    """
    获取机器人待买入股票代码集合
    
    Args:
        db: 数据库会话
        robot_id: 机器人ID
        signal_date: 信号日期
    
    Returns:
        待买入股票代码集合
    """
    result = await db.execute(
        select(RobotTrade.stock_code).where(
            and_(
                RobotTrade.robot_id == robot_id,
                RobotTrade.status == 'pending',
                RobotTrade.signal_date >= signal_date,
                RobotTrade.deleted_at.is_(None)
            )
        )
    )
    return {row[0] for row in result.all()}


# ============================================================
# 风控层过滤（在 risk_service.py 评分后过滤）
# ============================================================
async def check_ahs_allows_open(
    db: AsyncSession,
    robot_id: int,
    threshold_stop: float = 81.0
) -> tuple[bool, Optional[str]]:
    """
    检查AHS是否允许开仓
    
    Args:
        db: 数据库会话
        robot_id: 机器人ID
        threshold_stop: 停止开仓阈值（默认81分）
    
    Returns:
        (allowed, reason): 是否允许和原因
    """
    from backend import AhsHistory
    
    # 获取最新AHS评分
    result = await db.execute(
        select(AhsHistory).where(
            AhsHistory.robot_id == robot_id
        ).order_by(AhsHistory.score_date.desc()).limit(1)
    )
    ahs = result.scalar_one_or_none()
    
    if ahs is None:
        # 无评分记录，使用冷启动分数
        logger.warning(f"[Filter] 机器人 {robot_id} 无AHS历史，允许开仓（冷启动）")
        return True, None
    
    if ahs.ahs_score >= threshold_stop:
        return False, f"AHS评分 {ahs.ahs_score:.1f} >= 停仓阈值 {threshold_stop}"
    
    return True, None


async def check_single_position_limit(
    buy_amount: float,
    total_assets: float,
    max_ratio: float = 0.20
) -> tuple[bool, Optional[str]]:
    """
    检查单笔买入上限
    
    Args:
        buy_amount: 买入金额
        total_assets: 总资产
        max_ratio: 最大占比（默认20%）
    
    Returns:
        (passed, reason): 是否通过和原因
    """
    if buy_amount > total_assets * max_ratio:
        return False, f"单笔买入 {buy_amount:.2f} 超过总资产 {max_ratio:.0%} 上限"
    return True, None


async def check_daily_trade_limit(
    robot_id: int,
    new_buy_amount: float,
    total_assets: float,
    max_ratio: float = 0.50,
    db: Optional[AsyncSession] = None
) -> tuple[bool, Optional[str]]:
    """
    检查单日买入总额上限
    
    Args:
        robot_id: 机器人ID
        new_buy_amount: 新买入金额
        total_assets: 总资产
        max_ratio: 最大占比（默认50%）
        db: 数据库会话
    
    Returns:
        (passed, reason): 是否通过和原因
    """
    if db is None:
        return True, None
    
    from datetime import datetime
    from backend import TradeExecution
    
    today = date.today()
    today_start = datetime.combine(today, datetime.min.time())
    
    # 查询今日买入总额
    result = await db.execute(
        select(TradeExecution.execution_amount).where(
            and_(
                TradeExecution.trade_id.in_(
                    select(RobotTrade.id).where(RobotTrade.robot_id == robot_id)
                ),
                TradeExecution.execution_type == 'buy',
                TradeExecution.execution_time >= today_start
            )
        )
    )
    today_total = sum(row[0] for row in result.all())
    
    if today_total + new_buy_amount > total_assets * max_ratio:
        return False, f"当日买入总额 {today_total + new_buy_amount:.2f} 超过总资产 {max_ratio:.0%} 上限"
    
    return True, None


async def check_position_count_limit(
    db: AsyncSession,
    robot_id: int,
    max_positions: int = 3
) -> tuple[bool, Optional[str]]:
    """
    检查持仓数量限制
    
    Args:
        db: 数据库会话
        robot_id: 机器人ID
        max_positions: 最大持仓数（默认3）
    
    Returns:
        (passed, reason): 是否通过和原因
    """
    result = await db.execute(
        select(RobotTrade.stock_code).where(
            and_(
                RobotTrade.robot_id == robot_id,
                RobotTrade.status.in_(['open', 'partial']),
                RobotTrade.deleted_at.is_(None)
            )
        )
    )
    current_count = len(result.all())
    
    # 还要加上pending状态的数量
    pending_result = await db.execute(
        select(RobotTrade.stock_code).where(
            and_(
                RobotTrade.robot_id == robot_id,
                RobotTrade.status == 'pending',
                RobotTrade.deleted_at.is_(None)
            )
        )
    )
    pending_count = len(pending_result.all())
    
    total_count = current_count + pending_count
    
    if total_count >= max_positions:
        return False, f"当前持仓 {current_count} + 待买入 {pending_count} 已达上限 {max_positions}"
    
    return True, None


async def check_concentration_limit(
    db: AsyncSession,
    robot_id: int,
    stock_code: str,
    new_buy_amount: float,
    total_assets: float,
    max_ratio: float = 0.30
) -> tuple[bool, Optional[str]]:
    """
    检查单只股票集中度上限
    
    Args:
        db: 数据库会话
        robot_id: 机器人ID
        stock_code: 股票代码
        new_buy_amount: 新买入金额
        total_assets: 总资产
        max_ratio: 最大占比（默认30%）
    
    Returns:
        (passed, reason): 是否通过和原因
    """
    # 查询该股票当前持仓市值
    result = await db.execute(
        select(RobotTrade).where(
            and_(
                RobotTrade.robot_id == robot_id,
                RobotTrade.stock_code == stock_code,
                RobotTrade.status.in_(['open', 'partial']),
                RobotTrade.deleted_at.is_(None)
            )
        )
    )
    existing_trades = result.scalars().all()
    
    # 计算当前持仓市值（需要从 trade_executions 聚合）
    existing_amount = 0.0
    for trade in existing_trades:
        # 获取买入金额
        existing_amount += float(trade.buy_amount or 0)
    
    if existing_amount + new_buy_amount > total_assets * max_ratio:
        return False, (
            f"持仓 {stock_code} 集中度 "
            f"{(existing_amount + new_buy_amount) / total_assets:.1%} "
            f"超过 {max_ratio:.0%} 上限"
        )
    
    return True, None


# ============================================================
# 组合过滤函数
# ============================================================
async def apply_data_layer_filters(
    db: AsyncSession,
    stock_codes: list[str],
    exclude_bse: bool = True
) -> list[str]:
    """
    应用数据层过滤
    
    Args:
        db: 数据库会话
        stock_codes: 原始股票代码列表
        exclude_bse: 是否排除北交所
    
    Returns:
        过滤后的股票代码列表
    """
    if not stock_codes:
        return []
    
    # 1. 排除ST股
    stocks = await filter_st_stocks(db, stock_codes)
    logger.debug(f"[Filter] ST过滤: {len(stock_codes)} -> {len(stocks)}")
    
    # 2. 排除退市股
    stocks = await filter_delisted_stocks(db, stocks)
    logger.debug(f"[Filter] 退市过滤: {len(stocks)}")
    
    # 3. 排除北交所
    if exclude_bse:
        stocks = await filter_bse_stocks(db, stocks)
        logger.debug(f"[Filter] 北交所过滤: {len(stocks)}")
    
    return stocks


async def apply_strategy_layer_filters(
    db: AsyncSession,
    robot_id: int,
    stock_codes: list[str],
    signal_date: date
) -> list[str]:
    """
    应用策略层过滤
    
    Args:
        db: 数据库会话
        robot_id: 机器人ID
        stock_codes: 待过滤股票代码列表
        signal_date: 信号日期
    
    Returns:
        过滤后的股票代码列表
    """
    if not stock_codes:
        return []
    
    # 1. 排除已持仓
    stocks = await filter_existing_positions(db, robot_id, stock_codes)
    logger.debug(f"[Filter] 已持仓过滤: {len(stock_codes)} -> {len(stocks)}")
    
    # 2. 排除已候选
    stocks = await filter_pending_candidates(db, robot_id, stocks, signal_date)
    logger.debug(f"[Filter] 已候选过滤: {len(stocks)}")
    
    return stocks


async def get_rejection_reason(
    db: AsyncSession,
    robot_id: int,
    stock_code: str,
    buy_amount: float,
    total_assets: float,
    signal_date: date
) -> Optional[str]:
    """
    获取股票被拒绝的原因
    
    Returns:
        None 表示通过，否则返回拒绝原因
    """
    # 检查持仓数量
    passed, reason = await check_position_count_limit(db, robot_id)
    if not passed:
        return reason
    
    # 检查单笔上限
    passed, reason = await check_single_position_limit(buy_amount, total_assets)
    if not passed:
        return reason
    
    # 检查集中度
    passed, reason = await check_concentration_limit(
        db, robot_id, stock_code, buy_amount, total_assets
    )
    if not passed:
        return reason
    
    # 检查AHS
    passed, reason = await check_ahs_allows_open(db, robot_id)
    if not passed:
        return reason
    
    return None
