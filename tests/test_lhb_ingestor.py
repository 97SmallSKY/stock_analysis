"""backend.domain.lhb.ingestor 单元测试 (STO-8).

目标: 把 ingestor.py 覆盖率从 51% 抬到 ≥ 80%.

覆盖:
  - _norm_date: YYYYMMDD / date / datetime iso / 非法 passthrough
  - ensure_stock_info_skel: 全新/混合/空输入
  - upsert_main: 空输入 / 单行 / 重复 ts_code
  - upsert_seats: 无 seats 短路 / id_map 缺 key 跳过 / 正常写入
  - ingest_records: 空输入 / 正常组合

不依赖真实 PostgreSQL: 用 MagicMock/ _FakeCursor 模式 (与 test_aggregator 一致).
"""
from __future__ import annotations

from datetime import date, datetime
from unittest.mock import MagicMock, patch

import pytest

from backend.domain.lhb import ingestor as I
from backend.domain.lhb.parser import LhbRecord, LhbSeat


# --------------------------------------------------------------------
# Fake conn / cursor
# --------------------------------------------------------------------


class _FakeCursor:
    """记录 execute 调用;fetchall 返回预置 rows."""

    def __init__(self, rows=None):
        self._rows = rows or []
        self.calls: list[tuple[str, list]] = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.calls.append((sql, list(params) if params else []))

    def fetchall(self):
        return list(self._rows)


class _FakeConn:
    def __init__(self, cursor):
        self._cur = cursor

    def cursor(self):
        return self._cur


# --------------------------------------------------------------------
# _norm_date
# --------------------------------------------------------------------


class TestNormDate:
    def test_none(self):
        assert I._norm_date(None) is None

    def test_date_passthrough(self):
        d = date(2024, 10, 15)
        assert I._norm_date(d) is d

    def test_yyyymmdd_str(self):
        assert I._norm_date("20241015") == date(2024, 10, 15)

    def test_iso_str(self):
        assert I._norm_date("2024-10-15") == date(2024, 10, 15)

    def test_datetime_iso_str(self):
        # isoformat 带时间也能切到 date
        assert I._norm_date("2024-10-15T09:30:00") == date(2024, 10, 15)

    def test_invalid_returns_input(self):
        # 非法字符串 passthrough(留给 DB cast)
        assert I._norm_date("not-a-date") == "not-a-date"


# --------------------------------------------------------------------
# ensure_stock_info_skel
# --------------------------------------------------------------------


class TestEnsureStockInfoSkel:
    def test_empty_records(self):
        cur = _FakeCursor()
        conn = _FakeConn(cur)
        assert I.ensure_stock_info_skel(conn, []) == 0
        # 不应该 touch cursor
        assert cur.calls == []

    def test_empty_ts_codes_filtered(self):
        # ts_code 为空字符串/None 不算入
        recs = [LhbRecord(ts_code="", trade_date="20241015")]
        cur = _FakeCursor()
        conn = _FakeConn(cur)
        assert I.ensure_stock_info_skel(conn, recs) == 0

    def test_all_existing_short_circuit(self):
        recs = [LhbRecord(ts_code="000001.SZ", trade_date="20241015", name="平安银行")]
        cur = _FakeCursor(rows=[("000001.SZ",)])
        conn = _FakeConn(cur)
        assert I.ensure_stock_info_skel(conn, recs) == 0
        # 只查了一次, 没再 INSERT
        assert len(cur.calls) == 1

    def test_insert_missing_with_exchange_split(self):
        recs = [
            LhbRecord(ts_code="000001.SZ", trade_date="20241015", name="平安银行"),
            LhbRecord(ts_code="600000.SH", trade_date="20241015", name=None),
            LhbRecord(ts_code="430489.BJ", trade_date="20241015", name="XYZ"),
        ]
        cur = _FakeCursor(rows=[])  # 全部缺失
        conn = _FakeConn(cur)

        with patch.object(I, "execute_values") as mock_ev:
            n = I.ensure_stock_info_skel(conn, recs)

        assert n == 3
        args, kwargs = mock_ev.call_args
        # 第 3 参是 rows, 校验 exchange 映射
        skel_rows = args[2]
        code_to_row = {r[0]: r for r in skel_rows}
        assert code_to_row["000001.SZ"][3] == "SZSE"
        assert code_to_row["600000.SH"][3] == "SSE"
        assert code_to_row["430489.BJ"][3] == "BSE"
        # is_active=False (第 5 列)
        assert all(r[4] is False for r in skel_rows)

    def test_name_truncated_to_32(self):
        # name 超过 32 被截断
        long_name = "X" * 50
        recs = [LhbRecord(ts_code="000001.SZ", trade_date="20241015", name=long_name)]
        cur = _FakeCursor(rows=[])
        conn = _FakeConn(cur)
        with patch.object(I, "execute_values") as mock_ev:
            I.ensure_stock_info_skel(conn, recs)
        rows = mock_ev.call_args[0][2]
        # name 在 index 2
        assert len(rows[0][2]) == 32

    def test_no_dot_in_code_fallback(self):
        # 没有点的 code, symbol=code, exchange=None
        recs = [LhbRecord(ts_code="PURE", trade_date="20241015", name="N")]
        cur = _FakeCursor(rows=[])
        conn = _FakeConn(cur)
        with patch.object(I, "execute_values") as mock_ev:
            I.ensure_stock_info_skel(conn, recs)
        row = mock_ev.call_args[0][2][0]
        assert row[0] == "PURE"
        assert row[1] == "PURE"  # symbol fallback
        assert row[3] is None  # exchange 未知


# --------------------------------------------------------------------
# upsert_main
# --------------------------------------------------------------------


