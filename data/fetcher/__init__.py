"""
数据层模块

数据源优先级：Tushare(1) → AKShare(2) → Sina(3)
"""
from .base import BaseDataSource, FetchResult
from .tushare_fetcher import TushareDataSource
from .akshare_fetcher import AKShareDataSource
from .sina_fetcher import SinaDataSource
from .data_source_manager import DataSourceManager, get_data_manager

__all__ = [
    'BaseDataSource',
    'FetchResult',
    'TushareDataSource',
    'AKShareDataSource',
    'SinaDataSource',
    'DataSourceManager',
    'get_data_manager',
]
