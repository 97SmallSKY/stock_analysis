"""
Executor — 交易执行器
处理 T+1 开盘买入 + 日内 bar 遍历 + 保守路径未来函数修复
"""
import pandas as pd
import numpy as np
from datetime import datetime
from loguru import logger
from dataclasses import dataclass, field

from modules.backtest.portfolio import Position, Bar, Portfolio
from modules.backtest.data_feed import DataFeed
from modules.strategies.base import SellSignal, BuyOrder


@dataclass
class BacktestContext:
    """回测上下文，贯穿整个回测生命周期"""
    data_feed: DataFeed = field(default_factory=DataFeed)
    code: str = ""
    start: str = ""
    end: str = ""
    strategy_params: dict = field(default_factory=dict)
    benchmark_code: str = "000300.SH"
    extras: dict = field(default_factory=dict)


def _make_skip(reason: str) -> dict:
    return {"return_rate": None, "hold_days": 0, "exit_reason": reason, "skipped": True}


def _is_one_word_limit_up(row, prev_close: float, limit_pct: float, eps: float = 0.001) -> bool:
    """判断是否为 T+1 一字涨停（开盘=最高=最低=涨停价，无法买入）"""
    limit_price = prev_close * (1 + limit_pct)
    if row["开盘"] < limit_price * (1 - eps):
        return False
    return abs(row["开盘"] - row["最高"]) < 0.0001 and abs(row["开盘"] - row["最低"]) < 0.0001


