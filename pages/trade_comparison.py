"""
📊 选股决策复盘

同天龙虎榜候选股对比分析：
  Tab1: 同天候选对比  — 评分 vs 实际 T+5 收益散点图 + 对比表
  Tab2: 单次深度复盘  — 持股时间轴 + 平仓后跟踪 + 同天收益对比 + LLM 分析
  Tab3: 错失机会统计  — 日期范围内最大错失 Top10 + 评分体系偏差分析
"""

import json
from datetime import datetime, timedelta

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

_RED   = "#E84040"
_GREEN = "#33CC66"
_GOLD  = "#FFA500"
_BLUE  = "#4488FF"
_GRAY  = "#888888"


# ── 辅助函数 ──────────────────────────────────────────────────────────────────

def _fmt_pct(v) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "—"
    return f"{v:+.2f}%"


def _color_pct(v) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return _GRAY
    return _GREEN if v > 0 else _RED if v < 0 else _GRAY


def _get_closed_trades() -> pd.DataFrame:
    """读取所有已平仓交易（用于 Tab2 选择）"""
    try:
        from sqlalchemy import text
        from skills.stock_sim_robot.sim_trade_db import _get_engine
        engine = _get_engine()
        sql = text("""
            SELECT id, trade_date, code, name, buy_price, sell_price,
                   profit_pct, sell_type, sell_date
            FROM sim_trades
            WHERE status = 'closed'
            ORDER BY sell_date DESC, id DESC
            LIMIT 200
        """)
        return pd.read_sql(sql, engine)
    except Exception as e:
        st.error(f"读取交易记录失败: {e}")
        return pd.DataFrame()


def _get_available_dates() -> list[str]:
    """获取有候选数据的日期列表"""
    try:
        from sqlalchemy import text
        from skills.stock_sim_robot.sim_trade_db import _get_engine
        engine = _get_engine()
        sql = text(
            "SELECT DISTINCT trade_date FROM sim_selection_log ORDER BY trade_date DESC LIMIT 100"
        )
        df = pd.read_sql(sql, engine)
        return df["trade_date"].tolist() if not df.empty else []
    except Exception:
        return []


# ── Tab1：同天候选对比 ────────────────────────────────────────────────────────

def render_tab1():
    st.subheader("同天候选股对比")

    dates = _get_available_dates()
    if not dates:
        st.info("暂无候选数据，请先运行 daily_update step10 填充 T+N 收益")
        return

    col1, col2 = st.columns([2, 1])
    with col1:
        selected_date = st.selectbox("选择龙虎榜日期", dates, key="tab1_date")
    with col2:
        st.write("")
        refresh = st.button("刷新数据", key="tab1_refresh")

    cache_key = f"tab1_data_{selected_date}"
    if refresh or cache_key not in st.session_state:
        with st.spinner("加载对比数据..."):
            from modules.comparison_analyzer import build_comparison_table, analyze_same_day_candidates
            table = build_comparison_table(selected_date)
            summary = analyze_same_day_candidates(selected_date)
        st.session_state[cache_key] = (table, summary)
    else:
        table, summary = st.session_state[cache_key]

    if table.empty:
        st.warning(f"{selected_date} 无候选数据（T+5 收益可能还未填充）")
        return

    # 汇总指标卡
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.metric("候选总数", summary.get("total", "—"))
    with c2:
        best_ret = summary.get("best_t5_ret")
        st.metric("同天最高T+5收益", _fmt_pct(best_ret), delta=summary.get("best_t5_code", ""))
    with c3:
        bought_ret = summary.get("bought_t5_ret")
        st.metric("实际买入T+5收益", _fmt_pct(bought_ret))
    with c4:
        missed = summary.get("missed_ret")
        color  = "normal" if missed is None else ("off" if missed <= 0 else "inverse")
        st.metric("错失差距", _fmt_pct(missed), delta_color=color)

    corr = summary.get("score_correlation")
    optimal = summary.get("selected_optimal")
    col_a, col_b = st.columns(2)
    with col_a:
        if corr is not None:
            corr_label = "强" if abs(corr) > 0.6 else "中" if abs(corr) > 0.3 else "弱"
            st.info(f"📈 评分与T+5收益相关性: **{corr}**（{corr_label}相关）")
    with col_b:
        if optimal is not None:
            icon = "✅" if optimal else "❌"
            msg  = "选中了最优股" if optimal else f"未选最优股（最优: {summary.get('best_t5_code')}）"
            st.info(f"{icon} {msg}")

    # 散点图：评分 vs T+5 收益
    has_t5 = table["T+5收益%"].notna().any()
    if has_t5:
        fig = px.scatter(
            table,
            x="评分",
            y="T+5收益%",
            text="代码",
            color="是否买入",
            color_discrete_map={True: _BLUE, False: _GRAY},
            size_max=12,
            hover_data=["名称", "评分排名", "连板数"],
            title=f"{selected_date} 评分 vs T+5实际收益",
        )
        fig.update_traces(textposition="top center", marker={"size": 10})
        fig.add_hline(y=0, line_dash="dash", line_color=_GRAY, opacity=0.5)
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("T+5 收益尚未填充，请等待 daily_update step10 完成")

    # 对比表
    st.subheader("详细对比表")
    display_cols = ["代码", "名称", "评分", "评分排名", "T+1收益%", "T+5收益%", "T+10收益%", "收益排名T5", "是否买入", "未选主因"]
    display_cols = [c for c in display_cols if c in table.columns]

    def _highlight_bought(row):
        if row.get("是否买入"):
            return ["background-color: #1a3a5c"] * len(row)
        return [""] * len(row)

    st.dataframe(
        table[display_cols].style.apply(_highlight_bought, axis=1),
        use_container_width=True,
        hide_index=True,
    )


