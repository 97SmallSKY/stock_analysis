"""回测引擎领域层.

- engine.py  事件驱动日 K 回放, 调用 BaseStrategy + 风控
- portfolio.py 组合净值每日更新
- report.py 计算夏普 / 回撤 / 胜率 / 月度收益
"""
from .engine import BacktestEngine, BacktestResult, BacktestConfig, DailyProvider
from .portfolio import Portfolio, NavPoint
from .report import compute_metrics, BacktestMetrics

__all__ = [
    'BacktestEngine', 'BacktestResult', 'BacktestConfig', 'DailyProvider',
    'Portfolio', 'NavPoint',
    'compute_metrics', 'BacktestMetrics',
]
