"""
📊 选股决策复盘 v2

三个 Tab：
  Tab1: 同天候选对比  — 龙虎榜股票涨跌幅趋势叠加图 + T+5 横向对比柱状图
  Tab2: 单次交易深度复盘 — 蜡烛图 + 买入/卖出/最高点标注
  Tab3: 错失机会统计  — 龙虎榜全局分析，每天最优解 vs 实际买入对比
"""

from datetime import datetime, timedelta

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
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


def _pct_color(v) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return _GRAY
    return _GREEN if v > 0 else _RED if v < 0 else _GRAY


def _get_engine():
    from skills.stock_sim_robot.sim_trade_db import _get_engine as _eng
    return _eng()


def _get_selection_dates() -> list[str]:
    """从 sim_selection_log 取有候选股记录的 trade_date 列表"""
    try:
        from sqlalchemy import text
        engine = _get_engine()
        df = pd.read_sql(
            text("SELECT DISTINCT trade_date FROM sim_selection_log ORDER BY trade_date DESC LIMIT 90"),
            engine,
        )
        return df["trade_date"].tolist() if not df.empty else []
    except Exception:
        return []


def _get_lhb_dates() -> list[str]:
    """从 lhb_top10 取最近有数据的 trade_date 列表"""
    try:
        from sqlalchemy import text
        engine = _get_engine()
        df = pd.read_sql(
            text("SELECT DISTINCT trade_date FROM lhb_top10 ORDER BY trade_date DESC LIMIT 60"),
            engine,
        )
        return df["trade_date"].tolist() if not df.empty else []
    except Exception:
        return []


def _get_lhb_stocks(trade_date: str) -> pd.DataFrame:
    """取指定日期的龙虎榜股票（含 T+N 收益）"""
    try:
        from sqlalchemy import text
        engine = _get_engine()
        sql = text("""
            SELECT
                "代码", "名称", "收盘价", "涨跌幅",
                "龙虎榜净买额", "流通市值", "换手率", "上榜原因",
                "上榜后1日", "上榜后2日", "上榜后5日", "上榜后10日",
                trade_date
            FROM lhb_top10
            WHERE trade_date = :d
            ORDER BY "序号"
        """)
        return pd.read_sql(sql, engine, params={"d": trade_date})
    except Exception as e:
        st.error(f"读取龙虎榜数据失败: {e}")
        return pd.DataFrame()


def _get_bought_code(trade_date: str) -> str | None:
    """查找该龙虎榜日期是否有对应买入交易，返回买入股代码"""
    try:
        from sqlalchemy import text
        engine = _get_engine()
        df = pd.read_sql(
            text("SELECT code FROM sim_trades WHERE trade_date = :d AND status IN ('open','closed') LIMIT 1"),
            engine,
            params={"d": trade_date},
        )
        return str(df.iloc[0]["code"]) if not df.empty else None
    except Exception:
        return None


def _get_closed_trades() -> pd.DataFrame:
    """读取所有已平仓交易（含开仓中，用于 Tab2 选择）"""
    try:
        from sqlalchemy import text
        engine = _get_engine()
        sql = text("""
            SELECT id, trade_date, code, name, buy_price, sell_price,
                   profit_pct, sell_type, sell_date, status
            FROM sim_trades
            WHERE status IN ('closed', 'open')
            ORDER BY COALESCE(sell_date, trade_date) DESC, id DESC
            LIMIT 200
        """)
        return pd.read_sql(sql, engine)
    except Exception as e:
        st.error(f"读取交易记录失败: {e}")
        return pd.DataFrame()


def _get_post_tracking(trade_id: int) -> pd.DataFrame:
    """读取 sim_post_tracking"""
    try:
        from sqlalchemy import text
        engine = _get_engine()
        sql = text("SELECT * FROM sim_post_tracking WHERE trade_id = :tid ORDER BY track_day")
        return pd.read_sql(sql, engine, params={"tid": trade_id})
    except Exception:
        return pd.DataFrame()


# ── Tab1：同天候选对比（LHB top10 全量 + 评分系统 overlay）─────────────────────

def _get_sel_scores(trade_date: str) -> pd.DataFrame:
    """从 sim_selection_log 取该日评分数据，用于 overlay 到 LHB 全量数据上"""
    try:
        from sqlalchemy import text
        engine = _get_engine()
        df = pd.read_sql(
            text("SELECT code, signal_score, rank, trade_id FROM sim_selection_log WHERE trade_date=:d"),
            engine, params={"d": trade_date},
        )
        return df
    except Exception:
        return pd.DataFrame()


