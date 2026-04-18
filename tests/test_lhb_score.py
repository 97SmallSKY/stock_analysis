"""评分模型单元测试 (纯函数, 不依赖 DB)。"""
from __future__ import annotations

import pytest

from backend.scoring.lhb_score import (
    SENTIMENT_COEF,
    ScoreInput,
    compute_fund_score,
    compute_hist_score,
    compute_score,
    compute_tech_score,
)


# --------------------------------------------------------------------
# helper: 造一个完整的 60 天 kline 窗口
# --------------------------------------------------------------------


def _make_kline_window(n: int = 60, start_close: float = 10.0,
                      pct_per_day: float = 0.005, vol: float = 1_000_000):
    rows = []
    close = start_close
    for i in range(n):
        prev = close
        close = round(prev * (1 + pct_per_day), 4)
        rows.append({
            "trade_date": f"2024-01-{(i % 28) + 1:02d}",
            "open": prev,
            "high": close * 1.01,
            "low": prev * 0.99,
            "close": close,
            "pct_change": pct_per_day,
            "vol": vol,
            "amount": vol * close,
            "turnover_rate": 0.02,
        })
    return rows


# --------------------------------------------------------------------
# 技术面
# --------------------------------------------------------------------


class TestTechScore:
    def test_limit_up_day_full_window_full_score(self):
        """涨停 + 60 天窗口 + 量比 5 + 距高点 0% → 高分"""
        win = _make_kline_window(60, pct_per_day=0.02)  # 持续涨
        # 把最后一日改成涨停 + 5 倍量
        win[-1]["pct_change"] = 0.10
        win[-1]["vol"] = win[0]["vol"] * 5
        win[-1]["high"] = win[-1]["close"]  # 最高 = 收盘 = 当前 60 日最高
        row = {"pct_change": 0.10, "turnover_rate": 0.10}
        score, detail = compute_tech_score(
            ScoreInput(lhb_row=row, kline_window=win)
        )
        assert score >= 80, f"涨停日应高分, 实际 {score}, 明细 {detail}"

    def test_flat_day_low_score(self):
        win = _make_kline_window(60, pct_per_day=-0.001)
        row = {"pct_change": 0.001, "turnover_rate": 0.001}
        score, _ = compute_tech_score(
            ScoreInput(lhb_row=row, kline_window=win)
        )
        assert 20 <= score <= 60, f"无量平盘应中低分, 实际 {score}"

    def test_new_stock_penalty(self):
        """窗口不足 60 天打 0.92 折"""
        win = _make_kline_window(20)
        row = {"pct_change": 0.05, "turnover_rate": 0.10}
        score, detail = compute_tech_score(
            ScoreInput(lhb_row=row, kline_window=win)
        )
        assert detail.get("new_stock_penalty") == 0.92

    def test_empty_kline_no_crash(self):
        row = {"pct_change": 0.05, "turnover_rate": 0.10}
        score, _ = compute_tech_score(
            ScoreInput(lhb_row=row, kline_window=[])
        )
        assert 0 <= score <= 100


# --------------------------------------------------------------------
# 资金面
# --------------------------------------------------------------------


class TestFundScore:
    def test_strong_net_buy_high_score(self):
        """净买入 5 亿 + 占比 10% + 买卖比 4 → 高分"""
        row = {
            "l_net": 50000.0,    # 5 亿 = 50000 万
            "l_buy": 80000.0,    # 8 亿
            "l_sell": 30000.0,   # 3 亿
            "amount": 500000.0,  # 50 亿
            "float_market_cap": 1000000.0,  # 100 亿(适中)
            "reason": "连续三个交易日内,涨幅偏离值累计达到 30%",
        }
        score, detail = compute_fund_score(
            ScoreInput(lhb_row=row)
        )
        assert score >= 75, f"强净买入应高分, 实际 {score}, 明细 {detail}"

    def test_net_sell_low_score(self):
        row = {
            "l_net": -50000.0,
            "l_buy": 20000.0,
            "l_sell": 70000.0,
            "amount": 500000.0,
            "float_market_cap": 1000000.0,
        }
        score, _ = compute_fund_score(
            ScoreInput(lhb_row=row)
        )
        assert score < 60, f"净卖出应低分, 实际 {score}"

    def test_zero_amount_safe(self):
        row = {"l_net": 0, "l_buy": 0, "l_sell": 0, "amount": 0,
               "float_market_cap": 0}
        score, _ = compute_fund_score(ScoreInput(lhb_row=row))
        assert 0 <= score <= 100  # 不崩

    def test_reason_priority(self):
        """连续三日 > 涨幅 > 换手 > 振幅"""
        base = {"l_net": 10000, "l_buy": 15000, "l_sell": 5000,
                "amount": 100000, "float_market_cap": 500000}
        s_consec, _ = compute_fund_score(
            ScoreInput(lhb_row={**base, "reason": "连续三个交易日"}))
        s_pct, _ = compute_fund_score(
            ScoreInput(lhb_row={**base, "reason": "日涨幅偏离值达到 7%"}))
        s_turn, _ = compute_fund_score(
            ScoreInput(lhb_row={**base, "reason": "日换手率达到 30%"}))
        s_amp, _ = compute_fund_score(
            ScoreInput(lhb_row={**base, "reason": "日振幅值达到 15%"}))
        assert s_consec > s_pct >= s_turn >= s_amp


