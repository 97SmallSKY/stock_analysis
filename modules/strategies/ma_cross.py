"""双均线金叉策略：MA5 上穿 MA20，ATR 止损 + 时间止损"""
from .base import BaseStrategy, Signal
from .registry import register_strategy
from modules.backtest.exits import CompositeExit, ATRStop, TimeStop


@register_strategy("ma_cross")
class MaCrossStrategy(BaseStrategy):
    description = "双均线金叉：MA5 上穿 MA20，ATR 止损"
    mode = "event"
    data_requirements = {"kline": True, "lhb": False, "sentiment": False}
    param_schema = {
        "fast":          {"type": "int",   "default": 5,   "label": "快速均线"},
        "slow":          {"type": "int",   "default": 20,  "label": "慢速均线"},
        "atr_period":    {"type": "int",   "default": 14,  "label": "ATR 周期"},
        "atr_mult":      {"type": "float", "default": 2.0, "label": "ATR 止损倍数"},
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
        df["ma_fast"] = df["收盘"].rolling(self.params["fast"]).mean()
        df["ma_slow"] = df["收盘"].rolling(self.params["slow"]).mean()
        cross = (df["ma_fast"] > df["ma_slow"]) & (df["ma_fast"].shift(1) <= df["ma_slow"].shift(1))
        signals = []
        for _, row in df[cross].iterrows():
            d = row["日期"].strftime("%Y%m%d")
            if str(ctx.start)[:8] <= d <= str(ctx.end)[:8]:
                signals.append(Signal(
                    date=d, code=ctx.code,
                    meta={"ma_fast": round(row["ma_fast"], 2), "ma_slow": round(row["ma_slow"], 2)},
                ))
        return signals

    def build_exits(self):
        return CompositeExit([
            ATRStop(period=self.params["atr_period"], mult=self.params["atr_mult"]),
            TimeStop(max_days=self.params["max_hold_days"]),
        ])