def render_tab1():
    st.subheader("同天候选对比")
    st.caption("数据来源：龙虎榜 Top10 全量（T+N 收益）+ 模拟评分系统 overlay（已有评分的日期显示评分列）")

    dates = _get_lhb_dates()
    if not dates:
        st.warning("暂无龙虎榜数据，请先运行 daily_update step1")
        return

    col1, col2 = st.columns([3, 1])
    with col1:
        selected_date = st.selectbox("选择日期", dates, key="tab1_date")
    with col2:
        st.write("")
        refresh = st.button("刷新", key="tab1_refresh")

    cache_key = f"tab1_full_{selected_date}"
    if refresh or cache_key not in st.session_state:
        lhb_df  = _get_lhb_stocks(selected_date)
        sel_df  = _get_sel_scores(selected_date)
        st.session_state[cache_key] = (lhb_df, sel_df)
    else:
        lhb_df, sel_df = st.session_state[cache_key]

    if lhb_df.empty:
        st.warning(f"{selected_date} 无龙虎榜数据")
        return

    # ── 合并评分 overlay ────────────────────────────────────────────────────────
    df = lhb_df.copy()
    df["代码"] = df["代码"].astype(str)
    has_scores = not sel_df.empty

    if has_scores:
        sel_df["code"] = sel_df["code"].astype(str)
        score_map = sel_df.set_index("code")[["signal_score", "rank", "trade_id"]].to_dict("index")
        df["评分"]     = df["代码"].map(lambda c: score_map.get(c, {}).get("signal_score"))
        df["选股排名"] = df["代码"].map(lambda c: score_map.get(c, {}).get("rank"))
        df["是否买入"] = df["代码"].map(lambda c: pd.notna(score_map.get(c, {}).get("trade_id")))
    else:
        bought_code = _get_bought_code(selected_date)
        df["评分"]     = None
        df["选股排名"] = None
        df["是否买入"] = df["代码"] == str(bought_code or "")

    # ── 最优涨幅 = max(T+1, T+2, T+5, T+10)
    # 涨：取最高点；跌：取亏损最少的节点（max of negatives = least negative）
    lhb_ret_cols = [c for c in ["上榜后1日", "上榜后2日", "上榜后5日", "上榜后10日"] if c in df.columns]
    if lhb_ret_cols:
        df["最优涨幅"] = df[lhb_ret_cols].max(axis=1)
        df["最优节点"] = df[lhb_ret_cols].idxmax(axis=1).where(df[lhb_ret_cols].notna().any(axis=1))
        # 去掉"上榜后"前缀，只保留"1日"/"5日"
        df["最优节点"] = df["最优节点"].str.replace("上榜后", "T+").str.replace("日", "")
    else:
        df["最优涨幅"] = None
        df["最优节点"] = None

    has_t5   = df["上榜后5日"].notna().any() if "上榜后5日" in df.columns else False
    has_best = df["最优涨幅"].notna().any()
    bought_rows = df[df["是否买入"]]
    bought_code_str = str(bought_rows.iloc[0]["代码"]) if not bought_rows.empty else None

    # ── 指标卡 ─────────────────────────────────────────────────────────────────
    c1, c2, c3, c4, c5 = st.columns(5)
    with c1:
        st.metric("龙虎榜股数", len(df))
    with c2:
        if has_best:
            st.metric("最优涨幅均值", _fmt_pct(df["最优涨幅"].mean()))
        else:
            st.metric("最优涨幅均值", "待结算")
    with c3:
        if has_best:
            best_row = df.loc[df["最优涨幅"].idxmax()]
            st.metric("最优股", _fmt_pct(best_row["最优涨幅"]),
                      delta=f"{best_row['名称']}({best_row['代码']})@{best_row.get('最优节点','')}")
        else:
            st.metric("最优股", "待结算")
    with c4:
        if bought_code_str and has_best and not bought_rows.empty:
            b_best = bought_rows.iloc[0]["最优涨幅"]
            global_best = df["最优涨幅"].max()
            diff = (global_best - b_best) if pd.notna(b_best) else None
            st.metric("买入 vs 最优差距", _fmt_pct(diff),
                      delta=f"买入:{_fmt_pct(b_best)}",
                      delta_color="inverse" if (diff or 0) > 0 else "normal")
        elif not bought_code_str:
            st.metric("买入股", "本日未买入")
        else:
            st.metric("买入 vs 最优差距", "待结算")
    with c5:
        if has_scores and has_best:
            valid = df[df["最优涨幅"].notna() & df["评分"].notna()]
            if len(valid) >= 3:
                corr = valid["评分"].rank().corr(valid["最优涨幅"].rank())
                st.metric("评分↔最优涨幅相关性", f"{corr:+.2f}",
                          delta="有效" if corr > 0.3 else ("无效" if corr < -0.1 else "弱"),
                          delta_color="normal" if corr >= 0 else "inverse")
            else:
                st.metric("评分↔最优涨幅相关性", "样本不足")
        else:
            st.metric("评分系统", "本日未运行" if not has_scores else "待结算")

    # ── 买入股详情 ─────────────────────────────────────────────────────────────
    if bought_code_str and not bought_rows.empty:
        br = bought_rows.iloc[0]
        t1  = _fmt_pct(br.get("上榜后1日"))
        t5  = _fmt_pct(br.get("上榜后5日"))
        best = _fmt_pct(br.get("最优涨幅"))
        score_str = f"评分：{br['评分']:.0f} | 选股排名：#{int(br['选股排名'])}" if pd.notna(br.get("评分")) else "（本日未进入评分系统）"
        global_best = df["最优涨幅"].max() if has_best else None
        b_best_val = br.get("最优涨幅")
        msg = (f"**本日买入：{br['名称']}（{br['代码']}）** | {score_str} "
               f"| T+1：{t1} | T+5：{t5} | 最优涨幅：{best}")
        if pd.notna(global_best) and pd.notna(b_best_val) and global_best - b_best_val > 5:
            st.warning(msg + f" ⚠️ 最优股多赚 {global_best - b_best_val:+.1f}%")
        else:
            st.info(msg)
    else:
        st.caption(f"📋 {selected_date} 无模拟买入记录（仅展示龙虎榜对比）")

    if not has_best:
        st.info(f"⏳ {selected_date} 的 T+N 收益尚未到结算日，将在上榜后自动更新")

    # ── 散点图：评分 vs 最优涨幅（仅在有评分数据时显示）────────────────────────
    if has_scores and has_best:
        plot_df = df[df["最优涨幅"].notna() & df["评分"].notna()].copy()
        if len(plot_df) >= 2:
            st.subheader("评分 vs 最优涨幅 散点图")
            st.caption("右上角 = 高分高收益（评分有效）；右下角 = 高分低收益（评分高估）；左上角 = 错失机会")
            plot_df["角色"] = plot_df["是否买入"].map({True: "✅ 买入", False: "未买入"})

            fig_scatter = go.Figure()
            for role, color, size, opacity in [("未买入", _GRAY, 10, 0.7), ("✅ 买入", _BLUE, 18, 1.0)]:
                sub = plot_df[plot_df["角色"] == role]
                if sub.empty:
                    continue
                fig_scatter.add_trace(go.Scatter(
                    x=sub["评分"], y=sub["最优涨幅"],
                    mode="markers+text", name=role,
                    text=sub["名称"], textposition="top center", textfont=dict(size=9),
                    marker=dict(size=size, color=color, opacity=opacity, line=dict(width=1, color="white")),
                    hovertemplate="<b>%{text}</b><br>评分:%{x:.0f} 最优涨幅:%{y:+.1f}%<extra></extra>",
                ))
            fig_scatter.add_hline(y=0, line_dash="dash", line_color=_GRAY, opacity=0.4)
            fig_scatter.update_layout(
                title=f"{selected_date} 评分 vs 最优涨幅",
                xaxis_title="综合评分", yaxis_title="最优涨幅 %",
                height=400, legend=dict(orientation="h", y=-0.15),
            )
            st.plotly_chart(fig_scatter, use_container_width=True)

    # ── 最优涨幅横向柱状图 ──────────────────────────────────────────────────────
    _has_t1 = "上榜后1日" in df.columns and df["上榜后1日"].notna().any()
    bar_col   = "最优涨幅" if has_best else ("上榜后1日" if _has_t1 else None)
    bar_label = "最优涨幅" if has_best else "T+1"

    if bar_col and bar_col in df.columns:
        st.subheader(f"{bar_label} 横向对比（涨跌两端均标最优点）")
        df_sorted = df.sort_values(bar_col, ascending=False, na_position="last").copy()
        best_idx = df_sorted[bar_col].idxmax() if df_sorted[bar_col].notna().any() else None
        worst_idx = df_sorted[bar_col].idxmin() if df_sorted[bar_col].notna().any() else None

        colors, text_labels = [], []
        for idx, row in df_sorted.iterrows():
            if row["是否买入"]:
                colors.append(_BLUE)
            elif pd.notna(row[bar_col]) and row[bar_col] > 0:
                colors.append(_GREEN)
            else:
                colors.append(_RED)
            lbl = _fmt_pct(row[bar_col])
            if idx == best_idx:
                lbl = "★ " + lbl
            if row["是否买入"]:
                lbl = "▶ " + lbl
            text_labels.append(lbl)

        node_labels = (df_sorted["最优节点"].fillna("") if "最优节点" in df_sorted.columns else pd.Series([""] * len(df_sorted)))
        fig_bar = go.Figure(go.Bar(
            x=df_sorted["名称"] + "<br>" + df_sorted["代码"],
            y=df_sorted[bar_col],
            marker_color=colors,
            text=text_labels,
            textposition="outside",
            customdata=node_labels,
            hovertemplate="<b>%{x}</b><br>" + bar_label + ": %{y:+.2f}%<br>最优节点: %{customdata}<extra></extra>",
        ))
        fig_bar.add_hline(y=0, line_dash="dash", line_color=_GRAY, opacity=0.5)
        fig_bar.update_layout(
            title=f"{selected_date} {bar_label} 对比（蓝=买入 ★=最高涨 ▶=买入）",
            yaxis_title="涨跌幅 %", xaxis_tickangle=-30, height=420,
        )
        st.plotly_chart(fig_bar, use_container_width=True)

    # ── 详细数据表（全量，买入股高亮）──────────────────────────────────────────
    st.subheader("详细数据")
    show_cols = ["代码", "名称"]
    if has_scores:
        show_cols += ["评分", "选股排名"]
    show_cols += [c for c in ["上榜后1日", "上榜后2日", "上榜后5日", "上榜后10日"] if c in df.columns]
    show_cols += ["最优涨幅", "最优节点", "是否买入"]
    display_df = df[show_cols].copy()

    # 重命名 T+N 列
    rename_map = {"上榜后1日": "T+1%", "上榜后2日": "T+2%", "上榜后5日": "T+5%", "上榜后10日": "T+10%"}
    display_df = display_df.rename(columns=rename_map)

    def _style_row(row):
        if row.get("是否买入"):
            return ["background-color: #1a2a4a; font-weight:bold"] * len(row)
        return [""] * len(row)

    col_config = {
        "最优涨幅": st.column_config.NumberColumn(format="%.1f%%"),
    }
    for c in ["T+1%", "T+2%", "T+5%", "T+10%"]:
        col_config[c] = st.column_config.NumberColumn(format="%.1f%%")
    if "评分" in display_df.columns:
        col_config["评分"] = st.column_config.NumberColumn(format="%.0f")

    st.dataframe(
        display_df.style.apply(_style_row, axis=1),
        use_container_width=True, hide_index=True, column_config=col_config,
    )


