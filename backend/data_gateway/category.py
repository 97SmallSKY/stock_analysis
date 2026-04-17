"""
数据类别枚举
"""

from enum import Enum


class DataCategory(str, Enum):
    """数据类别枚举，每个类别有独立的数据源优先级链"""

    DAILY_KLINE = "DAILY_KLINE"  # 日K线
    LHB_DETAIL = "LHB_DETAIL"  # 龙虎榜明细
    LHB_BROKER = "LHB_BROKER"  # 龙虎榜营业部
    INSTITUTION = "INSTITUTION"  # 机构数据
    REALTIME_QUOTE = "REALTIME_QUOTE"  # 实时行情
    MARKET_SENTIMENT = "MARKET_SENTIMENT"  # 市场情绪
    FINANCIAL_DATA = "FINANCIAL_DATA"  # 财务数据
    STOCK_INFO = "STOCK_INFO"  # 股票基础信息
    TRADE_CALENDAR = "TRADE_CALENDAR"  # 交易日历
    INDEX_DAILY = "INDEX_DAILY"  # 指数日线


class AdapterName(str, Enum):
    """适配器名称"""

    TUSHARE = "tushare"
    AKSHARE = "akshare"
    SINA = "sina"
    CRAWLER = "crawler"
