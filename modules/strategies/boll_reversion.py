"""布林带回归策略：收盘跌破下轨后反弹上穿下轨，追踪止盈 + 时间止损"""
import numpy as np
from .base import BaseStrategy, Signal
from .registry import register_strategy
from modules.backtest.exits import CompositeExit, TrailingStop, TimeStop


@register_strategy("boll_reversion")
class BollReversionStrategy(BaseStrategy):
    description = "布林带回归：跌破下轨后反弹上穿下轨"
    mode = "event"
    data_requirements = {"kline": True, "lhb": False, "sentiment": False}
    param_schema = {
        "period":        {"type": "int",   "default": 20,  "label": "布林带周期"},
        "std_mult":      {"type": "float", "default": 2.0, "label": "标准差倍数"},
        "trail_activate":{"type": "float", "default": 0.05, "label": "追踪激活浮盈"},
        "trail_giveback":{"type": "float", "default": 0.4, "label": "追踪回撤比例"},
        "max_hold_days": {"type": "int",   "default": 8,   "label": "最大持有天数"},
        "max_buy_gap":   {"type": "float", "default": 0.09, "label": "T+1最大高开%"},
        "filter_one_word":{"type": "bool", "default": True, "label": "过滤T+1一字涨停"},
        "allow_overlap": {"type": "bool",  "default": False, "label": "允许重叠持仓"},
    }

    def select(self, trade_date, lhb_df, kline_cache):
        return []

    def scan_signals(self, ctx):
        df = ctx.data_feed.kline(ctx.code, ctx.start, ctx.end)
        if df.empty or len(df) < self.params["period"]:
            return []
        close = df["收盘"].values.astype(float)
        period = self.params["period"]
        mult = self.params["std_mult"]
        ma = np.array([close[max(0, i - period + 1):i + 1].mean() for i in range(len(close))])
        std = np.array([close[max(0, i - period + 1):i + 1].std(ddof=1) for i in range(len(close))])
        lower = ma - mult * std
        below = close < lower
        was_below = _shift_bool(below)
        recover = (close >= lower) & was_below
        signals = []
        for i in range(len(recover)):
            if recover[i]:
                d = df.iloc[i]["日期"].strftime("%Y%m%d")
                if str(ctx.start)[:8] <= d <= str(ctx.end)[:8]:
                    signals.append(Signal(
                        date=d, code=ctx.code,
                        meta={"ma": round(ma[i], 2), "lower": round(lower[i], 2), "close": round(close[i], 2)},
                    ))
        return signals

    def build_exits(self):
        return CompositeExit([
            TrailingStop(activate=self.params["trail_activate"], giveback=self.params["trail_giveback"]),
            TimeStop(max_days=self.params["max_hold_days"]),
        ])


def _shift_bool(arr, n=1):
    s = np.roll(arr, n)
    s[:n] = False
    return s
