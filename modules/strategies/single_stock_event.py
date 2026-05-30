from .base import BaseStrategy, BuyOrder, SellSignal
from .registry import register_strategy


@register_strategy("single_stock_event")
class SingleStockEventStrategy(BaseStrategy):
    description = "单股票龙虎榜事件研究：指定股票，每次上榜日模拟买入，验证信号质量"
    param_schema = {
        "stock_code":       {"type": "str",   "default": "000001", "label": "股票代码"},
        "stop_loss":        {"type": "float", "default": -0.07,   "label": "止损比例"},
        "stop_profit":      {"type": "float", "default": 0.15,    "label": "止盈比例"},
        "max_hold_days":    {"type": "int",   "default": 10,      "label": "最大持有天数"},
        "filter_one_word":  {"type": "bool",  "default": True,    "label": "过滤一字涨停"},
        "max_buy_gap":      {"type": "float", "default": 0.09,    "label": "T+1最大高开%"},
        "allow_overlap":    {"type": "bool",  "default": True,    "label": "允许重叠持仓"},
        "min_net_buy":      {"type": "float", "default": 0,       "label": "最低净买入（万元，0=不过滤）"},
    }

    def select(self, trade_date, lhb_df, kline_cache) -> list[BuyOrder]:
        code = self.params["stock_code"]
        code_clean = str(code).replace(".SZ", "").replace(".SH", "").replace(".BJ", "").zfill(6)

        date_col = "trade_date" if "trade_date" in lhb_df.columns else "上榜日期"
        code_col = "代码" if "代码" in lhb_df.columns else "股票代码"

        day_df = lhb_df[lhb_df[date_col].astype(str) == trade_date]
        matches = day_df[day_df[code_col].astype(str).str.contains(code_clean, na=False)]

        if matches.empty:
            return []

        # 计算净买入额用于过滤
        if "买入额" in matches.columns and "卖出额" in matches.columns:
            net_buy = (matches["买入额"].sum() - matches["卖出额"].sum())
        else:
            net_buy = 0

        min_net = self.params["min_net_buy"] * 10000
        if min_net > 0 and net_buy < min_net:
            return []

        name_col = "名称" if "名称" in matches.columns else "股票名称"
        name = matches[name_col].iloc[0] if name_col in matches.columns else code

        return [BuyOrder(
            code=code,
            reason="单股票龙虎榜事件买入",
            meta={"name": name, "net_buy": net_buy},
        )]

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
