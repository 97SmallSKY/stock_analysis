"""backend.domain.realtime — W8 盘中实时数据:

- subscription_pool: 维护订阅 ts_code 集合 (LHB T-20 ∪ 今日涨停池 ∪ 手工)
- snapshot_writer: market_snapshots 批量入库
- scheduler (W8-3): APScheduler 10s cron job
"""
