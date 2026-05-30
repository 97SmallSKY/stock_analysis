"""
modules/backtest — 策略回测引擎

架构:
  data_feed.py   DataFeed (统一数据访问层)
  portfolio.py   Position / Portfolio
  executor.py    Executor (交易执行 + 保守路径未来函数修复)
  engine.py      BacktestEngine (事件/全时序统一循环)
  metrics.py     analyze() (KPI / 回撤 / 滚动 / 基准 / exit 分布)
  exits/         Exit 组件库 (BaseExit / FixedPct / ATR / Trailing / Time / Sentiment / Composite)
"""

from .data_feed import DataFeed
from .portfolio import Position, Bar, Portfolio
from .executor import Executor, BacktestContext
from .engine import BacktestEngine, BacktestDiagnostic
from .metrics import analyze, benchmark_compare
from . import exits
