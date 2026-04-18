"""backend.domain.lhb 单元测试 (不依赖 DB)。"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock

import pandas as pd
import pytest

from backend.domain.lhb.parser import (
    LhbRecord,
    LhbSeat,
    classify_reason,
    merge_same_key,
    parse_lhb_dataframe,
)
from backend.domain.lhb.ingestor import (
    _norm_date,
    ensure_stock_info_skel,
)
from backend.domain.lhb.aggregator import _period_start


# --------------------------------------------------------------------
# classify_reason
# --------------------------------------------------------------------


class TestClassifyReason:
    @pytest.mark.parametrize("reason,expected", [
        ("连续三个交易日内,涨幅偏离值累计达到 20%", "CONSECUTIVE"),
        ("日涨幅偏离值达到 7% 的前 5 只证券", "PRICE_DEV"),
        ("日涨跌幅偏离", "PRICE_DEV"),
        ("日换手率达到 30%", "TURNOVER"),
        ("日振幅值达到 15%", "AMPLITUDE"),
        ("其他原因", "OTHER"),
        (None, "OTHER"),
        ("", "OTHER"),
    ])
    def test_variants(self, reason, expected):
        assert classify_reason(reason) == expected

    def test_consecutive_priority_over_price_dev(self):
        """同时含 '连续三' 和 '涨幅偏离' 时应归为 CONSECUTIVE"""
        s = "连续三个交易日内,涨幅偏离值累计达到 30%"
        assert classify_reason(s) == "CONSECUTIVE"


# --------------------------------------------------------------------
# parse_lhb_dataframe
# --------------------------------------------------------------------


class TestParser:
    def test_empty_df(self):
        assert parse_lhb_dataframe(None) == []
        assert parse_lhb_dataframe(pd.DataFrame()) == []

    def test_basic_row(self):
        df = pd.DataFrame([{
            "ts_code": "000001.SZ",
            "trade_date": date(2024, 10, 15),
            "name": "平安银行",
            "close_price": Decimal("12.34"),
            "pct_change": Decimal("0.0523"),
            "l_buy": 100.0, "l_sell": 50.0, "l_net": 50.0,
            "amount": 100000.0,
            "reason": "日涨幅偏离值达到 7%",
            "_source": "akshare",
        }])
        recs = parse_lhb_dataframe(df)
        assert len(recs) == 1
        r = recs[0]
        assert r.ts_code == "000001.SZ"
        assert r.name == "平安银行"
        assert r.close_price == 12.34
        assert r.pct_change == pytest.approx(0.0523)
        assert r.reason_type == "PRICE_DEV"
        assert r.source == "akshare"

    def test_missing_fields_defaults(self):
        df = pd.DataFrame([{"ts_code": "600000.SH", "trade_date": "2024-10-15"}])
        rec = parse_lhb_dataframe(df)[0]
        assert rec.l_buy is None
        assert rec.reason_type == "OTHER"

    def test_decimal_nan_safe(self):
        df = pd.DataFrame([{
            "ts_code": "000001.SZ",
            "trade_date": date(2024, 10, 15),
            "l_net": float("nan"),
        }])
        rec = parse_lhb_dataframe(df)[0]
        assert rec.l_net is None


# --------------------------------------------------------------------
# merge_same_key
# --------------------------------------------------------------------


class TestMergeSameKey:
    def test_merge_amounts_and_reasons(self):
        a = LhbRecord(
            ts_code="000001.SZ", trade_date=date(2024, 10, 15),
            l_buy=100, l_sell=40, l_net=60,
            reason="连续三个交易日", reason_type="CONSECUTIVE",
        )
        b = LhbRecord(
            ts_code="000001.SZ", trade_date=date(2024, 10, 15),
            l_buy=50, l_sell=20, l_net=30,
            reason="日涨幅偏离", reason_type="PRICE_DEV",
        )
        out = merge_same_key([a, b])
        assert len(out) == 1
        m = out[0]
        assert m.l_buy == 150
        assert m.l_sell == 60
        assert m.l_net == 90
        assert "连续三个交易日" in m.reason and "日涨幅偏离" in m.reason

    def test_different_keys_kept_separate(self):
        a = LhbRecord("000001.SZ", date(2024, 10, 15))
        b = LhbRecord("600000.SH", date(2024, 10, 15))
        out = merge_same_key([a, b])
        assert len(out) == 2

    def test_seats_appended(self):
        a = LhbRecord(
            "000001.SZ", date(2024, 10, 15),
            seats=[LhbSeat("游资A", "BUY", buy_amount=100)],
        )
        b = LhbRecord(
            "000001.SZ", date(2024, 10, 15),
            seats=[LhbSeat("游资B", "BUY", buy_amount=50)],
        )
        out = merge_same_key([a, b])
        assert len(out[0].seats) == 2


# --------------------------------------------------------------------
# ingestor._norm_date
# --------------------------------------------------------------------


class TestNormDate:
    def test_yyyymmdd_string(self):
        assert _norm_date("20241015") == date(2024, 10, 15)

    def test_iso_string(self):
        assert _norm_date("2024-10-15") == date(2024, 10, 15)

    def test_date_passthrough(self):
        d = date(2024, 10, 15)
        assert _norm_date(d) is d

    def test_none(self):
        assert _norm_date(None) is None


# --------------------------------------------------------------------
# ensure_stock_info_skel (mock conn)
# --------------------------------------------------------------------


class TestEnsureStockInfoSkel:
    def _make_conn(self, existing_codes: set[str]):
        conn = MagicMock()
        cur = MagicMock()
        cur.__enter__ = lambda self: self
        cur.__exit__ = lambda *a: False
        cur.fetchall.return_value = [(c,) for c in existing_codes]
        conn.cursor.return_value = cur
        return conn, cur

    def test_all_existing_no_insert(self):
        conn, cur = self._make_conn({"000001.SZ"})
        recs = [LhbRecord("000001.SZ", date(2024, 10, 15))]
        n = ensure_stock_info_skel(conn, recs)
        assert n == 0
        # 只应调 SELECT, 不调 INSERT
        exec_calls = [c for c in cur.execute.call_args_list
                      if c and c[0] and "INSERT" in str(c[0][0])]
        assert len(exec_calls) == 0

    def test_missing_codes_inserted(self):
        # 第一次 execute 是 SELECT, 我们用 side_effect 分两次
        conn = MagicMock()
        cur = MagicMock()
        cur.__enter__ = lambda self: self
        cur.__exit__ = lambda *a: False
        cur.fetchall.return_value = []  # 数据库里一条没有
        conn.cursor.return_value = cur

        recs = [
            LhbRecord("123456.SZ", date(2024, 10, 15), name="XX转债"),
            LhbRecord("600000.SH", date(2024, 10, 15), name="浦发银行"),
        ]
        with pytest.MonkeyPatch.context() as mp:
            from backend.domain.lhb import ingestor as ing_mod
            calls = []
            mp.setattr(ing_mod, "execute_values",
                       lambda cur, sql, rows, page_size=100: calls.append((sql, rows)))
            n = ensure_stock_info_skel(conn, recs)
        assert n == 2
        assert len(calls) == 1
        sql, rows = calls[0]
        assert "INSERT INTO stock_info" in sql
        assert len(rows) == 2
        # exchange 映射正确
        codes_exchange = {row[0]: row[3] for row in rows}
        assert codes_exchange["600000.SH"] == "SSE"
        assert codes_exchange["123456.SZ"] == "SZSE"


# --------------------------------------------------------------------
# aggregator._period_start
# --------------------------------------------------------------------


class TestAggregatorWindow:
    def test_7d(self):
        d = date(2024, 10, 15)
        assert _period_start(d, "7D") == date(2024, 10, 8)

    def test_30d(self):
        d = date(2024, 10, 15)
        assert _period_start(d, "30D") == date(2024, 9, 15)

    def test_all_returns_none(self):
        d = date(2024, 10, 15)
        assert _period_start(d, "ALL") is None


# --------------------------------------------------------------------
# LhbRecord / LhbSeat
# --------------------------------------------------------------------


class TestDataclasses:
    def test_lhb_record_to_main_row(self):
        r = LhbRecord("000001.SZ", date(2024, 10, 15), l_net=123.4,
                      reason="日涨幅偏离")
        row = r.to_main_row()
        assert row["ts_code"] == "000001.SZ"
        assert row["l_net"] == 123.4
        assert row["reason"] == "日涨幅偏离"

    def test_seat_to_db_row(self):
        s = LhbSeat("游资A", "BUY", buy_amount=100.0, rank=1,
                    is_institution=False, broker_type="HOT_MONEY")
        d = s.to_db_row("000001.SZ", date(2024, 10, 15), stock_daily_id=42)
        assert d["stock_daily_id"] == 42
        assert d["direction"] == "BUY"
        assert d["broker_type"] == "HOT_MONEY"
        assert d["rank"] == 1