# ── Tab2：单次交易深度复盘 ────────────────────────────────────────────────────

def render_tab2():
    st.subheader("单次交易深度复盘")

    trades = _get_closed_trades()
    if trades.empty:
        st.info("暂无已平仓交易记录")
        return

    # 构建选项
    trades["label"] = (
        trades["trade_date"] + " | " +
        trades["name"].fillna("") + "(" + trades["code"] + ")" +
        " | " + trades["profit_pct"].apply(lambda x: f"{x:+.1f}%" if pd.notna(x) else "—") +
        " | " + trades["sell_type"].fillna("")
    )

    col1, col2 = st.columns([3, 1])
    with col1:
        selected_label = st.selectbox("选择交易", trades["label"].tolist(), key="tab2_trade")
    with col2:
        st.write("")
        load_btn = st.button("加载复盘", key="tab2_load")

    if not load_btn and "tab2_trade_id" not in st.session_state:
        st.info("选择一笔交易后点击「加载复盘」")
        return

    trade_row = trades[trades["label"] == selected_label].iloc[0]
    trade_id  = int(trade_row["id"])

    if load_btn:
        st.session_state["tab2_trade_id"] = trade_id
        if f"tab2_timeline_{trade_id}" in st.session_state:
            del st.session_state[f"tab2_timeline_{trade_id}"]

    trade_id = st.session_state.get("tab2_trade_id", trade_id)

    cache_key = f"tab2_timeline_{trade_id}"
    if cache_key not in st.session_state:
        with st.spinner("加载复盘数据..."):
            from modules.comparison_analyzer import get_holding_timeline, analyze_same_day_candidates
            timeline = get_holding_timeline(trade_id)
            trade_date = str(trade_row.get("trade_date", ""))
            same_day   = analyze_same_day_candidates(trade_date) if trade_date else {}
        st.session_state[cache_key] = (timeline, same_day)
    else:
        timeline, same_day = st.session_state[cache_key]

    if "error" in timeline:
        st.error(f"加载失败: {timeline['error']}")
        return

    trade_info = timeline.get("trade_info", {})

    # 基本信息
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.metric("买入价", f"¥{trade_info.get('buy_price', '—')}")
    with c2:
        st.metric("卖出价", f"¥{trade_info.get('sell_price', '—')}")
    with c3:
        pct = trade_info.get("profit_pct")
        st.metric("盈亏", _fmt_pct(pct))
    with c4:
        st.metric("卖出类型", trade_info.get("sell_type", "—"))

    # 平仓后走势曲线
    post_tracking = timeline.get("post_tracking", [])
    if post_tracking:
        st.subheader("平仓后走势（相对卖出价）")
        pt_df = pd.DataFrame(post_tracking)
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=pt_df["track_day"].apply(lambda d: f"T+{d}"),
            y=pt_df["pct_vs_sell"],
            mode="lines+markers+text",
            text=pt_df["pct_vs_sell"].apply(_fmt_pct),
            textposition="top center",
            line={"color": _BLUE, "width": 2},
            name="相对卖出价涨跌%",
        ))
        fig.add_hline(y=0, line_dash="dash", line_color=_GRAY, opacity=0.5)
        fig.update_layout(title="平仓后 T+N 表现", yaxis_title="相对卖出价涨跌幅 %")
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("平仓后跟踪数据尚未填充（等待 daily_update step9）")

    # 同天候选收益对比柱状图
    same_day_table = same_day.get("comparison_table")
    if same_day_table is not None and not same_day_table.empty:
        st.subheader("同天候选股 T+5 收益对比")
        chart_df = same_day_table[same_day_table["T+5收益%"].notna()].copy()
        if not chart_df.empty:
            chart_df["颜色"] = chart_df.apply(
                lambda r: _BLUE if r["是否买入"] else (_GREEN if r["T+5收益%"] > 0 else _RED),
                axis=1
            )
            fig2 = go.Figure(go.Bar(
                x=chart_df["代码"] + "\n" + chart_df["名称"],
                y=chart_df["T+5收益%"],
                marker_color=chart_df["颜色"],
                text=chart_df["T+5收益%"].apply(_fmt_pct),
                textposition="outside",
            ))
            fig2.add_hline(y=0, line_dash="dash", line_color=_GRAY, opacity=0.5)
            fig2.update_layout(title="同天候选股 T+5 收益（蓝色=实际买入）", yaxis_title="T+5收益 %")
            st.plotly_chart(fig2, use_container_width=True)

    # 持股期间新闻事件
    news_events = timeline.get("news_events", [])
    if news_events:
        st.subheader("持股期间新闻事件")
        news_df = pd.DataFrame(news_events)
        news_df["情感"] = news_df["sentiment"].map({
            "positive": "🟢 利好",
            "negative": "🔴 利空",
            "neutral":  "⚪ 中性",
        }).fillna("⚪ 中性")
        display = news_df[["event_date", "情感", "title"]].rename(columns={
            "event_date": "日期",
            "title":      "标题",
        })
        st.dataframe(display, use_container_width=True, hide_index=True)
    else:
        st.info("持股期间无新闻记录（平仓时未触发新闻拉取，或该股无东方财富新闻）")

    # LLM 深度分析
    st.subheader("LLM 深度分析")
    llm_key = f"tab2_llm_{trade_id}"
    if llm_key in st.session_state:
        st.markdown(st.session_state[llm_key])
    else:
        if st.button("生成深度分析（调用 AI，约10秒）", key="tab2_llm_btn"):
            with st.spinner("AI 分析中..."):
                from modules.comparison_analyzer import generate_llm_deep_analysis
                analysis = generate_llm_deep_analysis(trade_id)
            if analysis:
                st.session_state[llm_key] = analysis
                st.markdown(analysis)
            else:
                st.info("该交易不符合 LLM 深度分析条件（需为止损单或同天错失>5%）")


