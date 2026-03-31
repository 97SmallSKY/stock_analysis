"""
主板精选策略页面 v3
==================
新增功能：
  1. 市值筛选（流通市值）：<100亿 / 100~300亿 / 300~1000亿 / >1000亿 / 全部
  2. 换手率档位（量比替代指标）：<1% / 1~3% / 3~7% / >7% / 全部
  3. TopN 扩选：10 / 20 / 30 / 40 / 50
  4. 多维排序：净买入额 / 流通市值 / 换手率 / 涨跌幅
  5. 分档盈利概率：+2% / +3% / +4% / +5% 的概率统计

策略逻辑：
  龙虎榜净买入 Top N → 主板过滤 → 用户筛选条件 → 净买入 No.1
  T+1 开盘集合竞价买入 → T+2 冲高走人

数据说明：
  - 市值 = 流通市值（lhb_top10 自带，单位：元）
  - 换手率 = lhb_top10 自带（%），量比 ≈ 换手率 / 近5日均换手率，因无均值数据，
    用换手率档位作为流动性活跃度的替代指标
"""
import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime, date, timedelta

from utils.storage import storage

try:
    from st_aggrid import AgGrid, GridOptionsBuilder, GridUpdateMode, DataReturnMode, JsCode
    _HAS_AGGRID = True
except ImportError:
    _HAS_AGGRID = False


# ── 主板判断 ──────────────────────────────────────────────────────────
def _is_mainboard(code: str) -> bool:
    c = str(code).zfill(6)
    if c.startswith("688"):
        return False
    if c.startswith("6"):
        return True
    if c[:3] in ("000", "001", "002", "003"):
        return True
    return False


def _board_label(code: str) -> str:
    c = str(code).zfill(6)
    if c.startswith("688"):  return "科创板"
    if c.startswith("6"):    return "沪主板 ✅"
    if c[:3] in ("000", "001", "002", "003"): return "深主板 ✅"
    if c.startswith("3"):    return "创业板"
    if c[0] in ("8", "4") or c[:2] in ("11", "12") or c[:2] == "92": return "北交所"
    return "其他"


# ── 市值分档 ──────────────────────────────────────────────────────────
def _mcap_label(mcap_yuan: float) -> str:
    """流通市值（元）→ 分档标签"""
    if pd.isna(mcap_yuan) or mcap_yuan <= 0:
        return "未知"
    mcap_yi = mcap_yuan / 1e8  # 转亿
    if mcap_yi < 100:    return "小盘 (<100亿)"
    if mcap_yi < 300:    return "中盘 (100~300亿)"
    if mcap_yi < 1000:   return "大盘 (300~1000亿)"
    return "超大盘 (>1000亿)"


# ── 换手率分档 ────────────────────────────────────────────────────────
def _turnover_label(turnover_pct: float) -> str:
    """换手率(%) → 分档标签（作为量比活跃度替代）"""
    if pd.isna(turnover_pct) or turnover_pct <= 0:
        return "未知"
    if turnover_pct < 1:   return "低换手 (<1%)"
    if turnover_pct < 3:   return "正常 (1~3%)"
    if turnover_pct < 7:   return "活跃 (3~7%)"
    return "高换手 (>7%)"


# ── 数据加载（核心，带缓存）────────────────────────────────────────────
@st.cache_data(ttl=300, show_spinner=False)
def _load_raw_data() -> tuple:
    """原始数据加载（缓存5分钟）"""
    top10  = storage.read("lhb_top10")
    prices = storage.read("lhb_top10_prices")
    return top10, prices


