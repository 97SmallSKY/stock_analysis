"""
Streamlit 主应用入口
龙虎榜交易策略分析系统
"""
import streamlit as st
from config.settings import APP_TITLE, APP_ICON, APP_VERSION

st.set_page_config(
    page_title=APP_TITLE,
    page_icon=APP_ICON,
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── URL 参数路由映射 ──────────────────────────────────────────────────────────
# 支持通过 ?page=xxx 固定跳转到指定页面
_PAGE_SLUG_MAP = {
    "dashboard":         "🏠 总览 Dashboard",
    "mainboard":         "🏆 主板精选策略",
    "signal":            "🎯 决策辅助信号",
    "returns":           "📈 龙虎榜盈亏分析",
    "sim_trading":       "🤖 模拟炒股",
    "daily_select":      "📡 每日选股",
    "market_sentiment":  "📊 市场情绪",
    "backtest":          "🔬 策略回测",
    "backtest_analysis": "📉 回测分析",
    "data_manager":      "⚙️ 数据管理",
}

_PAGE_LIST = [
    "🏠 总览 Dashboard",
    "🏆 主板精选策略",
    "🎯 决策辅助信号",
    "📈 龙虎榜盈亏分析",
    "🤖 模拟炒股",
    "📡 每日选股",
    "📊 市场情绪",
    "🔬 策略回测",
    "📉 回测分析",
    "⚙️ 数据管理",
]

# 从 URL 参数或 session_state 决定默认落地页
_url_slug    = st.query_params.get("page", "")
_default_page = _PAGE_SLUG_MAP.get(_url_slug, "🏠 总览 Dashboard")

# 允许其他页面通过 st.session_state["_nav_page"] 设置跳转目标，然后 st.rerun()
if "_nav_page" in st.session_state:
    _default_page = st.session_state.pop("_nav_page")

# ============================================================
# 侧边栏导航
# ============================================================
with st.sidebar:
    st.title(f"{APP_ICON} {APP_TITLE}")
    st.caption(f"v{APP_VERSION}")
    st.divider()

    _default_idx = _PAGE_LIST.index(_default_page) if _default_page in _PAGE_LIST else 0
    page = st.radio(
        "📋 功能导航",
        options=_PAGE_LIST,
        index=_default_idx,
        label_visibility="collapsed",
    )

    # 同步当前页面到 URL 参数，实现 URL 固定路由
    _slug_reverse = {v: k for k, v in _PAGE_SLUG_MAP.items()}
    _cur_slug = _slug_reverse.get(page, "dashboard")
    st.query_params["page"] = _cur_slug

    st.divider()
    st.caption("数据来源: AKShare / 东方财富")
    st.caption("⚠️ 本系统仅供研究学习，不构成投资建议")

# ============================================================
# 页面路由
# ============================================================
if page == "🤖 模拟炒股":
    from pages.sim_trading import render
    render()
elif page == "📈 龙虎榜盈亏分析":
    from pages.lhb_returns import render
    render()
elif page == "🎯 决策辅助信号":
    from pages.lhb_signal import render
    render()
elif page == "🏆 主板精选策略":
    from pages.mainboard_strategy import render
    render()
elif page == "🏠 总览 Dashboard":
    from pages.dashboard import render
    render()
elif page == "📡 每日选股":
    from pages.daily_select import render
    render()
elif page == "📊 市场情绪":
    from pages.market_sentiment import render
    render()
elif page == "🔬 策略回测":
    from pages.backtest import render
    render()
elif page == "📉 回测分析":
    from pages.backtest_analysis import render
    render()
elif page == "⚙️ 数据管理":
    from pages.data_manager import render
    render()
