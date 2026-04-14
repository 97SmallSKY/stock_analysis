"""
回测结果深度分析页面
"""
import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots

from utils.storage import storage
from modules.analyzer import analyze_backtest


def render():
    st.title("📊 回测结果深度分析")
    
    # 加载回测数据
    df = storage.read("backtest_results")
    
    if df.empty:
        st.warning("⚠️ 暂无回测数据，请先在「🔬 策略回测」页面运行回测")
        return
    
    # 数据预处理
    if "trade_date" in df.columns:
        df["trade_date"] = pd.to_datetime(df["trade_date"].astype(str))
    if "return_rate" in df.columns:
        df["return_rate"] = df["return_rate"].astype(float)
    
    stats = analyze_backtest(df)
    
    # ============================================================
    # 核心指标 KPI 卡片
    # ============================================================
    st.subheader("🎯 核心指标")
    
    cols = st.columns(6)
    metrics = [
        ("总交易次数", f"{stats.get('total_trades', 0)}", None),
        ("胜率", f"{stats.get('win_rate', 0):.1%}", None),
        ("平均收益", f"{stats.get('avg_return', 0):.2%}",
         "↑" if stats.get("avg_return", 0) > 0 else "↓"),
        ("中位数收益", f"{stats.get('median_return', 0):.2%}", None),
        ("盈亏比", f"{stats.get('pl_ratio', 0):.2f}", None),
        ("夏普比率(日)", f"{stats.get('sharpe', 0):.3f}", None),
    ]
    for col, (label, val, delta) in zip(cols, metrics):
        col.metric(label, val, delta)
    
    cols2 = st.columns(6)
    metrics2 = [
        ("盈利次数", f"{stats.get('win_count', 0)}"),
        ("亏损次数", f"{stats.get('loss_count', 0)}"),
        ("平均盈利", f"{stats.get('avg_win', 0):.2%}"),
        ("平均亏损", f"{stats.get('avg_loss', 0):.2%}"),
        ("单笔最大盈利", f"{stats.get('max_return', 0):.2%}"),
        ("最大连续亏损", f"{stats.get('max_consecutive_loss', 0)} 次"),
    ]
    for col, (label, val) in zip(cols2, metrics2):
        col.metric(label, val)
    
    st.divider()
    
    # ============================================================
    # 累计收益曲线
    # ============================================================
    st.subheader("📈 累计收益曲线")
    
    curve = stats.get("cumulative_curve", [])
    if curve:
        curve_df = pd.DataFrame(curve)
        curve_df["trade_date"] = pd.to_datetime(curve_df["trade_date"])
        
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=curve_df["trade_date"],
            y=curve_df["cumulative_return"] * 100,
            mode="lines",
            name="策略累计收益率(%)",
            line=dict(color="#10b981", width=2.5),
            fill="tozeroy",
            fillcolor="rgba(16,185,129,0.1)",
        ))
        fig.add_hline(y=0, line_dash="dash", line_color="#9ca3af")
        fig.update_layout(
            height=380,
            yaxis_title="累计收益率 (%)",
            xaxis_title="日期",
            hovermode="x unified",
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
        )
        st.plotly_chart(fig, use_container_width=True)
    
    # ============================================================
    # 每笔收益分布直方图
    # ============================================================
    st.subheader("📊 收益率分布")
    
    col1, col2 = st.columns(2)
    
    with col1:
        fig_hist = px.histogram(
            df,
            x="return_rate",
            nbins=50,
            title="收益率分布直方图",
            labels={"return_rate": "收益率"},
            color_discrete_sequence=["#6366f1"],
        )
        fig_hist.add_vline(
            x=df["return_rate"].mean(),
            line_dash="dash", line_color="#ef4444",
            annotation_text=f"均值 {df['return_rate'].mean():.2%}",
        )
        fig_hist.add_vline(
            x=0, line_dash="solid", line_color="#9ca3af",
            annotation_text="0%",
        )
        fig_hist.update_xaxes(tickformat=".1%")
        fig_hist.update_layout(
            height=350,
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
        )
        st.plotly_chart(fig_hist, use_container_width=True)
    
    with col2:
        # 盈亏比可视化
        win_df = df[df["return_rate"] > 0]["return_rate"]
        loss_df = df[df["return_rate"] <= 0]["return_rate"]
        
        fig_pl = go.Figure()
        fig_pl.add_trace(go.Box(
            y=win_df * 100, name="盈利交易",
            marker_color="#10b981",
            boxpoints="outliers",
        ))
        fig_pl.add_trace(go.Box(
            y=loss_df * 100, name="亏损交易",
            marker_color="#ef4444",
            boxpoints="outliers",
        ))
        fig_pl.add_hline(y=0, line_dash="dash", line_color="#9ca3af")
        fig_pl.update_layout(
            title="盈亏交易箱线图",
            yaxis_title="收益率 (%)",
            height=350,
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
        )
        st.plotly_chart(fig_pl, use_container_width=True)
    
    # ============================================================
    # 月度收益分析
    # ============================================================
    st.subheader("🗓️ 月度收益分析")
    
    monthly = stats.get("monthly_stats", [])
    if monthly:
        monthly_df = pd.DataFrame(monthly)
        
        fig_month = make_subplots(
            rows=2, cols=1,
            subplot_titles=("月度平均收益率", "月度胜率"),
            shared_xaxes=True,
            vertical_spacing=0.12,
        )
        
        colors = monthly_df["avg_return"].apply(
            lambda x: "#10b981" if x >= 0 else "#ef4444"
        )
        
        fig_month.add_trace(
            go.Bar(
                x=monthly_df["month"],
                y=monthly_df["avg_return"] * 100,
                name="月度平均收益(%)",
                marker_color=colors,
                text=monthly_df["avg_return"].apply(lambda x: f"{x:.2%}"),
                textposition="outside",
            ),
            row=1, col=1,
        )
        
        fig_month.add_trace(
            go.Bar(
                x=monthly_df["month"],
                y=monthly_df["win_rate"] * 100,
                name="月度胜率(%)",
                marker_color="#6366f1",
                text=monthly_df["win_rate"].apply(lambda x: f"{x:.1%}"),
                textposition="outside",
            ),
            row=2, col=1,
        )
        
        fig_month.add_hline(y=0, line_dash="dash", line_color="#9ca3af", row=1, col=1)
        fig_month.add_hline(y=50, line_dash="dash", line_color="#9ca3af", row=2, col=1)
        
        fig_month.update_layout(
            height=500,
            showlegend=False,
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
        )
        st.plotly_chart(fig_month, use_container_width=True)
        
        # 月度数据表
        with st.expander("📋 月度详细数据"):
            display_df = monthly_df.copy()
            display_df["avg_return"] = display_df["avg_return"].apply(lambda x: f"{x:.2%}")
            display_df["win_rate"] = display_df["win_rate"].apply(lambda x: f"{x:.1%}")
            display_df.columns = ["月份", "交易次数", "平均收益率", "胜率", "累计收益"]
            st.dataframe(display_df, use_container_width=True)
    
    # ============================================================
    # 席位类型分析
    # ============================================================
    if "has_institution" in df.columns:
        st.subheader("🏦 机构 vs 游资 表现对比")
        
        inst_df = df[df["has_institution"] == True]
        non_inst_df = df[df["has_institution"] == False]
        
        grp_data = []
        if not inst_df.empty:
            grp_data.append({
                "类型": "机构席位买入",
                "交易次数": len(inst_df),
                "胜率": f"{(inst_df['return_rate'] > 0).mean():.1%}",
                "平均收益": f"{inst_df['return_rate'].mean():.2%}",
                "中位数收益": f"{inst_df['return_rate'].median():.2%}",
            })
        if not non_inst_df.empty:
            grp_data.append({
                "类型": "游资席位",
                "交易次数": len(non_inst_df),
                "胜率": f"{(non_inst_df['return_rate'] > 0).mean():.1%}",
                "平均收益": f"{non_inst_df['return_rate'].mean():.2%}",
                "中位数收益": f"{non_inst_df['return_rate'].median():.2%}",
            })
        
        if grp_data:
            st.dataframe(pd.DataFrame(grp_data), use_container_width=True)
    
    # ============================================================
    # 技术面评分分析
    # ============================================================
    if "tech_score" in df.columns:
        st.subheader("🔢 技术面评分与收益关系")
        
        fig_scatter = px.scatter(
            df,
            x="tech_score",
            y=df["return_rate"] * 100,
            color="has_institution" if "has_institution" in df.columns else None,
            title="技术面评分 vs 收益率散点图",
            labels={"tech_score": "技术面评分", "y": "收益率(%)"},
            opacity=0.6,
            trendline="ols",
        )
        fig_scatter.add_hline(y=0, line_dash="dash", line_color="#9ca3af")
        fig_scatter.update_layout(
            height=400,
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
        )
        st.plotly_chart(fig_scatter, use_container_width=True)
        
        # 分段统计
        bins = [(50, 65), (65, 80), (80, 100)]
        bin_stats = []
        for lo, hi in bins:
            grp = df[(df["tech_score"] >= lo) & (df["tech_score"] < hi)]
            if len(grp) > 0:
                bin_stats.append({
                    "技术评分区间": f"{lo}~{hi}",
                    "交易次数": len(grp),
                    "胜率": f"{(grp['return_rate'] > 0).mean():.1%}",
                    "平均收益": f"{grp['return_rate'].mean():.2%}",
                })
        if bin_stats:
            st.dataframe(pd.DataFrame(bin_stats), use_container_width=True)
    
    # ============================================================
    # 净买入额分析
    # ============================================================
    if "net_buy" in df.columns:
        st.subheader("💵 净买入额与收益关系")
        
        bins_net = [(5e7, 1e8, "5千万~1亿"), (1e8, 3e8, "1亿~3亿"), (3e8, float("inf"), "3亿以上")]
        net_stats = []
        for lo, hi, label in bins_net:
            grp = df[(df["net_buy"] >= lo) & (df["net_buy"] < hi)]
            if len(grp) > 0:
                net_stats.append({
                    "净买入区间": label,
                    "交易次数": len(grp),
                    "胜率": f"{(grp['return_rate'] > 0).mean():.1%}",
                    "平均收益": f"{grp['return_rate'].mean():.2%}",
                })
        if net_stats:
            st.dataframe(pd.DataFrame(net_stats), use_container_width=True)
    
    # ============================================================
    # 原始数据
    # ============================================================
    st.divider()
    st.subheader("📋 回测明细数据")
    
    col_filter1, col_filter2 = st.columns(2)
    with col_filter1:
        if "has_institution" in df.columns:
            inst_filter = st.multiselect(
                "机构席位过滤",
                options=[True, False],
                default=[True, False],
                format_func=lambda x: "机构买入" if x else "游资买入",
            )
            df_show = df[df["has_institution"].isin(inst_filter)]
        else:
            df_show = df
    
    with col_filter2:
        sort_col = st.selectbox(
            "排序字段",
            options=[c for c in ["return_rate", "trade_date", "tech_score", "net_buy"]
                     if c in df.columns],
        )
    
    df_show = df_show.sort_values(sort_col, ascending=False)
    
    display_cols = [c for c in [
        "trade_date", "name", "code", "return_rate",
        "exit_reason", "hold_days", "buy_price", "sell_price",
        "net_buy", "seat_score", "tech_score", "has_institution",
    ] if c in df_show.columns]
    
    df_display = df_show[display_cols].copy()
    if "return_rate" in df_display.columns:
        df_display["return_rate"] = df_display["return_rate"].apply(lambda x: f"{x:.2%}")
    if "net_buy" in df_display.columns:
        df_display["net_buy"] = df_display["net_buy"].apply(lambda x: f"{x/1e8:.2f}亿")
    
    st.dataframe(df_display, use_container_width=True, height=400)
    
    # 下载按钮
    csv = df_show.to_csv(index=False, encoding="utf-8-sig")
    st.download_button(
        "📥 下载回测数据（CSV）",
        data=csv.encode("utf-8-sig"),
        file_name=f"backtest_results_{pd.Timestamp.now().strftime('%Y%m%d')}.csv",
        mime="text/csv",
    )