def _build_pool(top10: pd.DataFrame, top_n: int) -> pd.DataFrame:
    """
    构建每日候选池：每日净买入前 top_n → 主板过滤 → 附加分档标签
    """
    top10 = top10.copy()
    top10["代码"] = top10["代码"].astype(str).str.zfill(6)
    top10["龙虎榜净买额"] = pd.to_numeric(top10["龙虎榜净买额"], errors="coerce")
    top10["流通市值"]     = pd.to_numeric(top10["流通市值"],     errors="coerce")
    top10["换手率"]       = pd.to_numeric(top10["换手率"],       errors="coerce")
    top10["涨跌幅"]       = pd.to_numeric(top10["涨跌幅"],       errors="coerce")

    # 每日 top_n（按净买额降序）
    # 使用 rank 方式替代 groupby().apply(g.head())，避免 FutureWarning 且不丢失分组列
    top10_sorted = top10.sort_values(["trade_date", "龙虎榜净买额"], ascending=[True, False])
    top10_sorted["_rank"] = top10_sorted.groupby("trade_date")["龙虎榜净买额"].rank(
        method="first", ascending=False
    )
    pool = top10_sorted[top10_sorted["_rank"] <= top_n].drop(columns=["_rank"]).reset_index(drop=True)

    # 主板过滤
    pool["is_main"]   = pool["代码"].apply(_is_mainboard)
    pool["板块"]       = pool["代码"].apply(_board_label)
    pool["市值档位"]   = pool["流通市值"].apply(_mcap_label)
    pool["换手率档位"] = pool["换手率"].apply(_turnover_label)
    pool["流通市值亿"] = (pool["流通市值"] / 1e8).round(1)
    pool["净买入亿"]   = (pool["龙虎榜净买额"] / 1e8).round(2)

    pool = pool[pool["is_main"]].copy()

    # ── 综合评分（纯本地字段，无远程请求，缓存安全）──────────────────────────
    # 综合评分 = 净买额占比×30 + 换手率活跃×25 + 涨跌幅强度×20 + 市值适中×15 + 振幅适当×10
    def _calc_composite_score(row: pd.Series) -> float:
        """基于 lhb_top10 本地字段计算综合评分（0~100），无任何远程请求"""
        s1 = s2 = s3 = s4 = s5 = 0.0
        # 1. 净买额占比分（净买额 / 流通市值）
        net = float(row.get("龙虎榜净买额", 0) or 0)
        mcap = float(row.get("流通市值", 0) or 1e12)  # 避免除零，默认给超大市值
        if mcap > 0:
            pct_ratio = net / mcap * 100  # %
            if pct_ratio >= 3:
                s1 = 100.0
            elif pct_ratio >= 1:
                s1 = 50 + (pct_ratio - 1) / 2 * 50   # 1%~3% → 50~100
            elif pct_ratio >= 0.5:
                s1 = pct_ratio / 0.5 * 50             # 0.5%~1% → 0~50
        # 2. 换手率活跃分
        tr = float(row.get("换手率", 0) or 0)
        if 3 <= tr <= 7:
            s2 = 100.0
        elif 1 <= tr < 3 or 7 < tr <= 10:
            s2 = 70.0
        elif tr > 0:
            s2 = 30.0
        # 3. 涨跌幅强度分
        chg = float(row.get("涨跌幅", 0) or 0)
        if 3 <= chg <= 7:
            s3 = 100.0
        elif 1 <= chg < 3:
            s3 = 70.0
        elif 0 <= chg < 1:
            s3 = 40.0
        elif chg < 0:
            s3 = 0.0
        # 4. 市值适中分
        mcap_yi = mcap / 1e8
        if 50 <= mcap_yi < 300:
            s4 = 100.0
        elif 300 <= mcap_yi < 1000:
            s4 = 70.0
        elif mcap_yi < 50:
            s4 = 50.0
        else:
            s4 = 40.0
        # 5. 振幅合理分（若无振幅列则默认 0）
        amp = float(row.get("振幅", 0) or 0)
        if amp > 0:
            if 3 <= amp <= 8:
                s5 = 100.0
            elif 1 <= amp < 3:
                s5 = 60.0
            elif amp > 10:
                s5 = 30.0
            else:
                s5 = 50.0
        composite = s1 * 0.30 + s2 * 0.25 + s3 * 0.20 + s4 * 0.15 + s5 * 0.10
        return round(composite, 1)

    pool["综合评分"]   = pool.apply(_calc_composite_score, axis=1)
    pool["净买额占比%"] = (pool["龙虎榜净买额"] / pool["流通市值"].clip(lower=1) * 100).round(2)

    return pool


def _apply_filters(
    pool: pd.DataFrame,
    mcap_filter: str,
    turnover_filter: str,
    sort_by: str,
) -> pd.DataFrame:
    """按用户选择的条件筛选 + 排序，每日取 No.1"""
    df = pool.copy()

    # 市值筛选
    if mcap_filter != "全部":
        df = df[df["市值档位"] == mcap_filter]

    # 换手率筛选
    if turnover_filter != "全部":
        df = df[df["换手率档位"] == turnover_filter]

    if df.empty:
        return pd.DataFrame()

    # 排序（决定每日选哪只）
    asc = False
    if sort_by == "净买入额（主力意志）":
        sort_col = "龙虎榜净买额"
    elif sort_by == "流通市值（从小到大）":
        sort_col = "流通市值"
        asc = True
    elif sort_by == "流通市值（从大到小）":
        sort_col = "流通市值"
        asc = False
    elif sort_by == "换手率（活跃度↓）":
        sort_col = "换手率"
        asc = False
    elif sort_by == "涨跌幅（强势度↓）":
        sort_col = "涨跌幅"
        asc = False
    elif sort_by == "综合评分（推荐）★":
        sort_col = "综合评分"
        asc = False
    elif sort_by == "净买额占比%（主力密度↓）":
        sort_col = "净买额占比%"
        asc = False
    else:
        sort_col = "龙虎榜净买额"

    daily_top1 = (
        df.sort_values(sort_col, ascending=asc)
        .groupby("trade_date")
        .first()
        .reset_index()
    )
    return daily_top1


