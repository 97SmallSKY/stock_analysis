"""domain.strategy 基类 + 注册表 + 内置策略测试."""
from __future__ import annotations

from datetime import date

import pytest

from backend.domain.strategy import (
    BaseStrategy,
    Candidate,
    MarketContext,
    Position,
    SellSignal,
    clear_registry,
    get_strategy,
    list_strategies,
    register_strategy,
)


# --------------------------------------------------------------------
# 数据类
# --------------------------------------------------------------------


class TestDataclasses:
    def test_position_pnl(self):
        p = Position("000001.SZ", 1000, 10.0, 11.0,
                     open_date=date(2024, 10, 1), hold_days=3)
        assert p.pnl_pct == pytest.approx(0.1)

    def test_position_zero_cost(self):
        p = Position("x", 100, 0.0, 1.0,
                     open_date=date(2024, 1, 1), hold_days=0)
        assert p.pnl_pct == 0.0

    def test_candidate_defaults(self):
        c = Candidate("600519.SH", 85.0)
        assert c.reason == ""
        assert c.signal_detail == {}
        assert c.reference_price is None


# --------------------------------------------------------------------
# BaseStrategy 抽象
# --------------------------------------------------------------------


class TestBaseStrategy:
    def test_cannot_instantiate_abstract(self):
        with pytest.raises(TypeError):
            BaseStrategy()  # type: ignore

    def test_params_merge(self):
        class S(BaseStrategy):
            DEFAULT_PARAMS = {"a": 1, "b": 2}

            def select_stocks(self, ctx): return []
            def calc_buy_amount(self, c, cash, total): return 0.0
            def check_sell(self, p, ctx): return None

        s = S(params={"b": 99, "c": 3})
        assert s.params == {"a": 1, "b": 99, "c": 3}
        assert s.get_param("a") == 1
        assert s.get_param("missing", "fallback") == "fallback"

    def test_params_isolation_between_instances(self):
        class S(BaseStrategy):
            DEFAULT_PARAMS = {"a": 1}

            def select_stocks(self, ctx): return []
            def calc_buy_amount(self, c, cash, total): return 0.0
            def check_sell(self, p, ctx): return None

        s1 = S()
        s2 = S()
        s1.params["a"] = 99
        assert s2.params["a"] == 1


# --------------------------------------------------------------------
# registry
# --------------------------------------------------------------------


class TestRegistry:
    @pytest.fixture(autouse=True)
    def _clean(self):
        clear_registry()
        yield
        clear_registry()

    def _make_stub(self):
        class Stub(BaseStrategy):
            def select_stocks(self, ctx): return []
            def calc_buy_amount(self, c, cash, total): return 0.0
            def check_sell(self, p, ctx): return None
        return Stub

    def test_register_and_get(self):
        Stub = self._make_stub()
        register_strategy("my_x")(Stub)
        assert "my_x" in list_strategies()
        assert get_strategy("my_x") is Stub
        assert Stub.name == "my_x"

    def test_register_uses_class_name_attr(self):
        Stub = self._make_stub()
        Stub.name = "stub_from_attr"
        register_strategy()(Stub)
        assert "stub_from_attr" in list_strategies()

    def test_duplicate_raises(self):
        Stub = self._make_stub()
        register_strategy("dup")(Stub)
        with pytest.raises(ValueError):
            register_strategy("dup")(self._make_stub())

    def test_override(self):
        a = self._make_stub()
        b = self._make_stub()
        register_strategy("x")(a)
        register_strategy("x", override=True)(b)
        assert get_strategy("x") is b

    def test_register_rejects_non_strategy(self):
        with pytest.raises(TypeError):
            register_strategy("bad")(dict)  # type: ignore

    def test_get_unknown_raises(self):
        with pytest.raises(KeyError):
            get_strategy("no_such_thing")


# --------------------------------------------------------------------
# 内置策略
# --------------------------------------------------------------------


class FakeProvider:
    def __init__(self, records):
        self._records = records

    def list_lhb_records(self, as_of):
        return self._records


def _ensure_builtins_loaded():
    """确保 builtins 模块被加载 (首次 import 会触发装饰器注册).

    若已 import 过, 只要 registry 里还有 key, 就无需重注册; 否则
    主动把内置类显式重注册到当前 registry (使用 override=True 防冲突).
    """
    import backend.domain.strategy.builtins as b  # noqa: F401
    from backend.domain.strategy.builtins import (
        LhbHotMoneyStrategy, LhbInstitutionStrategy,
    )
    if "lhb_hot_money" not in list_strategies():
        register_strategy("lhb_hot_money", override=True)(LhbHotMoneyStrategy)
    if "lhb_institution" not in list_strategies():
        register_strategy("lhb_institution", override=True)(LhbInstitutionStrategy)


