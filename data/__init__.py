"""
数据层

提供统一的数据获取接口，支持多数据源降级：
1. Tushare（优先级最高，120积分限制）
2. AKShare（次优先，免费开源）
3. Sina（最低优先级，仅实时行情）
"""
from .fetcher import (
    get_data_manager,
    DataSourceManager,
    FetchResult,
    TushareDataSource,
    AKShareDataSource,
    SinaDataSource,
)

__all__ = [
    'get_data_manager',
    'DataSourceManager', 
    'FetchResult',
    'TushareDataSource',
    'AKShareDataSource',
    'SinaDataSource',
]