def _build_result(daily_top1: pd.DataFrame, prices: pd.DataFrame) -> pd.DataFrame:
    """合并价格，计算收益率"""
    if daily_top1.empty or prices.empty:
        return pd.DataFrame()

    prices = prices.copy()
    prices["代码"] = prices["代码"].astype(str).str.zfill(6)

    merged = daily_top1.merge(prices, on=["代码", "trade_date"], how="inner")

    for col in ["T+1买入价", "T+2最高价", "T+2收盘价",
                "T+1最高价", "T+2最低价"]:
        if col in merged.columns:
            merged[col] = pd.to_numeric(merged[col], errors="coerce")

    bp = merged["T+1买入价"]
    merged["买入价"]    = bp
    merged["冲高收益%"] = ((merged["T+2最高价"] / bp) - 1) * 100
    merged["收盘收益%"] = ((merged["T+2收盘价"] / bp) - 1) * 100

    # 透传综合评分字段
    for col in ["综合评分", "净买额占比%"]:
        if col in daily_top1.columns and col not in merged.columns:
            try:
                idx_map = daily_top1.set_index(["代码", "trade_date"])[col]
                merged[col] = merged.apply(
                    lambda r: idx_map.get((r["代码"], r["trade_date"]), None), axis=1
                )
            except Exception:
                pass

    # T+2 最高价 vs 买入价的分档 flag（用于概率统计）
    for thresh in [2, 3, 4, 5]:
        merged[f"达{thresh}%"] = merged["冲高收益%"] >= thresh

    merged["月份"] = merged["trade_date"].astype(str).str[:4] + "-" + merged["trade_date"].astype(str).str[4:6]
    valid = merged.dropna(subset=["买入价", "T+2最高价"]).copy()
    valid["冲高收益%"] = valid["冲高收益%"].round(2)
    valid["收盘收益%"] = valid["收盘收益%"].round(2)
    return valid


# ── 分档概率统计 ──────────────────────────────────────────────────────
def _calc_threshold_probs(valid: pd.DataFrame) -> pd.DataFrame:
    """计算冲高收益 ≥ 各阈值的概率"""
    n = len(valid)
    if n == 0:
        return pd.DataFrame()
    rows = []
    for t in [0, 1, 2, 3, 4, 5]:
        cnt = (valid["冲高收益%"] >= t).sum()
        rows.append({
            "目标收益": f"≥{t}%",
            "达标笔数": int(cnt),
            "总笔数":   n,
            "概率":     round(cnt / n * 100, 1),
        })
    return pd.DataFrame(rows)


# ── 带筛选控件的表格渲染 ────────────────────────────────────────────────
def _filterable_table(
    df: pd.DataFrame,
    key: str,
    height: int = 500,
    filter_cols: list = None,
    num_cols: list = None,
    pct_cols: list = None,
    default_sort_col: str = None,
    default_sort_asc: bool = False,
) -> None:
    """
    带内置筛选控件的表格：
      - filter_cols：显示 multiselect 下拉多选框
      - 全局文本搜索框
      - 数字列格式化
      - 百分比列颜色标注
    """
    filter_cols = filter_cols or []
    num_cols    = num_cols    or []
    pct_cols    = pct_cols    or []

    display = df.copy()

    if default_sort_col and default_sort_col in display.columns:
        display = display.sort_values(default_sort_col, ascending=default_sort_asc)

    with st.expander("🔍 表格筛选 / 搜索", expanded=False):
        search_key = f"{key}_search"
        search_text = st.text_input(
            "🔎 全局搜索（任意列）",
            key=search_key,
            placeholder="输入关键字过滤行...",
            label_visibility="visible",
        )
        if search_text.strip():
            mask = display.astype(str).apply(
                lambda col: col.str.contains(search_text.strip(), case=False, na=False)
            ).any(axis=1)
            display = display[mask]

        fc_in_df = [c for c in filter_cols if c in display.columns]
        if fc_in_df:
            ncols = min(len(fc_in_df), 4)
            cols_ = st.columns(ncols)
            for i, col in enumerate(fc_in_df):
                unique_vals = sorted(display[col].dropna().unique().tolist(), key=str)
                sel = cols_[i % ncols].multiselect(
                    f"{col}",
                    options=unique_vals,
                    default=[],
                    key=f"{key}_filter_{col}",
                    placeholder=f"全部 {col}",
                )
                if sel:
                    display = display[display[col].isin(sel)]

        st.caption(f"当前显示 {len(display)} 行（共 {len(df)} 行）")

    col_config = {}
    for c in num_cols:
        if c in display.columns:
            col_config[c] = st.column_config.NumberColumn(c, format="%.2f")
    for c in pct_cols:
        if c in display.columns:
            col_config[c] = st.column_config.NumberColumn(c, format="%.2f")

    st.dataframe(
        display.reset_index(drop=True),
        use_container_width=True,
        hide_index=True,
        height=height,
        column_config=col_config if col_config else None,
    )


# ── 图表函数 ──────────────────────────────────────────────────────────
def _plot_threshold_bar(valid: pd.DataFrame, key: str = "threshold_bar"):
    """分档概率柱状图（≥0% ~ ≥5%）"""
    thresholds = [0, 1, 2, 3, 4, 5]
    n = len(valid)
    probs = [round((valid["冲高收益%"] >= t).sum() / n * 100, 1) for t in thresholds]
    colors = ["#66bb6a", "#42a5f5", "#ffa726", "#ef5350", "#ab47bc", "#26c6da"]

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=[f"≥{t}%" for t in thresholds],
        y=probs,
        marker_color=colors,
        text=[f"{p:.1f}%" for p in probs],
        textposition="outside",
        width=0.5,
    ))
    # 添加参考线
    for ref_y, ref_label, ref_color in [
        (80, "80%", "rgba(100,200,100,0.4)"),
        (50, "50%", "rgba(200,200,100,0.4)"),
    ]:
        fig.add_hline(y=ref_y, line_dash="dot", line_color=ref_color,
                      annotation_text=ref_label, annotation_position="right")

    fig.update_layout(
        title="T+2 冲高收益分档概率（T+1开盘买入，T+2日内最高收益达标率）",
        xaxis_title="目标收益阈值",
        yaxis_title="达标概率 %",
        yaxis=dict(range=[0, 110]),
        height=400,
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#e0e0e0"), showlegend=False,
    )
    st.plotly_chart(fig, use_container_width=True, key=key)