class Executor:
    """交易执行器：统一处理 T+1 开盘买入 + 止盈止损 + 到期卖出"""

    def __init__(self, data_feed: DataFeed, roundtrip_cost: float = 0.0025):
        self.data_feed = data_feed
        self.roundtrip_cost = roundtrip_cost

    def execute_trade(
        self, code: str, signal_date: str, ctx: BacktestContext,
        strategy=None, exits=None,
    ) -> dict | None:
        """
        执行单笔交易模拟。

        返回 dict 或 None（K线数据不足）。
        返回 dict 的 skipped=True 表示该交易未执行（过滤）。
        """
        # 从 strategy.params 获取参数，或使用默认值
        if strategy is not None:
            params = strategy.params
            stop_loss = params.get("stop_loss", -0.05)
            stop_profit = params.get("stop_profit", 0.08)
            max_hold_days = params.get("max_hold_days", 10)
            filter_one_word = params.get("filter_one_word", True)
            max_buy_gap = params.get("max_buy_gap", 0.09)
        else:
            stop_loss = -0.05
            stop_profit = 0.08
            max_hold_days = 10
            filter_one_word = True
            max_buy_gap = 0.09

        # 加载 K 线从信号日到 signal_date + max_hold_days + 15
        from datetime import timedelta
        sd = pd.Timestamp(str(signal_date)[:8])
        ed = sd + timedelta(days=max_hold_days + 30)  # 覆盖春节+国庆叠加最长假期
        df = self.data_feed.kline(code, sd.strftime("%Y%m%d"), ed.strftime("%Y%m%d"))
        if df.empty or len(df) < 2:
            return None

        df["日期"] = pd.to_datetime(df["日期"])
        # 找到信号日对应的 K 线索引
        signal_date_str = str(signal_date)[:10]
        matches = df[df["日期"].dt.strftime("%Y-%m-%d") == signal_date_str[:4] + "-" + signal_date_str[4:6] + "-" + signal_date_str[6:8]]
        if matches.empty:
            # 尝试不分割的格式
            d = str(signal_date)[:8]
            matches = df[df["日期"].dt.strftime("%Y%m%d") == d]
        if matches.empty:
            return None
        buy_idx = matches.index[0]
        # 验证有 T+1 bar
        next_idx = buy_idx + 1
        if next_idx >= len(df):
            return None

        # 信号日收盘价
        prev_close = float(df.iloc[buy_idx]["收盘"])

        # T+1 bar
        buy_day = df.iloc[next_idx]
        buy_date_raw = buy_day["日期"].strftime("%Y%m%d")
        buy_price = float(buy_day["开盘"])
        t1_open_gap = (buy_price - prev_close) / prev_close

        base = {"buy_date": buy_date_raw, "t1_gap": round(t1_open_gap, 4),
                "signal_close": prev_close, "prev_close": prev_close}

        # 一字涨停过滤
        if filter_one_word:
            limit_pct = self.data_feed.limit_pct(code)
            if _is_one_word_limit_up(buy_day, prev_close, limit_pct):
                return {**base, **_make_skip("T+1一字涨停无法买入")}

        # 高开过滤
        if max_buy_gap and t1_open_gap >= max_buy_gap:
            return {**base, **_make_skip(f"T+1高开{t1_open_gap:.1%}超过阈值{max_buy_gap:.0%}放弃")}

        # 构建 Exit 链
        if exits is not None:
            exit_chain = exits
        elif strategy is not None and hasattr(strategy, 'build_exits'):
            exit_chain = strategy.build_exits()
        else:
            from modules.backtest.exits import FixedPctStop, TimeStop, CompositeExit
            exit_chain = CompositeExit([
                FixedPctStop(stop_loss=stop_loss, stop_profit=stop_profit),
                TimeStop(max_days=max_hold_days),
            ])

        # 持有期遍历
        last_hold_idx = min(len(df), next_idx + max_hold_days) - 1
        position = None
        for day_idx in range(next_idx, last_hold_idx + 1):
            held_days = day_idx - next_idx + 1
            is_last_day = (day_idx == last_hold_idx)

            row = df.iloc[day_idx]
            bar = Bar(
                date=row["日期"].strftime("%Y%m%d"),
                open=float(row["开盘"]), high=float(row["最高"]),
                low=float(row["最低"]), close=float(row["收盘"]),
                volume=float(row.get("成交量", 0)),
            )

            if position is None:
                position = Position.from_bar(code, buy_price, buy_date_raw, bar)
            else:
                position.update(bar)

            # 检查退出
            sell_signal = None
            if exit_chain:
                sell_signal = exit_chain.check(position, bar, ctx)
            if sell_signal is None and strategy is not None and hasattr(strategy, 'should_sell'):
                # 旧策略兼容：调用 should_sell
                pos_dict = position.to_dict()
                sell_signal = strategy.should_sell(pos_dict)
            if sell_signal is None and is_last_day:
                sell_signal = SellSignal(reason="到期卖出", price_type="close")

            if sell_signal is None:
                continue

            # ── 保守路径成交价 ──
            exit_reason = sell_signal.reason
            exit_component = getattr(sell_signal, 'component', '')
            price_type = sell_signal.price_type

            if price_type == "open":
                sell_price, gross_ret = bar.open, (bar.open - buy_price) / buy_price
            elif "止损" in exit_reason:
                stop_price = buy_price * (1 + stop_loss)
                # 验证当天 bar 是否真的触及止损价
                if bar.low <= stop_price:
                    # 当天触及：跳空低开用 open，否则用 stop_price
                    sell_price = bar.open if bar.open < stop_price else stop_price
                else:
                    # 当天未触及（历史低点触发）：保守使用当天最低价
                    sell_price = bar.low
                gross_ret = (sell_price - buy_price) / buy_price
            elif "止盈" in exit_reason:
                stop_price = buy_price * (1 + stop_profit)
                # 验证当天 bar 是否真的触及止盈价
                if bar.high >= stop_price:
                    # 当天触及：跳空高开用 open，否则用 stop_price
                    sell_price = bar.open if bar.open > stop_price else stop_price
                else:
                    # 当天未触及（历史高点触发）：保守使用当天最高价
                    sell_price = bar.high
                gross_ret = (sell_price - buy_price) / buy_price
            else:
                sell_price, gross_ret = bar.close, (bar.close - buy_price) / buy_price

            net_ret = gross_ret - self.roundtrip_cost
            return {
                **base,
                "sell_date": bar.date,
                "buy_price": buy_price, "sell_price": sell_price,
                "return_rate": net_ret, "return_rate_gross": gross_ret,
                "hold_days": held_days, "exit_reason": exit_reason,
                "exit_component": exit_component,
                "cost_rate": self.roundtrip_cost,
            }

        # 数据不足
        last_row = df.iloc[-1]
        sell_price = float(last_row["收盘"])
        gross_ret = (sell_price - buy_price) / buy_price
        net_ret = gross_ret - self.roundtrip_cost
        last_date = last_row["日期"].strftime("%Y%m%d")
        return {
            **base, "sell_date": last_date,
            "buy_price": buy_price, "sell_price": sell_price,
            "return_rate": net_ret, "return_rate_gross": gross_ret,
            "hold_days": max(1, len(df) - next_idx), "exit_reason": "数据截止",
            "exit_component": "", "cost_rate": self.roundtrip_cost,
        }