# --------------------------------------------------------------------
# 历史胜率
# --------------------------------------------------------------------


class TestHistScore:
    def test_first_time_neutral_50(self):
        score, detail = compute_hist_score(
            ScoreInput(lhb_row={}, hist_stats={"total_count": 0})
        )
        assert score == 50.0
        assert detail.get("first_time") == 50.0

    def test_strong_history_high_score(self):
        score, _ = compute_hist_score(
            ScoreInput(lhb_row={}, hist_stats={
                "total_count": 5,
                "avg_chg_3d": 0.06,
                "avg_chg_5d": 0.08,
                "win_rate_5d": 0.70,
            })
        )
        assert score >= 90

    def test_bad_history_low_score(self):
        score, _ = compute_hist_score(
            ScoreInput(lhb_row={}, hist_stats={
                "total_count": 6,
                "avg_chg_3d": -0.04,
                "avg_chg_5d": -0.06,
                "win_rate_5d": 0.30,
            })
        )
        assert score < 40

    def test_overcrowded_penalty(self):
        """上榜 20 次说明惯性炒作, 应该被扣分"""
        score, detail = compute_hist_score(
            ScoreInput(lhb_row={}, hist_stats={
                "total_count": 20,
                "avg_chg_3d": 0.04,
                "avg_chg_5d": 0.05,
                "win_rate_5d": 0.55,
            })
        )
        # total_count 因子降到 40, 整体分数低于 "3-8 次" 的同样输入
        score_normal, _ = compute_hist_score(
            ScoreInput(lhb_row={}, hist_stats={
                "total_count": 5,
                "avg_chg_3d": 0.04,
                "avg_chg_5d": 0.05,
                "win_rate_5d": 0.55,
            })
        )
        assert score < score_normal


# --------------------------------------------------------------------
# 综合
# --------------------------------------------------------------------


class TestComputeScore:
    def _make_strong_input(self, sentiment="HOT"):
        win = _make_kline_window(60, pct_per_day=0.015)
        win[-1]["pct_change"] = 0.10
        win[-1]["vol"] = win[0]["vol"] * 4
        return ScoreInput(
            lhb_row={
                "pct_change": 0.10, "turnover_rate": 0.12,
                "l_net": 60000.0, "l_buy": 90000.0, "l_sell": 30000.0,
                "amount": 600000.0, "float_market_cap": 1500000.0,
                "reason": "连续三个交易日内,涨幅偏离值累计达到 30%",
            },
            kline_window=win,
            sentiment_level=sentiment,
            hist_stats={
                "total_count": 5,
                "avg_chg_3d": 0.05,
                "avg_chg_5d": 0.07,
                "win_rate_5d": 0.65,
            },
        )

    def test_all_fields_present(self):
        result = compute_score(self._make_strong_input())
        assert {"score", "score_tech", "score_fund", "score_hist",
                "score_detail"} == set(result.keys())
        for k in ("score", "score_tech", "score_fund", "score_hist"):
            assert 0 <= result[k] <= 100

    def test_strong_inputs_high_score(self):
        result = compute_score(self._make_strong_input("HOT"))
        assert result["score"] >= 80, result

    def test_sentiment_coefficient_applies(self):
        hot = compute_score(self._make_strong_input("HOT"))["score"]
        cold = compute_score(self._make_strong_input("COLD"))["score"]
        assert hot > cold

    def test_unknown_sentiment_falls_back_neutral(self):
        result = compute_score(self._make_strong_input("WTF_UNKNOWN"))
        # 不崩, 应用 NEUTRAL=1.0
        assert 0 <= result["score"] <= 100

    def test_clamp_at_100(self):
        """HOT * 1.05 不会超过 100"""
        result = compute_score(self._make_strong_input("HOT"))
        assert result["score"] <= 100

    def test_score_detail_structure(self):
        result = compute_score(self._make_strong_input())
        d = result["score_detail"]
        assert "tech" in d and "fund" in d and "hist" in d
        assert d["weights"] == {"tech": 0.40, "fund": 0.35, "hist": 0.25}


# --------------------------------------------------------------------
# 健壮性
# --------------------------------------------------------------------


class TestRobustness:
    def test_none_values_no_crash(self):
        row = {"pct_change": None, "turnover_rate": None,
               "l_net": None, "l_buy": None, "l_sell": None,
               "amount": None, "float_market_cap": None}
        result = compute_score(
            ScoreInput(lhb_row=row, sentiment_level="NEUTRAL")
        )
        assert 0 <= result["score"] <= 100

    def test_decimal_inputs(self):
        """psycopg2 返回 Decimal, 应该兼容"""
        from decimal import Decimal
        row = {"pct_change": Decimal("0.05"), "turnover_rate": Decimal("0.10"),
               "l_net": Decimal("30000.50"), "l_buy": Decimal("50000"),
               "l_sell": Decimal("20000"), "amount": Decimal("300000"),
               "float_market_cap": Decimal("1000000")}
        result = compute_score(ScoreInput(lhb_row=row))
        assert 0 <= result["score"] <= 100
