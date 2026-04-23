"""策略分类常量(STO-75,拆自 STO-48 设计文档 §3.3)。

10 大类里近 6 个月聚焦 3 类,详见 `docs/design/strategy-taxonomy.md`。
设计原则:**代码层常量维护**,不建 DB 字典表(小表 overkill)。

使用:
  - `validate_parent_category(code)` 在 API/服务层做白名单校验
  - `PARENT_CATEGORIES` 直接用于前端 i18n / 下拉选项源
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# 聚焦 3 大类(用户面向)
# ---------------------------------------------------------------------------

PARENT_CATEGORIES: dict[str, str] = {
    "EVENT_DRIVEN": "事件驱动",
    "TREND_FOLLOW": "趋势跟踪",
    "MULTI_FACTOR": "多因子选股",
}

# 暂缓 / 排除的大类(代码层保留常量以便未来启用,但 UI 不显示)
_DEFERRED_CATEGORIES: dict[str, str] = {
    "MEAN_REV": "均值回归",       # 暂缓:分钟级粒度 + 换手成本门槛高
    "ML": "ML 统计学习",           # 暂缓:先让趋势/多因子打底
    "PORTFOLIO": "资产配置",       # 暂缓:用户当前关注 α 而非 β
}
_EXCLUDED_CATEGORIES: dict[str, str] = {
    "ARBITRAGE": "套利",           # 排除:需跨市场/品种
    "MARKET_MAKING": "做市",       # 排除:牌照门槛
    "HFT": "高频微结构",           # 排除:频率门槛
    "VOLATILITY": "期权波动率",    # 排除:需期权链数据
}


def validate_parent_category(code: str | None) -> bool:
    """校验 parent_category 值是否在白名单内。None 视为合法(未归类)。"""
    if code is None:
        return True
    return code in PARENT_CATEGORIES


def display_name(code: str | None) -> str:
    """返回分类中文名,用于前端展示。None / 未知 → '未归类'。"""
    if code is None:
        return "未归类"
    return PARENT_CATEGORIES.get(code) or _DEFERRED_CATEGORIES.get(code) \
        or _EXCLUDED_CATEGORIES.get(code) or code