# ── 辅助：批量拉取龙虎榜 K 线 ─────────────────────────────────────────────────

def _fetch_lhb_klines(df: "pd.DataFrame", trade_date: str) -> dict:
    """
    批量拉取龙虎榜 df 中所有股票的历史 K 线。
    范围：trade_date 前 35 个自然日 起，覆盖到 trade_date 后约 20 个交易日。
    返回 {code: kline_df}，kline_df 包含 日期(YYYYMMDD)/收盘 列。
    失败的股票跳过（不阻塞其他股票）。
    """
    from datetime import timedelta
    from modules.market_data import get_kline_raw

    try:
        start_dt = (pd.Timestamp(trade_date) - timedelta(days=35)).strftime("%Y%m%d")
    except Exception:
        start_dt = trade_date

    codes = df["代码"].astype(str).tolist()
    total = len(codes)

    progress_bar = st.progress(0, text=f"拉取 K 线数据（共 {total} 只）...")
    result = {}

    for i, code in enumerate(codes):
        try:
            kl = get_kline_raw(code, start_dt, days=80)
            if kl is not None and not kl.empty:
                kl = kl.copy()
                kl["日期"] = kl["日期"].astype(str).str.replace("-", "").str[:8]
                result[code] = kl
        except Exception:
            pass
        progress_bar.progress((i + 1) / total, text=f"已拉取 {i+1}/{total}：{code}")

    progress_bar.empty()
    return result


