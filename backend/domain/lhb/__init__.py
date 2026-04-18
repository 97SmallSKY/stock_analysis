"""龙虎榜领域。

模块划分:
  parser    各数据源 DataFrame -> LhbRecord (统一)
  ingestor  写 lhb_stock_daily / lhb_seats / lhb_tn_price
  aggregator 写 lhb_agg_stats / lhb_institution_stat
"""

from .parser import LhbRecord, LhbSeat, parse_lhb_dataframe

__all__ = ["LhbRecord", "LhbSeat", "parse_lhb_dataframe"]
