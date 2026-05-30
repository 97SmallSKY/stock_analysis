"""
数据层兼容层

从新架构 re-export 到旧路径，确保向后兼容，"""

# 数据获取 - 从新架构导入
from data.fetcher.base import BaseDataSource, FetchResult
from data.fetcher.tushare_fetcher import TushareDataSource
from data.fetcher.akshare_fetcher import AKShareDataSource
from data.fetcher.sina_fetcher import SinaDataSource
from data.fetcher.data_source_manager import DataSourceManager, get_data_manager

# 数据存储 - 保持原有实现
from data.storage.base import storage as _storage_instance

# 为旧代码提供 storage 单例
storage = _storage_instance

__all__ = [
    'BaseDataSource',
    'FetchResult',
    'TushareDataSource',
    'AKShareDataSource',
    'SinaDataSource',
    'DataSourceManager',
    'get_data_manager',
    'storage',
]
