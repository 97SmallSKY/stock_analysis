"""Changelog / Devlog API — 解析 docs/CHANGELOG.md + docs/DEVLOG.md 返回结构化条目。

前端「特性介绍」页面 `/changelog` 的数据源。P20.1 起拆分为两个 tab:

- `GET /api/changelog`  → 面向用户的特性日志(docs/CHANGELOG.md)
- `GET /api/devlog`     → 面向开发的开发日志(docs/DEVLOG.md)

共用同一套解析逻辑 parse_changelog(),只是指向不同文件。

返回格式:
{
  "source": "changelog" | "devlog",
  "entries": [
    {"date": "2026-04-20", "type": "feature", "title": "...", "body": "...md..."},
    ...
  ],
  "types": ["feature", "fix", ...],
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

_DOCS_ROOT = Path(__file__).resolve().parents[2] / "docs"
_CHANGELOG_PATH = _DOCS_ROOT / "CHANGELOG.md"
_DEVLOG_PATH = _DOCS_ROOT / "DEVLOG.md"

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


def _build_response(source: str, path: Path) -> Dict[str, Any]:
    if not path.is_file():
        return {
            "source": source,
            "entries": [],
            "types": [],
            "total": 0,
            "error": f"{path.name} not found",
        }
    md = path.read_text(encoding="utf-8")
    entries = parse_changelog(md)
    entries.sort(key=lambda e: e["date"], reverse=True)
    types = sorted({e["type"] for e in entries})
    return {
        "source": source,
        "entries": entries,
        "types": types,
        "total": len(entries),
    }


@router.get("")
@router.get("/")
def get_changelog() -> Dict[str, Any]:
    """面向用户的特性日志。"""
    return _build_response("changelog", _CHANGELOG_PATH)


# P20.1 (2026-04-20): 用同一 router 暴露 /devlog,便于 api/main.py 统一挂载
# 注意 main.py 注册此 router 时会用 prefix="/api/changelog",所以这里用
# 绝对路径 "/api/devlog" 会冲突。改为新建一个独立的 devlog_router。
devlog_router = APIRouter()


@devlog_router.get("")
@devlog_router.get("/")
def get_devlog() -> Dict[str, Any]:
    """面向开发的开发日志。"""
    return _build_response("devlog", _DEVLOG_PATH)
