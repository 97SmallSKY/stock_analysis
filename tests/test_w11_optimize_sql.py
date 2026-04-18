"""tests/test_w11_optimize_sql.py — W11-1

轻量检查 db/init/04_W11_optimize.sql:
  - 文件存在且非空
  - 含 market_snapshots 压缩配置 + compression_policy
  - 含关键索引名 (幂等 CREATE INDEX IF NOT EXISTS)
  - 所有 CREATE INDEX 使用 IF NOT EXISTS (避免重跑报错)
  - 通过 sqlparse 可解析 (最小语法校验)
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

PATH = Path(__file__).resolve().parents[1] / "db" / "init" / "04_W11_optimize.sql"


def test_file_exists_and_non_empty():
    assert PATH.is_file(), f"missing {PATH}"
    assert PATH.stat().st_size > 100


def test_contains_compression_and_policy():
    body = PATH.read_text(encoding="utf-8")
    assert "timescaledb.compress" in body
    assert "compress_segmentby" in body
    assert "compress_orderby" in body
    assert "add_compression_policy" in body
    assert "market_snapshots" in body


@pytest.mark.parametrize("idx_name", [
    "idx_lhb_daily_date_score",
    "idx_lhb_daily_date_net",
    "idx_lhb_seats_date_code_dir",
    "idx_market_sentiment_date_desc",
    "idx_backtest_tasks_status_finished",
    "idx_cash_ledger_robot_date",
    "idx_data_fetch_log_name_ts",
    "idx_market_snapshots_code_ts_desc",
])
def test_key_indexes_present(idx_name):
    body = PATH.read_text(encoding="utf-8")
    assert idx_name in body


def test_all_indexes_idempotent():
    body = PATH.read_text(encoding="utf-8")
    # 去掉 SQL 行注释 -- ... 与块注释 /* ... */
    cleaned = re.sub(r"/\*.*?\*/", "", body, flags=re.DOTALL)
    cleaned = "\n".join(
        line.split("--", 1)[0] for line in cleaned.splitlines()
    )
    for m in re.finditer(r"CREATE\s+INDEX\s+(?!IF\s+NOT\s+EXISTS)", cleaned, re.IGNORECASE):
        raise AssertionError(f"CREATE INDEX 缺 IF NOT EXISTS at offset {m.start()}")


def test_sqlparse_parses_without_error():
    sqlparse = pytest.importorskip("sqlparse")
    body = PATH.read_text(encoding="utf-8")
    stmts = sqlparse.parse(body)
    # 至少 8 条语句 (DO block + 若干 CREATE INDEX)
    assert len(stmts) >= 8