# ── Tab3：错失机会统计 ────────────────────────────────────────────────────────

def render_tab3():
    st.subheader("错失机会统计")

    col1, col2, col3 = st.columns(3)
    with col1:
        start_date = st.date_input(
            "开始日期",
            value=datetime.today() - timedelta(days=90),
            key="tab3_start",
        )
    with col2:
        end_date = st.date_input(
            "结束日期",
            value=datetime.today(),
            key="tab3_end",
        )
    with col3:
        threshold = st.number_input("最低错失收益 (%)", min_value=0.0, value=5.0, step=1.0, key="tab3_threshold")

    if st.button("查询", key="tab3_query"):
        start_str = start_date.strftime("%Y%m%d")
        end_str   = end_date.strftime("%Y%m%d")

        with st.spinner("分析中..."):
            from modules.comparison_analyzer import find_missed_opportunities
            missed = find_missed_opportunities(start_str, end_str, threshold=threshold)

        if missed.empty:
            st.success(f"该时间段内无错失机会（T+5>{threshold}%未买入的股票）")
            return

        # 汇总指标
        c1, c2, c3 = st.columns(3)
        with c1:
            st.metric("错失机会总数", len(missed))
        with c2:
            max_missed = missed["错失差距%"].max() if "错失差距%" in missed.columns else None
            st.metric("最大错失差距", _fmt_pct(max_missed))
        with c3:
            avg_missed = missed["错失差距%"].mean() if "错失差距%" in missed.columns else None
            st.metric("平均错失差距", _fmt_pct(avg_missed))

        # 错失机会表
        st.subheader(f"错失机会 Top{min(10, len(missed))}")
        st.dataframe(missed.head(10), use_container_width=True, hide_index=True)

        # 错失差距分布图
        if "错失差距%" in missed.columns and missed["错失差距%"].notna().any():
            fig = px.histogram(
                missed,
                x="错失差距%",
                nbins=20,
                title="错失差距分布（同天最优股收益 - 实际买入股收益）",
                color_discrete_sequence=[_GOLD],
            )
            st.plotly_chart(fig, use_container_width=True)

        # 评分排名分析：哪些排名的股票最容易被错过
        if "评分排名" in missed.columns:
            rank_dist = missed["评分排名"].value_counts().sort_index()
            st.subheader("错失股的评分排名分布")
            fig2 = go.Figure(go.Bar(
                x=rank_dist.index.map(lambda r: f"第{r}名"),
                y=rank_dist.values,
                marker_color=_GOLD,
            ))
            fig2.update_layout(
                title="哪个排名的股票最常被错过",
                xaxis_title="评分排名",
                yaxis_title="次数",
            )
            st.plotly_chart(fig2, use_container_width=True)

            if 1 in rank_dist.index:
                st.warning(f"⚠️ 有 {rank_dist[1]} 次：评分第1却未买入的股票后来涨超{threshold}%，需检查选股逻辑")


# ── 主渲染入口 ────────────────────────────────────────────────────────────────

def render():
    st.title("📊 选股决策复盘")

    tab1, tab2, tab3 = st.tabs(["📈 同天候选对比", "🔍 单次深度复盘", "🎯 错失机会统计"])

    with tab1:
        render_tab1()

    with tab2:
        render_tab2()

    with tab3:
        render_tab3()
