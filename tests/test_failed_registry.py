"""failed_registry: 失败清单 append / 读取 / 去重 / 清理 的单测。"""
from __future__ import annotations

from pathlib import Path

from backend.data_gateway import failed_registry as fr


def test_record_and_read(tmp_path: Path):
    p = tmp_path / "failed.csv"
    fr.record_failure(
        category="DAILY_KLINE", method="get_daily_kline",
        tag="ts_code=000001.SZ",
        ts_code="000001.SZ", start_date="20240101", end_date="20241231",
        last_error="Connection aborted",
        path=p,
    )
    fr.record_failure(
        category="DAILY_KLINE", method="get_daily_kline",
        tag="ts_code=600519.SH",
        ts_code="600519.SH", start_date="20240101", end_date="20241231",
        last_error="hard timeout",
        path=p,
    )
    rows = fr.read_failed(path=p)
    assert len(rows) == 2
    codes = sorted(r["ts_code"] for r in rows)
    assert codes == ["000001.SZ", "600519.SH"]


def test_dedup_keeps_latest(tmp_path: Path):
    p = tmp_path / "failed.csv"
    fr.record_failure(category="DAILY_KLINE", method="m", tag="",
                      ts_code="000001.SZ", start_date="20240101",
                      end_date="20241231", last_error="err1", path=p)
    fr.record_failure(category="DAILY_KLINE", method="m", tag="",
                      ts_code="000001.SZ", start_date="20240101",
                      end_date="20241231", last_error="err2", path=p)
    rows = fr.read_failed(path=p)
    assert len(rows) == 1
    assert rows[0]["last_error"] == "err2"  # 保留最新
    # 关闭去重看原始行数
    raw = fr.read_failed(path=p, deduplicate=False)
    assert len(raw) == 2


def test_clear_succeeded(tmp_path: Path):
    p = tmp_path / "failed.csv"
    fr.record_failure(category="DAILY_KLINE", method="m", tag="",
                      ts_code="A", start_date="s", end_date="e",
                      last_error="x", path=p)
    fr.record_failure(category="DAILY_KLINE", method="m", tag="",
                      ts_code="B", start_date="s", end_date="e",
                      last_error="x", path=p)
    # 移除 A
    removed = fr.clear_succeeded(
        [("DAILY_KLINE", "A", "", "s", "e")], path=p,
    )
    assert removed == 1
    rows = fr.read_failed(path=p)
    assert [r["ts_code"] for r in rows] == ["B"]


def test_filter_by_category(tmp_path: Path):
    p = tmp_path / "failed.csv"
    fr.record_failure(category="DAILY_KLINE", method="m", tag="",
                      ts_code="A", last_error="", path=p)
    fr.record_failure(category="LHB_DETAIL", method="m", tag="",
                      trade_date="20240101", last_error="", path=p)
    kline = fr.read_failed(only_category="DAILY_KLINE", path=p)
    lhb = fr.read_failed(only_category="LHB_DETAIL", path=p)
    assert len(kline) == 1 and kline[0]["ts_code"] == "A"
    assert len(lhb) == 1 and lhb[0]["trade_date"] == "20240101"
