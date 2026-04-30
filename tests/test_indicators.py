"""STO-115 技术指标纯函数单元测试."""
from __future__ import annotations

from backend.utils.indicators import calc_dma, calc_macd


def test_calc_dma_length():
    closes = [float(i) for i in range(1, 61)]
    result = calc_dma(closes, n=10, m=50)
    assert len(result['ma10']) == 60
    assert len(result['ma50']) == 60


def test_calc_dma_leading_none():
    closes = [float(i) for i in range(1, 61)]
    result = calc_dma(closes, n=10, m=50)
    # 前 9 个 ma10 为 None
    assert all(v is None for v in result['ma10'][:9])
    assert result['ma10'][9] is not None
    # 前 49 个 ma50 为 None
    assert all(v is None for v in result['ma50'][:49])
    assert result['ma50'][49] is not None


def test_calc_dma_short_sequence():
    closes = [10.0, 11.0, 12.0]
    result = calc_dma(closes, n=10, m=50)
    assert len(result['ma10']) == 3
    assert all(v is None for v in result['ma10'])
    assert all(v is None for v in result['ma50'])


def test_calc_dma_empty():
    result = calc_dma([], n=10, m=50)
    assert result['ma10'] == []
    assert result['ma50'] == []


def test_calc_macd_length():
    closes = [float(i + 10) for i in range(60)]
    result = calc_macd(closes)
    assert len(result['diff']) == 60
    assert len(result['dea']) == 60
    assert len(result['bar']) == 60


def test_calc_macd_leading_none():
    closes = [float(i + 10) for i in range(60)]
    result = calc_macd(closes, fast=12, slow=26, signal=9)
    # diff 前 25 个应为 None (slow-1=25)
    assert all(v is None for v in result['diff'][:25])
    assert result['diff'][25] is not None


def test_calc_macd_short_sequence():
    closes = [10.0, 11.0, 12.0]
    result = calc_macd(closes)
    assert len(result['diff']) == 3
    assert all(v is None for v in result['diff'])
    assert all(v is None for v in result['dea'])
    assert all(v is None for v in result['bar'])


def test_calc_macd_empty():
    result = calc_macd([])
    assert result['diff'] == []
    assert result['dea'] == []
    assert result['bar'] == []
