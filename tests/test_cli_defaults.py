"""CLI argparse 默认值与领域层常量的一致性回归。"""
import re
from pathlib import Path

from backend.domain.lhb.tn_price_backfill import DEFAULT_N_DAYS


def test_lhb_tn_price_n_days_default_matches_domain():
    cli_src = Path("backend/cli.py").read_text(encoding="utf-8")
    m = re.search(r'"--n-days",\s*default="([0-9,]+)"', cli_src)
    assert m, "未在 backend/cli.py 找到 --n-days default 定义"
    parsed = tuple(int(x) for x in m.group(1).split(","))
    assert parsed == DEFAULT_N_DAYS, (
        f"CLI default={parsed} 与 DEFAULT_N_DAYS={DEFAULT_N_DAYS} 不同步"
    )
