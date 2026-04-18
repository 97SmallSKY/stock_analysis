"""回测引擎 + 组合 + 指标报告测试 (纯内存, 无 DB)."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

import pytest

from backend.domain.backtest import (
    BacktestEngine,
    NavPoint,
    Portfolio,
    compute_metrics,
)
from backend.domain.backtest.engine import BacktestConfig as BC
from backend.domain.robot import RobotSnapshot, PositionState, RobotState
from backend.domain.strategy import (
    BaseStrategy,
    Candidate,
    MarketContext,
    Position,
    SellSignal,
)


TODAY = date(2024, 10, 15)


# --------------------------------------------------------------------
# Fake provider: 预生成交易日 + 价格
# --------------------------------------------------------------------


class FakeProvider:
    def __init__(self, days, prices, lhb_by_day=None):
        """prices: {(ts_code, date): {'open':x,'close':y}}"""
        self._days = days
        self._prices = prices
        self._lhb = lhb_by_day or {}

    def list_trade_days(self, start, end):
        return [d for d in self._days if start <= d <= end]

    def get_daily(self, ts_code, trade_date):
        return self._prices.get((ts_code, trade_date))

    def list_lhb_records(self, as_of):
        return self._lhb.get(as_of, [])


# --------------------------------------------------------------------
# 一个确定性的测试策略: 第一天买 A, 涨 5% 卖出
# --------------------------------------------------------------------


class BuyOnceSellIfUpStrategy(BaseStrategy):
    name = 'test_buy_once'
    DEFAULT_PARAMS = {
        'target': 'A.SZ',
        'buy_amount': 100_000,
        'take_profit_pct': 0.05,
        'score_threshold': 0,    # 不检查
    }

    def __init__(self, params=None):
        super().__init__(params)
        self._bought = False

    def select_stocks(self, ctx):
        if self._bought:
            return []
        self._bought = True
        return [Candidate(
            ts_code=self.get_param('target'),
            score=100.0, reason='test',
        )]

    def calc_buy_amount(self, candidate, cash, total):
        return float(self.get_param('buy_amount'))

    def check_sell(self, position, ctx):
        if position.pnl_pct >= self.get_param('take_profit_pct'):
            return SellSignal(reason='TAKE_PROFIT',
                              price_hint=position.current_price)
        return None


# --------------------------------------------------------------------
# Portfolio 单测
# --------------------------------------------------------------------


class TestPortfolio:
    def test_initial_state(self):
        pf = Portfolio(initial_capital=1_000_000)
        assert pf.latest_nav() == 1.0
        assert pf.latest_total_assets() == 1_000_000

    def test_mark_to_market_flat_cash(self):
        pf = Portfolio(initial_capital=1_000_000)
        snap = RobotSnapshot(robot_id=0, state=RobotState.RUNNING,
                             cash=1_000_000, total_assets=1_000_000)
        pt = pf.mark_to_market(TODAY, snap, close_prices={})
        assert pt.nav == 1.0
        assert pt.total_assets == 1_000_000
        assert pt.position_count == 0

    def test_mark_to_market_with_position(self):
        pf = Portfolio(initial_capital=1_000_000)
        pos = PositionState('A.SZ', 1000, 10.0, TODAY, current_price=10.0)
        snap = RobotSnapshot(robot_id=0, state=RobotState.RUNNING,
                             cash=990_000, total_assets=0,
                             positions=(pos,))
        pt = pf.mark_to_market(TODAY, snap, close_prices={'A.SZ': 12.0})
        # 持仓市值 = 12*1000 = 12000; total = 990000+12000 = 1002000
        assert pt.total_assets == 1_002_000
        assert pt.position_value == 12_000
        assert pt.nav == round(1_002_000 / 1_000_000, 6)


# --------------------------------------------------------------------
# Engine 单测
# --------------------------------------------------------------------


def _make_daily_series(ts_code, start, opens_closes):
    """(ts_code, start date, [(open,close), ...]) -> dict"""
    out = {}
    d = start
    for oc in opens_closes:
        if oc is None:
            d += timedelta(days=1)
            continue
        op, cl = oc
        out[(ts_code, d)] = {'open': op, 'close': cl, 'high': max(op, cl),
                             'low': min(op, cl)}
        d += timedelta(days=1)
    return out


class TestEngine:
    def test_empty_days_returns_flat(self):
        provider = FakeProvider(days=[], prices={})
        strat = BuyOnceSellIfUpStrategy()
        eng = BacktestEngine(strat, provider, BC(initial_capital=500_000))
        r = eng.run(TODAY, TODAY)
        assert r.final_assets == 500_000
        assert r.trades == []

    def test_buy_open_sell_on_profit(self):
        # 三天: D1 open=10, close=10;  D2 open=10.5, close=11;  D3 open=11, close=12
        d1 = date(2024, 1, 2)
        d2 = date(2024, 1, 3)
        d3 = date(2024, 1, 4)
        prices = {
            ('A.SZ', d1): {'open': 10.0, 'close': 10.0},
            ('A.SZ', d2): {'open': 10.5, 'close': 11.0},
            ('A.SZ', d3): {'open': 11.0, 'close': 12.0},
        }
        provider = FakeProvider(days=[d1, d2, d3], prices=prices)
        strat = BuyOnceSellIfUpStrategy(params={
            'target': 'A.SZ', 'buy_amount': 100_000,
            'take_profit_pct': 0.05,
        })
        cfg = BC(initial_capital=1_000_000, slippage_rate=0.0,
                 commission_rate=0.0, stamp_rate=0.0,
                 per_position_pct=0.5, risk_config={
                     'daily_loss_limit': -0.5, 'score_threshold': 0,
                 })
        r = BacktestEngine(strat, provider, cfg).run(d1, d3)

        # D1 盘后选股: 产出 buy A
        # D2 开盘执行买入 @10.5 * 100股 可买 100000/1050 = 95手 = 9500股
        # D2 收盘 close=11, 浮盈 (11-10.5)/10.5 = 4.76% < 5% -> 不卖
        # D3 收盘 close=12, 浮盈 (12-10.5)/10.5 = 14.29% >= 5% -> 卖出 @12
        directions = [t['direction'] for t in r.trades]
        assert directions == ['BUY', 'SELL']
        buy = r.trades[0]
        sell = r.trades[1]
        assert buy['ts_code'] == 'A.SZ'
        assert buy['shares'] == 9500
        assert sell['ts_code'] == 'A.SZ'
        assert sell['pnl_amount'] > 0
        # 最终资产 > 初始
        assert r.final_assets > r.initial_capital

    def test_no_sell_if_below_take_profit(self):
        d1 = date(2024, 1, 2)
        d2 = date(2024, 1, 3)
        d3 = date(2024, 1, 4)
        prices = {
            ('A.SZ', d1): {'open': 10.0, 'close': 10.0},
            ('A.SZ', d2): {'open': 10.0, 'close': 10.1},
            ('A.SZ', d3): {'open': 10.0, 'close': 10.1},
        }
        provider = FakeProvider([d1, d2, d3], prices)
        strat = BuyOnceSellIfUpStrategy(params={
            'take_profit_pct': 0.05, 'buy_amount': 100_000,
        })
        cfg = BC(initial_capital=1_000_000, slippage_rate=0.0,
                 commission_rate=0.0, stamp_rate=0.0,
                 per_position_pct=0.5, risk_config={
                     'daily_loss_limit': -0.5, 'score_threshold': 0,
                 })
        r = BacktestEngine(strat, provider, cfg).run(d1, d3)
        directions = [t['direction'] for t in r.trades]
        assert 'SELL' not in directions   # 没触发止盈

    def test_skip_day_without_data(self):
        d1 = date(2024, 1, 2)
        d2 = date(2024, 1, 3)
        prices = {
            ('A.SZ', d1): {'open': 10.0, 'close': 10.0},
            # d2 无数据 -> 执行买入会失败
        }
        provider = FakeProvider([d1, d2], prices)
        strat = BuyOnceSellIfUpStrategy(params={'buy_amount': 100_000})
        cfg = BC(initial_capital=1_000_000, risk_config={
            'daily_loss_limit': -0.5, 'score_threshold': 0,
        })
        r = BacktestEngine(strat, provider, cfg).run(d1, d2)
        # 没有 BUY 记录
        assert all(t['direction'] != 'BUY' for t in r.trades)

    def test_nav_series_length_equals_days(self):
        d1 = date(2024, 1, 2)
        d2 = date(2024, 1, 3)
        d3 = date(2024, 1, 4)
        prices = {
            ('A.SZ', d): {'open': 10.0, 'close': 10.0}
            for d in [d1, d2, d3]
        }
        provider = FakeProvider([d1, d2, d3], prices)
        strat = BuyOnceSellIfUpStrategy()
        cfg = BC(initial_capital=1_000_000, risk_config={
            'daily_loss_limit': -0.5, 'score_threshold': 0,
        })
        r = BacktestEngine(strat, provider, cfg).run(d1, d3)
        assert len(r.nav_series) == 3
        assert r.nav_series[0]['date'] == d1.isoformat()
        assert r.nav_series[-1]['date'] == d3.isoformat()


# --------------------------------------------------------------------
# Report 指标
# --------------------------------------------------------------------


class TestReport:
    def test_empty_result_zeros(self):
        from backend.domain.backtest.engine import BacktestResult
        res = BacktestResult(
            start_date=TODAY, end_date=TODAY,
            initial_capital=1_000_000, final_assets=1_000_000,
            nav_series=[], trades=[],
        )
        m = compute_metrics(res)
        assert m.total_return == 0.0
        assert m.sharpe_ratio == 0.0
        assert m.trade_count == 0

    def test_positive_return_and_drawdown(self):
        """手工构造 nav_series: 涨 20% -> 回撤 10% -> 新高 30%."""
        from backend.domain.backtest.engine import BacktestResult, TradeRecord

        def make_point(d, nav):
            total = nav * 1_000_000
            return NavPoint(
                trade_date=d, total_assets=total, cash=total,
                position_value=0, position_count=0, nav=nav,
            )

        nav_raw = [
            make_point(date(2024, 1, 2), 1.00),
            make_point(date(2024, 1, 3), 1.10),
            make_point(date(2024, 1, 4), 1.20),
            make_point(date(2024, 1, 5), 1.08),   # 从 1.2 回撤 10%
            make_point(date(2024, 1, 8), 1.30),
        ]
        trades_raw = [
            TradeRecord(date(2024, 1, 3), 'A.SZ', 'BUY',
                        price=10, shares=1000, amount=10000),
            TradeRecord(date(2024, 1, 5), 'A.SZ', 'SELL',
                        price=12, shares=1000, amount=12000,
                        pnl_amount=2000, pnl_pct=0.2, hold_days=2),
            TradeRecord(date(2024, 1, 4), 'B.SZ', 'BUY',
                        price=20, shares=500, amount=10000),
            TradeRecord(date(2024, 1, 8), 'B.SZ', 'SELL',
                        price=18, shares=500, amount=9000,
                        pnl_amount=-1000, pnl_pct=-0.1, hold_days=4),
        ]
        res = BacktestResult(
            start_date=date(2024, 1, 2), end_date=date(2024, 1, 8),
            initial_capital=1_000_000, final_assets=1_300_000,
            nav_series=[], trades=[],
            _nav_raw=nav_raw, _trades_raw=trades_raw,
        )
        m = compute_metrics(res)
        assert m.total_return == pytest.approx(0.30, abs=1e-6)
        assert m.max_drawdown < 0
        assert m.max_drawdown == pytest.approx(-0.10, abs=1e-6)
        assert m.trade_count == 4
        assert m.win_count == 1
        assert m.lose_count == 1
        assert m.win_rate == 0.5
        assert m.avg_win > 0
        assert m.avg_loss < 0
        assert m.equity_curve[0]['nav'] == 1.0
        assert m.drawdown_curve[-1]['drawdown'] == 0.0   # 最后是新高
        # 月度收益至少有 2024-01 一个
        assert '2024-01' in m.monthly_returns

    def test_sharpe_non_zero_when_volatility(self):
        from backend.domain.backtest.engine import BacktestResult

        navs = [1.0, 1.01, 0.99, 1.02, 1.01, 1.05]
        nav_raw = [
            NavPoint(trade_date=date(2024, 1, 2 + i),
                     total_assets=nav * 1_000_000,
                     cash=nav * 1_000_000, position_value=0,
                     position_count=0, nav=nav)
            for i, nav in enumerate(navs)
        ]
        res = BacktestResult(
            start_date=date(2024, 1, 2), end_date=date(2024, 1, 7),
            initial_capital=1_000_000, final_assets=1_050_000,
            nav_series=[], trades=[],
            _nav_raw=nav_raw, _trades_raw=[],
        )
        m = compute_metrics(res)
        assert m.volatility > 0
        # 正收益 + 波动 -> sharpe 正数
        assert m.sharpe_ratio > 0