def _plot_prob_by_mcap(valid: pd.DataFrame, key: str = "prob_mcap"):
    """按市值档位分组的 ≥3% 概率对比"""
    if "市值档位" not in valid.columns:
        return
    groups = valid.groupby("市值档位")
    labels, cnts, probs3, probs5 = [], [], [], []
    order = ["小盘 (<100亿)", "中盘 (100~300亿)", "大盘 (300~1000亿)", "超大盘 (>1000亿)", "未知"]
    for lbl in order:
        if lbl not in groups.groups:
            continue
        g = groups.get_group(lbl)
        if len(g) < 3:
            continue
        labels.append(lbl)
        cnts.append(len(g))
        probs3.append(round((g["冲高收益%"] >= 3).mean() * 100, 1))
        probs5.append(round((g["冲高收益%"] >= 5).mean() * 100, 1))

    if not labels:
        return

    fig = go.Figure()
    fig.add_trace(go.Bar(
        name="≥3% 概率", x=labels, y=probs3,
        marker_color="#42a5f5",
        text=[f"{p}%\n({c}笔)" for p, c in zip(probs3, cnts)],
        textposition="outside",
    ))
    fig.add_trace(go.Bar(
        name="≥5% 概率", x=labels, y=probs5,
        marker_color="#ffa726",
        text=[f"{p}%" for p in probs5],
        textposition="outside",
    ))
    fig.update_layout(
        title="不同市值档位的冲高达标率（筛选条件下）",
        barmode="group", height=380,
        yaxis=dict(range=[0, 110]),
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#e0e0e0"),
    )
    st.plotly_chart(fig, use_container_width=True, key=key)


def _plot_prob_by_turnover(valid: pd.DataFrame, key: str = "prob_turnover"):
    """按换手率档位分组的达标率对比"""
    if "换手率档位" not in valid.columns:
        return
    groups = valid.groupby("换手率档位")
    labels, probs3, probs5, cnts = [], [], [], []
    order = ["低换手 (<1%)", "正常 (1~3%)", "活跃 (3~7%)", "高换手 (>7%)"]
    for lbl in order:
        if lbl not in groups.groups:
            continue
        g = groups.get_group(lbl)
        if len(g) < 3:
            continue
        labels.append(lbl)
        cnts.append(len(g))
        probs3.append(round((g["冲高收益%"] >= 3).mean() * 100, 1))
        probs5.append(round((g["冲高收益%"] >= 5).mean() * 100, 1))

    if not labels:
        return

    fig = go.Figure()
    fig.add_trace(go.Bar(
        name="≥3% 概率", x=labels, y=probs3, marker_color="#42a5f5",
        text=[f"{p}%\n({c}笔)" for p, c in zip(probs3, cnts)],
        textposition="outside",
    ))
    fig.add_trace(go.Bar(
        name="≥5% 概率", x=labels, y=probs5, marker_color="#ffa726",
        text=[f"{p}%" for p in probs5], textposition="outside",
    ))
    fig.update_layout(
        title="不同换手率档位的冲高达标率",
        barmode="group", height=380,
        yaxis=dict(range=[0, 110]),
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#e0e0e0"),
    )
    st.plotly_chart(fig, use_container_width=True, key=key)


def _plot_cumulative(valid: pd.DataFrame, key: str = "cumulative"):
    df = valid.sort_values("trade_date").copy()
    df["冲高净值"] = (1 + df["冲高收益%"] / 100).cumprod()
    df["收盘净值"] = (1 + df["收盘收益%"] / 100).cumprod()
    
    x_dates = df["trade_date"].astype(str).apply(
        lambda d: f"{d[:4]}.{d[4:6]}.{d[6:]}" if len(d)==8 else d
    )
    
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=x_dates, y=df["冲高净值"],
        mode="lines+markers", name="冲高走人",
        line=dict(color="#ef5350", width=2), marker=dict(size=4),
    ))
    fig.add_trace(go.Scatter(
        x=x_dates, y=df["收盘净值"],
        mode="lines+markers", name="持有到收盘",
        line=dict(color="#26a69a", width=2, dash="dot"), marker=dict(size=4),
    ))
    fig.add_hline(y=1.0, line_color="gray", line_width=1, line_dash="dash")
    fig.update_layout(
        title="策略累计净值（满仓复利）",
        xaxis_title="上榜日", yaxis_title="净值",
        height=360, plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#e0e0e0"), legend=dict(x=0.01, y=0.99),
        xaxis=dict(type="category", tickangle=-45, nticks=15),
    )
    st.plotly_chart(fig, use_container_width=True, key=key)


