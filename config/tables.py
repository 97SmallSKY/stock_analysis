"""数据库表名常量（集中管理，避免跨文件拼写不一致）。"""


class Tables:
    LHB_TOP10    = "lhb_top10"
    LHB_DETAIL   = "lhb_detail"
    LHB_RETURNS  = "lhb_returns"
    KLINE_DAILY  = "kline_daily"
    SIM_TRADES   = "sim_trades"
    SIM_ORDERS   = "sim_orders"
    BACKTEST     = "backtest_results"
    SENTIMENT    = "market_sentiment"
    STOCK_INFO   = "stock_info"
