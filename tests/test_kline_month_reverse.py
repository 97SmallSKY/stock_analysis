"""月反向分段迭代器的单测 (2026-04-18).

只覆盖纯函数逻辑, 不涉及 DB (DB 相关走 integration).
"""
from __future__ import annotations

from backend.ingest.kline import _iter_month_segments_reverse


def test_single_month():
    segs = _iter_month_segments_reverse("20260401", "20260418")
    assert segs == [("20260401", "20260418")]


def test_cross_month():
    # 2026-03-15 → 2026-04-18 → 两段
    segs = _iter_month_segments_reverse("20260315", "20260418")
    assert segs == [
        ("20260401", "20260418"),  # 最近月(完整到 end)
        ("20260315", "20260331"),  # 起始月(完整到 start)
    ]


def test_full_range_reverse():
    # 3 年跨度, 首段必须是最近月
    segs = _iter_month_segments_reverse("20230101", "20260418")
    assert segs[0] == ("20260401", "20260418")
    assert segs[-1] == ("20230101", "20230131")
    # 2023-01 到 2026-04 共 40 个月
    assert len(segs) == 40
    # 确保反向
    for i in range(len(segs) - 1):
        assert segs[i][0] > segs[i + 1][1]


def test_full_month_segments_start_end_align():
    # start/end 刚好月底月初
    segs = _iter_month_segments_reverse("20260201", "20260331")
    assert segs == [
        ("20260301", "20260331"),
        ("20260201", "20260228"),
    ]


def test_empty_when_start_gt_end():
    assert _iter_month_segments_reverse("20260501", "20260401") == []


def test_same_day():
    # start==end
    segs = _iter_month_segments_reverse("20260418", "20260418")
    assert segs == [("20260418", "20260418")]