def _plot_bar_returns(valid: pd.DataFrame, key: str = "bar_returns"):
    df = valid.sort_values("trade_date").copy()
    name_col = "名称" if "名称" in df.columns else "代码"
    colors = ["#ef5350" if v >= 0 else "#26a69a" for v in df["冲高收益%"]]
    # trade_date: 20260327 → 03.27 简短横坐标，避免 Plotly 识别为日期
    x_labels = [
        f"{str(d)[4:6]}.{str(d)[6:]}\n{n}"
        for d, n in zip(df["trade_date"], df[name_col])
    ]
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=x_labels,
        y=df["冲高收益%"],
        marker_color=colors,
        text=[f"{v:+.1f}%" for v in df["冲高收益%"]],
        textposition="outside",
    ))
    fig.add_hline(y=0, line_color="gray", line_width=1)
    fig.update_layout(
        title="逐笔冲高收益",
        xaxis_title="上榜日 / 股票", yaxis_title="冲高收益%",
        height=400, showlegend=False,
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#e0e0e0"), xaxis=dict(tickfont=dict(size=9), type="category"),
    )
    st.plotly_chart(fig, use_container_width=True, key=key)


def _plot_monthly(valid: pd.DataFrame, key: str = "monthly"):
    monthly = (
        valid.groupby("月份")
        .agg(笔数=("冲高收益%", "count"),
             胜=("冲高收益%", lambda x: (x > 0).sum()),
             达3pct=("达3%", "sum"),
             均收益=("冲高收益%", "mean"))
        .reset_index()
    )
    monthly["胜率%"]  = monthly["胜"] / monthly["笔数"] * 100
    monthly["≥3%率"] = monthly["达3pct"] / monthly["笔数"] * 100
    # "2026-03" → "2026.03"，避免 Plotly 识别为英文日期
    monthly["月份"] = monthly["月份"].astype(str).str.replace("-", ".", regex=False)

    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.add_trace(go.Bar(
        x=monthly["月份"], y=monthly["胜率%"],
        name="冲高胜率%", marker_color="#42a5f5",
        text=[f"{v:.0f}%" for v in monthly["胜率%"]], textposition="outside",
    ), secondary_y=False)
    fig.add_trace(go.Bar(
        x=monthly["月份"], y=monthly["≥3%率"],
        name="≥3%率", marker_color="#ffa726", opacity=0.7,
        text=[f"{v:.0f}%" for v in monthly["≥3%率"]], textposition="inside",
    ), secondary_y=False)
    fig.add_trace(go.Scatter(
        x=monthly["月份"], y=monthly["均收益"],
        name="均收益%", mode="lines+markers",
        line=dict(color="#ef5350", width=2), marker=dict(size=8),
    ), secondary_y=True)
    fig.update_layout(
        title="月度统计：冲高胜率 / ≥3%达标率 / 均收益",
        height=380, barmode="group",
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#e0e0e0"), legend=dict(x=0.01, y=0.99),
        xaxis=dict(type="category"),
    )
    fig.update_yaxes(title_text="概率%", secondary_y=False, range=[0, 120])
    fig.update_yaxes(title_text="均收益%", secondary_y=True)
    st.plotly_chart(fig, use_container_width=True, key=key)


# ── 日期提示 ──────────────────────────────────────────────────────────
def _day_hint(trade_date_str: str, offset: int) -> str:
    try:
        d = datetime.strptime(str(trade_date_str), "%Y%m%d").date()
        delta = (date.today() - d).days
        target = d + timedelta(days=offset)
        if delta == 0 and offset == 1: return "明日"
        if delta == 1 and offset == 1: return "今日"
        if delta == 0 and offset == 2: return "后天"
        if delta == 1 and offset == 2: return "明日"
        return target.strftime("%m/%d")
    except Exception:
        return f"T+{offset}"


