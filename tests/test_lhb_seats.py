"""akshare_adapter.get_lhb_seats 相关纯函数测试 + parse_seats_dataframe 测试。

不连 akshare / DB / 网络, 仅测单位换算 / 分类 / DataFrame 解析。
"""
from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from backend.data_gateway.adapters.akshare_adapter import AKShareAdapter
from backend.domain.lhb import LhbSeat
from backend.domain.lhb.parser import parse_seats_dataframe


# --------------------------------------------------------------------
# _yuan_to_wan
# --------------------------------------------------------------------


class TestYuanToWan:
    def test_typical(self):
        assert AKShareAdapter._yuan_to_wan(1_000_000) == 100.0
        assert AKShareAdapter._yuan_to_wan(1234567.89) == 123.46

    def test_none_returns_zero(self):
        assert AKShareAdapter._yuan_to_wan(None) == 0.0

    def test_nan_returns_zero(self):
        assert AKShareAdapter._yuan_to_wan(float("nan")) == 0.0

    def test_invalid_string(self):
        assert AKShareAdapter._yuan_to_wan("abc") == 0.0

    def test_negative(self):
        assert AKShareAdapter._yuan_to_wan(-200_000) == -20.0


# --------------------------------------------------------------------
# _pct_to_dec
# --------------------------------------------------------------------


class TestPctToDec:
    def test_percent_to_decimal(self):
        assert AKShareAdapter._pct_to_dec(5.23) == pytest.approx(0.0523)
        assert AKShareAdapter._pct_to_dec(100) == pytest.approx(1.0)

    def test_already_decimal(self):
        """<=1 的值视为已是小数"""
        assert AKShareAdapter._pct_to_dec(0.05) == pytest.approx(0.05)

    def test_none(self):
        assert AKShareAdapter._pct_to_dec(None) is None

    def test_nan(self):
        assert AKShareAdapter._pct_to_dec(float("nan")) is None

    def test_invalid(self):
        assert AKShareAdapter._pct_to_dec("abc") is None


# --------------------------------------------------------------------
# _classify_broker
# --------------------------------------------------------------------


class TestClassifyBroker:
    @pytest.mark.parametrize("name,expect_inst,expect_type", [
        ("机构专用", True, "INSTITUTION"),
        ("深股通专用", True, "INSTITUTION"),
        ("沪股通专用", True, "INSTITUTION"),
        ("中国国际金融股份有限公司上海分公司", True, "INSTITUTION"),
        ("QFII专用", True, "INSTITUTION"),
        ("某某证券某某营业部", False, "HOT_MONEY"),
        ("东方财富证券股份有限公司拉萨金融城南环路证券营业部", False, "HOT_MONEY"),
        ("", False, "UNKNOWN"),
    ])
    def test_variants(self, name, expect_inst, expect_type):
        inst, typ = AKShareAdapter._classify_broker(name)
        assert inst is expect_inst
        assert typ == expect_type


# --------------------------------------------------------------------
# parse_seats_dataframe
# --------------------------------------------------------------------


class TestParseSeatsDataframe:
    def test_empty(self):
        assert parse_seats_dataframe(None) == {}
        assert parse_seats_dataframe(pd.DataFrame()) == {}

    def test_group_by_key(self):
        df = pd.DataFrame([
            {
                "ts_code": "000001.SZ", "trade_date": date(2024, 10, 15),
                "broker_name": "机构专用", "direction": "BUY",
                "buy_amount": 100.0, "sell_amount": 0.0, "net_amount": 100.0,
                "rank": 1, "broker_type": "INSTITUTION", "is_institution": True,
            },
            {
                "ts_code": "000001.SZ", "trade_date": date(2024, 10, 15),
                "broker_name": "游资A", "direction": "BUY",
                "buy_amount": 50.0, "sell_amount": 0.0, "net_amount": 50.0,
                "rank": 2, "broker_type": "HOT_MONEY", "is_institution": False,
            },
            {
                "ts_code": "600519.SH", "trade_date": date(2024, 10, 15),
                "broker_name": "营业部X", "direction": "SELL",
                "buy_amount": 0.0, "sell_amount": 200.0, "net_amount": -200.0,
                "rank": 1, "broker_type": "HOT_MONEY", "is_institution": False,
            },
        ])
        out = parse_seats_dataframe(df)
        assert set(out.keys()) == {
            ("000001.SZ", date(2024, 10, 15)),
            ("600519.SH", date(2024, 10, 15)),
        }
        seats_pa = out[("000001.SZ", date(2024, 10, 15))]
        assert len(seats_pa) == 2
        assert seats_pa[0].broker_name == "机构专用"
        assert seats_pa[0].is_institution is True
        assert seats_pa[0].broker_type == "INSTITUTION"
        assert seats_pa[1].direction == "BUY"
        seat_sell = out[("600519.SH", date(2024, 10, 15))][0]
        assert seat_sell.direction == "SELL"
        assert seat_sell.net_amount == -200.0

    def test_missing_broker_name_skipped(self):
        df = pd.DataFrame([{
            "ts_code": "000001.SZ", "trade_date": date(2024, 10, 15),
            "broker_name": "", "direction": "BUY",
            "buy_amount": 10, "sell_amount": 0, "net_amount": 10,
        }])
        assert parse_seats_dataframe(df) == {}

    def test_null_rank_safe(self):
        df = pd.DataFrame([{
            "ts_code": "000001.SZ", "trade_date": date(2024, 10, 15),
            "broker_name": "游资", "direction": "BUY",
            "buy_amount": 10, "sell_amount": 0, "net_amount": 10,
            "rank": None,
        }])
        out = parse_seats_dataframe(df)
        assert list(out.values())[0][0].rank is None


# --------------------------------------------------------------------
# LhbSeat.to_db_row round trip
# --------------------------------------------------------------------


def test_seat_direction_kept():
    s = LhbSeat("机构专用", "SELL", buy_amount=0, sell_amount=200,
                net_amount=-200, rank=3, is_institution=True,
                broker_type="INSTITUTION")
    d = s.to_db_row("000001.SZ", date(2024, 10, 15), stock_daily_id=99)
    assert d["direction"] == "SELL"
    assert d["is_institution"] is True
    assert d["sell_amount"] == 200
