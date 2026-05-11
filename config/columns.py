"""列名常量（消除各页面硬编码的中英文列名，方便上游数据源改名时统一替换）。"""


class LhbCols:
    CODE       = "代码"
    NAME       = "名称"
    CLOSE      = "收盘价"
    NET_BUY    = "龙虎榜净买额"
    CHANGE_PCT = "涨跌幅"
    RET_1D     = "上榜后1日"
    RET_5D     = "上榜后5日"
    RET_10D    = "上榜后10日"
    TRADE_DATE = "trade_date"
    TS_CODE    = "ts_code"


class KlineCols:
    TS_CODE    = "ts_code"
    TRADE_DATE = "trade_date"
    OPEN       = "open"
    HIGH       = "high"
    LOW        = "low"
    CLOSE      = "close"
    VOLUME     = "vol"
    CLOSE_QFQ  = "close_qfq"
    CLOSE_HFQ  = "close_hfq"
    ADJ_FACTOR = "adj_factor"
    MA5        = "ma5"
    MA20       = "ma20"
