"""P16.5 lhb_tn_price 回填单测 (mock pg_conn)."""
from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

from backend.domain.lhb import tn_price_backfill as mod


def _mock_conn():
    """构造一个可 __enter__ / cursor() / commit() 的 mock conn."""
    conn = MagicMock()
    cur = MagicMock()
    cur.rowcount = 42
    conn.__enter__.return_value = conn
    conn.__exit__.return_value = False
    conn.cursor.return_value.__enter__.return_value = cur
    conn.cursor.return_value.__exit__.return_value = False
    return conn, cur


def test_pg_date_accepts_date_obj():
    assert mod._pg_date(date(2026, 4, 17)) == "2026-04-17"


def test_pg_date_accepts_yyyymmdd_str():
    assert mod._pg_date("20260417") == "2026-04-17"


def test_pg_date_accepts_dashed_str():
    assert mod._pg_date("2026-04-17") == "2026-04-17"


def test_pg_date_rejects_bad():
    import pytest
    with pytest.raises(ValueError):
        mod._pg_date("bad")


def test_run_range_default_n_days_executes_5_upserts():
    conn, cur = _mock_conn()
    with patch.object(mod, "pg_conn", return_value=conn):
        stats = mod.run_range("20260101", "20260110")
    # STO-81: 默认 N=1,2,3,5,7,10 → 6 个 SQL upsert
    assert cur.execute.call_count == 6
    assert stats["n_days"] == [1, 2, 3, 5, 7, 10]
    for n in (1, 2, 3, 5, 7, 10):
        assert stats[f"n{n}_upserted"] == 42
    conn.commit.assert_called_once()


def test_run_range_custom_n_days():
    conn, cur = _mock_conn()
    with patch.object(mod, "pg_conn", return_value=conn):
        stats = mod.run_range("20260101", "20260110", n_days=[1, 3])
    assert cur.execute.call_count == 2
    assert stats["n_days"] == [1, 3]


def test_run_range_sql_contains_limit_pct_and_dates():
    conn, cur = _mock_conn()
    with patch.object(mod, "pg_conn", return_value=conn):
        mod.run_range("20260101", "20260110", n_days=[1])
    # 参数化 SQL 的 values 在 execute 的第二个参数里
    args, kwargs = cur.execute.call_args
    sql, params = args
    assert params["df"] == "2026-01-01"
    assert params["dt"] == "2026-01-10"
    assert params["n"] == 1
    assert params["lim"] == mod.LIMIT_PCT
    # SQL 应该含关键 CTE 名
    assert "WITH base AS" in sql
    assert "ranked" in sql
    assert "ON CONFLICT (stock_daily_id, n_day)" in sql


def test_run_range_rejects_empty_n_days():
    import pytest
    with pytest.raises(ValueError):
        mod.run_range("20260101", "20260110", n_days=[])


def test_run_all_empty_uses_lhb_range():
    conn, cur = _mock_conn()
    cur.fetchone.return_value = (date(2026, 1, 1), date(2026, 4, 17))
    with patch.object(mod, "pg_conn", return_value=conn):
        mod.run_all_empty()
    # 先 SELECT MIN/MAX, 然后 run_range 再开一个 conn
    assert cur.fetchone.called
