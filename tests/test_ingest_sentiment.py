"""tests/test_ingest_sentiment.py — W11-3 抬覆盖率

覆盖 backend/ingest/sentiment.py:
  - _calc_from_kline: 空 / 满 / 极端涨跌停数量导致 score clamp
  - _is_empty_sentiment
  - _load_covered_days
  - run(): 无交易日 / 外部源返回空壳 -> DB 反算 / NotImplementedError 退出
"""
from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest

import backend.ingest.sentiment as S


class _Cur:
    def __init__(self, one=None, rows=None):
        self._one = one
        self._rows = rows or []
        self.executed = []
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def execute(self, sql, params=()): self.executed.append((sql, params))
    def fetchone(self): return self._one
    def fetchall(self): return list(self._rows)


class _Conn:
    def __init__(self, cursors):
        self._it = iter(cursors)
        self.rolled_back = 0
        self.committed = 0
    def cursor(self, *a, **k): return next(self._it)
    def rollback(self): self.rolled_back += 1
    def commit(self): self.committed += 1
    def __enter__(self): return self
    def __exit__(self, *a): return False


# --------------------------------------------------------------------
# _calc_from_kline
# --------------------------------------------------------------------


class TestCalcFromKline:
    def test_no_rows_returns_none(self):
        # P18.13: SQL 多了 median_pct_change 列,row 从 8 → 9 元素
        conn = _Conn([_Cur(one=(0, 0, 0, 0, 0, 0, 0, None, None))])
        assert S._calc_from_kline(conn, "20241015") is None

    def test_normal_day(self):
        # total=4000, limit_up=50, limit_down=5, up=2300, down=1500, strong=400, weak=100, avg=0.01, median=0.008
        row = (4000, 50, 5, 2300, 1500, 400, 100, 0.01, 0.008)
        conn = _Conn([_Cur(one=row)])
        out = S._calc_from_kline(conn, "20241015")
        assert out["trade_date"] == "2024-10-15"
        assert out["limit_up_count"] == 50
        assert out["limit_down_count"] == 5
        assert out["up_down_ratio"] == round(2300 / max(1500, 1), 4)
        # P18.13 新增字段断言
        assert out["total_stocks"] == 4000
        assert out["up_count"] == 2300
        assert out["down_count"] == 1500
        assert out["median_pct_change"] == 0.008
        # score = 50 + (ratio-1)*20 + 0.01*600 + (50-5)*0.5; 位于合法范围
        assert 0.0 <= out["sentiment_score"] <= 100.0
        assert out["sentiment_level"] in ("HOT", "WARM", "NEUTRAL", "COOL", "COLD")

    def test_clamp_cold(self):
        # 大幅下跌, 均幅 -0.05, median=-0.04
        row = (4000, 0, 200, 200, 3000, 50, 500, -0.05, -0.04)
        conn = _Conn([_Cur(one=row)])
        out = S._calc_from_kline(conn, "20241015")
        assert out["sentiment_score"] <= 30      # COLD level
        assert out["sentiment_level"] == "COLD"

    def test_clamp_hot(self):
        row = (4000, 200, 0, 3500, 200, 800, 50, 0.04, 0.03)
        conn = _Conn([_Cur(one=row)])
        out = S._calc_from_kline(conn, "20241015")
        assert out["sentiment_score"] >= 70
        assert out["sentiment_level"] == "HOT"


# --------------------------------------------------------------------
# STO-32 新公式(方案 A)验证 — 3 组代表日 + 区分度防倒退
# --------------------------------------------------------------------