# ── Tab2：单次交易深度复盘（K 线蜡烛图）─────────────────────────────────────

def render_tab2():
    st.subheader("单次交易深度复盘")

    trades = _get_closed_trades()
    if trades.empty:
        st.info("暂无交易记录（模拟炒股机器人尚未产生交易）")
        return

    # 构建选项标签
    def _trade_label(row) -> str:
        status = "📂 持仓中" if row.get("status") == "open" else ""
        pct_str = _fmt_pct(row.get("profit_pct"))
        sell_t  = str(row.get("sell_type") or "")
        date_str = str(row.get("trade_date") or "")
        return f"{date_str} | {row.get('name','')}({row.get('code','')}) | {pct_str} {sell_t} {status}".strip()

    if "status" not in trades.columns:
        trades["status"] = "closed"
    trades["label"] = trades.apply(_trade_label, axis=1)

    col1, col2 = st.columns([3, 1])
    with col1:
        selected_label = st.selectbox("选择交易", trades["label"].tolist(), key="tab2_trade")
    with col2:
        st.write("")
        load_btn = st.button("加载复盘", key="tab2_load")

    trade_row = trades[trades["label"] == selected_label].iloc[0]
    trade_id  = int(trade_row["id"])
    code      = str(trade_row["code"])
    name      = str(trade_row.get("name") or code)

    if not load_btn and f"tab2_kline_{trade_id}" not in st.session_state:
        st.info("选择一笔交易后点击「加载复盘」")
        return

    if load_btn:
        for k in list(st.session_state.keys()):
            if k.startswith(f"tab2_kline_{trade_id}"):
                del st.session_state[k]

    cache_key = f"tab2_kline_{trade_id}"
    if cache_key not in st.session_state:
        with st.spinner(f"拉取 {name}({code}) K 线数据..."):
            kline_df = _fetch_trade_kline(trade_row)
        st.session_state[cache_key] = kline_df
    else:
        kline_df = st.session_state[cache_key]

    buy_price  = float(trade_row.get("buy_price") or 0) or None
    sell_price = float(trade_row.get("sell_price") or 0) or None
    profit_pct = trade_row.get("profit_pct")
    sell_type  = str(trade_row.get("sell_type") or "")
    trade_date = str(trade_row.get("trade_date") or "")
    buy_date   = str(trade_row.get("buy_date") or trade_row.get("trade_date") or "")
    sell_date  = str(trade_row.get("sell_date") or "")

    # ── 指标卡 ──────────────────────────────────────────────────────────────
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.metric("买入价", f"¥{buy_price:.2f}" if buy_price else "—")
    with c2:
        st.metric("卖出价", f"¥{sell_price:.2f}" if sell_price else "持仓中")
    with c3:
        color = "normal"
        if profit_pct is not None:
            color = "off" if profit_pct < 0 else "normal"
        st.metric("盈亏", _fmt_pct(profit_pct), delta_color=color)
    with c4:
        st.metric("卖出类型", sell_type or "持仓中")

    # ── 蜡烛图 + 成交量 ───────────────────────────────────────────────────────
    if kline_df is None or kline_df.empty:
        st.warning(f"未能拉取到 {name}({code}) 的 K 线数据（可能已退市或临时接口失败）")
    else:
        _render_kline_chart(kline_df, name, code, buy_date, sell_date, buy_price, sell_price, profit_pct)

    # ── 平仓后跟踪折线 ────────────────────────────────────────────────────────
    post_df = _get_post_tracking(trade_id)
    if not post_df.empty:
        st.subheader("平仓后走势（相对卖出价）")
        fig_post = go.Figure()
        fig_post.add_trace(go.Scatter(
            x=post_df["track_day"].apply(lambda d: f"T+{d}"),
            y=post_df["pct_vs_sell"],
            mode="lines+markers+text",
            text=post_df["pct_vs_sell"].apply(_fmt_pct),
            textposition="top center",
            line={"color": _BLUE, "width": 2},
            name="相对卖出价%",
            hovertemplate="<b>%{x}</b><br>相对卖出价: %{y:+.2f}%<extra></extra>",
        ))
        if "index_pct" in post_df.columns and post_df["index_pct"].notna().any():
            fig_post.add_trace(go.Scatter(
                x=post_df["track_day"].apply(lambda d: f"T+{d}"),
                y=post_df["index_pct"],
                mode="lines",
                line={"color": _GRAY, "width": 1, "dash": "dot"},
                name="上证指数%",
                hovertemplate="上证: %{y:+.2f}%<extra></extra>",
            ))
        fig_post.add_hline(y=0, line_dash="dash", line_color=_GRAY, opacity=0.4)
        fig_post.update_layout(
            title="平仓后 T+N 表现 vs 上证指数",
            xaxis_title="平仓后第N个交易日",
            yaxis_title="涨跌幅 %",
            height=320,
        )
        st.plotly_chart(fig_post, use_container_width=True)
    else:
        st.info("⏳ 平仓后跟踪数据将在卖出后次日起自动采集（daily_update step9）")

    # ── 同天龙虎榜对比 ────────────────────────────────────────────────────────
    if trade_date:
        lhb_df = _get_lhb_stocks(trade_date)
        if not lhb_df.empty and lhb_df["上榜后5日"].notna().any():
            st.subheader(f"同天龙虎榜 T+5 收益对比（{trade_date}）")
            lhb_df["is_me"] = lhb_df["代码"].astype(str) == code
            lhb_sorted = lhb_df.sort_values("上榜后5日", ascending=False)
            bar_colors = [_BLUE if r["is_me"] else (_GREEN if (r["上榜后5日"] or 0) > 0 else _RED)
                          for _, r in lhb_sorted.iterrows()]
            bar_labels = [("▶ " if r["is_me"] else "") + _fmt_pct(r["上榜后5日"])
                          for _, r in lhb_sorted.iterrows()]
            fig_comp = go.Figure(go.Bar(
                x=lhb_sorted["名称"] + "<br>" + lhb_sorted["代码"],
                y=lhb_sorted["上榜后5日"],
                marker_color=bar_colors,
                text=bar_labels,
                textposition="outside",
            ))
            fig_comp.add_hline(y=0, line_dash="dash", line_color=_GRAY, opacity=0.4)
            fig_comp.update_layout(
                title=f"{trade_date} 同天龙虎榜 T+5 收益（蓝=本次买入）",
                yaxis_title="T+5 涨跌幅 %",
                xaxis_tickangle=-30,
                height=380,
            )
            st.plotly_chart(fig_comp, use_container_width=True)


