"""
数据采集器包
"""
from .base import BaseCollector
from .lhb_collector import LhbCollector

__all__ = [
    'BaseCollector',
    'LhbCollector',
]
