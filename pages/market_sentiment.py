"""
市场情绪趋势页面
"""
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
from datetime import datetime, timedelta

from utils.storage import storage
from utils.chart_utils import plotly_dark
from modules.data_fetcher import fetch_market_sentiment, load_market_sentiment_history
import akshare as ak


def render():
    st.title("📈 市场情绪分析")
    
    with st.sidebar:
        st.subheader("⚙️ 参数配置")
        days = st.slider("展示近N天数据", 20, 180, 60)
        refresh_btn = st.button("🔄 刷新最新数据", use_container_width=True)
    
    # 刷新数据
    if refresh_btn:
        today = datetime.now().strftime("%Y%m%d")
        with st.spinner("正在获取今日情绪数据..."):
            sentiment = fetch_market_sentiment(today, save=True)
            if sentiment.get("_from_cache"):
                st.warning("⚠️ AKShare 接口当前繁忙，返回缓存数据，请稍后再试")
            else:
                st.success("数据已更新")
    
    # ============================================================
    # 加载历史情绪数据
    # ============================================================
    df = load_market_sentiment_history()
    
    if df.empty:
        st.warning("暂无市场情绪历史数据，请先在「总览Dashboard」页面刷新数据")
        return
    
    df = df.sort_values("trade_date").tail(days).copy()
    df["trade_date"] = pd.to_datetime(df["trade_date"].astype(str))
    
    # ============================================================
    # 大盘走势 + 涨跌停对比
    # ============================================================
    st.subheader("🏦 上证指数 + 涨跌停家数")
    
    fig = go.Figure()
    
    # 上证指数
    if "index_close" in df.columns:
        fig.add_trace(go.Scatter(
            x=df["trade_date"],
            y=df["index_close"],
            name="上证指数",
            line=dict(color="#f59e0b", width=3),
            yaxis="y2",
        ))
    
    # 涨停
    if "limit_up" in df.columns:
        fig.add_trace(go.Bar(
            x=df["trade_date"],
            y=df["limit_up"],
            name="涨停家数",
            marker_color="rgba(239,68,68,0.7)",
            yaxis="y",
        ))
    
    # 跌停
    if "limit_down" in df.columns:
        fig.add_trace(go.Bar(
            x=df["trade_date"],
            y=df["limit_down"].apply(lambda x: -x),
            name="跌停家数（负值）",
            marker_color="rgba(59,130,246,0.7)",
            yaxis="y",
        ))
    
    fig.update_layout(**plotly_dark(
        title="市场涨跌停家数与大盘走势",
        yaxis=dict(title="涨跌停家数"),
        yaxis2=dict(title="上证指数", overlaying="y", side="right"),
        height=450,
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
    ))
    st.plotly_chart(fig, use_container_width=True)
    
    # ============================================================
    # 涨跌停比率（赚钱效应指数）
    # ============================================================
    st.subheader("💰 赚钱效应指数（涨停/跌停比）")
    
    if "up_down_ratio" in df.columns:
        fig2 = go.Figure()
        
        colors = df["up_down_ratio"].apply(
            lambda x: "#10b981" if x >= 2 else ("#f59e0b" if x >= 1 else "#ef4444")
        )
        
        fig2.add_trace(go.Bar(
            x=df["trade_date"],
            y=df["up_down_ratio"],
            name="涨跌比",
            marker_color=colors,
            text=df["up_down_ratio"].round(2),
            textposition="outside",
        ))
        
        fig2.add_hline(y=2, line_dash="dash", line_color="#10b981",
                       annotation_text="优质赚钱效应(2x)")
        fig2.add_hline(y=1, line_dash="dash", line_color="#f59e0b",
                       annotation_text="均衡线(1x)")
        
        fig2.update_layout(**plotly_dark(height=350))
        st.plotly_chart(fig2, use_container_width=True)
    
    # ============================================================
    # 连板趋势（市场活跃度）
    # ============================================================
    if "continuous_limit" in df.columns:
        st.subheader("🔥 连板股数量趋势（市场活跃度）")
        
        fig3 = go.Figure()
        fig3.add_trace(go.Scatter(
            x=df["trade_date"],
            y=df["continuous_limit"],
            name="连板家数",
            line=dict(color="#f97316", width=2),
            fill="tozeroy",
            fillcolor="rgba(249,115,22,0.15)",
            mode="lines+markers",
        ))
        
        # 7日均线
        df["cont_ma7"] = df["continuous_limit"].rolling(7, min_periods=1).mean()
        fig3.add_trace(go.Scatter(
            x=df["trade_date"],
            y=df["cont_ma7"],
            name="7日均线",
            line=dict(color="#6366f1", width=2, dash="dot"),
        ))
        
        fig3.update_layout(**plotly_dark(height=300, hovermode="x unified"))
        st.plotly_chart(fig3, use_container_width=True)
    
    # ============================================================
    # 市场状态分布（饼图）
    # ============================================================
    st.subheader("🥧 历史市场状态分布")
    
    if "trade_signal" in df.columns:
        signal_counts = df["trade_signal"].value_counts()
        signal_labels = {
            "full_position": "满仓（强势）",
            "normal": "正常操作",
            "half_position": "半仓（弱势）",
            "no_trade": "不操作（差）",
        }
        
        fig4 = px.pie(
            values=signal_counts.values,
            names=[signal_labels.get(s, s) for s in signal_counts.index],
            color_discrete_map={
                "满仓（强势）": "#10b981",
                "正常操作": "#3b82f6",
                "半仓（弱势）": "#f59e0b",
                "不操作（差）": "#ef4444",
            },
            title="历史市场情绪状态分布",
        )
        fig4.update_layout(height=350)
        st.plotly_chart(fig4, use_container_width=True)
    
    # ============================================================
    # 原始数据展示
    # ============================================================
    with st.expander("📋 查看原始数据"):
        st.dataframe(df.sort_values("trade_date", ascending=False), use_container_width=True)
