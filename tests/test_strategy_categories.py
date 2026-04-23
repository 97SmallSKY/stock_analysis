"""STO-75 单测:strategy.categories 常量 + 校验函数。"""
from __future__ import annotations

import pytest

from backend.domain.strategy.categories import (
    PARENT_CATEGORIES,
    display_name,
    validate_parent_category,
)


class TestParentCategories:
    def test_focus_three_categories(self):
        # 近 6 个月聚焦 3 类
        assert set(PARENT_CATEGORIES.keys()) == {
            "EVENT_DRIVEN", "TREND_FOLLOW", "MULTI_FACTOR",
        }

    def test_chinese_names_present(self):
        assert PARENT_CATEGORIES["EVENT_DRIVEN"] == "事件驱动"
        assert PARENT_CATEGORIES["TREND_FOLLOW"] == "趋势跟踪"
        assert PARENT_CATEGORIES["MULTI_FACTOR"] == "多因子选股"


class TestValidateParentCategory:
    def test_valid_codes(self):
        for code in ("EVENT_DRIVEN", "TREND_FOLLOW", "MULTI_FACTOR"):
            assert validate_parent_category(code) is True

    def test_none_is_valid(self):
        # 未归类是合法状态(对应 DB DEFAULT NULL)
        assert validate_parent_category(None) is True

    def test_deferred_codes_rejected(self):
        # 暂缓类不在用户面向白名单,校验时拒绝
        for code in ("MEAN_REV", "ML", "PORTFOLIO"):
            assert validate_parent_category(code) is False

    def test_excluded_codes_rejected(self):
        for code in ("ARBITRAGE", "MARKET_MAKING", "HFT", "VOLATILITY"):
            assert validate_parent_category(code) is False

    def test_unknown_code_rejected(self):
        assert validate_parent_category("GARBAGE") is False


class TestDisplayName:
    def test_focus_display(self):
        assert display_name("EVENT_DRIVEN") == "事件驱动"

    def test_deferred_display(self):
        # 暂缓类虽不在白名单,但展示层仍给中文名(数据兼容)
        assert display_name("MEAN_REV") == "均值回归"

    def test_excluded_display(self):
        assert display_name("HFT") == "高频微结构"

    def test_none_display(self):
        assert display_name(None) == "未归类"

    def test_unknown_fallback_to_code(self):
        # 未知 code 原样返回,前端可自行兜底
        assert display_name("GARBAGE") == "GARBAGE"
