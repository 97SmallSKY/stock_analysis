"""放量突破策略：放量（vol ≥ 5日均量 × 2）+ 突破 20 日箱体上沿，ATR 止损 + 时间止损"""
import numpy as np
from .base import BaseStrategy, Signal
from .registry import register_strategy
from modules.backtest.exits import CompositeExit, ATRStop, TimeStop


@register_strategy("volume_breakout")
class VolumeBreakoutStrategy(BaseStrategy):
    description = "放量突破：量比 ≥ 2 + 突破 20 日箱体上沿"
    mode = "event"
    data_requirements = {"kline": True, "lhb": False, "sentiment": False}
    param_schema = {
        "vol_mult":      {"type": "float", "default": 2.0, "label": "量比倍数"},
        "box_period":    {"type": "int",   "default": 20,  "label": "箱体周期"},
        "atr_period":    {"type": "int",   "default": 14,  "label": "ATR 周期"},
        "atr_mult":      {"type": "float", "default": 2.0, "label": "ATR 止损倍数"},
        "max_hold_days": {"type": "int",   "default": 7,   "label": "最大持有天数"},
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
        period = self.params["box_period"]
        vol_mult = self.params["vol_mult"]
        vol = df["成交量"].values.astype(float)
        high = df["最高"].values.astype(float)
        close = df["收盘"].values.astype(float)
        ma_vol = np.array([vol[max(0, i - 5 + 1):i + 1].mean() for i in range(len(vol))])
        highest = np.array([high[max(0, i - period):i].max() if i >= 1 else high[0] for i in range(len(high))])
        vol_cond = vol >= ma_vol * vol_mult
        price_cond = close > highest
        mask = vol_cond & price_cond
        signals = []
        for i in range(1, len(mask)):
            if mask[i]:
                d = df.iloc[i]["日期"].strftime("%Y%m%d")
                if str(ctx.start)[:8] <= d <= str(ctx.end)[:8]:
                    signals.append(Signal(
                        date=d, code=ctx.code,
                        meta={"vol_ratio": round(vol[i] / ma_vol[i], 1), "high_close": round(highest[i], 2)},
                    ))
        return signals

    def build_exits(self):
        return CompositeExit([
            ATRStop(period=self.params["atr_period"], mult=self.params["atr_mult"]),
            TimeStop(max_days=self.params["max_hold_days"]),
        ])
