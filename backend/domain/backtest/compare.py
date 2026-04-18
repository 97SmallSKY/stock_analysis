"""W10-1 多策略并行回测对比.

输入一组 (name, strategy) 在同一时间窗/同一 provider/同一 cfg 下分别跑回测,
汇总指标得到 ComparisonReport.

并发:
    用 ThreadPoolExecutor (max_workers=min(8, N)): 回测主要是 Python+小量数据,
    线程足以重叠 IO/计算; 若需跨进程再单独做.

容错:
    单个策略失败不影响其它, 其行 ok=False + error.
"""
from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Optional

from backend.domain.backtest.engine import (
    BacktestConfig, BacktestEngine, BacktestResult, DailyProvider,
)
from backend.domain.backtest.report import BacktestMetrics, compute_metrics
from backend.domain.strategy.base import BaseStrategy

logger = logging.getLogger(__name__)


@dataclass
class StrategyRun:
    name: str
    ok: bool
    error: Optional[str] = None
    total_return: float = 0.0
    annual_return: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown: float = 0.0
    win_rate: float = 0.0
    trade_count: int = 0
    final_assets: float = 0.0
    duration_ms: int = 0

    @classmethod
    def from_metrics(cls, name: str, m: BacktestMetrics, result: BacktestResult,
                     duration_ms: int) -> "StrategyRun":
        return cls(
            name=name, ok=True,
            total_return=m.total_return,
            annual_return=m.annual_return,
            sharpe_ratio=m.sharpe_ratio,
            max_drawdown=m.max_drawdown,
            win_rate=m.win_rate,
            trade_count=m.trade_count,
            final_assets=result.final_assets,
            duration_ms=duration_ms,
        )


@dataclass
class ComparisonReport:
    start_date: date
    end_date: date
    initial_capital: float
    runs: list[StrategyRun] = field(default_factory=list)

    def winner(self) -> Optional[StrategyRun]:
        """按年化收益取最佳. 全失败返回 None."""
        oks = [r for r in self.runs if r.ok]
        if not oks:
            return None
        return max(oks, key=lambda r: r.annual_return)

    def to_dict(self) -> dict:
        return {
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
            "initial_capital": self.initial_capital,
            "runs": [asdict(r) for r in self.runs],
            "winner": (self.winner().name if self.winner() else None),
        }


def _run_one(
    name: str, strategy: BaseStrategy, provider: DailyProvider,
    config: BacktestConfig, start: date, end: date,
) -> StrategyRun:
    t0 = time.time()
    try:
        eng = BacktestEngine(strategy=strategy, provider=provider, config=config)
        result = eng.run(start, end)
        m = compute_metrics(result)
        return StrategyRun.from_metrics(
            name, m, result, int((time.time() - t0) * 1000),
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[compare] 策略 {name!r} 失败: {e!r}")
        return StrategyRun(
            name=name, ok=False, error=str(e),
            duration_ms=int((time.time() - t0) * 1000),
        )


def run_compare(
    strategies: list[tuple[str, BaseStrategy]],
    provider: DailyProvider,
    start: date,
    end: date,
    *,
    config: Optional[BacktestConfig] = None,
    max_workers: int = 4,
) -> ComparisonReport:
    """并行跑一组策略, 返回 ComparisonReport.

    strategies: [(name, strategy_instance), ...]
    每个策略独立 BacktestEngine/Portfolio, 不共享状态.
    """
    cfg = config or BacktestConfig()
    report = ComparisonReport(
        start_date=start, end_date=end,
        initial_capital=cfg.initial_capital,
    )
    if not strategies:
        return report

    workers = max(1, min(max_workers, len(strategies)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_run_one, name, strat, provider, cfg, start, end): name
            for name, strat in strategies
        }
        for f in as_completed(futures):
            report.runs.append(f.result())

    # 稳定排序: ok 优先, 然后 annual_return 降序, 便于前端直接渲染
    report.runs.sort(key=lambda r: (not r.ok, -r.annual_return, r.name))
    return report