class TestUpsertMain:
    def test_empty_records(self):
        cur = _FakeCursor()
        conn = _FakeConn(cur)
        assert I.upsert_main(conn, []) == {}
        assert cur.calls == []

    def test_single_row(self):
        rec = LhbRecord(
            ts_code="000001.SZ", trade_date="20241015", name="平安银行",
            close_price=12.3, l_buy=1000.0, l_net=500.0, reason="涨幅偏离",
            reason_type="PRICE_DEV",
        )
        cur = _FakeCursor()
        conn = _FakeConn(cur)
        with patch.object(I, "execute_values") as mock_ev:
            mock_ev.return_value = [(42, "000001.SZ", date(2024, 10, 15))]
            id_map = I.upsert_main(conn, [rec])
        assert id_map == {("000001.SZ", date(2024, 10, 15)): 42}
        sql = mock_ev.call_args[0][1]
        assert "ON CONFLICT (ts_code, trade_date) DO UPDATE" in sql
        assert "RETURNING id, ts_code, trade_date" in sql

    def test_duplicate_ts_code_different_dates(self):
        """同一 ts_code 两天 — 边界: duplicate ts_code."""
        recs = [
            LhbRecord(ts_code="000001.SZ", trade_date="20241015", name="平安"),
            LhbRecord(ts_code="000001.SZ", trade_date="20241016", name="平安"),
        ]
        cur = _FakeCursor()
        conn = _FakeConn(cur)
        with patch.object(I, "execute_values") as mock_ev:
            mock_ev.return_value = [
                (1, "000001.SZ", date(2024, 10, 15)),
                (2, "000001.SZ", date(2024, 10, 16)),
            ]
            id_map = I.upsert_main(conn, recs)
        assert len(id_map) == 2
        assert id_map[("000001.SZ", date(2024, 10, 15))] == 1
        assert id_map[("000001.SZ", date(2024, 10, 16))] == 2

    def test_trade_date_normalization(self):
        """trade_date 串应该被 _norm_date 处理."""
        rec = LhbRecord(ts_code="000001.SZ", trade_date="20241015", name="N")
        cur = _FakeCursor()
        conn = _FakeConn(cur)
        with patch.object(I, "execute_values") as mock_ev:
            mock_ev.return_value = [(1, "000001.SZ", date(2024, 10, 15))]
            I.upsert_main(conn, [rec])
        rows = mock_ev.call_args[0][2]
        # trade_date 位置是 index 1 in _MAIN_COLS
        assert rows[0][1] == date(2024, 10, 15)


# --------------------------------------------------------------------
# upsert_seats
# --------------------------------------------------------------------


class TestUpsertSeats:
    def test_no_seats_returns_zero(self):
        rec = LhbRecord(ts_code="000001.SZ", trade_date=date(2024, 10, 15), seats=[])
        cur = _FakeCursor()
        conn = _FakeConn(cur)
        n = I.upsert_seats(conn, [rec], {("000001.SZ", date(2024, 10, 15)): 1})
        assert n == 0
        assert cur.calls == []

    def test_missing_id_map_skipped(self):
        """id_map 没有对应 (ts_code, date) 的 record seats 被跳过."""
        seat = LhbSeat(broker_name="BrokerA", direction="BUY", buy_amount=100.0)
        rec = LhbRecord(
            ts_code="000001.SZ", trade_date="20241015",
            seats=[seat],
        )
        cur = _FakeCursor()
        conn = _FakeConn(cur)
        # id_map 是另一只股
        n = I.upsert_seats(conn, [rec], {("999999.SH", date(2024, 10, 15)): 1})
        assert n == 0

    def test_normal_insert(self):
        seats = [
            LhbSeat(broker_name="A", direction="BUY", buy_amount=100.0),
            LhbSeat(broker_name="B", direction="SELL", sell_amount=50.0),
        ]
        rec = LhbRecord(ts_code="000001.SZ", trade_date="20241015", seats=seats)
        cur = _FakeCursor()
        conn = _FakeConn(cur)
        with patch.object(I, "execute_values") as mock_ev:
            n = I.upsert_seats(
                conn, [rec],
                {("000001.SZ", date(2024, 10, 15)): 777},
            )
        assert n == 2
        sql = mock_ev.call_args[0][1]
        assert "lhb_seats" in sql
        assert "ON CONFLICT (ts_code, trade_date, broker_name, direction)" in sql
        rows = mock_ev.call_args[0][2]
        # stock_daily_id 列应为 777
        assert rows[0][0] == 777
        assert rows[1][0] == 777


# --------------------------------------------------------------------
# ingest_records (组合)
# --------------------------------------------------------------------


class TestIngestRecords:
    def test_empty_short_circuit(self):
        conn = MagicMock()
        out = I.ingest_records(conn, [])
        assert out == {"main_rows": 0, "seat_rows": 0, "skel_rows": 0}
        # conn 没有被 touch
        conn.cursor.assert_not_called()

    def test_happy_path_orchestration(self):
        """组合调用: ensure_skel + upsert_main + upsert_seats 各被调一次."""
        seats = [LhbSeat(broker_name="A", direction="BUY", buy_amount=1.0)]
        recs = [
            LhbRecord(ts_code="000001.SZ", trade_date="20241015", seats=seats),
        ]
        conn = MagicMock()
        with patch.object(I, "ensure_stock_info_skel", return_value=1) as m_skel, \
             patch.object(I, "upsert_main", return_value={("000001.SZ", date(2024, 10, 15)): 9}) as m_main, \
             patch.object(I, "upsert_seats", return_value=1) as m_seats:
            out = I.ingest_records(conn, recs)
        assert out == {"main_rows": 1, "seat_rows": 1, "skel_rows": 1}
        m_skel.assert_called_once()
        m_main.assert_called_once()
        m_seats.assert_called_once()
