from .base import BaseStrategy, BuyOrder, SellSignal
from .registry import register_strategy


@register_strategy("lhb_high_score")
class LhbHighScoreStrategy(BaseStrategy):
    description = "龙虎榜高分策略：综合席位质量、技术面、净买入额评分，每日选 Top N"
    param_schema = {
        "top_n":            {"type": "int",   "default": 1,    "label": "每日最多买入数量"},
        "min_net_buy":      {"type": "float", "default": 5000, "label": "最低净买入额（万元）"},
        "min_tech_score":   {"type": "int",   "default": 50,   "label": "最低技术评分"},
        "max_gain_20d":     {"type": "float", "default": 0.30, "label": "最大20日涨幅"},
        "stop_loss":        {"type": "float", "default": -0.05,"label": "止损比例"},
        "stop_profit":      {"type": "float", "default": 0.08, "label": "止盈比例"},
        "max_hold_days":    {"type": "int",   "default": 3,    "label": "最大持有天数"},
        "filter_one_word":  {"type": "bool",  "default": True, "label": "过滤T+1一字涨停"},
        "max_buy_gap":      {"type": "float", "default": 0.09, "label": "T+1最大高开%"},
        "allow_overlap":    {"type": "bool",  "default": False,"label": "允许重叠持仓"},
    }

    def select(self, trade_date, lhb_df, kline_cache) -> list[BuyOrder]:
        from modules.analyzer import select_stocks_daily

        stocks = select_stocks_daily(
            lhb_df=lhb_df,
            trade_date=trade_date,
            top_n=self.params["top_n"],
            min_net_buy=self.params["min_net_buy"] * 10000,
            min_tech_score=self.params["min_tech_score"],
            max_gain_20d=self.params["max_gain_20d"],
        )

        orders = []
        for s in stocks:
            orders.append(BuyOrder(
                code=s["code"],
                reason="龙虎榜高分买入",
                meta=s,
            ))
        return orders

    def should_sell(self, position: dict) -> SellSignal | None:
        stop_loss = self.params["stop_loss"]
        stop_profit = self.params["stop_profit"]
        max_hold = self.params["max_hold_days"]

        if position["hold_days"] > 1 and position["open_return"] <= stop_loss:
            return SellSignal(reason="开盘止损", price_type="open")
        if position["open_return"] >= stop_profit:
            return SellSignal(reason="开盘止盈", price_type="open")
        if position["min_return"] <= stop_loss:
            return SellSignal(reason="盘中止损", price_type="close")
        if position["max_return"] >= stop_profit:
            return SellSignal(reason="盘中止盈", price_type="close")
        if position["hold_days"] >= max_hold:
            return SellSignal(reason="到期卖出", price_type="close")
        return None
