"""tests for backend.api.changelog — docs/CHANGELOG.md 解析 + HTTP 端点。"""
from __future__ import annotations

import textwrap
from unittest.mock import patch

from fastapi.testclient import TestClient
from fastapi import FastAPI

from backend.api.changelog import parse_changelog, router


def test_parse_changelog_basic():
    md = textwrap.dedent(
        """
        # Changelog

        > intro text, should be ignored.

        ---

        ### 2026-04-20 · feature · 新增页面 A

        - 第一点
        - 第二点 `code`

        ---

        ### 2026-04-19 · fix · 修复 bug B

        简要说明。

        ---
        """
    ).strip()

    entries = parse_changelog(md)
    assert len(entries) == 2

    e0 = entries[0]
    assert e0["date"] == "2026-04-20"
    assert e0["type"] == "feature"
    assert e0["title"] == "新增页面 A"
    assert "第一点" in e0["body"]
    assert "第二点" in e0["body"]
    # 末尾分隔线应被去掉
    assert not e0["body"].rstrip().endswith("---")

    e1 = entries[1]
    assert e1["date"] == "2026-04-19"
    assert e1["type"] == "fix"
    assert e1["title"] == "修复 bug B"
    assert "简要说明" in e1["body"]


def test_parse_changelog_empty_and_no_match():
    assert parse_changelog("") == []
    assert parse_changelog("# 只有标题\n无条目") == []
    # 三级标题但不符合格式 "YYYY-MM-DD · type · title",不应被解析
    assert parse_changelog("### 随便写的标题\n正文") == []


def test_parse_changelog_type_case_insensitive():
    md = "### 2026-04-20 · FEATURE · 大写类型\n正文"
    entries = parse_changelog(md)
    assert len(entries) == 1
    assert entries[0]["type"] == "feature"  # 规范化为小写


def test_parse_changelog_last_entry_without_trailing_separator():
    """最后一条没有尾部 --- 也要能被完整解析。"""
    md = "### 2026-04-20 · ops · 无尾分隔线\n\n正文内容。"
    entries = parse_changelog(md)
    assert len(entries) == 1
    assert "正文内容" in entries[0]["body"]


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router, prefix="/api/changelog")
    return app


def test_api_changelog_ok():
    client = TestClient(_make_app())
    r = client.get("/api/changelog")
    assert r.status_code == 200
    body = r.json()
    # 真实 docs/CHANGELOG.md 至少应有若干条(本次提交就写了多条)
    assert "entries" in body
    assert "types" in body
    assert "total" in body
    assert isinstance(body["entries"], list)
    assert body["total"] == len(body["entries"])
    if body["entries"]:
        e = body["entries"][0]
        assert set(e.keys()) >= {"date", "type", "title", "body"}


def test_api_changelog_sorted_by_date_desc(tmp_path):
    """即便 md 内条目顺序被人为打乱,接口也应按 date 倒序返回。"""
    md_path = tmp_path / "CHANGELOG.md"
    md_path.write_text(
        "### 2026-01-01 · fix · 旧\n正文\n\n"
        "### 2026-05-01 · feature · 新\n正文\n\n"
        "### 2026-03-01 · docs · 中\n正文\n",
        encoding="utf-8",
    )
    with patch("backend.api.changelog._CHANGELOG_PATH", md_path):
        client = TestClient(_make_app())
        r = client.get("/api/changelog")
        assert r.status_code == 200
        dates = [e["date"] for e in r.json()["entries"]]
        assert dates == ["2026-05-01", "2026-03-01", "2026-01-01"]


def test_api_changelog_missing_file(tmp_path):
    """CHANGELOG.md 不存在时,返回空列表 + error 字段,不应 500。"""
    fake = tmp_path / "nope.md"
    with patch("backend.api.changelog._CHANGELOG_PATH", fake):
        client = TestClient(_make_app())
        r = client.get("/api/changelog")
        assert r.status_code == 200
        body = r.json()
        assert body["entries"] == []
        assert body["total"] == 0
        assert "error" in body


# ---------------------------------------------------------------------
# P20.1: /api/devlog 独立端点,面向开发者
# ---------------------------------------------------------------------
def _make_devlog_app() -> FastAPI:
    from backend.api.changelog import devlog_router
    app = FastAPI()
    app.include_router(devlog_router, prefix="/api/devlog")
    return app


def test_api_devlog_ok():
    client = TestClient(_make_devlog_app())
    r = client.get("/api/devlog")
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "devlog"
    assert isinstance(body["entries"], list)
    # 真实 DEVLOG.md 已包含若干条
    assert body["total"] == len(body["entries"])
    if body["entries"]:
        e = body["entries"][0]
        assert set(e.keys()) >= {"date", "type", "title", "body"}


def test_api_devlog_missing_file(tmp_path):
    fake = tmp_path / "no.md"
    with patch("backend.api.changelog._DEVLOG_PATH", fake):
        client = TestClient(_make_devlog_app())
        r = client.get("/api/devlog")
        assert r.status_code == 200
        body = r.json()
        assert body["source"] == "devlog"
        assert body["entries"] == []
        assert "error" in body


def test_api_changelog_has_source_key():
    """P20.1: 响应增加 source 字段,前端可区分 changelog vs devlog。"""
    client = TestClient(_make_app())
    r = client.get("/api/changelog")
    assert r.json()["source"] == "changelog"
