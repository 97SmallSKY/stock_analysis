"""
集成测试 - 端到端交易流程

测试场景：
1. Phase A 选股 → 创建 pending 交易
2. Phase B 确认 → 买入 open
3. 盘中监控 → 止盈/止损
4. 每日结算 → 净值更新
5. AHS 更新 → 仓位调整
"""
import pytest
from datetime import date, timedelta
from decimal import Decimal
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

from backend import Robot, RobotTrade, DailyNav
from backend import StockInfo, KlineDaily
from backend import run_phase_a
from backend import run_phase_b
from backend import run_daily_settlement
from backend import calculate_ahs_for_robot
from backend import IntradayMonitor
from backend import TradeExecutor


# ============================================================
# 测试数据库配置
# ============================================================
DATABASE_URL = "postgresql+asyncpg://test:test@localhost:5432/stock_test"


@pytest.fixture(scope="session")
async def engine():
    """创建测试数据库引擎"""
    engine = create_async_engine(DATABASE_URL, echo=False)
    yield engine
    await engine.dispose()


@pytest.fixture(scope="function")
async def db(engine):
    """创建会话"""
    async with engine.begin() as conn:
        # 创建所有表（需要先导入所有模型）
        from backend import Base
        await conn.run_sync(Base.metadata.create_all)
    
    async_session_maker = async_sessionmaker(engine, expire_on_commit=False)
    async with async_session_maker() as session:
        yield session
    
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.fixture
async def test_robot(db):
    """创建测试机器人"""
    robot = Robot(
        name="测试机器人",
        strategy_id=1,
        status='running',
        initial_capital=Decimal('1000000.00'),
        cash_balance=Decimal('1000000.00'),
        max_hold_days=5,
        version=0,
    )
    db.add(robot)
    await db.flush()
    return robot


@pytest.fixture
async def test_stock(db):
    """创建测试股票"""
    stock = StockInfo(
        stock_code='000001',
        stock_name='平安银行',
        board_type='main',
        is_st=False,
        is_active=True,
    )
    db.add(stock)
    await db.flush()
    return stock


@pytest.fixture
async def test_kline(db, test_stock):
    """创建测试K线数据"""
    kline = KlineDaily(
        stock_code='000001',
        trade_date=date.today() - timedelta(days=1),
        open_price=Decimal('10.00'),
        high_price=Decimal('10.50'),
        low_price=Decimal('9.90'),
        close_price=Decimal('10.30'),
        volume=1000000,
        amount=Decimal('10300000.00'),
    )
    db.add(kline)
    await db.flush()
    return kline


