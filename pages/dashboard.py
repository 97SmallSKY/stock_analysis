"""
总览 Dashboard 页面
"""
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
from datetime import datetime, timedelta

from utils.storage import storage
from modules.data_fetcher import fetch_market_sentiment, load_market_sentiment_history

import time as _time


def _ss_get(key: str, ttl: int):
    val = st.session_state.get(f"__cache_{key}")
    ts  = st.session_state.get(f"__cache_ts_{key}", 0)
    if val is not None and (_time.time() - ts) < ttl:
        return val
    return None


def _ss_set(key: str, val):
    st.session_state[f"__cache_{key}"] = val
    st.session_state[f"__cache_ts_{key}"] = _time.time()


def _nav_to(page_name: str):
    """通过 session_state + query_params 实现页面跳转"""
    st.session_state["_nav_page"] = page_name
    st.query_params["page"] = {
        "🤖 模拟炒股":       "sim_trading",
        "🏆 主板精选策略":   "mainboard",
        "🎯 决策辅助信号":   "signal",
        "📈 龙虎榜盈亏分析": "returns",
    }.get(page_name, "dashboard")
    st.rerun()


def render():
    st.title("🏠 龙虎榜策略 - 总览 Dashboard")
    st.caption(f"最后更新: {datetime.now().strftime('%Y-%m-%d %H:%M')}")

    # ============================================================
    # 今日市场情绪快览
    # ============================================================
    today = datetime.now().strftime("%Y%m%d")

    with st.spinner("正在获取今日市场数据..."):
        try:
            sentiment = fetch_market_sentiment(today, save=True)
        except Exception as e:
            st.error(f"获取市场情绪失败: {e}")
            sentiment = {}

    if sentiment:
        _from_cache = sentiment.pop("_from_cache", False)
        if _from_cache:
            st.warning("⚠️ AKShare 接口当前繁忙，以下数据来自本地缓存。稍后可刷新页面重试。")

        limit_up = sentiment.get("limit_up", 0)
        limit_down = sentiment.get("limit_down", 0)
        cont_limit = sentiment.get("continuous_limit", 0)
        index_close = sentiment.get("index_close", 0)
        index_change = sentiment.get("index_change", 0)
        trend = sentiment.get("index_trend", "neutral")
        signal = sentiment.get("trade_signal", "normal")

        signal_map = {
            "full_position": ("✅ 满仓操作", "#E84040"),
            "normal": ("✅ 正常操作", "#E84040"),
            "half_position": ("⚠️ 半仓操作", "#FFA500"),
            "no_trade": ("🚫 今日不操作", "#888888"),
        }
        sig_label, sig_color = signal_map.get(signal, ("❓ 未知", "#888888"))

        st.markdown(
            f"<div style='padding:20px;background:#1a1a2e;border-left:8px solid {sig_color};border-radius:8px;margin-bottom:20px;'>"
            f"<h2 style='margin-top:0;color:{sig_color};'>🎯 今日操作建议：{sig_label}</h2>"
            f"<div style='display:flex; justify-content:space-between; margin-bottom:10px; font-size:1.1em;'>"
            f"<span>📈 涨停：<b style='color:#E84040'>{limit_up}</b> 家</span>"
            f"<span>📉 跌停：<b style='color:#33CC66'>{limit_down}</b> 家</span>"
            f"<span>🔥 连板：<b>{cont_limit}</b> 家</span>"
            f"<span>📊 上证指数：<b>{index_close} ({index_change}%)</b></span>"
            f"</div>"
            f"<div style='color:#ccc;font-size:0.95em; border-top:1px solid #333; padding-top:10px;'>"
            f"💡 <b>情绪解读：</b>{sentiment.get('signal_reason', '暂无解读')}</div></div>",
            unsafe_allow_html=True
        )

    # ────────────────────────────────────────────────────────────
    # 快捷导航入口
    # ────────────────────────────────────────────────────────────
    st.markdown("#### 🚀 快速跳转")
    nav_c1, nav_c2, nav_c3, nav_c4 = st.columns(4)
    with nav_c1:
        if st.button("🤖 模拟炒股", use_container_width=True, type="primary"):
            _nav_to("🤖 模拟炒股")
    with nav_c2:
        if st.button("🏆 主板精选策略", use_container_width=True):
            _nav_to("🏆 主板精选策略")
    with nav_c3:
        if st.button("🎯 决策辅助信号", use_container_width=True):
            _nav_to("🎯 决策辅助信号")
    with nav_c4:
        if st.button("📈 龙虎榜盈亏分析", use_container_width=True):
            _nav_to("📈 龙虎榜盈亏分析")

    # ── 模拟账户 AHS 健康仪表盘（首页简版）──────────────────────────────
    _cached = _ss_get("dash_health", 180)
    if _cached is None:
        try:
            from skills.stock_sim_robot.sim_trade_db import (
                get_performance_stats, calc_account_health_score, check_circuit_breaker
            )
            _s   = get_performance_stats()
            _ahs = calc_account_health_score(_s)
            _cb  = check_circuit_breaker(stats=_s)
            _cached = (_s, _ahs, _cb)
        except Exception:
            _cached = ({}, 60, {"allow_open": True, "level": "ok", "reason": "", "ahs": 60})
        _ss_set("dash_health", _cached)
    _ds, _dahs, _dcb = _cached

    # 只在有数据时才展示（不足3笔不渲染，避免干扰首页洁净感）
    if _ds.get("total_closed", 0) >= 1:
        st.markdown("#### 🤖 模拟账户健康状态")

        # 熔断告警横幅（首页版，精简）
        if _dcb["level"] == "halt":
            st.error(
                f"🔴 **熔断触发 — Phase A 今日暂停开仓** ｜ {_dcb.get('reason','')}\n\n"
                "前往 [🤖 模拟炒股](?page=sim_trading) 页面可查看详情并手动覆盖。",
                icon="🔴",
            )
        elif _dcb["level"] == "warning":
            st.warning(
                f"⚠️ **账户健康预警** ｜ {_dcb.get('reason','')}\n\n"
                "AHS 偏低，仓位已自动压缩。",
                icon="⚠️",
            )

        # AHS 指标卡片行
        _dahs_icon = "🟢" if _dahs >= 70 else ("🟡" if _dahs >= 50 else ("🟠" if _dahs >= 30 else "🔴"))
        _dc1, _dc2, _dc3, _dc4 = st.columns(4)
        _dc1.metric(
            f"{_dahs_icon} AHS 健康评分",
            f"{_dahs}/100",
            help="账户健康评分：0~100，基于近10笔实战绩效。",
        )
        _dc2.metric(
            "📊 近10笔胜率",
            f"{_ds.get('recent_win_rate', 0):.0f}%",
            delta=f"近{_ds.get('recent_trades', 0)}笔",
            delta_color="off",
        )
        _cl_d = _ds.get("consecutive_losses", 0)
        _dc3.metric(
            "🔴 连续亏损",
            f"{_cl_d} 笔",
            delta="接近熔断" if _cl_d == 2 else None,
            delta_color="inverse",
        )
        _cb_txt = "🔴 熔断中" if _dcb["level"] == "halt" else ("🟡 预警" if _dcb["level"] == "warning" else "🟢 正常")
        _dc4.metric("🛡️ 熔断状态", _cb_txt)

        st.divider()

    st.divider()

    # ============================================================
    # 核心数据 Tabs 收纳区
    # ============================================================
    tab_overview, tab_returns, tab_backtest = st.tabs([
        "📊 市场与龙虎榜概览", "💰 龙虎榜盈亏统计", "🔬 策略回测表现"
    ])

    with tab_overview:
        col_lhb, col_trend = st.columns([1, 1])

        with col_lhb:
            st.markdown("#### 📋 近期龙虎榜数据")
            lhb_df = storage.read("lhb_detail")
            if lhb_df.empty:
                st.warning("暂无龙虎榜数据，请前往「数据管理」拉取")
            else:
                date_col = "trade_date" if "trade_date" in lhb_df.columns else "上榜日期"
                if date_col in lhb_df.columns:
                    lhb_df[date_col] = lhb_df[date_col].astype(str)

                    c1, c2, c3 = st.columns(3)
                    c1.metric("📅 起始", lhb_df[date_col].min())
                    c2.metric("📆 覆盖", f"{lhb_df[date_col].nunique()} 天")
                    c3.metric("📌 涉及", f"{lhb_df['代码'].nunique() if '代码' in lhb_df.columns else 0} 只")

                    recent_dates = sorted(lhb_df[date_col].unique())[-5:]
                    recent_df = lhb_df[lhb_df[date_col].isin(recent_dates)]
                    daily_count = recent_df.groupby(date_col).agg(
                        上榜股票数=("代码", "nunique") if "代码" in recent_df.columns else ("名称", "nunique"),
                    ).reset_index()

                    # 将 8位日期字符串转为 YYYY-MM-DD 供 Plotly 识别为日期
                    daily_count[date_col] = daily_count[date_col].apply(
                        lambda x: f"{x[:4]}-{x[4:6]}-{x[6:]}" if len(str(x)) == 8 else str(x)
                    )

                    fig = px.bar(
                        daily_count, x=date_col, y="上榜股票数",
                        title="最近5个交易日上榜数量", color="上榜股票数",
                        color_continuous_scale="Blues", text="上榜股票数",
                    )
                    fig.update_traces(textposition="outside")
                    fig.update_xaxes(type="category")  # 避免日期轴被压缩为数字
                    fig.update_layout(height=280, showlegend=False, plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)", margin=dict(t=30, b=0, l=0, r=0))
                    st.plotly_chart(fig, use_container_width=True)

        with col_trend:
            st.markdown("#### 📈 历史市场情绪趋势")
            sentiment_df = load_market_sentiment_history()
            if not sentiment_df.empty and "trade_date" in sentiment_df.columns:
                sentiment_df = sentiment_df.sort_values("trade_date").tail(60)

                # 将 8位日期字符串转为 YYYY-MM-DD，避免 x 轴显示为数字/科学计数法
                sentiment_df = sentiment_df.copy()
                sentiment_df["trade_date_str"] = sentiment_df["trade_date"].astype(str).apply(
                    lambda x: f"{x[:4]}-{x[4:6]}-{x[6:]}" if len(x) == 8 else x
                )

                fig_sent = go.Figure()
                if "limit_up" in sentiment_df.columns:
                    fig_sent.add_trace(go.Scatter(
                        x=sentiment_df["trade_date_str"], y=sentiment_df["limit_up"],
                        name="涨停家数", line=dict(color="#E84040", width=2),
                        fill="tozeroy", fillcolor="rgba(232,64,64,0.1)"
                    ))
                if "limit_down" in sentiment_df.columns:
                    fig_sent.add_trace(go.Scatter(
                        x=sentiment_df["trade_date_str"], y=sentiment_df["limit_down"],
                        name="跌停家数", line=dict(color="#33CC66", width=2),
                        fill="tozeroy", fillcolor="rgba(51,204,102,0.1)"
                    ))
                fig_sent.update_layout(
                    title="近60日涨跌停家数趋势",
                    height=350,
                    plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
                    hovermode="x unified",
                    margin=dict(t=30, b=0, l=0, r=0),
                    xaxis=dict(type="category", tickangle=-45),
                )
                st.plotly_chart(fig_sent, use_container_width=True)
            else:
                st.info("暂无市场情绪历史数据")

    with tab_returns:
        ret_df = storage.read("lhb_returns")
        if ret_df.empty:
            st.info("暂无盈亏数据，请前往「📈 龙虎榜盈亏分析」页面计算")
        else:
            ok_df = ret_df[ret_df["data_ok"] == True]
            cols_ret = [c for c in ["ret_1d", "ret_3d", "ret_5d", "ret_10d"] if c in ok_df.columns]
            labels   = {"ret_1d": "T+1", "ret_3d": "T+3", "ret_5d": "T+5", "ret_10d": "T+10"}

            metric_cols = st.columns(len(cols_ret))
            for col, rc in zip(metric_cols, cols_ret):
                s = ok_df[rc].dropna()
                win_rate = (s > 0).mean() * 100 if len(s) > 0 else 0
                avg_ret  = s.mean() if len(s) > 0 else 0
                col.metric(
                    f"{labels[rc]} 胜率", f"{win_rate:.1f}%", delta=f"均 {avg_ret:+.2f}%",
                    delta_color="normal" if avg_ret >= 0 else "inverse", help=f"样本 {len(s)} 条",
                )

            if "ret_3d" in ok_df.columns:
                s3 = ok_df["ret_3d"].dropna()
                fig_mini = go.Figure()
                fig_mini.add_trace(go.Histogram(x=s3, nbinsx=40, marker_color="#E84040", opacity=0.8, showlegend=False))
                fig_mini.add_vline(x=0, line_dash="dash", line_color="white", opacity=0.4)
                fig_mini.update_layout(title="T+3 收益率分布直方图", height=280, margin=dict(t=40, b=5, l=5, r=5), template="plotly_dark", xaxis_title="T+3 收益率 (%)", yaxis_title="次数")
                st.plotly_chart(fig_mini, use_container_width=True)

            st.caption(f"📊 样本：{len(ok_df)} 条 | 日期：{ok_df['trade_date'].min()} ~ {ok_df['trade_date'].max()}")

    with tab_backtest:
        bt_df = storage.read("backtest_results")
        if bt_df.empty:
            st.info("暂无回测数据，请前往「策略回测」页面运行回测")
        else:
            from modules.analyzer import analyze_backtest
            stats = analyze_backtest(bt_df)

            col1, col2, col3, col4, col5 = st.columns(5)
            col1.metric("总交易次数", stats.get("total_trades", 0))
            col2.metric("胜率", f"{stats.get('win_rate', 0):.1%}")
            col3.metric("平均收益", f"{stats.get('avg_return', 0):.2%}")
            col4.metric("盈亏比", f"{stats.get('pl_ratio', 0):.2f}")
            col5.metric("最大连续亏损", f"{stats.get('max_consecutive_loss', 0)} 次")

            curve = stats.get("cumulative_curve", [])
            if curve:
                curve_df = pd.DataFrame(curve)

                # 将 8位日期字符串转为 YYYY-MM-DD，避免 x 轴显示为数字/科学计数法
                x_dates = curve_df["trade_date"].astype(str).apply(
                    lambda x: f"{x[:4]}-{x[4:6]}-{x[6:]}" if len(x) == 8 else x
                )

                fig_curve = go.Figure()
                fig_curve.add_trace(go.Scatter(
                    x=x_dates, y=curve_df["cumulative_return"] * 100,
                    name="累计收益率(%)", line=dict(color="#E84040", width=2),
                    fill="tozeroy", fillcolor="rgba(232,64,64,0.1)",
                ))
                fig_curve.add_hline(y=0, line_dash="dash", line_color="gray")
                fig_curve.update_layout(
                    title="策略累计收益曲线",
                    yaxis_title="累计收益率 (%)",
                    height=320,
                    plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
                    margin=dict(t=40, b=0, l=0, r=0),
                    xaxis=dict(type="category", tickangle=-45),
                )
                st.plotly_chart(fig_curve, use_container_width=True)
