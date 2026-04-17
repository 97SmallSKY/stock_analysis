"""
回测框架
"""
from .metrics import (
    BacktestMetrics,
    calculate_metrics,
    generate_report,
    generate_html_report,
)

__all__ = [
    'BacktestMetrics',
    'calculate_metrics',
    'generate_report',
    'generate_html_report',
]
