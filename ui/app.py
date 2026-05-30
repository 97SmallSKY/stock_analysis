"""
Streamlit 主应用入口（新版）
龙虎榜交易策略分析系统

使用 Streamlit 1.30+ 的 st.navigation API 管理多页面导航

目录结构（新版）：
- data/       数据层（数据获取、存储）
- engine/     引擎层（回测、实盘）
- strategy/   策略层（选股、评分）
- robot/      机器人模块
- ui/         用户界面
- risk/       风控层
"""
import sys
from pathlib import Path

# 将项目根目录加入 sys.path
_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import streamlit as st
from config.settings import APP_TITLE, APP_ICON, APP_VERSION

# ─────────────────────────────────────────────────────────────────────────────
# Streamlit 页面全局配置
# ─────────────────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title=APP_TITLE,
    page_icon=APP_ICON,
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─────────────────────────────────────────────────────────────────────────────
# 导入各页面模块（兼容新旧目录）
# ─────────────────────────────────────────────────────────────────────────────
try:
    # 尝试从新目录导入
    from ui.pages import (
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
    )
except ImportError:
    # 降级到旧目录
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
    )

# ─────────────────────────────────────────────────────────────────────────────
# 定义页面对象
# ─────────────────────────────────────────────────────────────────────────────
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

# 存入 session_state
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

# 页面列表
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
    _data_manager_page,
    _trade_comparison_page,
]

# 导航
_nav = st.navigation(_pages, position="sidebar")

# 运行
_nav.run()
