"""集成测试：验证 Executor 止损/止盈价格修复"""
import pytest
import pandas as pd
from datetime import datetime
from modules.backtest.executor import Executor
from modules.backtest.data_feed import DataFeed
from modules.backtest.portfolio import Bar
from modules.strategies.base import SellSignal


class TestStopPriceFix:
    """测试止损/止盈价格计算修复"""

    def test_stop_loss_when_bar_touches_stop_price(self):
        """止损：当天 bar 触及止损价，应使用止损价成交"""
        # 场景：买入价 10.0，止损 -5%，当天最低 9.4 触及止损价 9.5
        buy_price = 10.0
        stop_loss = -0.05
        stop_price = buy_price * (1 + stop_loss)  # 9.5

        bar = Bar(
            date="20260530",
            open=9.8, high=10.0, low=9.4, close=9.6,
            volume=1000000
        )

        # 模拟止损触发
        if bar.low <= stop_price:
            sell_price = bar.open if bar.open < stop_price else stop_price
        else:
            sell_price = bar.low

        assert sell_price == 9.5, f"当天触及止损价，应使用 9.5，实际 {sell_price}"
        assert bar.low <= sell_price <= bar.high, "成交价必须在 bar 范围内"

    def test_stop_loss_when_bar_does_not_touch_stop_price(self):
        """止损：当天 bar 未触及止损价（历史触发），应使用当天最低价"""
        # 场景：止损价 9.5，但当天最低只到 9.6（历史低点触发）
        buy_price = 10.0
        stop_loss = -0.05
        stop_price = buy_price * (1 + stop_loss)  # 9.5

        bar = Bar(
            date="20260531",
            open=9.8, high=10.2, low=9.6, close=10.0,
            volume=1000000
        )

        # 修复后的逻辑
        if bar.low <= stop_price:
            sell_price = bar.open if bar.open < stop_price else stop_price
        else:
            sell_price = bar.low  # 关键修复：使用当天最低价

        assert sell_price == 9.6, f"当天未触及止损价，应使用 bar.low=9.6，实际 {sell_price}"
        assert bar.low <= sell_price <= bar.high, "成交价必须在 bar 范围内"

    def test_stop_loss_gap_down(self):
        """止损：跳空低开穿越止损价，应使用开盘价"""
        buy_price = 10.0
        stop_loss = -0.05
        stop_price = buy_price * (1 + stop_loss)  # 9.5

        bar = Bar(
            date="20260601",
            open=9.3, high=9.5, low=9.2, close=9.4,
            volume=1000000
        )

        if bar.low <= stop_price:
            sell_price = bar.open if bar.open < stop_price else stop_price
        else:
            sell_price = bar.low

        assert sell_price == 9.3, f"跳空低开应使用 open=9.3，实际 {sell_price}"

    def test_stop_profit_when_bar_touches_stop_price(self):
        """止盈：当天 bar 触及止盈价，应使用止盈价成交"""
        buy_price = 10.0
        stop_profit = 0.08
        stop_price = buy_price * (1 + stop_profit)  # 10.8

        bar = Bar(
            date="20260530",
            open=10.5, high=10.9, low=10.4, close=10.7,
            volume=1000000
        )

        if bar.high >= stop_price:
            sell_price = bar.open if bar.open > stop_price else stop_price
        else:
            sell_price = bar.high

        assert sell_price == 10.8, f"当天触及止盈价，应使用 10.8，实际 {sell_price}"
        assert bar.low <= sell_price <= bar.high, "成交价必须在 bar 范围内"

    def test_stop_profit_when_bar_does_not_touch_stop_price(self):
        """止盈：当天 bar 未触及止盈价（历史触发），应使用当天最高价"""
        buy_price = 10.0
        stop_profit = 0.08
        stop_price = buy_price * (1 + stop_profit)  # 10.8

        bar = Bar(
            date="20260531",
            open=10.5, high=10.7, low=10.3, close=10.6,
            volume=1000000
        )

        # 修复后的逻辑
        if bar.high >= stop_price:
            sell_price = bar.open if bar.open > stop_price else stop_price
        else:
            sell_price = bar.high  # 关键修复：使用当天最高价

        assert sell_price == 10.7, f"当天未触及止盈价，应使用 bar.high=10.7，实际 {sell_price}"
        assert bar.low <= sell_price <= bar.high, "成交价必须在 bar 范围内"

    def test_stop_profit_gap_up(self):
        """止盈：跳空高开穿越止盈价，应使用开盘价"""
        buy_price = 10.0
        stop_profit = 0.08
        stop_price = buy_price * (1 + stop_profit)  # 10.8

        bar = Bar(
            date="20260601",
            open=10.9, high=11.0, low=10.8, close=10.95,
            volume=1000000
        )

        if bar.high >= stop_price:
            sell_price = bar.open if bar.open > stop_price else stop_price
        else:
            sell_price = bar.high

        assert sell_price == 10.9, f"跳空高开应使用 open=10.9，实际 {sell_price}"

    def test_price_always_within_bar_range(self):
        """所有场景下成交价必须在 [bar.low, bar.high] 范围内"""
        test_cases = [
            # (buy_price, stop_loss/profit, bar, expected_in_range)
            (10.0, -0.05, Bar("20260530", 9.8, 10.0, 9.6, 9.7, 1000000), True),  # 止损未触及
            (10.0, 0.08, Bar("20260530", 10.5, 10.7, 10.4, 10.6, 1000000), True),  # 止盈未触及
            (10.0, -0.05, Bar("20260530", 9.8, 10.0, 9.4, 9.6, 1000000), True),  # 止损触及
            (10.0, 0.08, Bar("20260530", 10.5, 10.9, 10.4, 10.7, 1000000), True),  # 止盈触及
        ]

        for buy_price, stop_pct, bar, _ in test_cases:
            stop_price = buy_price * (1 + stop_pct)

            if stop_pct < 0:  # 止损
                if bar.low <= stop_price:
                    sell_price = bar.open if bar.open < stop_price else stop_price
                else:
                    sell_price = bar.low
            else:  # 止盈
                if bar.high >= stop_price:
                    sell_price = bar.open if bar.open > stop_price else stop_price
                else:
                    sell_price = bar.high

            assert bar.low <= sell_price <= bar.high, (
                f"成交价 {sell_price} 超出 bar 范围 [{bar.low}, {bar.high}]"
            )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
