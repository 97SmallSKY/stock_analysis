"""
回测结果深度分析页面
新增：最大回撤曲线、滚动胜率、基准对比、K线信号图、Exit分布
"""
import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots
import json

from utils.storage import storage
from utils.chart_utils import plotly_dark
from modules.backtest.metrics import analyze, benchmark_compare
from modules.backtest.data_feed import DataFeed


def _has_statsmodels():
    try:
        import statsmodels
        return True
    except ImportError:
        return False


def render():
    st.title("📊 回测结果深度分析")
    st.caption("逐笔信号回测统计 | 不考虑资金占用和持仓重叠 | 不代表真实组合收益")

    all_results = storage.read("backtest_results")
    if all_results.empty:
        st.warning("暂无回测数据，请先在「策略回测」页面运行回测")
        return

    runs_df = storage.read("backtest_runs")

    # ── Run 选择器 ──
    if not runs_df.empty and "run_id" in all_results.columns:
        run_ids = all_results["run_id"].dropna().unique().tolist()
        # backtest_runs 可能用 task_id 或 run_id 作为主键
        run_key = "run_id" if "run_id" in runs_df.columns else ("task_id" if "task_id" in runs_df.columns else None)
        if runs_df is not None and not runs_df.empty and run_key:
            run_map = {}
            for _, r in runs_df[runs_df[run_key].isin(run_ids)].iterrows():
                rid = r[run_key]
                strat = r.get("strategy_name", "?")
                rtype = r.get("run_type", "?")
                cnt = r.get("trade_count", 0)
                created = str(r.get("created_at", ""))[:16]
                run_map[rid] = f"{created} | {strat} | {rtype} | {cnt}笔"
        else:
            run_map = {rid: rid for rid in run_ids}
        selected_run = st.selectbox(
            "选择回测批次", run_ids,
            format_func=lambda rid: run_map.get(rid, rid),
            key="analysis_run"
        )
        df = all_results[all_results["run_id"] == selected_run].copy()
        if selected_run in run_map:
            st.caption(f"当前批次: {run_map[selected_run]}")
    else:
        df = all_results.copy()
        selected_run = None
        st.info("历史数据无 run_id，显示全部结果。")

    # 数据预处理
    if "trade_date" in df.columns:
        df["trade_date"] = pd.to_datetime(df["trade_date"].astype(str))
    if "return_rate" in df.columns:
        df["return_rate"] = df["return_rate"].astype(float)

    # 解析 signal_meta（如果有）
    if "signal_meta" in df.columns and not df["signal_meta"].empty:
        try:
            if isinstance(df["signal_meta"].iloc[0], str):
                df["signal_meta_parsed"] = df["signal_meta"].apply(
                    lambda x: json.loads(x) if isinstance(x, str) and x.startswith("{") else {}
                )
        except Exception:
            df["signal_meta_parsed"] = df["signal_meta"]

    stats = analyze(df)

    if stats.get("total_trades", 0) == 0:
        st.warning("无有效交易数据")
        return

    # ============================================================
    # KPI 卡片
    # ============================================================
    st.subheader("🎯 核心指标")

    cols = st.columns(7)
    metrics_list = [
        ("逐笔信号数", f"{stats['total_trades']}", None),
        ("逐笔胜率", f"{stats['win_rate']:.1%}", None),
        ("逐笔均收益", f"{stats['avg_return']:.2%}", "↑" if stats['avg_return'] > 0 else "↓"),
        ("最大回撤", f"{stats.get('max_drawdown', 0):.2%}", None),
        ("Calmar比率", f"{stats.get('calmar', 0):.2f}", None),
        ("平均持有", f"{stats.get('avg_hold_days', 0):.1f}天", None),
        ("盈亏比", f"{stats['pl_ratio']:.2f}", None),
    ]
    for col, (label, val, delta) in zip(cols, metrics_list):
        col.metric(label, val, delta)

    cols2 = st.columns(7)
    metrics2 = [
        ("盈利次数", f"{stats['win_count']}"),
        ("亏损次数", f"{stats['loss_count']}"),
        ("平均盈利", f"{stats['avg_win']:.2%}"),
        ("平均亏损", f"{stats['avg_loss']:.2%}"),
        ("单笔最大盈利", f"{stats['max_return']:.2%}"),
        ("单笔最大亏损", f"{stats['min_return']:.2%}"),
        ("最大连续亏损", f"{stats['max_consecutive_loss']}次"),
    ]
    for col, (label, val) in zip(cols2, metrics2):
        col.metric(label, val)

    st.divider()

    # ============================================================
    # 净值曲线 + 水下曲线 + 滚动胜率（3 图联动）
    # ============================================================
    st.subheader("📈 净值曲线 & 回撤 & 滚动胜率")

    cum_curve = stats.get("cumulative_curve", [])
    dd_curve = stats.get("drawdown_curve", [])
    roll_wr = stats.get("rolling_win_rate", [])

    if cum_curve:
        n_charts = 2 + (1 if roll_wr else 0)
        fig_combo = make_subplots(
            rows=n_charts, cols=1,
            subplot_titles=["累计收益曲线", "水下曲线（最大回撤）"] +
                           (["20笔滚动胜率"] if roll_wr else []),
            shared_xaxes=True, vertical_spacing=0.08,
            row_heights=[0.45, 0.30] + ([0.25] if roll_wr else []),
        )

        # 1. 累计收益
        curve_df = pd.DataFrame(cum_curve)
        curve_df["trade_date"] = pd.to_datetime(curve_df["trade_date"])
        fig_combo.add_trace(go.Scatter(
            x=curve_df["trade_date"], y=curve_df["cumulative_return"] * 100,
            mode="lines", name="策略累计收益(%)",
            line=dict(color="#ef4444", width=2),
            fill="tozeroy", fillcolor="rgba(239,68,68,0.1)",
        ), row=1, col=1)

        # 基准对比
        benchmark_ret = _get_benchmark(df, cum_curve)
        if benchmark_ret is not None:
            fig_combo.add_trace(go.Scatter(
                x=curve_df["trade_date"], y=benchmark_ret,
                mode="lines", name="沪深300基准(%)",
                line=dict(color="#9ca3af", width=1.5, dash="dash"),
            ), row=1, col=1)

        fig_combo.add_hline(y=0, line_dash="dash", line_color="#9ca3af", row=1, col=1)

        # 2. 水下曲线
        if dd_curve:
            dd_df = pd.DataFrame(dd_curve)
            dd_df["trade_date"] = pd.to_datetime(dd_df["trade_date"])
            fig_combo.add_trace(go.Scatter(
                x=dd_df["trade_date"], y=dd_df["drawdown"] * 100,
                mode="lines", name="回撤(%)",
                line=dict(color="#6366f1", width=1.5),
                fill="tozeroy", fillcolor="rgba(99,102,241,0.15)",
            ), row=2, col=1)
            fig_combo.add_hline(y=0, line_dash="dash", line_color="#9ca3af", row=2, col=1)

        # 3. 滚动胜率
        if roll_wr:
            wr_df = pd.DataFrame(roll_wr)
            fig_combo.add_trace(go.Scatter(
                x=wr_df["trade_index"], y=wr_df["win_rate"] * 100,
                mode="lines", name="滚动胜率(%)",
                line=dict(color="#10b981", width=2),
            ), row=3, col=1)
            fig_combo.add_hline(y=50, line_dash="dash", line_color="#9ca3af", row=3, col=1)

        fig_combo.update_layout(**plotly_dark(
            height=250 * n_charts,
            hovermode="x unified",
            showlegend=True,
        ))
        st.plotly_chart(fig_combo, use_container_width=True)

    st.divider()

    # ============================================================
    # Exit 原因分布
    # ============================================================
    st.subheader("🔚 Exit 原因分布")
    exit_dist = stats.get("exit_distribution", [])
    if exit_dist:
        col_pie, col_bar = st.columns(2)
        with col_pie:
            exit_df = pd.DataFrame(exit_dist)
            fig_pie = px.pie(
                exit_df, values="count", names="exit_reason",
                title="Exit 原因占比",
                color_discrete_sequence=px.colors.qualitative.Set2,
            )
            fig_pie.update_layout(**plotly_dark(height=350))
            st.plotly_chart(fig_pie, use_container_width=True)

        with col_bar:
            fig_bar = go.Figure()
            colors = ["#10b981" if v > 0 else "#ef4444" for v in exit_df["avg_return"]]
            fig_bar.add_trace(go.Bar(
                x=exit_df["exit_reason"], y=exit_df["avg_return"] * 100,
                marker_color=colors,
                text=exit_df["avg_return"].apply(lambda x: f"{x:.2%}"),
                textposition="outside",
            ))
            fig_bar.update_layout(**plotly_dark(
                title="各 Exit 平均收益",
                yaxis_title="平均收益 (%)",
                height=350,
            ))
            fig_bar.add_hline(y=0, line_dash="dash", line_color="#9ca3af")
            st.plotly_chart(fig_bar, use_container_width=True)

        # 汇总表
        st.dataframe(exit_df.set_index("exit_reason"), use_container_width=True)
    else:
        st.info("无退出原因数据")

    st.divider()

    # ============================================================
    # 收益分布 + 月度
    # ============================================================
    st.subheader("📊 收益分布 & 月度分析")

    col1, col2 = st.columns(2)
    with col1:
        fig_hist = px.histogram(
            df, x="return_rate", nbins=40,
            title="收益率分布直方图",
            labels={"return_rate": "收益率"},
            color_discrete_sequence=["#6366f1"],
        )
        fig_hist.add_vline(x=df["return_rate"].mean(), line_dash="dash", line_color="#ef4444",
                           annotation_text=f"均值 {df['return_rate'].mean():.2%}")
        fig_hist.add_vline(x=0, line_dash="solid", line_color="#9ca3af", annotation_text="0%")
        fig_hist.update_xaxes(tickformat=".1%")
        fig_hist.update_layout(**plotly_dark(height=350))
        st.plotly_chart(fig_hist, use_container_width=True)

    with col2:
        win_df = df[df["return_rate"] > 0]["return_rate"]
        loss_df = df[df["return_rate"] <= 0]["return_rate"]
        fig_pl = go.Figure()
        fig_pl.add_trace(go.Box(y=win_df * 100, name="盈利交易", marker_color="#10b981", boxpoints="outliers"))
        fig_pl.add_trace(go.Box(y=loss_df * 100, name="亏损交易", marker_color="#ef4444", boxpoints="outliers"))
        fig_pl.add_hline(y=0, line_dash="dash", line_color="#9ca3af")
        fig_pl.update_layout(**plotly_dark(title="盈亏交易箱线图", yaxis_title="收益率 (%)", height=350))
        st.plotly_chart(fig_pl, use_container_width=True)

    # 月度
    monthly = stats.get("monthly_stats", [])
    if monthly:
        st.subheader("🗓️ 月度收益分析")
        monthly_df = pd.DataFrame(monthly)
        fig_month = make_subplots(
            rows=2, cols=1, subplot_titles=("月度平均收益率", "月度胜率"),
            shared_xaxes=True, vertical_spacing=0.12,
        )
        colors = monthly_df["avg_return"].apply(lambda x: "#10b981" if x >= 0 else "#ef4444")
        fig_month.add_trace(go.Bar(
            x=monthly_df["month"], y=monthly_df["avg_return"] * 100,
            marker_color=colors, text=monthly_df["avg_return"].apply(lambda x: f"{x:.2%}"),
            textposition="outside",
        ), row=1, col=1)
        fig_month.add_trace(go.Bar(
            x=monthly_df["month"], y=monthly_df["win_rate"] * 100,
            marker_color="#6366f1", text=monthly_df["win_rate"].apply(lambda x: f"{x:.1%}"),
            textposition="outside",
        ), row=2, col=1)
        fig_month.add_hline(y=0, line_dash="dash", line_color="#9ca3af", row=1, col=1)
        fig_month.add_hline(y=50, line_dash="dash", line_color="#9ca3af", row=2, col=1)
        fig_month.update_layout(**plotly_dark(height=500, showlegend=False))
        st.plotly_chart(fig_month, use_container_width=True)

    st.divider()

    # ============================================================
    # LHB 专属分析（条件显示）
    # ============================================================
    _render_lhb_sections(df)

    st.divider()

    # ============================================================
    # 每笔 K 线信号图（可展开）
    # ============================================================
    st.subheader("🎯 每笔 K 线信号图")

    show_candlestick = st.checkbox("展开查看 K 线信号详情", value=False, key="show_cs")
    if show_candlestick:
        _render_candlestick_charts(df)

    st.divider()

    # ============================================================
    # 明细表
    # ============================================================
    st.subheader("📋 回测明细数据")

    display_cols = [c for c in [
        "trade_date", "name", "code", "return_rate",
        "exit_reason", "hold_days", "buy_price", "sell_price",
        "net_buy", "seat_score", "tech_score", "has_institution",
        "exit_component",
    ] if c in df.columns]

    if display_cols:
        col_f1, col_f2 = st.columns(2)
        with col_f1:
            if "has_institution" in df.columns and df["has_institution"].notna().any():
                inst_filter = st.multiselect(
                    "机构席位过滤", options=[True, False], default=[True, False],
                    format_func=lambda x: "机构买入" if x else "游资买入",
                )
                df_show = df[df["has_institution"].isin(inst_filter)]
            else:
                df_show = df

        with col_f2:
            sort_options = [c for c in ["return_rate", "trade_date", "hold_days"] if c in df.columns]
            if sort_options:
                sort_col = st.selectbox("排序", sort_options)

        df_show = df_show.sort_values(sort_col, ascending=False)

        df_display = df_show[display_cols].copy()
        if "return_rate" in df_display.columns:
            df_display["return_rate"] = df_display["return_rate"].apply(lambda x: f"{float(x):.2%}")
        if "net_buy" in df_display.columns:
            df_display["net_buy"] = df_display["net_buy"].apply(
                lambda x: f"{float(x)/1e8:.2f}亿" if pd.notna(x) else "—"
            )

        st.dataframe(df_display, use_container_width=True, height=400)

        csv = df_show.to_csv(index=False, encoding="utf-8-sig")
        st.download_button(
            "📥 下载回测数据（CSV）",
            data=csv.encode("utf-8-sig"),
            file_name=f"backtest_results_{pd.Timestamp.now().strftime('%Y%m%d')}.csv",
            mime="text/csv",
        )


