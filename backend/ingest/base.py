"""ingest 基础工具。"""

from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, Optional

import pandas as pd

logger = logging.getLogger(__name__)


def df_to_rows(df: Optional[pd.DataFrame], colmap: Dict[str, str]) -> list[dict]:
    """将 DataFrame 转为 list[dict]，按 colmap 做列重命名；缺失列留 None。

    colmap = {src_col: dst_col}
    """
    if df is None or df.empty:
        return []
    present = {src: dst for src, dst in colmap.items() if src in df.columns}
    out = df[list(present.keys())].rename(columns=present)
    out = out.where(pd.notnull(out), None)
    return out.to_dict(orient="records")


def normalize_date(v):
    """把 '20240101' / '2024-01-01' / datetime / date 归一为 'YYYY-MM-DD' 字符串。"""
    if v is None:
        return None
    if hasattr(v, "strftime"):
        return v.strftime("%Y-%m-%d")
    s = str(v).strip()
    if not s:
        return None
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    return s


def chunked(seq: Iterable, n: int):
    buf = []
    for x in seq:
        buf.append(x)
        if len(buf) >= n:
            yield buf
            buf = []
    if buf:
        yield buf


def get_manager():
    """惰性创建 DataSourceManager 单例。"""
    from backend.core.startup import bootstrap
    from backend.data_gateway.manager import DataSourceManager
    from backend.config import get_settings

    bootstrap()
    mgr = DataSourceManager.get_instance()
    s = get_settings()
    mgr.initialize({
        "tushare": {"token": s.ds.tushare_token},
        "akshare": {},
        "sina": {},
        "crawler": {},
    })
    return mgr