# ── 主渲染 ────────────────────────────────────────────────────────────
def render():
    st.title("🏆 主板精选策略")
    st.caption(
        "龙虎榜净买入 Top N（主板）→ 多维筛选 → **T+1 开盘买入 → T+2 冲高走人**  |  "
        "**T 日**=上榜当日 · **T+1**=次日集合竞价买入 · **T+2**=上榜后第2交易日卖出"
    )

    # ══════════════════════════════════════════
    # 侧边栏：筛选参数
    # ══════════════════════════════════════════
    with st.sidebar:
        st.divider()
        st.subheader("🔧 策略筛选参数")

        top_n = st.select_slider(
            "选股池宽度（Top N）",
            options=[10, 20, 30, 40, 50],
            value=10,
            help="从龙虎榜净买入前 N 名主板股中筛选，默认 Top10 质量最高",
        )

        st.markdown("**市值筛选**")
        mcap_filter = st.radio(
            "流通市值",
            options=["全部", "小盘 (<100亿)", "中盘 (100~300亿)",
                     "大盘 (300~1000亿)", "超大盘 (>1000亿)"],
            index=0,
            help="基于上榜日的流通市值分档",
            label_visibility="collapsed",
        )

        st.markdown("**换手率档位**（量比替代指标）")
        turnover_filter = st.radio(
            "换手率",
            options=["全部", "低换手 (<1%)", "正常 (1~3%)",
                     "活跃 (3~7%)", "高换手 (>7%)"],
            index=0,
            help="上榜日当天换手率，反映当日成交活跃度（量比 ≈ 换手率/均换手率，此处直接用换手率分档）",
            label_visibility="collapsed",
        )

        st.markdown("**排序依据**（决定每日选哪只）")
        sort_by = st.radio(
            "排序",
            options=[
                "净买入额（主力意志）",
                "综合评分（推荐）★",
                "净买额占比%（主力密度↓）",
                "换手率（活跃度↓）",
                "流通市值（从小到大）",
                "流通市值（从大到小）",
                "涨跌幅（强势度↓）",
            ],
            index=0,
            label_visibility="collapsed",
            help="综合评分 = 净买额占比×30% + 换手率×25% + 涨跌幅×20% + 市值适中×15% + 振幅×10%，全为本地字段计算，无远程请求",
        )

        st.caption(
            f"当前：Top{top_n} | {mcap_filter} | {turnover_filter} | {sort_by}"
        )

    # ══════════════════════════════════════════
    # 加载 & 构建数据
    # ══════════════════════════════════════════
    with st.spinner("加载数据..."):
        top10_raw, prices = _load_raw_data()

    if top10_raw.empty:
        st.error("暂无龙虎榜数据，请先在「⚙️ 数据管理」拉取")
        return

    pool      = _build_pool(top10_raw, top_n)
    daily_top1 = _apply_filters(pool, mcap_filter, turnover_filter, sort_by)
    valid      = _build_result(daily_top1, prices)

    total_days = top10_raw["trade_date"].nunique()
    date_min   = str(top10_raw["trade_date"].min())
    date_max   = str(top10_raw["trade_date"].max())

    # ══════════════════════════════════════════
    # 🌟 置顶：今日首选标的
    # ══════════════════════════════════════════
    try:
        latest_date = top10_raw["trade_date"].max()
        top10_raw_c = top10_raw.copy()
        top10_raw_c["代码"] = top10_raw_c["代码"].astype(str).str.zfill(6)
        top10_raw_c["龙虎榜净买额"] = pd.to_numeric(top10_raw_c["龙虎榜净买额"], errors="coerce")
        top10_raw_c["流通市值"]     = pd.to_numeric(top10_raw_c["流通市值"], errors="coerce")
        top10_raw_c["换手率"]       = pd.to_numeric(top10_raw_c["换手率"], errors="coerce")

        today_pool = (
            top10_raw_c[top10_raw_c["trade_date"] == latest_date]
            .sort_values("龙虎榜净买额", ascending=False)
            .head(top_n)
            .copy()
        )
        today_pool["is_main"]   = today_pool["代码"].apply(_is_mainboard)
        today_pool["板块"]       = today_pool["代码"].apply(_board_label)
        today_pool["市值档位"]   = today_pool["流通市值"].apply(_mcap_label)
        today_pool["换手率档位"] = today_pool["换手率"].apply(_turnover_label)
        today_pool["流通市值亿"] = (today_pool["流通市值"] / 1e8).round(1)
        today_pool["净买入亿"]   = (today_pool["龙虎榜净买额"] / 1e8).round(2)
        main_today = today_pool[today_pool["is_main"]].copy()

        if mcap_filter != "全部":
            main_today = main_today[main_today["市值档位"] == mcap_filter]
        if turnover_filter != "全部":
            main_today = main_today[main_today["换手率档位"] == turnover_filter]

        if not main_today.empty:
            # 为今日候选池也计算综合评分（复用 _build_pool 里的 _calc_composite_score 逻辑）
            today_pool_full = _build_pool(top10_raw_c[top10_raw_c["trade_date"] == latest_date], top_n)
            score_map = {}
            if not today_pool_full.empty and "综合评分" in today_pool_full.columns:
                score_map = dict(zip(today_pool_full["代码"], today_pool_full["综合评分"]))
                pct_map   = dict(zip(today_pool_full["代码"], today_pool_full.get("净买额占比%", pd.Series())))

            sort_map = {
                "净买入额（主力意志）":          ("龙虎榜净买额", False),
                "综合评分（推荐）★":             ("综合评分",   False),
                "净买额占比%（主力密度↓）":       ("净买额占比%", False),
                "换手率（活跃度↓）":             ("换手率",     False),
                "流通市值（从小到大）":           ("流通市值",   True),
                "流通市值（从大到小）":           ("流通市值",   False),
                "涨跌幅（强势度↓）":             ("涨跌幅",     False),
            }
            # 附上评分（如有）
            if score_map:
                main_today["综合评分"]   = main_today["代码"].map(score_map)
                main_today["净买额占比%"] = main_today["代码"].map(pct_map)

            sc_key = sort_by
            sc, sa = sort_map.get(sc_key, ("龙虎榜净买额", False))
            # 如果排序列不存在，降级到净买入额
            if sc not in main_today.columns:
                sc, sa = "龙虎榜净买额", False
            main_today = main_today.sort_values(sc, ascending=sa).head(5)
            main_today["建议操作"] = [
                f"🎯 {_day_hint(latest_date, 1)} 开盘集合竞价" if i == 0 else "👀 备选"
                for i in range(len(main_today))
            ]

            top1 = main_today.iloc[0]
            # 综合评分行（如有）
            score_line = ""
            if "综合评分" in top1.index and pd.notna(top1.get("综合评分")):
                pct_v = top1.get("净买额占比%", None)
                pct_str = f" | 净买额占比 <b>{pct_v:.2f}%</b>" if pct_v and pd.notna(pct_v) else ""
                score_line = f"<div style='color:#FFD700;font-size:1em;margin-bottom:6px;'>⭐ 综合评分 <b>{top1['综合评分']:.1f}</b>{pct_str}</div>"
            st.markdown(
                f"<div style='padding:20px;background:#1a1a2e;border-left:8px solid #E84040;border-radius:8px;margin-bottom:20px;'>"
                f"<h2 style='margin-top:0;color:#E84040;'>🏆 {latest_date} 首选主板标的：{top1.get('名称','')}（{top1['代码']}）</h2>"
                f"{score_line}"
                f"<div style='font-size:1.15em;margin-bottom:10px;'>"
                f"<b>{top1.get('板块','')}</b> | 净买入 <b>{top1['净买入亿']:.2f} 亿</b> | 流通市值 <b>{top1['流通市值亿']:.0f} 亿</b> | 换手率 <b>{top1['换手率']:.2f}%</b>"
                f"</div>"
                f"<div style='color:#33CC66;font-size:1.1em;'>"
                f"<b>📌 操作计划：</b>{_day_hint(latest_date, 1)} 开盘集合竞价买入，{_day_hint(latest_date, 2)} 盘中涨达 3~5% 分批止盈卖出。"
                f"</div></div>", unsafe_allow_html=True
            )

            with st.expander("👀 查看今日其他备选标的", expanded=False):
                show_cols = ["代码", "名称", "板块", "综合评分", "净买额占比%",
                             "流通市值亿", "换手率", "市值档位", "换手率档位", "净买入亿", "涨跌幅", "建议操作"]
                avcols = [c for c in show_cols if c in main_today.columns]
                _filterable_table(
                    main_today[avcols].reset_index(drop=True),
                    key="today_recommend_table",
                    height=200,
                    filter_cols=["板块", "市值档位", "换手率档位", "建议操作"],
                    num_cols=["流通市值亿", "净买入亿", "涨跌幅", "换手率", "综合评分", "净买额占比%"],
                )
        else:
            st.info(f"📅 最新龙虎榜（{latest_date}）：当前筛选条件在今日无主板标的匹配。")
    except Exception as e:
        st.error(f"今日推荐数据加载失败: {e}")

    st.divider()

    # ══════════════════════════════════════════
    # 数据范围 & 样本量 KPI
    # ══════════════════════════════════════════
    ci1, ci2, ci3, ci4 = st.columns(4)
    ci1.metric(
        "历史数据",
        f"{date_min[:4]}/{date_min[4:6]} ~ {date_max[:4]}/{date_max[4:6]}",
        delta=f"{total_days} 个交易日",
        delta_color="off",
    )
    ci2.metric(
        "当前筛选池",
        f"Top{top_n} × 主板",
        delta=f"{len(pool)} 只次 / {pool['trade_date'].nunique()} 天",
        delta_color="off",
    )
    ci3.metric(
        "筛选后候选",
        f"{len(daily_top1)} 天有信号",
        delta=f"{'筛选条件过严，无匹配' if daily_top1.empty else '有效信号天'}",
        delta_color="off" if not daily_top1.empty else "inverse",
    )
    ci4.metric(
        "有效回测",
        f"{len(valid)} 笔",
        delta=f"含完整T+N价格",
        delta_color="normal" if len(valid) >= 30 else "off",
    )

    if daily_top1.empty:
        st.warning("当前筛选条件下无匹配记录，请放宽筛选范围（建议先选「全部」）")
        return
    if len(valid) < 10:
        st.warning(
            f"有效样本仅 {len(valid)} 笔，统计意义有限。"
            "建议：放宽筛选条件 / 增大 Top N / 等待更多历史数据积累"
        )
        if valid.empty:
            return

    st.divider()

    # ══════════════════════════════════════════
    # 核心 KPI：概率分档 + 传统指标
    # ══════════════════════════════════════════
    r  = valid["冲高收益%"]
    r2 = valid["收盘收益%"]
    n  = len(r)

    st.subheader(f"📊 策略统计（{n} 笔 | Top{top_n} | {mcap_filter} | {turnover_filter}）")

    # 第一行：传统指标
    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("交易笔数", f"{n} 笔")
    k2.metric("冲高胜率(>0%)", f"{(r>0).mean()*100:.0f}%",
              delta=f"{(r>0).sum()}/{n}",
              delta_color="normal" if (r>0).mean() >= 0.7 else "off")
    k3.metric("均冲高收益", f"{r.mean():.2f}%",
              delta=f"中位 {r.median():.2f}%",
              delta_color="normal" if r.mean() > 0 else "inverse")
    k4.metric("收盘胜率", f"{(r2>0).mean()*100:.0f}%",
              delta="拿到收盘", delta_color="off")
    k5.metric("累计复利", f"{((1+r/100).prod()-1)*100:.0f}%",
              delta_color="normal" if ((1+r/100).prod()-1) > 0 else "inverse")

    st.divider()

    # ══════════════════════════════════════════
    # 分档概率表 + 柱状图（核心新功能）
    # ══════════════════════════════════════════
    st.subheader("🎯 冲高达标概率分析（T+2日内最高收益）")
    st.caption(
        "说明：**T+1 买入价** = 上榜次日开盘集合竞价价格；**T+2 最高价** = 上榜后第2个交易日盘中最高价（理论最优卖点）。"
        "实际执行时，挂单卖点建议比阈值低 0.5~1%。"
    )

    prob_df = _calc_threshold_probs(valid)
    if not prob_df.empty:
        # 横向指标卡展示
        pcols = st.columns(6)
        colors_emoji = ["🟢", "🟡", "🟠", "🔴", "🔴", "🔴"]
        for i, (_, row) in enumerate(prob_df.iterrows()):
            p = row["概率"]
            emoji = colors_emoji[i]
            pcols[i].metric(
                label=f"{emoji} {row['目标收益']}",
                value=f"{p:.1f}%",
                delta=f"{int(row['达标笔数'])}/{int(row['总笔数'])} 笔",
                delta_color="off",
                help=f"T+2日内最高收益达到 {row['目标收益']} 的历史概率",
            )

    # 分档概率柱状图移至「多维分析图表」→「📊 分档概率」tab 中展示

    st.divider()

    # ══════════════════════════════════════════
    # 可视化图表 & 历史明细
    # ══════════════════════════════════════════
    st.subheader("📈 策略历史表现与多维分析")
    tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs(
        ["📋 回测逐笔明细", "📊 分档概率", "📅 月度统计", "📈 累计净值", "🏗 市值分组", "🔄 换手率分组"]
    )
    with tab1:
        show_cols2 = ["trade_date", "代码", "名称", "板块", "综合评分", "净买额占比%",
                      "流通市值亿", "换手率", "市值档位", "换手率档位", "净买入亿",
                      "T+1买入价", "T+2最高价", "T+2收盘价", "冲高收益%", "收盘收益%"]
        avcols2 = [c for c in show_cols2 if c in valid.columns]
        display = valid[avcols2].sort_values("trade_date", ascending=False).copy()
        # 保留数字格式（AgGrid 用数字过滤器更准确）
        display["冲高收益%"] = display["冲高收益%"].round(2)
        display["收盘收益%"] = display["收盘收益%"].round(2)
        # 重命名列头（展示更友好）
        display = display.rename(columns={
            "trade_date": "上榜日",
            "流通市值亿": "市值(亿)",
            "净买入亿":   "净买入(亿)",
            "T+1买入价":  "买入价",
            "T+2最高价":  "T+2最高",
            "T+2收盘价":  "T+2收盘",
        })
        st.caption("综合评分 = 净买额占比×30% + 换手率×25% + 涨跌幅×20% + 市值×15% + 振幅×10%（全基于T日本地字段）")
        _filterable_table(
            display,
            key="backtest_detail_table",
            height=450,
            filter_cols=["板块", "市值档位", "换手率档位"],
            num_cols=["市值(亿)", "换手率", "净买入(亿)", "买入价", "T+2最高", "T+2收盘", "综合评分", "净买额占比%"],
            pct_cols=["冲高收益%", "收盘收益%"],
            default_sort_col="上榜日",
            default_sort_asc=False,
        )

    with tab2:
        _plot_threshold_bar(valid, key="tab_threshold_bar")
        # 同时展示概率表
        if not prob_df.empty:
            st.dataframe(prob_df, use_container_width=True, hide_index=True,
                         column_config={
                             "概率": st.column_config.NumberColumn("达标概率%", format="%.1f%%"),
                         })
    with tab3: _plot_monthly(valid, key="tab_monthly")
    with tab4: _plot_cumulative(valid, key="tab_cumulative")
    with tab5: _plot_prob_by_mcap(valid, key="tab_prob_mcap")
    with tab6: _plot_prob_by_turnover(valid, key="tab_prob_turnover")

    st.divider()

    # ══════════════════════════════════════════
    # 操作指南
    # ══════════════════════════════════════════
    with st.expander("📖 筛选参数说明 & 操作指南", expanded=False):
        st.markdown(f"""
### 🔧 筛选参数含义

| 参数 | 含义 | 建议 |
|------|------|------|
| **Top N** | 每日从龙虎榜净买入前 N 名中筛选主板 | Top10 质量最高；Top50 样本更多 |
| **市值筛选** | 上榜日的流通市值分档 | 中小盘弹性大，超大盘稳定性强 |
| **换手率** | 上榜日当天换手率，反映主力活跃度 | 活跃(3~7%)冲高概率通常更高 |
| **排序** | 决定多只主板股同时上榜时选哪只 | 净买入额反映主力决心 |

### 📊 分档概率解读

- **≥0%**（冲高胜率）：T+2日内最高价超过买入价的概率，反映方向正确率
- **≥2%**：可覆盖手续费并有少量盈利
- **≥3%**：**核心目标卖点**，3% 以上分批卖出
- **≥5%**：强势信号，激进卖点

> 实际执行建议：将目标价设为目标阈值 **-0.5~1%**（如目标 ≥3%，挂卖单在 +2.2~2.5%），
> 防止冲高到目标价后迅速回落来不及成交。

### 🎯 每日操作流程

| 时间 | 动作 |
|------|------|
| **18:40（周一~五）** | 查看「今日推荐」，确认满足筛选条件 |
| **次日 9:15~9:25** | 挂集合竞价单，价格略高于前收盘（市价委托）|
| **次次日盘中** | 观察分时图，涨 **{(valid['冲高收益%'] >= 3).mean()*100:.0f}%** 历史概率下目标价 |
| **次次日 14:30** | 无论如何强制离场 |

### ⚠️ 止损
- T+2 开盘低于买入价 **-2%** 立即止损
- 单笔仓位建议 ≤ 总资金 20%
        """)