def _fetch_trade_kline(trade_row) -> pd.DataFrame | None:
    """
    拉取该笔交易对应的 K 线。
    范围：trade_date 前约 15 个自然日 ~ sell_date 后约 15 个自然日。
    get_kline_raw(code, start_date, days) 中 start_date 是起始日，
    end_date = start_date + days*2+5，因此需要从 trade_date 往前推。
    """
    import pandas as _pd_local
    from datetime import timedelta
    from modules.market_data import get_kline_raw

    code       = str(trade_row.get("code") or "")
    trade_date = str(trade_row.get("trade_date") or "")
    sell_date  = str(trade_row.get("sell_date") or "")

    if not code or not trade_date:
        return None

    # 起点：trade_date 前 15 个自然日（确保买入前有足够走势）
    try:
        start_dt = (_pd_local.Timestamp(trade_date) - timedelta(days=15)).strftime("%Y%m%d")
    except Exception:
        start_dt = trade_date

    # 结束点：sell_date 后 20 个自然日（看平仓后走势）
    # days 参数 = (end - start) / 2（get_kline_raw 内部 end = start + days*2+5）
    # 用大窗口 80 天确保覆盖整个区间
    kline = get_kline_raw(code, start_dt, days=80)
    if kline is None or kline.empty:
        return None

    # 统一日期格式为 YYYYMMDD
    kline["日期"] = kline["日期"].astype(str).str.replace("-", "").str[:8]
    return kline.reset_index(drop=True)


