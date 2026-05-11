"""唐奇安突破策略（海龟）：收盘突破 20 日最高价，ATR 止损 + 追踪止盈"""
import numpy as np
from .base import BaseStrategy, Signal
from .registry import register_strategy
from modules.backtest.exits import CompositeExit, ATRStop, TrailingStop


@register_strategy("donchian_breakout")
class DonchianBreakoutStrategy(BaseStrategy):
    description = "唐奇安突破（海龟）：收盘突破 20 日最高价"
    mode = "event"
    data_requirements = {"kline": True, "lhb": False, "sentiment": False}
    param_schema = {
        "channel_period":{"type": "int",   "default": 20,  "label": "通道周期"},
        "atr_period":    {"type": "int",   "default": 14,  "label": "ATR 周期"},
        "atr_mult":      {"type": "float", "default": 2.5, "label": "ATR 止损倍数"},
        "trail_activate":{"type": "float", "default": 0.08, "label": "追踪激活浮盈"},
        "trail_giveback":{"type": "float", "default": 0.4, "label": "追踪回撤比例"},
        "max_hold_days": {"type": "int",   "default": 10,  "label": "最大持有天数"},
        "max_buy_gap":   {"type": "float", "default": 0.09, "label": "T+1最大高开%"},
        "filter_one_word":{"type": "bool", "default": True, "label": "过滤T+1一字涨停"},
        "allow_overlap": {"type": "bool",  "default": False, "label": "允许重叠持仓"},
    }

    def select(self, trade_date, lhb_df, kline_cache):
        return []

    def scan_signals(self, ctx):
        df = ctx.data_feed.kline(ctx.code, ctx.start, ctx.end)
        if df.empty:
            return []
        period = self.params["channel_period"]
        high = df["最高"].values.astype(float)
        close = df["收盘"].values.astype(float)
        highest = np.array([high[max(0, i - period):i].max() if i >= 1 else high[0] for i in range(len(high))])
        breakout = close > highest
        signals = []
        for i in range(1, len(breakout)):
            if breakout[i]:
                d = df.iloc[i]["日期"].strftime("%Y%m%d")
                if str(ctx.start)[:8] <= d <= str(ctx.end)[:8]:
                    signals.append(Signal(
                        date=d, code=ctx.code,
                        meta={"high_close": round(highest[i], 2), "close": round(close[i], 2)},
                    ))
        return signals

    def build_exits(self):
        return CompositeExit([
            ATRStop(period=self.params["atr_period"], mult=self.params["atr_mult"]),
            TrailingStop(activate=self.params["trail_activate"], giveback=self.params["trail_giveback"]),
        ])