class TestNewFormulaFromSTO32:
    """PM 在 STO-32 拍板的方案 A 公式验证(2026-04-20 P20.6)。

    公式: score = 50 + (ratio-1)*10 + avg*300 + (lu-ld)*0.1, clip [0,100]
    阈值: >=70 HOT, >=55 WARM, >=45 NEUTRAL, >=30 COOL, else COLD

    目的:
      1. 3 组代表日用精确 score 数值锁定(公式系数被改一眼可见)
      2. 区分度防倒退测试 - 防止未来改回旧的两极化双峰分布
    """

    def test_hot_limit_up_surge_day(self):
        """涨停潮 2026-04-16: LU=79, ratio~4.05, avg=+1.46%
        旧公式:score=45(被 NEUTRAL 吞没) → 新公式:~92.4 HOT
        """
        # total,lu,ld,up,down,strong,weak,avg,median
        row = (5000, 79, 4, 4053, 1000, 800, 100, 0.0146, 0.012)
        conn = _Conn([_Cur(one=row)])
        out = S._calc_from_kline(conn, "20260416")
        # 50 + (4.053-1)*10 + 0.0146*300 + (79-4)*0.1 = 50 + 30.53 + 4.38 + 7.5 = 92.41
        assert out["sentiment_score"] == pytest.approx(92.41, abs=0.1)
        assert out["sentiment_level"] == "HOT"

    def test_cold_sharp_down_day(self):
        """大跌日 2026-03-23: LU=34, LD=200, ratio~0.06, avg=-5.12%
        旧公式:score=0(clip 到最小) → 新公式:~8.6 COLD
        """
        row = (5000, 34, 200, 200, 3333, 100, 1500, -0.0512, -0.04)
        conn = _Conn([_Cur(one=row)])
        out = S._calc_from_kline(conn, "20260323")
        # 50 + (0.06-1)*10 + (-0.0512)*300 + (34-200)*0.1 = 50 - 9.4 - 15.36 - 16.6 = 8.64
        assert out["sentiment_score"] == pytest.approx(8.64, abs=0.2)
        assert out["sentiment_level"] == "COLD"

    def test_neutral_choppy_day(self):
        """震荡日 2026-04-20: LU=59, ratio=1.00, avg=0.00%
        旧公式:score=45(贴底 NEUTRAL) → 新公式:~54.9 NEUTRAL(已接近 WARM 边界)
        """
        row = (5000, 59, 10, 2500, 2500, 600, 600, 0.0, 0.0)
        conn = _Conn([_Cur(one=row)])
        out = S._calc_from_kline(conn, "20260420")
        # 50 + (1.0-1)*10 + 0.0*300 + (59-10)*0.1 = 50 + 0 + 0 + 4.9 = 54.9
        assert out["sentiment_score"] == pytest.approx(54.9, abs=0.1)
        assert out["sentiment_level"] == "NEUTRAL"

    def test_distribution_stddev_regression_guard(self):
        """区分度防倒退:对 20 组模拟日计算 score,要求 stddev > 10 且 unique >= 10。

        目的:防止未来改回 `(lu-ld)*0.5` 等大系数的两极化公式。
        数据覆盖:涨停潮 / 下跌潮 / 震荡 / 小涨小跌 / 极端 的混合场景。
        """
        import statistics
        samples = [
            (5000, 59, 10, 2500, 2500, 600, 600, 0.0, 0.0),
            (5000, 79, 4,  4053, 1000, 800, 100, 0.0146, 0.012),
            (5000, 57, 20, 1500, 3000, 300, 800, -0.0036, -0.002),
            (5000, 55, 15, 3300, 1417, 500, 400, 0.01, 0.008),
            (5000, 123, 5, 4200, 700, 1000, 50, 0.0387, 0.03),
            (5000, 36, 50, 600, 3750, 200, 1200, -0.0208, -0.018),
            (5000, 138, 3, 4500, 700, 1100, 50, 0.0344, 0.028),
            (5000, 34, 200, 200, 3333, 100, 1500, -0.0512, -0.04),
            (5000, 20, 10, 2300, 2200, 300, 350, 0.002, 0.001),
            (5000, 75, 8, 3500, 1200, 700, 150, 0.015, 0.012),
            (5000, 48, 12, 2800, 1800, 450, 300, 0.006, 0.004),
            (5000, 90, 5, 3800, 900, 900, 120, 0.02, 0.015),
            (5000, 25, 35, 1200, 3200, 200, 800, -0.012, -0.01),
            (5000, 15, 80, 500, 3800, 100, 1400, -0.03, -0.025),
            (5000, 45, 18, 2600, 2000, 400, 350, 0.003, 0.002),
            (5000, 105, 4, 4100, 800, 1000, 80, 0.025, 0.02),
            (5000, 32, 22, 1800, 2700, 250, 600, -0.008, -0.005),
            (5000, 62, 9, 2900, 1900, 550, 300, 0.008, 0.006),
            (5000, 18, 45, 900, 3500, 150, 900, -0.018, -0.015),
            (5000, 52, 14, 2700, 2100, 480, 320, 0.005, 0.003),
        ]
        scores: list[float] = []
        for row in samples:
            conn = _Conn([_Cur(one=row)])
            out = S._calc_from_kline(conn, "20260420")
            scores.append(out["sentiment_score"])

        stddev = statistics.stdev(scores)
        assert stddev > 10, f"stddev={stddev:.2f} 过低,区分度不足,疑似公式倒退"
        assert len(set(scores)) >= 10, (
            f"unique score 只有 {len(set(scores))},分布严重聚集,疑似回到双峰公式"
        )
        assert min(scores) < 30, f"min={min(scores):.2f} 偏高,未覆盖 COOL/COLD"
        assert max(scores) > 70, f"max={max(scores):.2f} 偏低,未覆盖 HOT"