def _render_kline_chart(kline_df, name, code, buy_date, sell_date, buy_price, sell_price, profit_pct=None):
    """渲染蜡烛图主图 + 成交量子图，标注买入/卖出/最高点 + MA5/MA10/MA20"""
    df = kline_df.copy()

    # 统一日期格式（x 轴用 YYYY-MM-DD 格式更好看）
    df["date_fmt"] = df["日期"].apply(lambda d: f"{d[:4]}-{d[4:6]}-{d[6:]}" if len(str(d)) == 8 else str(d))

    # 持仓期间最高收盘价
    if buy_date and sell_date:
        hold = df[(df["日期"] >= buy_date) & (df["日期"] <= sell_date)]
    elif buy_date:
        hold = df[df["日期"] >= buy_date]
    else:
        hold = df
    max_close_idx = hold["收盘"].idxmax() if not hold.empty and "收盘" in hold.columns else None
    max_row = df.loc[max_close_idx] if max_close_idx is not None else None

    # 创建子图（主图 K 线 + 副图成交量）
    fig = make_subplots(
        rows=2, cols=1,
        shared_xaxes=True,
        vertical_spacing=0.05,
        row_heights=[0.72, 0.28],
    )

    # K 线蜡烛图
    open_col  = next((c for c in ["开盘", "open", "Open"] if c in df.columns), None)
    high_col  = next((c for c in ["最高", "high", "High"] if c in df.columns), None)
    low_col   = next((c for c in ["最低", "low", "Low"] if c in df.columns), None)
    close_col = next((c for c in ["收盘", "close", "Close"] if c in df.columns), None)
    vol_col   = next((c for c in ["成交量", "volume", "Volume"] if c in df.columns), None)

    if all([open_col, high_col, low_col, close_col]):
        fig.add_trace(go.Candlestick(
            x=df["date_fmt"],
            open=df[open_col],
            high=df[high_col],
            low=df[low_col],
            close=df[close_col],
            name=f"{name}({code})",
            increasing_line_color=_RED,    # A股：红涨绿跌
            decreasing_line_color=_GREEN,
        ), row=1, col=1)
    elif close_col:
        # 没有完整 OHLC，降级为折线
        fig.add_trace(go.Scatter(
            x=df["date_fmt"], y=df[close_col],
            mode="lines", name="收盘价",
            line={"color": _BLUE},
        ), row=1, col=1)

    # MA 均线（基于收盘价）
    if close_col:
        for ma_n, ma_color in [(5, "#FFD700"), (10, "#00BFFF"), (20, "#FF69B4")]:
            ma_vals = df[close_col].rolling(ma_n).mean()
            if ma_vals.notna().any():
                fig.add_trace(go.Scatter(
                    x=df["date_fmt"], y=ma_vals,
                    mode="lines",
                    name=f"MA{ma_n}",
                    line={"color": ma_color, "width": 1.2, "dash": "dot"},
                    opacity=0.75,
                    hovertemplate=f"MA{ma_n}: %{{y:.2f}}<extra></extra>",
                ), row=1, col=1)

    # 成交量子图
    if vol_col:
        vol_colors = []
        for i, row in df.iterrows():
            if open_col and close_col:
                vol_colors.append(_RED if row[close_col] >= row[open_col] else _GREEN)
            else:
                vol_colors.append(_BLUE)
        fig.add_trace(go.Bar(
            x=df["date_fmt"], y=df[vol_col],
            marker_color=vol_colors,
            name="成交量",
            opacity=0.6,
        ), row=2, col=1)

    # ── 标注线 ────────────────────────────────────────────────────────────────
    buy_date_fmt  = f"{buy_date[:4]}-{buy_date[4:6]}-{buy_date[6:]}"  if buy_date  and len(buy_date) == 8  else buy_date
    sell_date_fmt = f"{sell_date[:4]}-{sell_date[4:6]}-{sell_date[6:]}" if sell_date and len(sell_date) == 8 else sell_date

    shapes = []
    annotations = []

    # 买入线（蓝色虚线）
    if buy_date and buy_date_fmt in df["date_fmt"].values:
        shapes.append(dict(
            type="line", x0=buy_date_fmt, x1=buy_date_fmt,
            y0=0, y1=1, yref="paper",
            line={"color": _BLUE, "width": 2, "dash": "dash"},
        ))
        annotations.append(dict(
            x=buy_date_fmt,
            y=buy_price if buy_price and close_col else 0,
            yref="y" if buy_price and close_col else "paper",
            text=f"买入 ¥{buy_price:.2f}" if buy_price else "买入",
            showarrow=True, arrowhead=2, arrowcolor=_BLUE,
            font={"color": _BLUE, "size": 11},
            bgcolor="rgba(0,80,200,0.15)",
            bordercolor=_BLUE,
        ))

    # 卖出线（红色虚线）
    if sell_date and sell_price and sell_date_fmt in df["date_fmt"].values:
        shapes.append(dict(
            type="line", x0=sell_date_fmt, x1=sell_date_fmt,
            y0=0, y1=1, yref="paper",
            line={"color": _RED, "width": 2, "dash": "dash"},
        ))
        pct_str = _fmt_pct(profit_pct)
        annotations.append(dict(
            x=sell_date_fmt,
            y=sell_price,
            yref="y",
            text=f"卖出 ¥{sell_price:.2f} ({pct_str})",
            showarrow=True, arrowhead=2, arrowcolor=_RED,
            font={"color": _RED, "size": 11},
            bgcolor="rgba(200,0,0,0.15)",
            bordercolor=_RED,
            ax=40, ay=-30,
        ))

    # 最高点标注（金色三角）
    if max_row is not None and close_col:
        max_date_fmt = f"{str(max_row['日期'])[:4]}-{str(max_row['日期'])[4:6]}-{str(max_row['日期'])[6:]}"
        max_close = float(max_row[close_col])
        max_pct = ((max_close - buy_price) / buy_price * 100) if buy_price else None
        fig.add_trace(go.Scatter(
            x=[max_date_fmt],
            y=[max_close],
            mode="markers+text",
            marker={"symbol": "triangle-up", "size": 14, "color": _GOLD},
            text=[f"最高 ¥{max_close:.2f}" + (f"<br>{_fmt_pct(max_pct)}" if max_pct else "")],
            textposition="top center",
            textfont={"color": _GOLD, "size": 10},
            name="持仓最高点",
            showlegend=True,
        ), row=1, col=1)

    fig.update_layout(
        title=f"{name}({code}) 复盘 K 线",
        shapes=shapes,
        annotations=annotations,
        xaxis_rangeslider_visible=False,
        hovermode="x unified",
        height=600,
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.02},
    )
    fig.update_yaxes(title_text="价格 (元)", row=1, col=1)
    fig.update_yaxes(title_text="成交量", row=2, col=1)

    st.plotly_chart(fig, use_container_width=True)


# ── Tab3：错失机会统计（lhb_top10 全局分析）─────────────────────────────────

