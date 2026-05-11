"""情绪反转退出"""

from .base import BaseExit
from modules.backtest.portfolio import Position, Bar
from modules.strategies.base import SellSignal


class SentimentReversal(BaseExit):
    """市场情绪由 bullish 转 bearish 且持仓浮盈时平仓"""

    name = "sentiment_reversal"

    def check(self, position: Position, bar: Bar, ctx: "BacktestContext" = None) -> SellSignal | None:
        if ctx is None or ctx.data_feed is None:
            return None
        snap = ctx.data_feed.sentiment(bar.date)
        if snap is None:
            return None
        if snap.get("trend_flip_to_bearish") and position.close_return > 0:
            return SellSignal(reason="情绪反转平仓", price_type="close")
        return None
