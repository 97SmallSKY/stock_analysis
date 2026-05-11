"""均线突破策略：收盘站上 MA60 且近 5 日未站上，追踪止盈 + 时间止损"""
from .base import BaseStrategy, Signal
from .registry import register_strategy
from modules.backtest.exits import CompositeExit, TrailingStop, TimeStop


@register_strategy("ma_breakout")
class MaBreakoutStrategy(BaseStrategy):
    description = "均线突破：收盘站上 MA60 且近 5 日未站上，追踪止盈"
    mode = "event"
    data_requirements = {"kline": True, "lhb": False, "sentiment": False}
    param_schema = {
        "ma_period":     {"type": "int",   "default": 60,  "label": "均线周期"},
        "break_days":    {"type": "int",   "default": 5,   "label": "突破确认天数"},
        "trail_activate":{"type": "float", "default": 0.05, "label": "追踪激活浮盈"},
        "trail_giveback":{"type": "float", "default": 0.4, "label": "追踪回撤比例"},
        "max_hold_days": {"type": "int",   "default": 15,  "label": "最大持有天数"},
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
        period = self.params["ma_period"]
        break_days = self.params["break_days"]
        df["ma"] = df["收盘"].rolling(period).mean()
        above = df["收盘"] > df["ma"]
        was_below = above.shift(1).rolling(break_days).sum() == 0
        breakout = above & was_below
        signals = []
        for _, row in df[breakout].iterrows():
            d = row["日期"].strftime("%Y%m%d")
            if str(ctx.start)[:8] <= d <= str(ctx.end)[:8]:
                signals.append(Signal(
                    date=d, code=ctx.code,
                    meta={"ma": round(row["ma"], 2), "close": round(row["收盘"], 2)},
                ))
        return signals

    def build_exits(self):
        return CompositeExit([
            TrailingStop(activate=self.params["trail_activate"], giveback=self.params["trail_giveback"]),
            TimeStop(max_days=self.params["max_hold_days"]),
        ])