# --------------------------------------------------------------------
# P18.13 _merge_db_fill: 外部源部分字段缺失时用 DB 反算补齐
# --------------------------------------------------------------------


class TestMergeDbFill:
    def test_external_none_filled_by_db(self):
        external = {"trade_date": "2026-04-17", "limit_up_count": 71,
                    "limit_down_count": 3, "up_down_ratio": None,
                    "avg_pct_change": None}
        db = {"up_down_ratio": 1.8, "avg_pct_change": 0.012,
              "total_stocks": 5300, "up_count": 3400, "down_count": 1900,
              "median_pct_change": 0.005, "strong_stock_count": 500,
              "weak_stock_count": 200, "sentiment_score": 62.5,
              "sentiment_level": "WARM"}
        merged = S._merge_db_fill(external, db)
        # 外部源已有的字段不被覆盖
        assert merged["limit_up_count"] == 71
        assert merged["limit_down_count"] == 3
        # 外部源缺失,从 DB 补
        assert merged["up_down_ratio"] == 1.8
        assert merged["avg_pct_change"] == 0.012
        assert merged["total_stocks"] == 5300
        assert merged["up_count"] == 3400
        assert merged["down_count"] == 1900
        assert merged["median_pct_change"] == 0.005

    def test_external_zero_treated_as_missing(self):
        # 0 和 0.0 也视为缺失,会被 DB 反算覆盖
        external = {"up_down_ratio": 0.0, "avg_pct_change": 0,
                    "limit_up_count": 10}
        db = {"up_down_ratio": 1.5, "avg_pct_change": 0.01}
        merged = S._merge_db_fill(external, db)
        assert merged["up_down_ratio"] == 1.5
        assert merged["avg_pct_change"] == 0.01
        assert merged["limit_up_count"] == 10  # 非 0/None 保留

    def test_db_none_returns_external_unchanged(self):
        external = {"limit_up_count": 71, "up_down_ratio": None}
        merged = S._merge_db_fill(external, None)
        assert merged == external

    def test_external_values_not_overridden(self):
        """外部源已有有效值时,不应被 DB 反算覆盖。"""
        external = {"up_down_ratio": 2.5, "avg_pct_change": 0.02}
        db = {"up_down_ratio": 999.0, "avg_pct_change": 999.0}
        merged = S._merge_db_fill(external, db)
        assert merged["up_down_ratio"] == 2.5
        assert merged["avg_pct_change"] == 0.02