# ============================================================
# LHB 专属分析
# ============================================================
def _render_lhb_sections(df: pd.DataFrame):
    """LHB 专属维度分析（仅在数据存在时显示）"""

    # 机构 vs 游资
    if "has_institution" in df.columns and df["has_institution"].notna().any():
        st.subheader("🏦 机构 vs 游资 表现对比")
        inst_df = df[df["has_institution"] == True]
        non_inst_df = df[df["has_institution"] == False]
        grp_data = []
        for label, grp in [("机构席位买入", inst_df), ("游资席位", non_inst_df)]:
            if not grp.empty:
                grp_data.append({
                    "类型": label, "交易次数": len(grp),
                    "胜率": f"{(grp['return_rate'] > 0).mean():.1%}",
                    "平均收益": f"{grp['return_rate'].mean():.2%}",
                    "中位数收益": f"{grp['return_rate'].median():.2%}",
                })
        if grp_data:
            st.dataframe(pd.DataFrame(grp_data), use_container_width=True)

    # 技术评分
    if "tech_score" in df.columns and df["tech_score"].notna().any():
        st.subheader("🔢 技术面评分与收益关系")
        fig_scatter = px.scatter(
            df, x="tech_score", y=df["return_rate"] * 100,
            color="has_institution" if "has_institution" in df.columns else None,
            title="技术面评分 vs 收益率",
            labels={"tech_score": "技术面评分", "y": "收益率(%)"},
            opacity=0.6,
            trendline="ols" if _has_statsmodels() else None,
        )
        fig_scatter.add_hline(y=0, line_dash="dash", line_color="#9ca3af")
        fig_scatter.update_layout(**plotly_dark(height=400))
        st.plotly_chart(fig_scatter, use_container_width=True)

    # 净买入
    net_col = "net_buy" if "net_buy" in df.columns else ("l_net" if "l_net" in df.columns else None)
    if net_col and df[net_col].notna().any():
        if "net_buy" not in df.columns and net_col == "l_net":
            df["net_buy"] = df["l_net"]
        st.subheader("💵 净买入额与收益关系")
        bins_net = [(5e7, 1e8, "5千万~1亿"), (1e8, 3e8, "1亿~3亿"), (3e8, float("inf"), "3亿以上")]
        net_stats = []
        for lo, hi, label in bins_net:
            grp = df[(df["net_buy"] >= lo) & (df["net_buy"] < hi)]
            if len(grp) > 0:
                net_stats.append({
                    "净买入区间": label, "交易次数": len(grp),
                    "胜率": f"{(grp['return_rate'] > 0).mean():.1%}",
                    "平均收益": f"{grp['return_rate'].mean():.2%}",
                })
        if net_stats:
            st.dataframe(pd.DataFrame(net_stats), use_container_width=True)


