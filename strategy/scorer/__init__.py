"""
评分模块包兼容层。

历史代码通过 `from strategy.scorer import ...` 导入评分函数，
但仓库同时存在 `strategy/scorer.py`（模块）与 `strategy/scorer/`（包）。
Python 会优先解析为包，因此这里显式桥接到模块文件，避免导入失败。
"""

from __future__ import annotations

from importlib.util import spec_from_file_location, module_from_spec
from pathlib import Path

_SCORER_FILE = Path(__file__).resolve().parent.parent / "scorer.py"
_SPEC = spec_from_file_location("strategy._legacy_scorer_file", _SCORER_FILE)
if _SPEC is None or _SPEC.loader is None:
    raise ImportError(f"无法加载评分模块文件: {_SCORER_FILE}")

_MOD = module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MOD)

DEFAULT_WEIGHTS = _MOD.DEFAULT_WEIGHTS
DEFAULT_BENCHMARKS = _MOD.DEFAULT_BENCHMARKS
calc_robot_metrics = _MOD.calc_robot_metrics
calc_robot_score = _MOD.calc_robot_score
rank_robots = _MOD.rank_robots
load_weights_config = _MOD.load_weights_config

__all__ = [
    "DEFAULT_WEIGHTS",
    "DEFAULT_BENCHMARKS",
    "calc_robot_metrics",
    "calc_robot_score",
    "rank_robots",
    "load_weights_config",
]
