"""MACD 金叉策略：DIF 上穿 DEA 且 0 轴下方（趋势反转）"""
import numpy as np
from .base import BaseStrategy, Signal
from .registry import register_strategy
from modules.backtest.exits import CompositeExit, FixedPctStop, TimeStop
from modules.indicators import ema as _ema


@register_strategy("macd_golden")
class MacdGoldenStrategy(BaseStrategy):
    description = "MACD 金叉：DIF 上穿 DEA 且 0 轴下方（趋势反转）"
    mode = "event"
    data_requirements = {"kline": True, "lhb": False, "sentiment": False}
    param_schema = {
        "fast":          {"type": "int",   "default": 12,  "label": "快线周期"},
        "slow":          {"type": "int",   "default": 26,  "label": "慢线周期"},
        "signal_period": {"type": "int",   "default": 9,   "label": "信号线周期"},
        "stop_loss":     {"type": "float", "default": -0.05, "label": "止损比例"},
        "stop_profit":   {"type": "float", "default": 0.08, "label": "止盈比例"},
        "max_hold_days": {"type": "int",   "default": 5,   "label": "最大持有天数"},
        "max_buy_gap":   {"type": "float", "default": 0.09, "label": "T+1最大高开%"},
        "filter_one_word":{"type": "bool", "default": True, "label": "过滤T+1一字涨停"},
        "allow_overlap": {"type": "bool",  "default": False, "label": "允许重叠持仓"},
    }

    def select(self, trade_date, lhb_df, kline_cache):
        return []

    def build_exits(self):
        return CompositeExit([
            FixedPctStop(stop_loss=self.params["stop_loss"], stop_profit=self.params["stop_profit"]),
            TimeStop(max_days=self.params["max_hold_days"]),
        ])

    def scan_signals(self, ctx):
        df = ctx.data_feed.kline(ctx.code, ctx.start, ctx.end)
        if df.empty or len(df) < self.params["slow"] + self.params["signal_period"]:
            return []
        close = df["收盘"].values.astype(float)
        ema_fast = _ema(close, self.params["fast"])
        ema_slow = _ema(close, self.params["slow"])
        dif = ema_fast - ema_slow
        dea = _ema(dif, self.params["signal_period"])
        cross_up = (dif > dea) & (_shift(dif) <= _shift(dea))
        below_zero = dif < 0
        mask = cross_up & below_zero
        signals = []
        for i in range(len(mask)):
            if mask[i]:
                d = df.iloc[i]["日期"].strftime("%Y%m%d")
                if str(ctx.start)[:8] <= d <= str(ctx.end)[:8]:
                    signals.append(Signal(
                        date=d, code=ctx.code,
                        meta={"dif": round(dif[i], 4), "dea": round(dea[i], 4)},
                    ))
        return signals


def _shift(arr, n=1):
    s = np.roll(arr, n)
    s[:n] = s[n]
    return s
