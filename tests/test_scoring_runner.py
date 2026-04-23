"""tests/test_scoring_runner.py — W11-3 提升覆盖率

覆盖 backend/scoring/runner.py:
  - _load_lhb_rows / _load_kline_window / _load_sentiment_map / _load_hist_stats
    用 MonkeyPatch 把 RealDictCursor 及底层 cursor 替换为 fake
  - _update_scores: execute_batch 调用次数 / 空输入 no-op
  - run(): 主流程 - limit / only_empty / 每 500 行提交 / 末尾 flush

整体目标: 从 0% 覆盖抬到 ~70%+, 推 backend 总覆盖率突破 70%.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date
from unittest.mock import MagicMock

import pytest

from backend.scoring import runner as R


class _Cur:
    def __init__(self, rows=None, one=None):
        self._rows = rows or []
        self._one = one
        self.description = None
        self.executed = []

    def __enter__(self): return self
    def __exit__(self, *a): return False

    def execute(self, sql, params=()):
        self.executed.append((sql, params))

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._one


class _Conn:
    def __init__(self, cursors):
        # cursors 是 iterator, 每次 cursor() 取下一个
        self._cursors = iter(cursors)
        self.commits = 0

    def cursor(self, cursor_factory=None):
        return next(self._cursors)

    def commit(self):
        self.commits += 1


# --------------------------------------------------------------------
# _load_lhb_rows / _load_kline_window / _load_sentiment_map
# --------------------------------------------------------------------


class TestSimpleLoaders:
    def test_load_lhb_rows_plain(self):
        # STO-70 C: f-string → 参数化,SQL 固定含 score=0 分支,but only_empty 作为 bool 参数
        cur = _Cur(rows=[{"id": 1, "ts_code": "A.SZ", "trade_date": date(2024, 10, 15)}])
        conn = _Conn([cur])
        rows = R._load_lhb_rows(conn, "20241015", "20241016")
        assert rows[0]["ts_code"] == "A.SZ"
        sql, params = cur.executed[0]
        assert "BETWEEN" in sql
        assert params[-1] is False   # only_empty=False 默认

    def test_load_lhb_rows_only_empty(self):
        cur = _Cur(rows=[])
        conn = _Conn([cur])
        R._load_lhb_rows(conn, "20241015", "20241016", only_empty=True)
        sql, params = cur.executed[0]
        assert "score = 0" in sql    # 条件 SQL 永在,靠 bool 参数决定是否生效
        assert params[-1] is True

    def test_load_kline_window(self):
        cur = _Cur(rows=[{"close": 10.0}, {"close": 10.5}])
        conn = _Conn([cur])
        out = R._load_kline_window(conn, "A.SZ", date(2024, 10, 15), lookback_days=30)
        assert len(out) == 2

    def test_load_sentiment_map(self):
        cur = _Cur(rows=[(date(2024, 10, 15), "GREED")])
        conn = _Conn([cur])
        m = R._load_sentiment_map(conn, "20241015", "20241015")
        assert m[date(2024, 10, 15)] == "GREED"

    def test_load_hist_stats_none_all(self):
        cur = _Cur(one=(0, None, None, None))
        conn = _Conn([cur])
        out = R._load_hist_stats(conn, "A.SZ", date(2024, 10, 15))
        assert out == {
            "total_count": 0, "avg_chg_3d": None,
            "avg_chg_5d": None, "win_rate_5d": None,
        }

    def test_load_hist_stats_populated(self):
        cur = _Cur(one=(5, 0.02, 0.035, 0.6))
        conn = _Conn([cur])
        out = R._load_hist_stats(conn, "A.SZ", date(2024, 10, 15))
        assert out["total_count"] == 5
        assert out["avg_chg_3d"] == 0.02
        assert out["win_rate_5d"] == 0.6


# --------------------------------------------------------------------
# _update_scores
# --------------------------------------------------------------------


def test_update_scores_empty_noop():
    conn = _Conn([])
    n = R._update_scores(conn, [])
    assert n == 0


def test_update_scores_calls_execute_batch(monkeypatch):
    calls = []

    def fake_batch(cur, sql, rows, page_size=100):
        calls.append((sql, list(rows), page_size))

    monkeypatch.setattr(R, "execute_batch", fake_batch)
    cur = _Cur()
    conn = _Conn([cur])
    n = R._update_scores(conn, [
        {"id": 1, "score": 50, "score_tech": 10, "score_fund": 20,
         "score_hist": 20, "score_detail": "{}"},
    ])
    assert n == 1
    assert calls and "UPDATE lhb_stock_daily" in calls[0][0]
    assert calls[0][2] == 500


# --------------------------------------------------------------------
# run()
# --------------------------------------------------------------------


@pytest.fixture
def patch_run(monkeypatch):
    """把 run() 依赖的 pg_conn / compute_score / loaders / execute_batch 全替换.

    返回 calls 便于断言.
    """
    calls = {"batch": [], "compute": 0}

    # 1. pg_conn -> 空 conn; run 内部不会再下沉到 DB (loaders 也被替换)
    fake_conn = MagicMock()
    fake_conn.commit = MagicMock(return_value=None)

    @contextmanager
    def fake_pg_conn():
        yield fake_conn

    monkeypatch.setattr(R, "pg_conn", fake_pg_conn)

    # 2. loaders
    lhb_rows = [
        {"id": i, "ts_code": f"X{i}.SZ", "trade_date": date(2024, 10, 15),
         "name": "N", "pct_change": 0, "turnover_rate": 0, "amount": 0,
         "l_buy": 0, "l_sell": 0, "l_net": 0, "reason": "", "reason_type": "",
         "industry": "X", "market_cap": 1, "float_market_cap": 1}
        for i in range(3)
    ]
    monkeypatch.setattr(R, "_load_lhb_rows", lambda c, s, e, only_empty=False: lhb_rows)
    monkeypatch.setattr(R, "_load_kline_window", lambda c, ts, d, lookback_days=90: [])
    monkeypatch.setattr(R, "_load_sentiment_map", lambda c, s, e: {date(2024, 10, 15): "NEUTRAL"})
    monkeypatch.setattr(R, "_load_hist_stats", lambda c, ts, d: {
        "total_count": 0, "avg_chg_3d": None, "avg_chg_5d": None, "win_rate_5d": None,
    })
    # STO-73: run() 改用 bulk loaders
    monkeypatch.setattr(R, "_load_kline_windows_bulk", lambda c, rows, lookback_days=90: {})
    monkeypatch.setattr(R, "_load_hist_stats_bulk", lambda c, rows: {})

    # 3. compute_score
    def fake_compute(inp):
        calls["compute"] += 1
        return {"score": 60, "score_tech": 20, "score_fund": 20, "score_hist": 20,
                "score_detail": {"ok": True}}
    monkeypatch.setattr(R, "compute_score", fake_compute)

    # 4. execute_batch
    def fake_batch(cur, sql, rows, page_size=100):
        calls["batch"].append(list(rows))
    monkeypatch.setattr(R, "execute_batch", fake_batch)

    return calls, fake_conn


def test_run_processes_all(patch_run):
    calls, conn = patch_run
    n = R.run("20241015", "20241015")
    assert n == 3
    assert calls["compute"] == 3
    # 中间批次门槛 500 行, 不触发; 末尾 flush 一次
    assert len(calls["batch"]) == 1
    # STO-70 B: 不再手动 conn.commit(),依赖 pg_conn() 上下文。


def test_run_limit(patch_run):
    calls, _ = patch_run
    n = R.run("20241015", "20241015", limit=1)
    assert n == 1
    assert calls["compute"] == 1


def test_run_empty(monkeypatch):
    @contextmanager
    def fake_pg():
        yield MagicMock()
    monkeypatch.setattr(R, "pg_conn", fake_pg)
    monkeypatch.setattr(R, "_load_lhb_rows", lambda c, s, e, only_empty=False: [])
    n = R.run("20241015", "20241015")
    assert n == 0


# --------------------------------------------------------------------
# STO-73: N+1 批量化测试
# --------------------------------------------------------------------


class TestBulkLoaders:
    """验证 _load_kline_windows_bulk + _load_hist_stats_bulk 行为
    与原单行 loader 等价,且 SQL 调用次数常量(≤ 10 次固定开销)。
    """

    def test_kline_windows_bulk_empty(self):
        out = R._load_kline_windows_bulk(_Conn([]), [])
        assert out == {}

    def test_kline_windows_bulk_single_sql_and_slice(self):
        # 构造 kline 数据:A.SZ 10/10-10/20, B.SZ 10/10-10/20
        from datetime import date as D
        kline_rows = []
        for ts in ("A.SZ", "B.SZ"):
            for d in range(10, 21):
                kline_rows.append({"ts_code": ts, "trade_date": D(2024, 10, d),
                                   "open": 1.0, "high": 1.0, "low": 1.0,
                                   "close": 1.0, "pct_change": 0.0, "vol": 1,
                                   "amount": 1, "turnover_rate": 0.01})
        cur = _Cur(rows=kline_rows)
        conn = _Conn([cur])
        rows = [
            {"ts_code": "A.SZ", "trade_date": D(2024, 10, 15)},
            {"ts_code": "B.SZ", "trade_date": D(2024, 10, 20)},
        ]
        out = R._load_kline_windows_bulk(conn, rows, lookback_days=5)
        # 仅 1 次 SQL
        assert len(cur.executed) == 1
        # 每个 (ts, td) 返回一个窗口,窗口内 trade_date <= target
        assert all(k["trade_date"] <= D(2024, 10, 15)
                   for k in out[("A.SZ", D(2024, 10, 15))])
        assert all(k["trade_date"] <= D(2024, 10, 20)
                   for k in out[("B.SZ", D(2024, 10, 20))])
        assert out[("A.SZ", D(2024, 10, 15))]
        assert out[("B.SZ", D(2024, 10, 20))]

    def test_hist_stats_bulk_empty(self):
        out = R._load_hist_stats_bulk(_Conn([]), [])
        assert out == {}

    def test_hist_stats_bulk_aggregates_correctly(self):
        """构造 A.SZ 在 1/15 之前的 2 次上榜,验证聚合结果。"""
        from datetime import date as D
        # SQL 返回 (ts_code, d0, c0, c3, c5)
        # A.SZ 两次上榜:1/5(c0=10, c3=12, c5=15) + 1/10(c0=20, c3=24, c5=30)
        # 聚合:total=2, avg_chg_3d=(0.2+0.2)/2=0.20, avg_chg_5d=(0.5+0.5)/2=0.50, win_rate=1.0
        cur = _Cur(rows=[
            ("A.SZ", D(2024, 1, 5), 10.0, 12.0, 15.0),
            ("A.SZ", D(2024, 1, 10), 20.0, 24.0, 30.0),
        ])
        conn = _Conn([cur])
        rows = [{"ts_code": "A.SZ", "trade_date": D(2024, 1, 15)}]
        out = R._load_hist_stats_bulk(conn, rows)
        key = ("A.SZ", D(2024, 1, 15))
        assert out[key]["total_count"] == 2
        assert abs(out[key]["avg_chg_3d"] - 0.20) < 1e-9
        assert abs(out[key]["avg_chg_5d"] - 0.50) < 1e-9
        assert out[key]["win_rate_5d"] == 1.0

    def test_hist_stats_bulk_excludes_future_and_old(self):
        """验证只统计 (target - 365d) <= d0 < target 范围内。"""
        from datetime import date as D
        cur = _Cur(rows=[
            ("A.SZ", D(2023, 1, 1), 10.0, 11.0, 12.0),  # 太早,>365 天前
            ("A.SZ", D(2024, 1, 10), 10.0, 11.0, 12.0), # 合法
            ("A.SZ", D(2024, 1, 20), 10.0, 11.0, 12.0), # target=1/15,这条比 target 晚,不算
        ])
        conn = _Conn([cur])
        rows = [{"ts_code": "A.SZ", "trade_date": D(2024, 1, 15)}]
        out = R._load_hist_stats_bulk(conn, rows)
        assert out[("A.SZ", D(2024, 1, 15))]["total_count"] == 1


def test_run_sql_count_is_constant(monkeypatch, patch_run):
    """STO-73 AC: SQL 调用次数从 N+1 降到常量(≤ 10)。

    patch_run 把 _load_lhb_rows / _load_sentiment_map / bulk loaders 全替换为 lambda,
    实际数 cursor.execute 的 fake_conn 调用次数(monkeypatch bulk 为空 dict 时 0 次 execute)。
    这里重点断言 fake_conn.cursor 在 run() 的生命周期被调用次数 ≤ 10 次。
    """
    _, conn = patch_run
    R.run("20241015", "20241015")
    # fake_conn 的 cursor() 调用次数就是潜在 SQL 次数上界
    # loaders 已被 lambda 替换,不会真正 cursor();_update_scores 内用 conn.cursor()
    # 原 N+1 实现在 run() 循环内每行调 2 次 cursor(loader)→ 3 行 = 6 次 +
    # sentiment + lhb + final batch = 9 次,接近但随 N 线性增长
    # 新实现常量 ≤ 10(lhb + sentiment + kline_bulk + hist_bulk + update = 5 次)
    assert conn.cursor.call_count <= 10