# ============================================================
# K 线信号图
# ============================================================
def _render_candlestick_charts(df: pd.DataFrame):
    """为每笔交易绘制带标注的 K 线图"""
    feed = DataFeed()

    for idx, (_, trade) in enumerate(df.iterrows()):
        try:
            code = str(trade.get("code", ""))
            trade_date_raw = str(trade.get("trade_date", ""))
            buy_date_raw = str(trade.get("buy_date", ""))
            sell_date_raw = str(trade.get("sell_date", ""))
            exit_reason = str(trade.get("exit_reason", "未知"))
            ret = float(trade.get("return_rate", 0))

            if not code or not trade_date_raw:
                continue

            # 日期范围：信号日前 30 天 → 卖出日后 5 天
            start_dt = pd.Timestamp(trade_date_raw[:8])
            end_dt = pd.Timestamp(sell_date_raw[:8] if sell_date_raw else trade_date_raw[:8]) + pd.Timedelta(days=5)
            lookback = start_dt - pd.Timedelta(days=30)

            kdf = feed.kline(code, lookback.strftime("%Y%m%d"), end_dt.strftime("%Y%m%d"))
            if kdf.empty or len(kdf) < 5:
                continue

            kdf["日期"] = pd.to_datetime(kdf["日期"])

            fig = go.Figure()
            fig.add_trace(go.Candlestick(
                x=kdf["日期"],
                open=kdf["开盘"], high=kdf["最高"],
                low=kdf["最低"], close=kdf["收盘"],
                name="K线",
                increasing_line_color="#ef4444",
                decreasing_line_color="#10b981",
            ))

            # 标注信号日
            sig_dt = pd.Timestamp(trade_date_raw[:8])
            if sig_dt in kdf["日期"].values:
                sig_row = kdf[kdf["日期"] == sig_dt]
                if not sig_row.empty:
                    fig.add_trace(go.Scatter(
                        x=[sig_dt], y=[sig_row.iloc[0]["最高"] * 1.02],
                        mode="markers+text",
                        marker=dict(symbol="diamond", size=12, color="#3b82f6"),
                        text=["信号"], textposition="top center",
                        name="信号日",
                    ))

            # 标注买入日
            if buy_date_raw:
                buy_dt = pd.Timestamp(buy_date_raw[:8])
                if buy_dt in kdf["日期"].values:
                    buy_row = kdf[kdf["日期"] == buy_dt]
                    if not buy_row.empty:
                        fig.add_trace(go.Scatter(
                            x=[buy_dt], y=[buy_row.iloc[0]["最低"] * 0.98],
                            mode="markers+text",
                            marker=dict(symbol="triangle-up", size=12, color="#10b981"),
                            text=["买入"], textposition="bottom center",
                            name="买入日",
                        ))

            # 标注卖出日
            if sell_date_raw:
                sell_dt = pd.Timestamp(sell_date_raw[:8])
                if sell_dt in kdf["日期"].values:
                    sell_row = kdf[kdf["日期"] == sell_dt]
                    if not sell_row.empty:
                        fig.add_trace(go.Scatter(
                            x=[sell_dt], y=[sell_row.iloc[0]["最高"] * 1.02],
                            mode="markers+text",
                            marker=dict(symbol="triangle-down", size=12, color="#ef4444"),
                            text=[f"卖出({exit_reason})"], textposition="top center",
                            name="卖出日",
                        ))

            fig.update_layout(**plotly_dark(
                title=f"#{idx+1} {code} | {trade_date_raw[:8]} → {sell_date_raw[:8]} | 收益: {ret:.2%} | {exit_reason}",
                xaxis_rangeslider_visible=False,
                height=400,
            ))
            st.plotly_chart(fig, use_container_width=True)

        except Exception as e:
            st.caption(f"#{idx+1} 绘图失败: {e}")


# ============================================================
# 基准对比
# ============================================================
def _get_benchmark(df: pd.DataFrame, cum_curve: list) -> np.ndarray | None:
    """返回基准（沪深300）的同步累计收益序列"""
    try:
        feed = DataFeed()
        dates = sorted(df["trade_date"].astype(str).unique())
        if not dates:
            return None
        start, end = dates[0][:8], dates[-1][:8]
        bench_df = feed.kline("000300.SH", start, end)
        if bench_df.empty:
            return None
        bench_df["日期"] = pd.to_datetime(bench_df["日期"])
        first_close = float(bench_df.iloc[0]["收盘"])
        bench_df["bench_ret"] = (bench_df["收盘"] - first_close) / first_close

        # 对齐到 cum_curve 的日期
        curve_df = pd.DataFrame(cum_curve)
        curve_df["trade_date"] = pd.to_datetime(curve_df["trade_date"])
        aligned = []
        for _, row in curve_df.iterrows():
            match = bench_df[bench_df["日期"] <= row["trade_date"]]
            if not match.empty:
                aligned.append(float(match.iloc[-1]["bench_ret"]) * 100)
            else:
                aligned.append(None)
        return np.array(aligned)
    except Exception:
        return None