class TestBuiltinsHotMoney:
    @pytest.fixture(autouse=True)
    def _load(self):
        _ensure_builtins_loaded()
        yield

    def test_registered(self):
        names = list_strategies()
        assert "lhb_hot_money" in names
        assert "lhb_institution" in names

    def test_select_respects_score_threshold(self):
        from backend.domain.strategy.builtins import LhbHotMoneyStrategy

        records = [
            {"ts_code": "A.SZ", "score": 60, "seats": [
                {"broker_type": "HOT_MONEY", "direction": "BUY", "net_amount": 5000}
            ]},
            {"ts_code": "B.SZ", "score": 80, "seats": [
                {"broker_type": "HOT_MONEY", "direction": "BUY", "net_amount": 5000}
            ]},
        ]
        ctx = MarketContext(as_of=date.today(),
                            data_provider=FakeProvider(records))
        strat = LhbHotMoneyStrategy(params={"score_threshold": 70,
                                              "hot_money_net_min": 1000})
        cands = strat.select_stocks(ctx)
        assert [c.ts_code for c in cands] == ["B.SZ"]

    def test_select_respects_hot_money_min(self):
        from backend.domain.strategy.builtins import LhbHotMoneyStrategy

        records = [
            {"ts_code": "A.SZ", "score": 90, "seats": [
                {"broker_type": "HOT_MONEY", "direction": "BUY", "net_amount": 100}
            ]},
            {"ts_code": "B.SZ", "score": 90, "seats": [
                {"broker_type": "HOT_MONEY", "direction": "BUY", "net_amount": 5000}
            ]},
        ]
        ctx = MarketContext(as_of=date.today(),
                            data_provider=FakeProvider(records))
        strat = LhbHotMoneyStrategy(params={"score_threshold": 70,
                                              "hot_money_net_min": 1000})
        cands = strat.select_stocks(ctx)
        assert [c.ts_code for c in cands] == ["B.SZ"]

    def test_select_sorts_by_score_and_limits(self):
        from backend.domain.strategy.builtins import LhbHotMoneyStrategy

        records = [
            {"ts_code": f"X{i}.SZ", "score": float(70 + i),
             "seats": [{"broker_type": "HOT_MONEY",
                        "direction": "BUY", "net_amount": 10000}]}
            for i in range(10)
        ]
        ctx = MarketContext(as_of=date.today(),
                            data_provider=FakeProvider(records))
        strat = LhbHotMoneyStrategy(params={"max_candidates": 3,
                                              "hot_money_net_min": 1000})
        cands = strat.select_stocks(ctx)
        assert len(cands) == 3
        assert [c.ts_code for c in cands] == ["X9.SZ", "X8.SZ", "X7.SZ"]

    def test_calc_buy_amount_cap_by_cash(self):
        from backend.domain.strategy.builtins import LhbHotMoneyStrategy
        strat = LhbHotMoneyStrategy(params={"per_position_pct": 0.20})
        # 总资产 100w, 现金 10w, 计划 20w -> 被 cash cap 到 10w
        amt = strat.calc_buy_amount(
            Candidate("A.SZ", 80.0),
            robot_cash=100_000, robot_total_assets=1_000_000,
        )
        assert amt == 100_000

    def test_calc_buy_amount_normal(self):
        from backend.domain.strategy.builtins import LhbHotMoneyStrategy
        strat = LhbHotMoneyStrategy(params={"per_position_pct": 0.20})
        amt = strat.calc_buy_amount(
            Candidate("A.SZ", 80.0),
            robot_cash=1_000_000, robot_total_assets=1_000_000,
        )
        assert amt == 200_000

    def test_check_sell_stop_loss(self):
        from backend.domain.strategy.builtins import LhbHotMoneyStrategy
        strat = LhbHotMoneyStrategy(params={"stop_loss_pct": -0.07})
        pos = Position("A.SZ", 1000, 10.0, 9.2,
                       open_date=date(2024, 10, 1), hold_days=3)
        sig = strat.check_sell(pos, MarketContext(as_of=date.today()))
        assert isinstance(sig, SellSignal)
        assert sig.reason == "STOP_LOSS"

    def test_check_sell_take_profit(self):
        from backend.domain.strategy.builtins import LhbHotMoneyStrategy
        strat = LhbHotMoneyStrategy(params={"take_profit_pct": 0.15})
        pos = Position("A.SZ", 1000, 10.0, 11.6,
                       open_date=date(2024, 10, 1), hold_days=3)
        sig = strat.check_sell(pos, MarketContext(as_of=date.today()))
        assert sig and sig.reason == "TAKE_PROFIT"

    def test_check_sell_max_hold(self):
        from backend.domain.strategy.builtins import LhbHotMoneyStrategy
        strat = LhbHotMoneyStrategy(params={"max_hold_days": 5})
        pos = Position("A.SZ", 1000, 10.0, 10.1,
                       open_date=date(2024, 10, 1), hold_days=5)
        sig = strat.check_sell(pos, MarketContext(as_of=date.today()))
        assert sig and sig.reason == "MAX_HOLD"

    def test_check_sell_hold(self):
        from backend.domain.strategy.builtins import LhbHotMoneyStrategy
        strat = LhbHotMoneyStrategy()
        pos = Position("A.SZ", 1000, 10.0, 10.5,
                       open_date=date(2024, 10, 1), hold_days=2)
        assert strat.check_sell(pos, MarketContext(as_of=date.today())) is None

    def test_select_no_provider(self):
        from backend.domain.strategy.builtins import LhbHotMoneyStrategy
        strat = LhbHotMoneyStrategy()
        assert strat.select_stocks(MarketContext(as_of=date.today())) == []


class TestBuiltinsInstitution:
    @pytest.fixture(autouse=True)
    def _load(self):
        _ensure_builtins_loaded()
        yield

    def test_requires_enough_institution_seats(self):
        from backend.domain.strategy.builtins import LhbInstitutionStrategy
        records = [
            {"ts_code": "A.SZ", "score": 90, "seats": [
                {"broker_type": "INSTITUTION", "direction": "BUY", "net_amount": 1000},
            ]},
            {"ts_code": "B.SZ", "score": 90, "seats": [
                {"broker_type": "INSTITUTION", "direction": "BUY", "net_amount": 1000},
                {"broker_type": "INSTITUTION", "direction": "BUY", "net_amount": 500},
            ]},
        ]
        ctx = MarketContext(as_of=date.today(),
                            data_provider=FakeProvider(records))
        strat = LhbInstitutionStrategy(params={"min_institution_seats": 2,
                                                "score_threshold": 70})
        cands = strat.select_stocks(ctx)
        assert [c.ts_code for c in cands] == ["B.SZ"]
        assert cands[0].signal_detail["inst_buy_seats"] == 2
