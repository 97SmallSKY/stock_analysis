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
                   profit_pct, sell_type, sell_date, buy_date
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


# ── Tab1：同天候选对比（基于 lhb_top10）────────────────────────────────────────

def render_tab1():
    st.subheader("同天龙虎榜候选股对比")
    st.caption("数据来源：龙虎榜 Top10 历史收益（东方财富 `上榜后N日`）")

    dates = _get_lhb_dates()
    if not dates:
        st.warning("暂无龙虎榜数据，请先运行 daily_update step1")
        return

    col1, col2 = st.columns([3, 1])
    with col1:
        selected_date = st.selectbox("选择龙虎榜日期", dates, key="tab1_date")
    with col2:
        st.write("")
        refresh = st.button("刷新", key="tab1_refresh")

    cache_key = f"tab1_{selected_date}"
    if refresh or cache_key not in st.session_state:
        df = _get_lhb_stocks(selected_date)
        bought_code = _get_bought_code(selected_date)
        st.session_state[cache_key] = (df, bought_code)
    else:
        df, bought_code = st.session_state[cache_key]

    if df.empty:
        st.warning(f"{selected_date} 无龙虎榜数据")
        return

    # 标记是否买入
    df["是否买入"] = df["代码"].astype(str) == str(bought_code or "")

    # 检查 T+N 数据是否填充
    has_t1  = df["上榜后1日"].notna().any()
    has_t5  = df["上榜后5日"].notna().any()
    has_t10 = df["上榜后10日"].notna().any()

    # ── 指标卡 ────────────────────────────────────────────────────────────────
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.metric("龙虎榜股票数", len(df))
    with c2:
        if has_t5:
            avg_t5 = df["上榜后5日"].mean()
            st.metric("T+5 平均涨幅", _fmt_pct(avg_t5))
        else:
            st.metric("T+5 平均涨幅", "待结算")
    with c3:
        if has_t5:
            best_row = df.loc[df["上榜后5日"].idxmax()]
            st.metric(
                "最优股 T+5",
                _fmt_pct(best_row["上榜后5日"]),
                delta=f"{best_row['名称']}({best_row['代码']})",
            )
        else:
            st.metric("最优股 T+5", "待结算")
    with c4:
        if bought_code and has_t5:
            bought_rows = df[df["是否买入"]]
            if not bought_rows.empty:
                bought_t5 = bought_rows.iloc[0]["上榜后5日"]
                best_t5 = df["上榜后5日"].max()
                diff = (best_t5 - bought_t5) if pd.notna(bought_t5) else None
                st.metric(
                    "买入 vs 最优差距",
                    _fmt_pct(diff),
                    delta=f"买入:{_fmt_pct(bought_t5)}",
                    delta_color="inverse" if (diff or 0) > 0 else "normal",
                )
            else:
                st.metric("买入 vs 最优差距", "—")
        elif not bought_code:
            st.metric("买入股", "本日未买入")
        else:
            st.metric("买入 vs 最优差距", "待结算")

    # ── 买入股详情卡 ───────────────────────────────────────────────────────────
    if bought_code:
        bought_rows = df[df["是否买入"]]
        if not bought_rows.empty:
            br = bought_rows.iloc[0]
            st.info(
                f"**本日买入股：{br['名称']}（{br['代码']}）** | "
                f"上榜收盘价：¥{br['收盘价']:.2f} | "
                f"当日涨跌：{_fmt_pct(br.get('涨跌幅'))} | "
                f"T+1：{_fmt_pct(br.get('上榜后1日'))} | "
                f"T+5：{_fmt_pct(br.get('上榜后5日'))} | "
                f"上榜原因：{str(br.get('上榜原因') or '—')[:40]}"
            )
    else:
        st.caption(f"📋 {selected_date} 无模拟买入记录（仅展示龙虎榜对比）")

    if not has_t1:
        st.info(f"⏳ {selected_date} 的 T+N 收益尚未到结算日，将在上榜后1~10个交易日内自动更新")

    # ── 归一化 K 线多股对比图（前1个月 ~ 上榜后~15交易日）─────────────────────
    st.subheader("近1个月走势对比（以上榜日收盘价归一化）")
    st.caption("所有股票以上榜当天（T+0）收盘价为基准（=0%），展示前后走势；蓝色粗线 = 本次买入股")

    kline_cache_key = f"tab1_klines_{selected_date}"
    if refresh or kline_cache_key not in st.session_state:
        kline_dict = _fetch_lhb_klines(df, selected_date)
        st.session_state[kline_cache_key] = kline_dict
    else:
        kline_dict = st.session_state[kline_cache_key]

    if kline_dict:
        # T+0 日期格式化为 YYYY-MM-DD（用于 X 轴标注）
        t0_fmt = f"{selected_date[:4]}-{selected_date[4:6]}-{selected_date[6:]}"

        fig_kline = go.Figure()
        for _, row in df.iterrows():
            code_str  = str(row["代码"])
            name_str  = str(row["名称"])
            is_bought = bool(row["是否买入"])
            kl = kline_dict.get(code_str)
            if kl is None or kl.empty or "收盘" not in kl.columns:
                continue

            # 找到 T+0 当天的收盘价作为基准
            t0_rows = kl[kl["日期"] == selected_date]
            if t0_rows.empty:
                # T+0 停牌，取最近一个交易日
                before = kl[kl["日期"] <= selected_date]
                if before.empty:
                    continue
                base_price = float(before.iloc[-1]["收盘"])
            else:
                base_price = float(t0_rows.iloc[0]["收盘"])

            if base_price <= 0:
                continue

            # 归一化：相对 T+0 收盘价的涨跌幅 %
            kl = kl.copy()
            kl["归一化%"] = (kl["收盘"] / base_price - 1) * 100
            # X 轴：YYYY-MM-DD 格式
            kl["date_fmt"] = kl["日期"].apply(
                lambda d: f"{str(d)[:4]}-{str(d)[4:6]}-{str(d)[6:]}" if len(str(d)) == 8 else str(d)
            )

            label = f"{name_str}({code_str})"
            fig_kline.add_trace(go.Scatter(
                x=kl["date_fmt"],
                y=kl["归一化%"],
                mode="lines",
                name=label,
                line={
                    "width": 3   if is_bought else 1.2,
                    "color": _BLUE if is_bought else None,
                    "dash":  "solid" if is_bought else "solid",
                },
                opacity=1.0 if is_bought else 0.55,
                hovertemplate=f"<b>{label}</b><br>%{{x}}<br>相对T+0: %{{y:+.2f}}%<extra></extra>",
            ))

        # T+0 竖线标注（用 add_shape 替代 add_vline，避免 Plotly 对字符串 x 值报 TypeError）
        fig_kline.add_shape(
            type="line",
            x0=t0_fmt, x1=t0_fmt,
            y0=0, y1=1,
            xref="x", yref="paper",
            line={"color": _GOLD, "width": 2, "dash": "dash"},
        )
        fig_kline.add_annotation(
            x=t0_fmt, y=1,
            xref="x", yref="paper",
            text="上榜日",
            showarrow=False,
            font={"color": _GOLD, "size": 12},
            xanchor="left",
            yanchor="top",
        )
        fig_kline.add_hline(y=0, line_dash="dot", line_color=_GRAY, opacity=0.4)

        legend_note = f"蓝色粗线 = 买入股({bought_code})  |  其余为同天龙虎榜股票" if bought_code else "展示同天龙虎榜所有股票走势"
        fig_kline.update_layout(
            title=f"{selected_date} 龙虎榜股票近1个月走势（以上榜日收盘价归一化）",
            xaxis_title="日期",
            yaxis_title="相对上榜日收盘价涨跌幅 %",
            legend={"orientation": "v", "x": 1.01, "y": 1},
            hovermode="x unified",
            height=580,
            annotations=[dict(
                text=legend_note,
                xref="paper", yref="paper",
                x=0.01, y=1.04, showarrow=False,
                font={"size": 11, "color": _BLUE if bought_code else _GRAY},
            )],
        )
        st.plotly_chart(fig_kline, use_container_width=True)
    else:
        st.warning("K 线数据获取失败，请检查网络或稍后刷新")

    # ── 横向对比柱状图（优先 T+5，降级用 T+1）────────────────────────────────
    bar_col    = "上榜后5日" if has_t5 else ("上榜后1日" if has_t1 else None)
    bar_label  = "T+5" if has_t5 else "T+1"

    if bar_col:
        st.subheader(f"{bar_label} 收益横向对比" + ("" if has_t5 else "（T+5 待结算，暂用 T+1 对比）"))
        df_sorted = df.sort_values(bar_col, ascending=False).copy()

        colors, text_labels = [], []
        best_idx = df_sorted[bar_col].idxmax() if df_sorted[bar_col].notna().any() else None
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

        fig_bar = go.Figure(go.Bar(
            x=df_sorted["名称"] + "<br>" + df_sorted["代码"],
            y=df_sorted[bar_col],
            marker_color=colors,
            text=text_labels,
            textposition="outside",
            hovertemplate="<b>%{x}</b><br>" + bar_label + ": %{y:+.2f}%<extra></extra>",
        ))
        fig_bar.add_hline(y=0, line_dash="dash", line_color=_GRAY, opacity=0.5)
        fig_bar.update_layout(
            title=f"{selected_date} {bar_label} 收益横向对比（蓝=买入 ★=最优）",
            yaxis_title=f"{bar_label} 涨跌幅 %",
            xaxis_tickangle=-30,
            height=420,
        )
        st.plotly_chart(fig_bar, use_container_width=True)

    # ── 详细数据表 ─────────────────────────────────────────────────────────────
    st.subheader("详细数据")
    display_df = df[[
        "代码", "名称", "收盘价", "涨跌幅",
        "上榜后1日", "上榜后2日", "上榜后5日", "上榜后10日",
        "龙虎榜净买额", "换手率", "上榜原因", "是否买入",
    ]].copy()

    # 净买额换算成万元
    if "龙虎榜净买额" in display_df.columns:
        display_df["净买额(万)"] = (display_df["龙虎榜净买额"] / 1e4).round(0)
        display_df = display_df.drop(columns=["龙虎榜净买额"])

    # 重命名
    display_df = display_df.rename(columns={
        "涨跌幅":    "T+0涨跌%",
        "上榜后1日": "T+1%",
        "上榜后2日": "T+2%",
        "上榜后5日": "T+5%",
        "上榜后10日":"T+10%",
        "换手率":    "换手%",
    })

    def _style_row(row):
        if row.get("是否买入"):
            return ["background-color: #1a2a4a; font-weight:bold"] * len(row)
        return [""] * len(row)

    st.dataframe(
        display_df.style.apply(_style_row, axis=1),
        use_container_width=True,
        hide_index=True,
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
    buy_date   = str(trade_row.get("buy_date") or trade_date)
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
