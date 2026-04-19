"""Changelog API — 解析 docs/CHANGELOG.md 返回结构化条目。

前端「特性介绍」页面 `/changelog` 的数据源。

返回格式:
{
  "entries": [
    {"date": "2026-04-20", "type": "feature", "title": "新增特性介绍页面", "body": "...md..."},
    ...
  ],
  "types": ["feature", "fix", "refactor", "perf", "docs", "ops"],
  "total": N
}

解析规则:匹配 `### YYYY-MM-DD · <type> · <title>`,body 为下一个 `### ` 之前的 md 文本。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import List, Dict, Any

from fastapi import APIRouter

router = APIRouter()

_CHANGELOG_PATH = Path(__file__).resolve().parents[2] / "docs" / "CHANGELOG.md"

# ### 2026-04-20 · feature · 标题
_HEADING_RE = re.compile(
    r"^###\s+(\d{4}-\d{2}-\d{2})\s*·\s*([A-Za-z]+)\s*·\s*(.+?)\s*$",
    re.MULTILINE,
)


def parse_changelog(md_text: str) -> List[Dict[str, Any]]:
    """把 md 文本切成结构化条目列表(保持文件内原有顺序)。"""
    matches = list(_HEADING_RE.finditer(md_text))
    entries: List[Dict[str, Any]] = []
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(md_text)
        body = md_text[start:end].strip()
        # 去掉条目末尾的分隔线 ---
        body = re.sub(r"\n?-{3,}\s*$", "", body).strip()
        entries.append({
            "date": m.group(1),
            "type": m.group(2).lower(),
            "title": m.group(3),
            "body": body,
        })
    return entries


@router.get("")
@router.get("/")
def get_changelog() -> Dict[str, Any]:
    if not _CHANGELOG_PATH.is_file():
        return {"entries": [], "types": [], "total": 0, "error": "CHANGELOG.md not found"}
    md = _CHANGELOG_PATH.read_text(encoding="utf-8")
    entries = parse_changelog(md)
    types = sorted({e["type"] for e in entries})
    return {
        "entries": entries,
        "types": types,
        "total": len(entries),
    }