def render_tab3():
    st.subheader("龙虎榜错失机会统计")
    st.caption("分析每天龙虎榜股票中的最优解（上榜后 T+5 最高涨幅），对比实际买入选择")

    # 日期范围
    col1, col2 = st.columns(2)
    with col1:
        start_date = st.date_input(
            "开始日期", value=datetime.today() - timedelta(days=90), key="tab3_start"
        )
    with col2:
        end_date = st.date_input("结束日期", value=datetime.today(), key="tab3_end")

    if st.button("开始分析", key="tab3_run"):
        start_str = start_date.strftime("%Y%m%d")
        end_str   = end_date.strftime("%Y%m%d")

        with st.spinner("分析中..."):
            result = _analyze_missed_opportunities(start_str, end_str)

        st.session_state["tab3_result"] = result

    if "tab3_result" not in st.session_state:
        st.info("选择日期范围后点击「开始分析」")
        return

    result = st.session_state["tab3_result"]
    if result is None or result.get("daily_df") is None:
        st.warning("该时间段内无龙虎榜数据（T+5 收益需等上榜后5个交易日）")
        return

    daily_df = result["daily_df"]
    summary  = result["summary"]

    if daily_df.empty:
        st.info("该时间段内暂无有 T+5 数据的龙虎榜日期")
        return

    # ── 汇总指标卡 ─────────────────────────────────────────────────────────────
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.metric("分析天数", summary["total_days"])
    with c2:
        st.metric("有买入记录天数", summary["traded_days"])
    with c3:
        st.metric("买中最优股次数", f"{summary['optimal_hits']} / {summary['traded_days']}")
    with c4:
        st.metric("平均错失差距", _fmt_pct(summary["avg_missed"]))

    # ── 每日最优 vs 买入对比柱状图 ─────────────────────────────────────────────
    st.subheader("每日：最优股 vs 买入股 T+5 收益")
    traded_days = daily_df[daily_df["bought_code"].notna()]

    if not traded_days.empty:
        fig_daily = go.Figure()
        # 最优股柱（金色）
        fig_daily.add_trace(go.Bar(
            name="最优股 T+5",
            x=traded_days["trade_date"],
            y=traded_days["best_t5"],
            marker_color=_GOLD,
            text=traded_days.apply(
                lambda r: f"{r['best_name']}({r['best_code']})<br>{_fmt_pct(r['best_t5'])}", axis=1
            ),
            textposition="outside",
            opacity=0.85,
        ))
        # 买入股柱（蓝色）
        fig_daily.add_trace(go.Bar(
            name="买入股 T+5",
            x=traded_days["trade_date"],
            y=traded_days["bought_t5"],
            marker_color=_BLUE,
            text=traded_days.apply(
                lambda r: f"{r['bought_name']}({r['bought_code']})<br>{_fmt_pct(r['bought_t5'])}", axis=1
            ),
            textposition="inside",
            opacity=0.9,
        ))
        # 买中最优的日期打绿色标记
        optimal_dates = traded_days[traded_days["is_optimal"]]["trade_date"].tolist()
        if optimal_dates:
            fig_daily.add_trace(go.Scatter(
                name="✅ 买中最优",
                x=optimal_dates,
                y=[traded_days.loc[traded_days["trade_date"] == d, "best_t5"].values[0] + 0.5
                   for d in optimal_dates],
                mode="markers",
                marker={"symbol": "star", "size": 12, "color": _GREEN},
            ))
        fig_daily.add_hline(y=0, line_dash="dash", line_color=_GRAY, opacity=0.4)
        fig_daily.update_layout(
            barmode="group",
            title="每日最优股 vs 实际买入股 T+5 收益对比",
            xaxis_title="龙虎榜日期",
            yaxis_title="T+5 涨跌幅 %",
            xaxis_tickangle=-30,
            height=450,
        )
        st.plotly_chart(fig_daily, use_container_width=True)

    # ── 最优股评分分布（反映评分体系准确率）──────────────────────────────────
    if not traded_days.empty and "best_signal_rank" in traded_days.columns:
        rank_data = traded_days["best_signal_rank"].dropna()
        if not rank_data.empty:
            st.subheader("最优股在我们评分中的排名分布")
            rank_counts = rank_data.astype(int).value_counts().sort_index()
            hit_pct = (rank_counts.get(1, 0) / len(rank_data) * 100)
            st.caption(f"评分第1名命中最优股的概率：**{hit_pct:.0f}%**（越高说明评分体系越准确）")

            fig_rank = go.Figure(go.Bar(
                x=[f"评分第{r}名" for r in rank_counts.index],
                y=rank_counts.values,
                marker_color=[_GREEN if r == 1 else _GOLD if r == 2 else _GRAY for r in rank_counts.index],
                text=rank_counts.values,
                textposition="outside",
            ))
            fig_rank.update_layout(
                title="最优股出现在哪个评分排名？（命中率分析）",
                yaxis_title="次数",
                height=350,
            )
            st.plotly_chart(fig_rank, use_container_width=True)

    # ── 明细表 ─────────────────────────────────────────────────────────────────
    st.subheader("每日详情")
    disp = daily_df[[
        "trade_date", "lhb_count",
        "best_code", "best_name", "best_t5",
        "bought_code", "bought_name", "bought_t5",
        "missed_gap", "is_optimal",
    ]].rename(columns={
        "trade_date":  "日期",
        "lhb_count":   "龙虎榜股数",
        "best_code":   "最优股代码",
        "best_name":   "最优股名称",
        "best_t5":     "最优T+5%",
        "bought_code": "买入代码",
        "bought_name": "买入名称",
        "bought_t5":   "买入T+5%",
        "missed_gap":  "错失差距%",
        "is_optimal":  "买中最优",
    })
    disp = disp.sort_values("日期", ascending=False)

    def _highlight(row):
        if row.get("买中最优"):
            return ["background-color: #0d2e14"] * len(row)
        if pd.notna(row.get("错失差距%")) and row["错失差距%"] > 5:
            return ["background-color: #2e0d0d"] * len(row)
        return [""] * len(row)

    st.dataframe(disp.style.apply(_highlight, axis=1), use_container_width=True, hide_index=True)


