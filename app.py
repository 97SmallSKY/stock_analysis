"""
Streamlit 主应用入口
龙虎榜交易策略分析系统

使用 Streamlit 1.30+ 的 st.navigation API 管理多页面导航
"""
import sys
from pathlib import Path

# 将项目根目录加入 sys.path，确保各模块能正确 import
_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import streamlit as st
from config.settings import APP_TITLE, APP_ICON, APP_VERSION

# ─────────────────────────────────────────────────────────────────────────────
# Streamlit 页面全局配置（必须在所有 st.xxx 调用之前设置）
# ─────────────────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title=APP_TITLE,
    page_icon=APP_ICON,
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─────────────────────────────────────────────────────────────────────────────
# 导入各页面模块
# ─────────────────────────────────────────────────────────────────────────────
from pages import (
    dashboard,
    mainboard_strategy,
    lhb_signal,
    lhb_returns,
    sim_trading,
    daily_select,
    market_sentiment,
    backtest,
    backtest_analysis,
    data_manager,
    trade_comparison,
    realtime_monitor,
    strategy_comparison,
)

# ─────────────────────────────────────────────────────────────────────────────
# 定义页面对象（st.Page + st.navigation 模式）
# ─────────────────────────────────────────────────────────────────────────────
# 页面对象存入 session_state 供其他页面跳转使用（避免循环 import）
_dashboard_page = st.Page(dashboard.render, title="总览 Dashboard", icon="🏠", url_path="dashboard", default=True)
_mainboard_page = st.Page(mainboard_strategy.render, title="主板精选策略", icon="🏆", url_path="mainboard")
_realtime_page = st.Page(realtime_monitor.render, title="实时监控", icon="📡", url_path="realtime")
_signal_page = st.Page(lhb_signal.render, title="决策辅助信号", icon="🎯", url_path="signal")
_returns_page = st.Page(lhb_returns.render, title="龙虎榜盈亏分析", icon="📈", url_path="returns")
_sim_trading_page = st.Page(sim_trading.render, title="模拟炒股", icon="🤖", url_path="sim_trading")
_daily_select_page = st.Page(daily_select.render, title="每日选股", icon="🔍", url_path="daily_select")
_sentiment_page = st.Page(market_sentiment.render, title="市场情绪", icon="📊", url_path="sentiment")
_backtest_page = st.Page(backtest.render, title="策略回测", icon="🔬", url_path="backtest")
_backtest_analysis_page = st.Page(backtest_analysis.render, title="回测分析", icon="📉", url_path="backtest_analysis")
_data_manager_page = st.Page(data_manager.render, title="数据管理", icon="⚙️", url_path="data_manager")
_trade_comparison_page = st.Page(trade_comparison.render, title="选股决策复盘", icon="📊", url_path="trade_comparison")
_strategy_comparison_page = st.Page(strategy_comparison.render, title="策略对比看板", icon="🏆", url_path="strategy_comparison")

# 存入 session_state 供页面间跳转
st.session_state["_dashboard_page"] = _dashboard_page
st.session_state["_mainboard_page"] = _mainboard_page
st.session_state["_realtime_page"] = _realtime_page
st.session_state["_signal_page"] = _signal_page
st.session_state["_returns_page"] = _returns_page
st.session_state["_sim_trading_page"] = _sim_trading_page
st.session_state["_daily_select_page"] = _daily_select_page
st.session_state["_sentiment_page"] = _sentiment_page
st.session_state["_backtest_page"] = _backtest_page
st.session_state["_backtest_analysis_page"] = _backtest_analysis_page
st.session_state["_data_manager_page"] = _data_manager_page
st.session_state["_trade_comparison_page"] = _trade_comparison_page
st.session_state["_strategy_comparison_page"] = _strategy_comparison_page

# 页面列表（顺序决定侧边栏显示顺序）
_pages = [
    _dashboard_page,
    _mainboard_page,
    _realtime_page,
    _signal_page,
    _returns_page,
    _sim_trading_page,
    _daily_select_page,
    _sentiment_page,
    _backtest_page,
    _backtest_analysis_page,
    _strategy_comparison_page,
    _data_manager_page,
    _trade_comparison_page,
]

# ─────────────────────────────────────────────────────────────────────────────
# 执行路由（st.navigation 自动生成侧边栏导航）
# ─────────────────────────────────────────────────────────────────────────────
_pg = st.navigation(_pages, position="sidebar")

# ─────────────────────────────────────────────────────────────────────────────
# 全局侧边栏扩展（所有页面共享）
# ─────────────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.divider()
    st.caption(f"v{APP_VERSION}")
    st.caption("数据来源: AKShare / 东方财富")
    st.caption("⚠️ 本系统仅供研究学习，不构成投资建议")

# ─────────────────────────────────────────────────────────────────────────────
# 运行当前选中页面
# ─────────────────────────────────────────────────────────────────────────────
_pg.run()
