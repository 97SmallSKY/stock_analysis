"""
数据层兼容层 - 逐步迁移到 data/ 目录
"""
# 数据获取
from data.fetcher.akshare_fetcher import *
from data.fetcher.realtime_fetcher import *
from data.fetcher.news_fetcher import *

# 数据存储
from data.storage.base import *

__all__ = [
    'data_fetcher',
    'realtime_fetcher', 
    'news_fetcher',
    'storage',
]
