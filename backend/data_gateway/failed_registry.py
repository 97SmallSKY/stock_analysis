"""失败采集清单登记器 (2026-04-18).

设计:
- 全链路失败的 fetch 请求, 按 (category, ts_code, trade_date) 去重登记到
  一个 append-only 的 CSV 文件, 供后续 retry-failed 子命令逐个补采.
- 写入路径默认 logs/failed_symbols.csv (可通过 STOCK_ANALYSIS_FAILED_CSV
  环境变量覆盖).
- 并发安全: 用文件锁 (fcntl on posix) 保护 append; 无 fcntl 时退化到
  threading.Lock (多进程下仍可能交叉, 但 CSV 一行一写, 损失可接受).
- 字段(列名): ts,category,method,tag,ts_code,trade_date,start_date,end_date,last_error

读取侧:
    read_failed(only_category="DAILY_KLINE") -> list[dict]  # 自动去重
    clear_failed(rows_to_remove)  # 成功补采后移除
"""
from __future__ import annotations

import csv
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

_LOCK = threading.Lock()

DEFAULT_PATH = Path(
    os.environ.get("STOCK_ANALYSIS_FAILED_CSV", "logs/failed_symbols.csv")
)

_FIELDS = [
    "ts", "category", "method", "tag",
    "ts_code", "trade_date", "start_date", "end_date",
    "last_error",
]


def _ensure_header(path: Path) -> None:
    if path.exists() and path.stat().st_size > 0:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(_FIELDS)


def record_failure(
    *,
    category: str,
    method: str,
    tag: str,
    ts_code: Optional[str] = None,
    trade_date: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    last_error: Optional[str] = None,
    path: Optional[Path] = None,
) -> None:
    """追加一条失败记录 (append-only).

    同一 (category, ts_code, trade_date/date_range) 可能反复出现 (重试),
    这里不做去重, 由下游 read_failed 统一去重. 单次 I/O 成本最小.
    """
    p = Path(path) if path else DEFAULT_PATH
    with _LOCK:
        _ensure_header(p)
        try:
            with p.open("a", encoding="utf-8", newline="") as f:
                w = csv.writer(f)
                w.writerow([
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    category, method, tag,
                    ts_code or "",
                    trade_date or "",
                    start_date or "",
                    end_date or "",
                    (last_error or "")[:500],  # 截断防止超长
                ])
        except Exception:
            # 永不影响主流程
            pass


def read_failed(
    only_category: Optional[str] = None,
    path: Optional[Path] = None,
    deduplicate: bool = True,
) -> list[dict]:
    """读失败清单. 默认按 (category, ts_code, trade_date, start_date, end_date)
    去重, 保留最新一条 last_error. 没文件返回 []."""
    p = Path(path) if path else DEFAULT_PATH
    if not p.exists():
        return []
    rows: list[dict] = []
    with p.open("r", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if only_category and r.get("category") != only_category:
                continue
            rows.append(r)
    if not deduplicate:
        return rows
    seen: dict[tuple, dict] = {}
    for r in rows:
        key = (
            r.get("category"), r.get("ts_code"),
            r.get("trade_date"), r.get("start_date"), r.get("end_date"),
        )
        seen[key] = r  # 覆盖, 保留最新
    return list(seen.values())


def clear_succeeded(
    keys_to_remove: list[tuple],
    path: Optional[Path] = None,
) -> int:
    """重写清单, 移除已补采成功的条目.
    keys_to_remove 中的 tuple 与 read_failed 的 key 结构一致:
        (category, ts_code, trade_date, start_date, end_date)
    返回被移除的行数.
    """
    p = Path(path) if path else DEFAULT_PATH
    if not p.exists():
        return 0
    remove_set = {tuple(k) for k in keys_to_remove}
    with _LOCK:
        rows = read_failed(path=p, deduplicate=False)
        kept = []
        removed = 0
        for r in rows:
            key = (
                r.get("category"), r.get("ts_code"),
                r.get("trade_date"), r.get("start_date"), r.get("end_date"),
            )
            if key in remove_set:
                removed += 1
                continue
            kept.append(r)
        # 全量重写
        with p.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=_FIELDS)
            w.writeheader()
            for r in kept:
                w.writerow({k: r.get(k, "") for k in _FIELDS})
        return removed