# --------------------------------------------------------------------
# _is_empty_sentiment
# --------------------------------------------------------------------


class TestIsEmpty:
    def test_all_zero(self):
        assert S._is_empty_sentiment({
            "limit_up_count": 0, "limit_down_count": 0,
            "up_down_ratio": 0, "avg_pct_change": 0,
        })

    def test_some_non_zero(self):
        assert S._is_empty_sentiment({
            "limit_up_count": 10, "limit_down_count": 0,
            "up_down_ratio": 0, "avg_pct_change": 0,
        }) is False

    def test_none_treated_as_empty(self):
        assert S._is_empty_sentiment({
            "limit_up_count": None, "limit_down_count": None,
            "up_down_ratio": None, "avg_pct_change": None,
        })


# --------------------------------------------------------------------
# _load_covered_days
# --------------------------------------------------------------------


def test_load_covered_days(monkeypatch):
    cur = _Cur(rows=[("20241014",), ("20241015",)])
    conn = _Conn([cur])

    @contextmanager
    def fake_pg():
        yield conn
    monkeypatch.setattr(S, "pg_conn", fake_pg)
    out = S._load_covered_days("20241014", "20241016")
    assert out == {"20241014", "20241015"}


# --------------------------------------------------------------------
# run()
# --------------------------------------------------------------------


class TestRun:
    def test_no_trade_days(self, monkeypatch):
        monkeypatch.setattr(S, "get_manager", lambda: MagicMock())
        monkeypatch.setattr(S, "_load_trading_days", lambda s, e: [])
        n = S.run("20241015", "20241016")
        assert n == 0

    def test_external_empty_fallback_to_kline(self, monkeypatch):
        """外部源抛异常 / 返回空 -> 走 _calc_from_kline."""
        monkeypatch.setattr(S, "_load_trading_days", lambda s, e: ["20241015"])
        monkeypatch.setattr(S, "_load_covered_days", lambda s, e: set())

        mgr = MagicMock()
        mgr.get_market_sentiment.return_value = None  # 导致 df_to_rows 返回 []
        monkeypatch.setattr(S, "get_manager", lambda: mgr)
        monkeypatch.setattr(S, "df_to_rows", lambda df, cm: [])

        # _calc_from_kline 返回一个合法 dict
        monkeypatch.setattr(S, "_calc_from_kline", lambda conn, d: {
            "trade_date": "2024-10-15", "limit_up_count": 30,
            "limit_down_count": 5, "up_down_ratio": 1.5,
            "avg_pct_change": 0.01, "strong_stock_count": 200,
            "weak_stock_count": 50, "sentiment_score": 65.0,
            "sentiment_level": "WARM",
        })
        monkeypatch.setattr(S, "upsert_market_sentiment", lambda conn, rows: len(rows))

        # pg_conn 返回一个空 conn
        fake_conn = MagicMock()
        fake_conn.commit = MagicMock()

        @contextmanager
        def fake_pg():
            yield fake_conn
        monkeypatch.setattr(S, "pg_conn", fake_pg)

        n = S.run("20241015", "20241015")
        assert n == 1

    def test_not_implemented_breaks(self, monkeypatch):
        monkeypatch.setattr(S, "_load_trading_days", lambda s, e: ["20241015", "20241016"])
        monkeypatch.setattr(S, "_load_covered_days", lambda s, e: set())

        mgr = MagicMock()
        mgr.get_market_sentiment.side_effect = NotImplementedError("nope")
        monkeypatch.setattr(S, "get_manager", lambda: mgr)

        fake_conn = MagicMock()

        @contextmanager
        def fake_pg():
            yield fake_conn
        monkeypatch.setattr(S, "pg_conn", fake_pg)

        n = S.run("20241015", "20241016")
        assert n == 0  # 第一天就 break