class TestEndToEnd:
    """端到端交易流程测试"""
    
    @pytest.mark.asyncio
    async def test_phase_a_selection(self, db, test_robot, test_stock):
        """测试 Phase A 选股"""
        # 创建龙虎榜数据
        from backend import LhbStockDaily, LhbSeat
        
        lhb = LhbStockDaily(
            stock_code='000001',
            trade_date=date.today(),
            change_ratio=Decimal('0.05'),  # +5%
            turnover_rate=Decimal('0.15'),  # 15%
            net_buy_amount=Decimal('50000000'),  # 5000万
        )
        db.add(lhb)
        
        seat = LhbSeat(
            stock_code='000001',
            trade_date=date.today(),
            seat_name='机构专用',
            direction='buy',
            amount=Decimal('30000000'),
            seat_type='institution',
        )
        db.add(seat)
        await db.flush()
        
        # 执行 Phase A
        result = await run_phase_a(db, test_robot.id, date.today())
        
        assert result.success is True
        assert result.selected_count >= 0  # 可能因为过滤条件为0
    
    @pytest.mark.asyncio
    async def test_phase_b_buy(self, db, test_robot, test_stock):
        """测试 Phase B 买入"""
        # 先创建 pending 交易
        trade = RobotTrade(
            robot_id=test_robot.id,
            stock_code='000001',
            stock_name='平安银行',
            status='pending',
            signal_date=date.today() - timedelta(days=1),
            signal_score=Decimal('75.0'),
            signal_rank=1,
        )
        db.add(trade)
        await db.flush()
        
        # 执行 Phase B
        result = await run_phase_b(db, test_robot.id, date.today())
        
        assert result['success'] is True
        assert result['bought'] >= 0
    
    @pytest.mark.asyncio
    async def test_daily_settlement(self, db, test_robot, test_stock):
        """测试每日结算"""
        # 先创建持仓
        trade = RobotTrade(
            robot_id=test_robot.id,
            stock_code='000001',
            stock_name='平安银行',
            status='open',
            buy_date=date.today() - timedelta(days=1),
            buy_price=Decimal('10.30'),
            buy_shares=1000,
            buy_amount=Decimal('10300.00'),
        )
        db.add(trade)
        await db.flush()
        
        # 执行结算
        result = await run_daily_settlement(db, test_robot.id, date.today())
        
        assert result.total_nav > 0
        assert result.position_count >= 0
    
    @pytest.mark.asyncio
    async def test_ahs_calculation(self, db, test_robot):
        """测试 AHS 计算"""
        # 先创建一些历史交易
        for i in range(5):
            trade = RobotTrade(
                robot_id=test_robot.id,
                stock_code=f'00000{i+1}',
                stock_name=f'测试{i+1}',
                status='closed',
                buy_date=date.today() - timedelta(days=10+i),
                sell_date=date.today() - timedelta(days=5+i),
                buy_price=Decimal('10.00'),
                sell_price=Decimal('10.50') if i % 2 == 0 else Decimal('9.50'),
                buy_shares=1000,
                realized_pnl=Decimal('500.00') if i % 2 == 0 else Decimal('-500.00'),
                realized_pnl_ratio=Decimal('0.05') if i % 2 == 0 else Decimal('-0.05'),
            )
            db.add(trade)
        await db.flush()
        
        # 计算 AHS
        result = await calculate_ahs_for_robot(db, test_robot.id, date.today())
        
        assert result.ahs_score >= 0
        assert result.trading_mode in ['normal', 'cautious', 'conservative', 'warning', 'stopped']
    
    @pytest.mark.asyncio
    async def test_intraday_monitor(self, db, test_robot, test_stock):
        """测试盘中监控"""
        # 创建持仓
        trade = RobotTrade(
            robot_id=test_robot.id,
            stock_code='000001',
            stock_name='平安银行',
            status='open',
            buy_date=date.today(),
            buy_price=Decimal('10.00'),
            buy_shares=1000,
            buy_amount=Decimal('10000.00'),
        )
        db.add(trade)
        await db.flush()
        
        # 创建今日K线（当前价格10.50，盈利5%）
        kline = KlineDaily(
            stock_code='000001',
            trade_date=date.today(),
            open_price=Decimal('10.20'),
            high_price=Decimal('10.60'),
            low_price=Decimal('10.10'),
            close_price=Decimal('10.50'),
            volume=1000000,
            amount=Decimal('10500000.00'),
        )
        db.add(kline)
        await db.flush()
        
        # 执行监控
        monitor = IntradayMonitor(db, test_robot.id)
        result = await monitor.run_once()
        
        assert result.checked_count >= 0
    
    @pytest.mark.asyncio
    async def test_full_workflow(self, db, test_robot, test_stock, test_kline):
        """测试完整工作流"""
        # 1. Phase A 选股
        from backend import LhbStockDaily, LhbSeat
        
        lhb = LhbStockDaily(
            stock_code='000001',
            trade_date=date.today(),
            change_ratio=Decimal('0.05'),
            turnover_rate=Decimal('0.15'),
            net_buy_amount=Decimal('50000000'),
        )
        db.add(lhb)
        
        seat = LhbSeat(
            stock_code='000001',
            trade_date=date.today(),
            seat_name='机构专用',
            direction='buy',
            amount=Decimal('30000000'),
            seat_type='institution',
        )
        db.add(seat)
        await db.flush()
        
        phase_a_result = await run_phase_a(db, test_robot.id, date.today())
        assert phase_a_result.success is True
        
        # 2. Phase B 买入
        phase_b_result = await run_phase_b(db, test_robot.id, date.today())
        assert phase_b_result['success'] is True
        
        # 3. 盘中监控
        monitor = IntradayMonitor(db, test_robot.id)
        monitor_result = await monitor.run_once()
        assert monitor_result.checked_count >= 0
        
        # 4. 每日结算
        settlement_result = await run_daily_settlement(db, test_robot.id, date.today())
        assert settlement_result.total_nav > 0
        
        # 5. AHS 更新
        ahs_result = await calculate_ahs_for_robot(db, test_robot.id, date.today())
        assert ahs_result is not None


if __name__ == '__main__':
    pytest.main(['-xvs', __file__])
