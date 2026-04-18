"""tests/test_backtest_compare_attr.py — W10-1

覆盖:
  compare.run_compare
    - 并行跑 2 个策略, 返回 ComparisonReport, runs 按 annual_return 降序
    - 一个策略抛异常, 另一个正常 -> 不影响, 失败行 ok=False + error, ok 优先排序
    - 空输入返回空 runs, winner=None
    - winner() 取年化最高
  attribution.build_attribution
    - 按 industry / reason / seat 分桶, 只累积 closed SELL
    - seats 为空映射到 '未知'
    - 未知行业 / 原因 映射到 '未知'
    - 排序稳定: 交易多/盈利高/bucket 名字做 tiebreak
    - BucketStat.win_rate / avg_pnl_pct 计算正确
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from backend.domain.backtest.compare import run_compare
from backend.domain.backtest.attribution import (
    AttributionReport, BucketStat, build_attribution,
)
from backend.domain.backtest.engine import BacktestConfig, TradeRecord
from backend.domain.strategy import (
    BaseStrategy, Candidate, MarketContext, Position, SellSignal,
)


# 复用 test_backtest.py 的 fake 思路
TODAY = date(2024, 10, 15)
DAYS = [TODAY + timedelta(days=i) for i in range(5)]


class FakeProvider:
    def __init__(self, days, prices):
        self._days = days
        self._prices = prices

    def list_trade_days(self, start, end):
        return [d for d in self._days if start <= d <= end]

    def get_daily(self, ts_code, trade_date):
        return self._prices.get((ts_code, trade_date))

    def list_lhb_records(self, as_of):
        return []


class BuyOnceStrategy(BaseStrategy):
    name = "cmp_test"
    DEFAULT_PARAMS = {"target": "A.SZ", "buy_amount": 100_000, "sell_pct": 0.05}

    def __init__(self, params=None):
        super().__init__(params)
        self._bought = False

    def select_stocks(self, ctx):
        if self._bought: return []
        self._bought = True
        return [Candidate(ts_code=self.get_param("target"), score=100.0, reason="t")]

    def calc_buy_amount(self, candidate, cash, total):
        return float(self.get_param("buy_amount"))

    def check_sell(self, pos, ctx):
        if pos.pnl_pct >= self.get_param("sell_pct"):
            return SellSignal(reason="TP", price_hint=pos.current_price)
        return None


class ExplodingStrategy(BaseStrategy):
    name = "boom"
    def select_stocks(self, ctx): raise RuntimeError("boom")
    def calc_buy_amount(self, candidate, cash, total): return 0.0
    def check_sell(self, pos, ctx): return None


def _make_prices(up=True):
    start = 10.0
    rows = {}
    for i, d in enumerate(DAYS):
        p = start * (1 + (0.02 if up else -0.02) * i)
        rows[("A.SZ", d)] = {"open": p, "close": p, "high": p * 1.01, "low": p * 0.99, "volume": 1e6}
    return rows


# --------------------------------------------------------------------
# compare.run_compare
# --------------------------------------------------------------------


class TestRunCompare:
    def test_empty_input(self):
        provider = FakeProvider(DAYS, _make_prices())
        rep = run_compare([], provider, DAYS[0], DAYS[-1])
        assert rep.runs == []
        assert rep.winner() is None

    def test_two_strategies_sorted(self):
        provider = FakeProvider(DAYS, _make_prices(up=True))
        cfg = BacktestConfig(initial_capital=500_000)
        strategies = [
            ("up", BuyOnceStrategy({"target": "A.SZ", "buy_amount": 200_000, "sell_pct": 0.03})),
            ("noop", BuyOnceStrategy({"target": "NOPE.SZ"})),
        ]
        rep = run_compare(strategies, provider, DAYS[0], DAYS[-1], config=cfg)
        assert len(rep.runs) == 2
        assert {r.name for r in rep.runs} == {"up", "noop"}
        # 全部 ok, 排序: annual_return 降序
        assert rep.runs[0].ok and rep.runs[1].ok
        assert rep.runs[0].annual_return >= rep.runs[1].annual_return
        assert rep.winner().name == rep.runs[0].name

    def test_exploding_strategy_isolated(self, monkeypatch):
        provider = FakeProvider(DAYS, _make_prices())

        # 让一个策略在 BacktestEngine.run() 入口就抛 (compare 捕获之)
        from backend.domain.backtest import engine as eng_mod
        orig_run = eng_mod.BacktestEngine.run

        def flaky_run(self, start, end):
            if getattr(self.strategy, "_should_boom", False):
                raise RuntimeError("boom")
            return orig_run(self, start, end)

        monkeypatch.setattr(eng_mod.BacktestEngine, "run", flaky_run)

        bad = BuyOnceStrategy()
        bad._should_boom = True  # type: ignore[attr-defined]

        strategies = [
            ("ok", BuyOnceStrategy()),
            ("broken", bad),
        ]
        rep = run_compare(strategies, provider, DAYS[0], DAYS[-1])
        names_ok = {r.name: r.ok for r in rep.runs}
        assert names_ok["ok"] is True
        assert names_ok["broken"] is False
        # ok 排在前 (ok 优先)
        assert rep.runs[0].ok is True
        assert rep.runs[-1].ok is False
        broken = [r for r in rep.runs if r.name == "broken"][0]
        assert "boom" in (broken.error or "")

    def test_to_dict_shape(self):
        provider = FakeProvider(DAYS, _make_prices())
        rep = run_compare([("a", BuyOnceStrategy())], provider, DAYS[0], DAYS[-1])
        d = rep.to_dict()
        assert set(d) >= {"start_date", "end_date", "initial_capital", "runs", "winner"}


# --------------------------------------------------------------------
# attribution.build_attribution
# --------------------------------------------------------------------


def tr(ts_code, pnl_amount, pnl_pct, direction="SELL"):
    return TradeRecord(
        trade_date=TODAY, ts_code=ts_code, direction=direction,
        price=10.0, shares=100, amount=1000,
        pnl_amount=pnl_amount, pnl_pct=pnl_pct, hold_days=3,
    )


class TestAttribution:
    def test_buckets_by_industry_reason_seat(self):
        trades = [
            tr("A.SZ", 100, 0.02),
            tr("B.SZ", -50, -0.01),
            tr("C.SZ", 200, 0.05),
        ]
        meta = {
            "A.SZ": {"industry": "电子", "reason": "日涨幅", "seats": ["S1", "S2"]},
            "B.SZ": {"industry": "电子", "reason": "日涨幅", "seats": ["S1"]},
            "C.SZ": {"industry": "医药", "reason": "成交额", "seats": ["S3"]},
        }
        rep = build_attribution(trades, meta)
        # industry
        d_ind = {b.bucket: b for b in rep.by_industry}
        assert set(d_ind) == {"电子", "医药"}
        assert d_ind["电子"].trade_count == 2
        assert d_ind["电子"].win_count == 1
        assert abs(d_ind["电子"].total_pnl - 50) < 1e-9
        # seat: S1 在两行 A+B 里都出现
        d_seat = {b.bucket: b for b in rep.by_seat}
        assert d_seat["S1"].trade_count == 2
        assert d_seat["S2"].trade_count == 1
        assert d_seat["S3"].trade_count == 1
        # reason
        d_rs = {b.bucket: b for b in rep.by_reason}
        assert d_rs["日涨幅"].trade_count == 2
        assert d_rs["成交额"].trade_count == 1

    def test_closed_sell_only(self):
        trades = [
            tr("A.SZ", 10, 0.01, direction="BUY"),     # 非 SELL
            TradeRecord(
                trade_date=TODAY, ts_code="A.SZ", direction="SELL",
                price=1, shares=1, amount=1,
                pnl_amount=None, pnl_pct=None,           # 未平仓,忽略
            ),
            tr("A.SZ", 5, 0.005),                      # 算
        ]
        rep = build_attribution(trades, {"A.SZ": {"industry": "X"}})
        assert rep.by_industry[0].bucket == "X"
        assert rep.by_industry[0].trade_count == 1

    def test_unknown_buckets_labelled(self):
        trades = [tr("Z.SZ", 10, 0.01)]
        rep = build_attribution(trades, {})
        assert rep.by_industry[0].bucket == "未知"
        assert rep.by_reason[0].bucket == "未知"
        assert rep.by_seat[0].bucket == "未知"

    def test_sort_stable_by_count_then_pnl_then_name(self):
        trades = [
            tr("A", 100, 0.01),
            tr("B", 50, 0.01),
        ]
        meta = {
            "A": {"industry": "X"},
            "B": {"industry": "Y"},
        }
        rep = build_attribution(trades, meta)
        # 同 trade_count=1, X pnl=100 高 -> 排前
        assert [b.bucket for b in rep.by_industry] == ["X", "Y"]

    def test_bucketstat_win_rate_and_avg(self):
        s = BucketStat(bucket="t", trade_count=4, win_count=3,
                       total_pnl=400, sum_pnl_pct=0.12)
        assert s.win_rate == 0.75
        assert round(s.avg_pnl_pct, 4) == 0.03
        d = s.to_dict()
        assert d["win_rate"] == 0.75 and d["avg_pnl_pct"] == 0.03

    def test_to_dict_shape(self):
        rep = AttributionReport(by_industry=[BucketStat("x", 1, 1, 10, 0.1)])
        d = rep.to_dict()
        assert "by_industry" in d and "by_seat" in d and "by_reason" in d
        assert d["by_industry"][0]["bucket"] == "x"
