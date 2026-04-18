"""tests/test_intraday_snapshot.py — W8-1

验证 CrawlerAdapter.get_intraday_snapshot:
  - ts_code <-> secid 互转
  - _scale 处理 ×100 / None / '-' / 无效字符串
  - get_intraday_snapshot() 用 mock session 拉回东财 push2 格式, 正确解析
    最新价/涨跌幅/成交量/成交额/开高低/昨收, 填充 ts_code 与 ts.
  - 空入参返回空 DataFrame, 全部无效 ts_code 返回空 DataFrame.
  - 非法字段 '-' 全部落入 None, 不抛异常.
  - DataCategory.INTRADAY_SNAPSHOT 被 supports() 识别; 基类返回 None.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pandas as pd
import pytest

from backend.data_gateway.adapters.crawler_adapter import CrawlerAdapter
from backend.data_gateway.base_adapter import BaseDataAdapter
from backend.data_gateway.category import AdapterName, DataCategory


# ----------------------------------------------------------------------
# _ts_code_to_secid / _scale
# ----------------------------------------------------------------------


@pytest.mark.parametrize("ts_code,expected", [
    ("600000.SH", "1.600000"),
    ("000001.SZ", "0.000001"),
    ("300750.SZ", "0.300750"),
    ("833454.BJ", "0.833454"),
    ("600000",     None),       # 无 mkt 后缀
    ("",           None),
    (None,         None),
    ("AAPL.US",    None),       # 不支持市场
])
def test_ts_code_to_secid(ts_code, expected):
    assert CrawlerAdapter._ts_code_to_secid(ts_code) == expected


def test_scale_valid_and_invalid():
    assert CrawlerAdapter._scale(1234) == 12.34
    assert CrawlerAdapter._scale(1234, factor=1.0) == 1234.0
    assert CrawlerAdapter._scale(None) is None
    assert CrawlerAdapter._scale("-") is None
    assert CrawlerAdapter._scale("") is None
    assert CrawlerAdapter._scale("abc") is None


# ----------------------------------------------------------------------
# supports() — category 映射
# ----------------------------------------------------------------------


def test_supports_intraday_snapshot():
    ad = CrawlerAdapter()
    assert ad.supports(DataCategory.INTRADAY_SNAPSHOT) is True


def test_base_adapter_returns_none_by_default():
    class _Dummy(BaseDataAdapter):
        def _do_initialize(self): pass
        def health_check(self) -> bool: return True
    d = _Dummy(name=AdapterName.TUSHARE, config={})
    assert d.get_intraday_snapshot(["600000.SH"]) is None
    assert d.supports(DataCategory.INTRADAY_SNAPSHOT) is False


# ----------------------------------------------------------------------
# get_intraday_snapshot — mock session
# ----------------------------------------------------------------------


def _make_adapter_with_fake_session(payload: dict) -> CrawlerAdapter:
    ad = CrawlerAdapter()
    ad._initialized = True  # 绕过 initialize 的网络探针
    ad.circuit_breaker.force_close()

    fake_session = MagicMock()
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json = MagicMock(return_value=payload)
    fake_session.get = MagicMock(return_value=resp)
    ad._session = fake_session
    return ad, fake_session


def test_get_intraday_snapshot_parses_fields():
    # 东财 push2 典型返回结构
    payload = {
        "data": {
            "diff": [
                {
                    "f1": 1, "f12": "600000", "f14": "浦发银行",
                    "f2": 1025,    # 10.25
                    "f3": 152,     # 1.52%
                    "f4": 15,      # 0.15
                    "f5": 123456,  # 成交量
                    "f6": 78910.5, # 成交额
                    "f15": 1030, "f16": 1010, "f17": 1015, "f18": 1010,
                },
                {
                    "f1": 0, "f12": "000001", "f14": "平安银行",
                    "f2": "-",     # 无最新价 (停牌)
                    "f3": None,
                    "f5": "-", "f6": None,
                    "f15": "-", "f16": "-", "f17": "-", "f18": 1200,
                },
            ]
        }
    }
    ad, sess = _make_adapter_with_fake_session(payload)
    df = ad.get_intraday_snapshot(["600000.SH", "000001.SZ"])
    assert isinstance(df, pd.DataFrame)
    required = {
        "ts_code", "ts", "price", "pct_change", "change",
        "volume", "amount", "open", "high", "low", "prev_close",
    }
    assert required <= set(df.columns)
    # 行 1: 600000.SH
    r0 = df[df["ts_code"] == "600000.SH"].iloc[0]
    assert r0["price"] == pytest.approx(10.25)
    assert r0["pct_change"] == pytest.approx(1.52)
    assert r0["volume"] == 123456
    assert r0["amount"] == pytest.approx(78910.5)
    assert r0["open"] == pytest.approx(10.15)
    assert r0["prev_close"] == pytest.approx(10.10)
    # 行 2: 停牌, 主要字段落 NaN
    r1 = df[df["ts_code"] == "000001.SZ"].iloc[0]
    assert pd.isna(r1["price"])
    assert pd.isna(r1["volume"])
    assert r1["prev_close"] == pytest.approx(12.00)

    # 请求了正确的 secids 参数
    _, kwargs = sess.get.call_args
    secids = kwargs["params"]["secids"]
    assert "1.600000" in secids
    assert "0.000001" in secids


def test_get_intraday_snapshot_empty_input():
    ad = CrawlerAdapter()
    ad._initialized = True
    ad.circuit_breaker.force_close()
    df = ad.get_intraday_snapshot([])
    assert isinstance(df, pd.DataFrame) and df.empty


def test_get_intraday_snapshot_all_invalid_codes():
    ad = CrawlerAdapter()
    ad._initialized = True
    ad.circuit_breaker.force_close()
    # 即使传进一堆无法映射 secid 的, 也应返回空 DataFrame, 不调用 session.
    ad._session = MagicMock()
    df = ad.get_intraday_snapshot(["AAPL.US", "BAD", ""])
    assert isinstance(df, pd.DataFrame) and df.empty
    assert ad._session.get.call_count == 0


def test_get_intraday_snapshot_non_json_graceful():
    # session.get 抛出 (模拟 502), _safe_request 会吞掉返回 None.
    ad = CrawlerAdapter()
    ad._initialized = True
    ad.circuit_breaker.force_close()
    fake_session = MagicMock()
    fake_session.get.side_effect = RuntimeError("502 bad gateway")
    ad._session = fake_session
    out = ad.get_intraday_snapshot(["600000.SH"])
    # _safe_request 对可重试错误最终返回 None
    assert out is None
