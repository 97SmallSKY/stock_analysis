"""
每日选股页面
"""
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
from datetime import datetime

from utils.storage import storage
from modules.data_fetcher import fetch_lhb_detail, fetch_market_sentiment
from modules.analyzer import select_stocks_daily
from config.settings import STRATEGY_CONFIG


def render():
    st.title("📡 每日龙虎榜选股")
    
    # ============================================================
    # 侧边参数配置
    # ============================================================
    with st.sidebar:
        st.subheader("⚙️ 选股参数")
        
        select_date = st.date_input(
            "选择日期",
            value=datetime.now(),
            help="选择龙虎榜上榜日期",
        ).strftime("%Y%m%d")
        
        top_n = st.number_input(
            "最多选股数", min_value=1, max_value=10,
            value=STRATEGY_CONFIG["top_n"],
        )
        
        min_net_buy = st.number_input(
            "净买入额阈值（万元）",
            min_value=1000, max_value=100000,
            value=int(STRATEGY_CONFIG["min_net_buy"] / 10000),
            step=1000,
        ) * 10000
        
        min_tech_score = st.slider(
            "最低技术面评分", 0, 100,
            value=STRATEGY_CONFIG["min_tech_score"],
        )
        
        max_gain = st.slider(
            "最大20日涨幅 (%)", 10, 80,
            value=int(STRATEGY_CONFIG["max_gain_20d"] * 100),
        ) / 100
        
        run_btn = st.button("🚀 运行选股", type="primary", use_container_width=True)
        
        st.divider()
        refresh_data = st.button("🔄 刷新当日数据", use_container_width=True)
    
    # ============================================================
    # 刷新数据
    # ============================================================
    if refresh_data:
        with st.spinner(f"正在拉取 {select_date} 龙虎榜数据..."):
            new_df = fetch_lhb_detail(select_date, select_date, save=True)
            if not new_df.empty:
                st.success(f"数据拉取完成，共 {len(new_df)} 条记录")
            else:
                st.warning("未获取到数据（可能是非交易日或数据源暂时不可用）")
    
    # ============================================================
    # 加载当日市场情绪
    # ============================================================
    st.subheader(f"📊 {select_date} 市场情绪")
    
    try:
        sentiment = fetch_market_sentiment(select_date, save=False)
    except Exception:
        sentiment = {}
    
    if sentiment:
        _from_cache = sentiment.pop("_from_cache", False)
        if _from_cache:
            st.caption("⚠️ 情绪数据来自缓存（接口繁忙）")
        signal = sentiment.get("trade_signal", "normal")
        reason = sentiment.get("signal_reason", "")
        
        cols = st.columns(5)
        cols[0].metric("涨停", sentiment.get("limit_up", "?"))
        cols[1].metric("跌停", sentiment.get("limit_down", "?"))
        cols[2].metric("连板", sentiment.get("continuous_limit", "?"))
        cols[3].metric("上证", sentiment.get("index_close", "?"),
                       delta=f"{sentiment.get('index_change', 0)}%")
        
        if signal == "no_trade":
            cols[4].error("🚫 不宜交易")
            st.error(f"今日建议：{reason}")
        elif signal == "half_position":
            cols[4].warning("⚠️ 半仓")
            st.warning(f"今日建议：{reason}")
        else:
            cols[4].success("✅ 正常")
    
    # ============================================================
    # 运行选股
    # ============================================================
    if run_btn:
        with st.spinner("正在加载龙虎榜数据..."):
            lhb_df = storage.read_recent("lhb_detail", days=90)
            
            date_col = "trade_date" if "trade_date" in (lhb_df.columns if not lhb_df.empty else []) else "上榜日期"
            
            if lhb_df.empty or select_date not in (
                lhb_df[date_col].astype(str).values if not lhb_df.empty and date_col in lhb_df.columns else []
            ):
                st.info("本地无当日数据，正在实时拉取...")
                lhb_df = fetch_lhb_detail(select_date, select_date, save=True)
        
        if lhb_df.empty:
            st.error("无法获取龙虎榜数据，请检查网络或尝试刷新")
        else:
            with st.spinner("正在运行选股算法（计算技术面评分）..."):
                selected = select_stocks_daily(
                    lhb_df, select_date,
                    top_n=int(top_n),
                    min_net_buy=float(min_net_buy),
                    min_tech_score=int(min_tech_score),
                    max_gain_20d=float(max_gain),
                )
            
            st.subheader(f"🎯 {select_date} 选股结果")
            
            if not selected:
                st.warning("今日无符合条件的股票，请调整筛选参数或检查龙虎榜数据")
            else:
                st.success(f"共筛选出 **{len(selected)}** 只股票")
                
                # 结果卡片展示
                for i, stock in enumerate(selected, 1):
                    with st.container():
                        st.markdown(f"### {i}. {stock.get('name', '')}（{stock.get('code', '')}）")
                        
                        col1, col2, col3, col4, col5 = st.columns(5)
                        col1.metric(
                            "净买入额",
                            f"{stock.get('net_buy', 0)/1e8:.2f} 亿",
                        )
                        col2.metric("席位评分", f"{stock.get('seat_score', 0)}")
                        col3.metric("技术评分", f"{stock.get('tech_score', 0)}")
                        col4.metric("20日涨幅", f"{stock.get('gain_20d', 0):.1%}")
                        col5.metric("综合评分", f"{stock.get('final_score', 0):.1f}")
                        
                        detail_cols = st.columns(3)
                        detail_cols[0].write(
                            "🏦 **机构买入**：" + ("✅" if stock.get("has_institution") else "❌")
                        )
                        famous = stock.get("famous_names", [])
                        detail_cols[1].write(
                            f"🎯 **知名游资**：{', '.join(famous) if famous else '无'}"
                        )
                        detail_cols[2].write(
                            f"👥 **游资数量**：{stock.get('famous_count', 0)} 个"
                        )
                        st.divider()
                
                # 保存每日选股结果
                result_df = pd.DataFrame(selected)
                result_df["select_date"] = select_date
                storage.write(result_df, "selected_stocks", mode="append")
                
                # 雷达图：综合对比
                if len(selected) > 0:
                    st.subheader("📊 综合评分对比")
                    _render_radar_chart(selected)
    
    # ============================================================
    # 历史选股记录
    # ============================================================
    st.divider()
    st.subheader("📋 历史选股记录")
    
    hist_df = storage.read_recent("selected_stocks", days=90)
    if not hist_df.empty:
        if "select_date" in hist_df.columns:
            hist_df = hist_df.sort_values("select_date", ascending=False)
        
        # 展示最近20条
        display_cols = [c for c in ["select_date", "name", "code", "net_buy",
                                     "seat_score", "tech_score", "final_score",
                                     "has_institution", "gain_20d"] if c in hist_df.columns]
        st.dataframe(hist_df[display_cols].head(50), use_container_width=True)
    else:
        st.info("暂无历史选股记录")


def _render_radar_chart(selected: list):
    """绘制多只股票综合评分雷达图"""
    if not selected:
        return
    
    categories = ["净买入(归一)", "席位评分", "技术评分", "低位加分", "综合评分"]
    
    fig = go.Figure()
    
    # 归一化净买入
    max_net = max(s.get("net_buy", 0) for s in selected)
    
    for stock in selected:
        net_norm = (stock.get("net_buy", 0) / max_net * 100) if max_net > 0 else 50
        low_score = max(0, 30 - stock.get("gain_20d", 0) * 100)
        
        values = [
            net_norm,
            min(stock.get("seat_score", 0), 100),
            stock.get("tech_score", 0),
            low_score,
            stock.get("final_score", 0),
        ]
        values_closed = values + [values[0]]
        
        fig.add_trace(go.Scatterpolar(
            r=values_closed,
            theta=categories + [categories[0]],
            fill="toself",
            name=f"{stock.get('name', '')}({stock.get('code', '')})",
            opacity=0.7,
        ))
    
    fig.update_layout(
        polar=dict(radialaxis=dict(visible=True, range=[0, 100])),
        showlegend=True,
        height=450,
    )
    st.plotly_chart(fig, use_container_width=True)