def _analyze_missed_opportunities(start_str: str, end_str: str) -> dict | None:
    """
    基于 lhb_top10 计算每天最优股 vs 实际买入股的差距。
    返回 {daily_df, summary} 或 None
    """
    try:
        from sqlalchemy import text
        engine = _get_engine()

        # 取指定日期范围内所有有 T+5 数据的龙虎榜记录
        sql = text("""
            SELECT
                lhb.trade_date,
                lhb."代码"     AS code,
                lhb."名称"     AS name,
                lhb."上榜后5日" AS t5_ret,
                t.code         AS trade_code,
                t.name         AS trade_name,
                t.profit_pct,
                t.sell_type
            FROM lhb_top10 lhb
            LEFT JOIN sim_trades t
                ON t.trade_date = lhb.trade_date
               AND t."code" = lhb."代码"
            WHERE lhb.trade_date BETWEEN :s AND :e
              AND lhb."上榜后5日" IS NOT NULL
            ORDER BY lhb.trade_date, lhb."上榜后5日" DESC
        """)
        df = pd.read_sql(sql, engine, params={"s": start_str, "e": end_str})
        if df.empty:
            return None

        # 同时获取每天的买入情况（sim_trades）
        trades_sql = text("""
            SELECT trade_date, code, name,
                   (SELECT lhb2."上榜后5日"
                    FROM lhb_top10 lhb2
                    WHERE lhb2.trade_date = t.trade_date
                      AND lhb2."代码" = t.code
                    LIMIT 1) AS t5_ret
            FROM sim_trades t
            WHERE trade_date BETWEEN :s AND :e
        """)
        trades_df = pd.read_sql(trades_sql, engine, params={"s": start_str, "e": end_str})

        # 也获取每天买入股的评分排名（来自 sim_selection_log）
        rank_sql = text("""
            SELECT trade_date, code,
                   rank AS signal_rank
            FROM sim_selection_log
            WHERE trade_date BETWEEN :s AND :e
        """)
        rank_df = pd.read_sql(rank_sql, engine, params={"s": start_str, "e": end_str})

        # 按天计算：最优股 + 买入股
        daily_rows = []
        for trade_date, day_group in df.groupby("trade_date"):
            # 最优股（T+5 最高）
            best_row = day_group.iloc[0]  # 已经按 T+5 desc 排序

            # 买入股
            bought = trades_df[trades_df["trade_date"] == trade_date]
            bought_code  = None
            bought_name  = None
            bought_t5    = None
            if not bought.empty:
                b = bought.iloc[0]
                bought_code = b["code"]
                bought_name = b["name"]
                bought_t5   = b.get("t5_ret")

            # 买入股在评分中的排名
            best_signal_rank = None
            if rank_df is not None and not rank_df.empty:
                best_rank_row = rank_df[
                    (rank_df["trade_date"] == trade_date) &
                    (rank_df["code"] == best_row["code"])
                ]
                if not best_rank_row.empty:
                    best_signal_rank = int(best_rank_row.iloc[0]["signal_rank"])

            is_optimal = (bought_code == best_row["code"]) if bought_code else False
            missed_gap = (float(best_row["t5_ret"]) - float(bought_t5)) if (
                pd.notna(best_row["t5_ret"]) and pd.notna(bought_t5)
            ) else None

            daily_rows.append({
                "trade_date":       trade_date,
                "lhb_count":        len(day_group),
                "best_code":        best_row["code"],
                "best_name":        best_row["name"],
                "best_t5":          best_row["t5_ret"],
                "best_signal_rank": best_signal_rank,
                "bought_code":      bought_code,
                "bought_name":      bought_name,
                "bought_t5":        bought_t5,
                "is_optimal":       is_optimal,
                "missed_gap":       round(missed_gap, 2) if missed_gap is not None else None,
            })

        daily_df = pd.DataFrame(daily_rows)
        # 将 trade_date "20260326" → "2026-03-26"，防止 Plotly 将其识别为数字
        daily_df["trade_date"] = daily_df["trade_date"].apply(
            lambda d: f"{str(d)[:4]}-{str(d)[4:6]}-{str(d)[6:]}" if len(str(d)) == 8 else str(d)
        )
        traded_days = daily_df[daily_df["bought_code"].notna()]

        summary = {
            "total_days":    len(daily_df),
            "traded_days":   len(traded_days),
            "optimal_hits":  int(traded_days["is_optimal"].sum()) if not traded_days.empty else 0,
            "avg_missed":    round(traded_days["missed_gap"].mean(), 2) if not traded_days.empty and traded_days["missed_gap"].notna().any() else None,
        }

        return {"daily_df": daily_df, "summary": summary}

    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"_analyze_missed_opportunities 失败: {e}")
        return None


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
