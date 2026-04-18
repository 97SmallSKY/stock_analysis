"""龙虎榜评分模块。

导出:
    compute_score(inp) -> dict
    ScoreInput
"""

from .lhb_score import ScoreInput, compute_score

__all__ = ["ScoreInput", "compute_score"]
