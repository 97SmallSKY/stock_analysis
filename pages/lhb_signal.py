"""
🎯 龙虎榜决策辅助信号

在「龙虎榜盈亏分析」的基础上，从以下四个维度为每只上榜股票进行综合评分：
  - 技术面（40分）：均线趋势、RSI、MACD金叉、近20日涨幅位置、量能、K线形态
  - 资金面（35分）：龙虎榜净买额占比、机构/知名游资席位识别、主力资金流向
  - 历史胜率（25分）：该股历史上榜后 T+2/T+5 胜率
  - 市场情绪联动：涨停家数、大盘趋势，影响最终操作建议

最终输出：综合评分（0-100）、操作建议、集合竞价策略、止盈止损参考位。

注：所有缓存使用 session_state（避免 Python 3.13 semaphore 泄漏）。
"""
import time
import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from datetime import datetime, timedelta

from utils.storage import storage
from utils.retry import safe_akshare_call
from modules.data_fetcher import fetch_lhb_netbuy_by_date
from config.settings import FAMOUS_TRADERS, STRATEGY_CONFIG
from modules.scorer import score_tech as _score_tech_shared, score_fund as _score_fund_shared, score_history as _score_history_shared

_RED   = "#E84040"
_GREEN = "#33CC66"
_GOLD  = "#FFA500"
_GRAY  = "#888888"
_BLUE  = "#4488FF"


# ============================================================
# Session State 缓存
# ============================================================

def _ss_get(key: str, max_age: int = 300):
    import time as _t
    cache = st.session_state.get("_sig_cache", {})
    entry = cache.get(key)
    if entry is None:
        return None
    if _t.time() - entry["ts"] > max_age:
        return None
    return entry["val"]


def _ss_set(key: str, val):
    import time as _t
    if "_sig_cache" not in st.session_state:
        st.session_state["_sig_cache"] = {}
    st.session_state["_sig_cache"][key] = {"ts": _t.time(), "val": val}


# ============================================================
# 数据获取
# ============================================================

def _get_top10(trade_date: str) -> pd.DataFrame:
    key = f"sig_top10_{trade_date}"
    cached = _ss_get(key, 300)
    if cached is not None:
        return cached
    try:
        df = fetch_lhb_netbuy_by_date(trade_date, top_n=10)
    except Exception:
        df = pd.DataFrame()
    _ss_set(key, df)
    return df


def _get_kline(code: str, trade_date: str, days: int = 80) -> pd.DataFrame:
    """拉取 trade_date 前后 days 个交易日的日K（前复权），新浪接口优先"""
    key = f"sig_kline_{code}_{trade_date}_{days}"
    cached = _ss_get(key, 600)
    if cached is not None:
        return cached

    import akshare as ak
    end_dt   = (pd.Timestamp(trade_date) + pd.Timedelta(days=10)).strftime("%Y%m%d")
    start_dt = (pd.Timestamp(trade_date) - pd.Timedelta(days=int(days * 2.2))).strftime("%Y%m%d")
    prefix   = "sh" if code.startswith(("6", "9")) else "sz"
    df = None

    try:
        raw = safe_akshare_call(
            ak.stock_zh_a_daily,
            symbol=f"{prefix}{code}", adjust="qfq",
            start_date=start_dt, end_date=end_dt,
            max_retries=2, base_delay=1.5, fallback=None,
        )
        if raw is not None and len(raw) >= 10:
            raw = raw.rename(columns={"date": "日期", "open": "开盘",
                                      "high": "最高", "low": "最低",
                                      "close": "收盘", "volume": "成交量"})
            df = raw.sort_values("日期").tail(days).reset_index(drop=True)
    except Exception:
        pass

    if df is None:
        df = pd.DataFrame()
    _ss_set(key, df)
    return df


def _get_lhb_seat_detail(code: str, trade_date: str) -> pd.DataFrame:
    """拉取某只股票龙虎榜席位详情（东方财富 stock_lhb_stock_statistic_em）"""
    key = f"sig_seat_{code}_{trade_date}"
    cached = _ss_get(key, 600)
    if cached is not None:
        return cached

    import akshare as ak
    df = safe_akshare_call(
        ak.stock_lhb_stock_statistic_em,
        symbol=code,
        max_retries=2, base_delay=1.5, fallback=pd.DataFrame(),
    )
    if df is None:
        df = pd.DataFrame()
    _ss_set(key, df)
    return df


def _get_lhb_detail_seat(code: str, trade_date: str) -> pd.DataFrame:
    """拉取当日该股龙虎榜买卖席位（东方财富 stock_lhb_detail_em 直接过滤）"""
    key = f"sig_seat2_{code}_{trade_date}"
    cached = _ss_get(key, 600)
    if cached is not None:
        return cached

    import akshare as ak
    df = pd.DataFrame()
    try:
        raw = safe_akshare_call(
            ak.stock_lhb_detail_em,
            start_date=trade_date, end_date=trade_date,
            max_retries=2, base_delay=2.0, fallback=None,
        )
        if raw is not None and not raw.empty:
            code_col = next((c for c in raw.columns if "代码" in c), None)
            if code_col:
                df = raw[raw[code_col].astype(str).str.zfill(6) == code].copy()
    except Exception:
        pass

    _ss_set(key, df)
    return df


def _get_fund_flow(code: str) -> dict:
    """主力资金流向（近5日合计），失败时静默返回空"""
    key = f"sig_fund_{code}"
    cached = _ss_get(key, 300)
    if cached is not None:
        return cached

    import akshare as ak
    result = {"main_net": None, "note": ""}
    try:
        market = "sh" if code.startswith(("6", "9")) else "sz"
        df = safe_akshare_call(
            ak.stock_individual_fund_flow,
            stock=code, market=market,
            max_retries=2, base_delay=2.0, fallback=None,
        )
        if df is not None and not df.empty and len(df) >= 3:
            recent = df.tail(5)
            main_cols = [c for c in recent.columns if "主力" in c and "净" in c]
            if main_cols:
                result["main_net"] = float(recent[main_cols[0]].sum())
    except Exception as e:
        result["note"] = str(e)[:60]

    _ss_set(key, result)
    return result


def _get_history_winrate(code: str) -> dict:
    """从 lhb_top10_prices 计算该股历史上榜后胜率"""
    key = f"sig_hist_{code}"
    cached = _ss_get(key, 120)
    if cached is not None:
        return cached

    result = {"count": 0, "t2_win": None, "t5_win": None, "t2_avg": None, "t5_avg": None}
    try:
        df = storage.read("lhb_top10_prices")
        if not df.empty:
            code_norm = str(code).lstrip("0")
            sub = df[df["代码"].astype(str).str.lstrip("0") == code_norm]
            sub = sub[sub["T+1买入价"].notna()]
            result["count"] = len(sub)
            for n, k in [(2, "t2"), (5, "t5")]:
                col = f"T+{n}收盘收益%"
                if col in sub.columns:
                    vals = sub[col].dropna()
                    if len(vals) > 0:
                        result[f"{k}_win"] = round((vals > 0).mean() * 100, 1)
                        result[f"{k}_avg"] = round(vals.mean(), 2)
    except Exception:
        pass

    _ss_set(key, result)
    return result


def _get_market_sentiment(trade_date: str) -> dict:
    """读取当日市场情绪（涨停数/跌停数/大盘趋势）"""
    key = f"sig_mkt_{trade_date}"
    cached = _ss_get(key, 300)
    if cached is not None:
        return cached

    result = {"limit_up": None, "limit_down": None, "index_trend": None}
    try:
        df = storage.read("market_sentiment")
        if not df.empty and "trade_date" in df.columns:
            row = df[df["trade_date"].astype(str) == trade_date]
            if not row.empty:
                r = row.iloc[0]
                for col, key2 in [("涨停数", "limit_up"), ("跌停数", "limit_down")]:
                    if col in r.index and pd.notna(r[col]):
                        result[key2] = int(r[col])
    except Exception:
        pass

    # 大盘趋势：从 K 线判断 MA5 > MA20
    try:
        import akshare as ak
        raw = safe_akshare_call(
            ak.stock_zh_index_daily, symbol="sh000001",
            max_retries=1, base_delay=2.0, fallback=None,
        )
        if raw is not None and len(raw) >= 25:
            close = raw["close"].astype(float).values[-25:]
            ma5  = close[-5:].mean()
            ma20 = close.mean()
            result["index_trend"] = "多头" if ma5 > ma20 else "空头"
    except Exception:
        pass

    _ss_set(key, result)
    return result


def _get_lianban_map(trade_date: str) -> dict[str, int]:
    """
    获取指定交易日的连板股 code→连板数 映射（带 session_state 缓存）

    数据来源：AKShare stock_zt_pool_strong_em
    用途：在 score_tech() 中传入 lianban_count，确保页面与机器人评分一致
    """
    key = f"sig_lianban_{trade_date}"
    cached = _ss_get(key, 600)
    if cached is not None:
        return cached

    result: dict[str, int] = {}
    try:
        import akshare as ak
        df_lb = safe_akshare_call(
            ak.stock_zt_pool_strong_em, date=trade_date,
            max_retries=2, base_delay=1.0, fallback=None,
        )
        if df_lb is not None and not df_lb.empty:
            code_col = next((c for c in df_lb.columns if "代码" in c), None)
            lian_col = next((c for c in df_lb.columns if "连板" in c), None)
            if code_col and lian_col:
                for _, row in df_lb.iterrows():
                    c_ = str(row[code_col]).zfill(6)
                    try:
                        result[c_] = int(row[lian_col])
                    except (ValueError, TypeError):
                        pass
    except Exception:
        pass  # 获取失败时静默降级，不影响其他评分

    _ss_set(key, result)
    return result


# ============================================================
# 评分引擎（委托给 modules/scorer.py 统一实现）
# ============================================================
# 原始内置实现已迁移至 modules/scorer.py，此处保留同名薄包装以确保向后兼容。
# 页面内的 np/sma/ema 工具函数已由 scorer 模块内部使用，此处不再重复定义。

def _score_tech(kline: pd.DataFrame, trade_date: str, lianban_count: int = 0) -> dict:
    """技术面评分 — 委托给 modules/scorer.score_tech()"""
    return _score_tech_shared(kline, trade_date, lianban_count=lianban_count)


def _score_fund(lhb_row: pd.Series, fund_flow: dict, seat_df: pd.DataFrame) -> dict:
    """资金面评分 — 委托给 modules/scorer.score_fund()"""
    return _score_fund_shared(lhb_row, seat_df=seat_df, fund_flow=fund_flow)


def _score_history(hist: dict) -> dict:
    """历史胜率评分 — 委托给 modules/scorer.score_history()"""
    return _score_history_shared(hist)


def _apply_market_filter(advice: dict, mkt: dict) -> dict:
    """根据市场情绪调整操作建议（不改评分，只改文字和仓位）"""
    lu  = mkt.get("limit_up")
    ld  = mkt.get("limit_down")
    idx = mkt.get("index_trend")
    cfg = STRATEGY_CONFIG
    notes = list(advice.get("notes", []))
    position = "正常仓位"

    if lu is not None and ld is not None:
        if lu < cfg["min_limit_up"] or ld > cfg["max_limit_down"]:
            position = "半仓或回避"
            notes.append(f"⚠️ 市场情绪偏弱（涨停{lu}家/跌停{ld}家），建议半仓操作")
        elif lu >= 80:
            notes.append(f"🔥 强赚钱效应（涨停{lu}家），可适当加仓")
    if idx == "空头":
        position = "半仓"
        notes.append("📉 大盘 MA5 < MA20（空头趋势），降低仓位")
    elif idx == "多头":
        notes.append("📈 大盘 MA5 > MA20（多头趋势），顺势操作")

    advice["position"] = position
    advice["notes"]    = notes
    advice["mkt_lu"]   = lu
    advice["mkt_ld"]   = ld
    advice["mkt_idx"]  = idx
    return advice


def _gen_advice(total: int, tech: dict, fund: dict, hist: dict) -> dict:
    """根据综合评分生成基础操作建议"""
    if total >= 80:
        action = "🟢 重点关注，可考虑买入"
        color  = _RED
    elif total >= 65:
        action = "🟡 关注，谨慎参与"
        color  = _GOLD
    elif total >= 50:
        action = "🟠 观望，等待更好信号"
        color  = _GOLD
    else:
        action = "🔴 回避，风险偏高"
        color  = _GREEN

    notes = []
    rsi = tech.get("rsi")
    if rsi and rsi > 80:
        notes.append("⚠️ RSI 超买（>80），留意短期回调")
    if rsi and rsi < 35:
        notes.append("📌 RSI 超卖（<35），等待企稳确认")
    g20 = tech.get("gain20")
    if g20 is not None and g20 > 30:
        notes.append(f"⚠️ 近20日已涨 {g20:+.1f}%，高位追涨风险较大")
    if fund.get("has_jigou_sell"):
        notes.append("⚠️ 存在机构卖出席位，主力可能在派发")
    if fund.get("score", 0) <= 5:
        notes.append("⚠️ 资金面信号偏弱，谨慎操作")
    if hist.get("count", 0) == 0:
        notes.append("ℹ️ 该股暂无历史上榜记录，胜率评分含中间分")

    return {"action": action, "color": color, "notes": notes, "total": total, "position": "正常仓位"}


# ============================================================
# 集合竞价策略建议
# ============================================================

def _auction_advice(gap_pct: float | None) -> str:
    """根据集合竞价涨幅给出操作策略"""
    if gap_pct is None:
        return "暂无竞价数据，默认开盘买入"
    if gap_pct > 7:
        return "❌ 高开过多（>7%），放弃，等下一只"
    elif gap_pct > 5:
        return "⚠️ 高开较多（5-7%），减半仓位买入"
    elif gap_pct > 2:
        return "✅ 适度高开（2-5%），正常买入"
    elif gap_pct >= -2:
        return "✅ 平开/小幅波动，正常或加仓买入"
    else:
        return "🔍 低开（<-2%），先观察30分钟确认企稳再买"


# ============================================================
# 页面渲染
# ============================================================

def render():
    st.title("🎯 龙虎榜决策辅助信号")
    st.caption(
        "**技术面(40) + 资金面(35) + 历史胜率(25)** 三维评分 | "
        "自动识别机构/知名游资席位 | 市场情绪联动仓位建议 | 评分仅供参考，不构成投资建议"
    )

    # ── 可折叠评分说明区块（Step 8）──────────────────────────────────
    with st.expander("📖 评分体系说明（点击展开）", expanded=False):
        st.markdown("""
### 综合评分（0 ~ 100 分）

综合评分由三个维度加权组成，满分 100 分：

| 维度 | 权重 | 核心指标 |
|------|------|----------|
| 🔵 技术面 | 40 分 | 均线趋势（多头排列）、RSI(14)、MACD 金叉/死叉、量比、近 20 日涨幅位置 |
| 🟢 资金面 | 35 分 | 净买入强度（%流通市值）、机构买方席位数、知名游资上榜数、主力5日净流入 |
| 🟡 历史胜率 | 25 分 | 该股历史上榜后 T+2 胜率、T+5 胜率、样本量（≥5次更可信） |

---

### 评分档位解读

| 分数段 | 建议 | 说明 |
|--------|------|------|
| **≥ 80 分** ⭐⭐⭐ | **重点关注** | 三维均强，可考虑全仓参与（结合市场情绪） |
| **65 ~ 79 分** ⭐⭐ | **谨慎参与** | 多数维度良好，建议半仓或等待回踩确认 |
| **50 ~ 64 分** ⭐ | **观望为主** | 存在明显短板，风险收益比偏低 |
| **< 50 分** ❌ | **建议回避** | 多维度信号偏弱，不建议参与 |

---

### 关键指标说明

- **T+2 胜率**：该股过去在龙虎榜上榜后，持有 2 个交易日的盈利概率（即从 T+1 买入，T+2 收盘时的涨跌比）
- **T 日** = 龙虎榜上榜当日（榜单公布日）
- **T+1 日** = 上榜次日（实际买入日，集合竞价参与）
- **T+2 日** = 上榜后第 2 个交易日（挂单卖出日）
- **净买入额**：龙虎榜买方金额 - 卖方金额（正值表示主力净买入）
- **净买入强度**：净买入额 / 流通市值 × 100%。归一化后可公平比较大小盘股票。≥ 1% 为强度信号；≥ 2% 为极强信号
- **量比**：当日成交量 / 近 5 个交易日平均成交量（标准量比定义）。量比 > 3 为极度放量；> 2 为明显放量；≈ 1 为平量；< 1 为缩量
- **对倒检测**：当龙虎榜净买额为负但解读含"买"字时，疑似对倒（一方卖出一方买入制造成交量假象），评分惩罚 -10 分
- **市场情绪**：涨停家数 ≥ 30 且跌停 ≤ 5 为情绪良好，否则建议半仓

> ⚠️ **免责声明**：以上评分与分析仅为量化辅助工具，不构成任何投资建议。股市有风险，投资需谨慎。
        """)

    # ── ① 选择日期 ─────────────────────────────────────────────
    st.subheader("① 选择交易日")
    _date_set = set()
    for _tbl in ["lhb_top10", "lhb_detail", "lhb_top10_prices"]:
        try:
            _df = storage.read(_tbl)
            if not _df.empty and "trade_date" in _df.columns:
                _date_set.update(_df["trade_date"].astype(str).unique())
        except Exception:
            pass
    local_dates = sorted(_date_set, reverse=True)
    today_str   = datetime.now().strftime("%Y%m%d")
    all_options = list(dict.fromkeys(
        local_dates + ([today_str] if today_str not in local_dates else [])
    ))
    if not all_options:
        all_options = [today_str]

    col_d, col_m = st.columns([2, 3])
    with col_d:
        sel_date = st.selectbox(
            "选择交易日",
            options=all_options,
            format_func=lambda d: f"{d[:4]}/{d[4:6]}/{d[6:]}  {'📡 今日' if d == today_str else ''}",
        )

    # 市场情绪（后台静默拉取，不影响主流程）
    with col_m:
        mkt = _get_market_sentiment(sel_date)
        lu  = mkt.get("limit_up")
        ld  = mkt.get("limit_down")
        idx = mkt.get("index_trend", "—")
        cfg = STRATEGY_CONFIG
        if lu is not None:
            mkt_ok = lu >= cfg["min_limit_up"] and (ld is None or ld <= cfg["max_limit_down"])
            mkt_color = _RED if mkt_ok else _GOLD
            st.markdown(
                f"<div style='padding:8px;border-left:3px solid {mkt_color};margin-top:8px'>"
                f"📊 <b>市场情绪</b>：涨停 <b>{lu}</b> 家 / 跌停 <b>{ld}</b> 家 | "
                f"大盘趋势：<b>{idx or '—'}</b><br/>"
                f"<small>{'✅ 情绪良好，可正常操作' if mkt_ok else '⚠️ 情绪偏弱，建议半仓'}</small></div>",
                unsafe_allow_html=True,
            )
        else:
            st.info("市场情绪数据加载中...")

    # ── ② 选择股票 ─────────────────────────────────────────────
    st.subheader("② 选择分析股票")
    with st.spinner(f"获取 {sel_date} 龙虎榜 Top10..."):
        top10 = _get_top10(sel_date)

    if top10.empty:
        st.warning(f"⚠️ {sel_date} 暂无龙虎榜数据，请选择其他日期。")
        return

    codes       = top10["代码"].astype(str).str.zfill(6).tolist()
    names       = top10["名称"].tolist() if "名称" in top10.columns else codes
    options_list = [f"{r+1}. {n}（{c}）" for r, (n, c) in enumerate(zip(names, codes))]

    batch_mode   = st.checkbox("📊 批量评分所有 Top10（对比视图）", value=False)

    if not batch_mode:
        selected_opt = st.selectbox("选择股票", options=options_list, key="sig_stock_sel")
        idx          = options_list.index(selected_opt)
        # 连板池（仅拉取当日，带 session_state 缓存，失败时静默降级）
        lianban_map = _get_lianban_map(sel_date)
        _render_single(codes[idx], names[idx], sel_date, top10.iloc[idx], mkt, lianban_map)
    else:
        lianban_map = _get_lianban_map(sel_date)
        _render_batch(codes, names, sel_date, top10, mkt, lianban_map)


# ============================================================
# 单股详情视图
# ============================================================

def _render_single(code: str, name: str, trade_date: str, lhb_row: pd.Series, mkt: dict,
                   lianban_map: dict | None = None):
    st.divider()
    st.subheader(f"③ {name}（{code}）综合决策")

    with st.spinner("拉取数据并计算评分..."):
        kline    = _get_kline(code, trade_date)
        fund_flow = _get_fund_flow(code)
        seat_df  = _get_lhb_seat_detail(code, trade_date)
        hist     = _get_history_winrate(code)

        lianban_count = (lianban_map or {}).get(code, 0)
        t_res = _score_tech(kline, trade_date, lianban_count=lianban_count)
        f_res = _score_fund(lhb_row, fund_flow, seat_df)
        h_res = _score_history(hist)
        total = t_res["score"] + f_res["score"] + h_res["score"]
        advice = _gen_advice(total, t_res, f_res, hist)
        advice = _apply_market_filter(advice, mkt)

    # ── 置顶：操作建议横幅 ─────────────────────────────────
    pos_label = f"  |  仓位：{advice['position']}" if advice.get("position") else ""
    st.markdown(
        f"<div style='padding:18px;background:#1a1a2e;border-left:8px solid {advice['color']};"
        f"border-radius:8px;font-size:1.3em;margin-bottom:12px;'>"
        f"<b>💡 操作建议：</b><span style='color:{advice['color']}'>{advice['action']}</span>{pos_label}</div>",
        unsafe_allow_html=True,
    )
    if advice.get("notes"):
        for note in advice["notes"]:
            st.markdown(f"<div style='color:#ffcc00;font-size:0.95em;margin-bottom:6px;padding-left:14px;'>{note}</div>", unsafe_allow_html=True)
    st.write("")

    # ── KPI 卡片（评分 + 实际数据 + ? 解释）─────────────────
    c1, c2, c3, c4 = st.columns(4)

    # 取实际数值用于副标题显示
    rsi_val   = t_res.get("rsi")
    gain20    = t_res.get("gain20")
    macd_s    = t_res.get("macd_score", 0)
    net_buy   = lhb_row.get("龙虎榜净买额")
    main_net  = fund_flow.get("main_net")
    jg        = f_res.get("jigou_buy", 0)
    famous    = f_res.get("famous_buy", [])
    t2_win    = hist.get("t2_win")
    t5_win    = hist.get("t5_win")
    hist_cnt  = hist.get("count", 0)

    def _score_color(score, max_score):
        pct = score / max_score
        return _RED if pct >= 0.7 else (_GOLD if pct >= 0.5 else _GREEN)

    # 综合评分卡
    col_color = _score_color(total, 100)
    with c1:
        st.metric(
            label="综合评分",
            value=f"{total}/100",
            help="三个维度的加总分：技术面(40)+资金面(35)+历史胜率(25)。≥80重点关注，≥65谨慎参与，<50建议回避。",
        )
        # 颜色边框用 markdown 补充
        st.markdown(
            f"<div style='margin-top:-16px;text-align:center;font-size:.85em;color:{col_color}'>"
            f"{'⭐⭐⭐ 强' if total>=80 else ('⭐⭐ 中' if total>=65 else ('⭐ 弱' if total>=50 else '❌ 回避'))}"
            f"</div>", unsafe_allow_html=True
        )

    # 技术面卡
    with c2:
        rsi_sub = f"RSI {rsi_val}" if rsi_val else "RSI—"
        g20_sub = f" | 20日{gain20:+.1f}%" if gain20 is not None else ""
        tc = _score_color(t_res["score"], 40)
        st.metric(
            label="📈 技术面",
            value=f"{t_res['score']}/40",
            delta=f"{rsi_sub}{g20_sub}",
            delta_color="normal" if (rsi_val and 45 <= rsi_val <= 70) else "off",
            help=(
                "技术面评分由6项指标组成（满分40分）：\n\n"
                "• **均线多头排列**：价格 > MA5 > MA10 > MA20，说明短中长期趋势一致向上\n"
                "• **RSI**（相对强弱指数）：衡量股票超买/超卖程度。45~70为健康强势区，>80超买（可能回调），<30超卖\n"
                "• **MACD金叉**：短期均线上穿长期均线，是买入信号；红柱放大说明上涨动能增强\n"
                "• **量比**：当日成交量 / 近5个交易日均成交量（标准量比定义）。>3极度放量，>2明显放量，≈1平量，<1缩量\n"
                "• **近20日涨幅**：涨幅<10%为低位蓄力（高分），>30%为高位追涨（低分）\n"
                "• **K线形态**：大阳线（实体占比>60%）得满分，长上影线（主力派发）扣分"
            ),
        )
        st.markdown(
            f"<div style='margin-top:-16px;text-align:center;font-size:.8em;color:{tc}'>"
            f"{'MACD金叉✅' if macd_s>=10 else ('多头延续' if macd_s>=8 else ('弱多' if macd_s>=5 else 'MACD空头⚠️'))}"
            f"</div>", unsafe_allow_html=True
        )
        # 量比展示
        _vr = t_res.get("volume_ratio")
        if _vr is not None:
            if _vr >= 3.0:   _vr_icon, _vr_label = "🔴", f"量比 {_vr:.2f} 极度放量"
            elif _vr >= 2.0: _vr_icon, _vr_label = "🟡", f"量比 {_vr:.2f} 明显放量"
            elif _vr >= 1.5: _vr_icon, _vr_label = "🟢", f"量比 {_vr:.2f} 温和放量"
            elif _vr >= 1.0: _vr_icon, _vr_label = "➖", f"量比 {_vr:.2f} 平量"
            else:            _vr_icon, _vr_label = "🔵", f"量比 {_vr:.2f} 缩量"
            st.caption(f"{_vr_icon} {_vr_label}")

    # 资金面卡
    with c3:
        nb_sub  = f"{float(net_buy)/1e8:+.2f}亿" if net_buy is not None and pd.notna(net_buy) else "净买额—"
        mf_sub  = f" | 主力{float(main_net)/1e8:+.2f}亿" if main_net is not None else ""
        fc = _score_color(f_res["score"], 35)
        st.metric(
            label="💰 资金面",
            value=f"{f_res['score']}/35",
            delta=f"{nb_sub}{mf_sub}",
            delta_color="normal" if (net_buy is not None and pd.notna(net_buy) and float(net_buy) > 0) else "off",
            help=(
                "资金面评分由3项指标组成（满分35分）：\n\n"
                "• **龙虎榜净买额**：龙虎榜买入方合计金额 - 卖出方合计金额。正数说明大资金净流入，越大越强\n"
                "• **席位质量**：识别买方席位中是否有机构（基金/券商自营）或知名游资（如赵老哥、拉萨天团）。机构买入是重要信号\n"
                "• **主力净流入（5日）**：近5个交易日大单（单笔≥50万）资金净流入金额。正数说明主力持续加仓"
            ),
        )
        jg_tag = f"机构{jg}席" if jg else ""
        fy_tag = "/".join(famous[:2]) if famous else ""
        seat_sub = " | ".join(filter(None, [jg_tag, fy_tag])) or "无机构/知名游资"
        st.markdown(
            f"<div style='margin-top:-16px;text-align:center;font-size:.8em;color:{fc}'>{seat_sub}</div>",
            unsafe_allow_html=True
        )
        # 净买入强度附加展示
        _f_strength = f_res.get("net_buy_strength_pct")
        if _f_strength is not None:
            _s_icon = "🟢" if _f_strength >= 1.0 else ("🟡" if _f_strength >= 0.5 else "🔴")
            st.caption(f"{_s_icon} 净买入强度：**{_f_strength:.2f}%** 流通市值")

    # 历史胜率卡
    with c4:
        if hist_cnt > 0 and t2_win is not None:
            hist_sub = f"T+2胜率{t2_win}% | T+5胜率{t5_win or '—'}%"
            hist_dc  = "normal" if t2_win >= 50 else "inverse"
        elif hist_cnt > 0:
            hist_sub = f"上榜{hist_cnt}次，数据积累中"
            hist_dc  = "off"
        else:
            hist_sub = "暂无历史记录"
            hist_dc  = "off"
        hc = _score_color(h_res["score"], 25)
        st.metric(
            label="📋 历史胜率",
            value=f"{h_res['score']}/25",
            delta=hist_sub,
            delta_color=hist_dc,
            help=(
                "历史胜率评分基于该股**过去每次上龙虎榜**后的真实盈亏记录（满分25分）：\n\n"
                "• **T+2胜率**：上榜后第2个交易日收盘卖出，历史上盈利的概率。≥70%得满分\n"
                "• **T+5胜率**：上榜后第5个交易日收盘卖出，历史上盈利的概率。≥70%得满分\n\n"
                "⚠️ 该股历史上榜次数越多，评分参考价值越高。样本<3次时仅供参考。\n\n"
                f"当前样本数：{hist_cnt}次"
            ),
        )

    # 席位标签
    seat_tags = f_res.get("seat_tags", [])
    if jg or famous:
        tag_parts = []
        if jg:     tag_parts.append(f"🏛️ 机构买方 {jg} 席")
        for a in famous: tag_parts.append(f"⭐ {a}")
        st.info("**识别到的关键席位：** " + "  |  ".join(tag_parts))

    st.write("")

    # ── 图表收纳区 (Tabs) ────────────────────────────────────
    tab_kline, tab_radar, tab_history, tab_op = st.tabs([
        "📈 技术走势图", "🕸️ 三维评分明细", "📋 历史上榜表现", "✅ 止盈止损与操作清单"
    ])

    with tab_radar:
        col_radar, col_detail = st.columns([1, 1])
        with col_radar:
            fig_radar = go.Figure(go.Scatterpolar(
                r=[t_res["score"], f_res["score"], h_res["score"], t_res["score"]],
                theta=["技术面(40)", "资金面(35)", "历史胜率(25)", "技术面(40)"],
                fill="toself",
                fillcolor="rgba(228,64,64,0.2)",
                line=dict(color=_RED, width=2),
            ))
            fig_radar.update_layout(
                polar=dict(radialaxis=dict(visible=True, range=[0, 40])),
                showlegend=False, height=320,
                margin=dict(l=40, r=40, t=30, b=20),
                title=f"{name} 三维评分雷达图",
            )
            st.plotly_chart(fig_radar, use_container_width=True)

        with col_detail:
            st.markdown("**📋 各项评分 + 实际数据**")
            _HELP = {
                "①均线多头排列": "MA5/MA10/MA20 三条均线依次递减，价格在 MA5 之上，代表趋势向上",
                "②RSI健康区间":  "RSI（相对强弱指数）衡量超买/超卖。45~70为强势健康区，>80超买，<30超卖",
                "③MACD金叉/多头": "MACD 是趋势指标。金叉=买入信号，红柱放大=上涨动能增强",
                "④量比":         "量比 = 当日成交量 / 近5个交易日均成交量（标准量比定义）。>3极度放量，>2明显放量，≈1平量，<1缩量",
                "④量能放大":     "量比 = 当日成交量 / 近5个交易日均量。>3极度放量，>2明显放量，≈1平量，<1缩量",
                "⑤近20日涨幅":   "以当前价相对20个交易日前的涨幅，用于判断是否处于高位（>30%则高位风险大）",
                "⑥K线形态":      "当日K线形态：大阳线=买力强；长上影线=高位有抛压（主力可能在出货）",
                "①龙虎榜净买额": "龙虎榜上买入席位合计金额 - 卖出席位合计金额，正数越大说明主力买入越积极",
                "①净买入强度":  "净买入强度 = 龙虎榜净买额 / 流通市值（%）。归一化后可公平比较大小盘。≥1%为强信号，≥2%为极强",
                "②席位质量":     "识别买方席位是否有机构（基金/券商自营）或知名游资（知名短线操盘手的营业部）",
                "③主力净流入(5日)": "近5日单笔≥50万元的大单净流入金额（买入大单 - 卖出大单），正数代表主力持续加仓",
                "⚠️对倒风险":    "净买额为负但龙虎榜解读含'买'字时，疑似对倒（一方卖一方买制造成交量），惩罚-10分",
                "⚠️机构卖出":    "机构只卖不买时惩罚-5分（出逃信号）",
            }
            all_d = {}
            all_d.update(t_res.get("detail", {}))
            all_d.update(f_res.get("detail", {}))
            all_d.update(h_res.get("detail", {}))
            rows = []
            for k, v in all_d.items():
                import re
                score_part = re.search(r"(\d+)/(\d+)分", v)
                data_part  = re.search(r"[（(](.+?)[）)]", v)
                rows.append({
                    "指标":     k,
                    "得分":     score_part.group(0).replace("分","") if score_part else "—",
                    "实际数据": data_part.group(1) if data_part else "—",
                    "指标说明": _HELP.get(k, ""),
                })
            st.dataframe(
                pd.DataFrame(rows),
                use_container_width=True, hide_index=True, height=320,
                column_config={
                    "指标":     st.column_config.TextColumn("指标", width="medium"),
                    "得分":     st.column_config.TextColumn("得分", width="small"),
                    "实际数据": st.column_config.TextColumn("实际数值", width="medium"),
                    "指标说明": st.column_config.TextColumn("📖 指标说明（新手必看）", width="large"),
                }
            )

    with tab_kline:
        _render_kline(code, name, trade_date, kline, t_res)

    with tab_history:
        _render_history(code, name, hist)

    with tab_op:
        col_sl, col_op = st.columns([1, 1])
        with col_sl:
            _render_stoploss(code, trade_date)
        with col_op:
            _render_op_checklist(name, code, advice)


def _render_kline(code: str, name: str, trade_date: str, kline: pd.DataFrame, t_res: dict):
    """K线 + MA均线 + MACD + RSI 子图"""
    if kline.empty or len(kline) < 5:
        st.info("⚠️ K线数据获取失败，技术图表无法显示。")
        return

    close  = kline["收盘"].astype(float).values
    # 把 "2025-10-21" → "2025.10.21"，避免 Plotly 自动识别为英文日期格式（Oct 2025）
    dates  = [d.replace("-", ".") for d in kline["日期"].astype(str).tolist()]
    ma5    = _sma(close, 5)
    ma10   = _sma(close, 10)
    ma20   = _sma(close, 20)

    fig_k = go.Figure()
    if all(c in kline.columns for c in ["开盘", "最高", "最低", "收盘"]):
        fig_k.add_trace(go.Candlestick(
            x=dates,
            open=kline["开盘"].astype(float), high=kline["最高"].astype(float),
            low=kline["最低"].astype(float),  close=kline["收盘"].astype(float),
            increasing_line_color=_RED, decreasing_line_color=_GREEN, name="K线",
        ))
    else:
        fig_k.add_trace(go.Scatter(x=dates, y=close, name="收盘价", line=dict(color=_BLUE)))

    fig_k.add_trace(go.Scatter(x=dates, y=ma5,  name="MA5",  line=dict(color=_GOLD,  width=1.5)))
    fig_k.add_trace(go.Scatter(x=dates, y=ma10, name="MA10", line=dict(color=_BLUE,  width=1.5)))
    fig_k.add_trace(go.Scatter(x=dates, y=ma20, name="MA20", line=dict(color=_GREEN, width=1.5)))

    # 上榜日竖线：用 Scatter trace 模拟竖线，标注用 layout.annotations 定位，避免文字重叠
    td_fmt = f"{trade_date[:4]}.{trade_date[4:6]}.{trade_date[6:]}"
    k_annotations = []
    if td_fmt in dates and len(close) >= 1:
        close_vals = kline["收盘"].astype(float)
        y_min = float(close_vals.min()) * 0.98
        y_max = float(close_vals.max()) * 1.02
        fig_k.add_trace(go.Scatter(
            x=[td_fmt, td_fmt],
            y=[y_min, y_max],
            mode="lines",                        # 只画线，不带 text
            line=dict(color=_GOLD, dash="dash", width=1.5),
            showlegend=False,
            hoverinfo="skip",
            name="上榜日",
        ))
        # 标注固定在竖线顶部，用 xref="x" yref="paper" 精确定位
        td_idx = dates.index(td_fmt)
        k_annotations.append(dict(
            x=td_fmt, y=0.97,
            xref="x", yref="paper",
            text="上榜日",
            showarrow=False,
            font=dict(color=_GOLD, size=10),
            bgcolor="rgba(20,20,40,0.75)",
            borderpad=3,
            xanchor="left", yanchor="top",
        ))

    fig_k.update_layout(
        height=360, margin=dict(l=40, r=40, t=40, b=30),
        xaxis_rangeslider_visible=False,
        xaxis=dict(type="category", tickangle=-45, nticks=12),
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
        annotations=k_annotations,
    )
    st.plotly_chart(fig_k, use_container_width=True)

    # MACD 子图
    if len(close) >= 35:
        ema12    = _ema(close, 12)
        ema26    = _ema(close, 26)
        dif      = ema12 - ema26
        dea      = _ema(dif, 9)
        hist_arr = (dif - dea) * 2
        colors   = [_RED if v >= 0 else _GREEN for v in hist_arr]
        fig_macd = go.Figure()
        fig_macd.add_trace(go.Bar(x=dates, y=hist_arr, name="MACD柱",
                                   marker_color=colors, opacity=0.7))
        fig_macd.add_trace(go.Scatter(x=dates, y=dif, name="DIF",
                                       line=dict(color=_GOLD, width=1.5)))
        fig_macd.add_trace(go.Scatter(x=dates, y=dea, name="DEA",
                                       line=dict(color=_BLUE, width=1.5)))
        fig_macd.update_layout(
            height=185, margin=dict(l=40, r=40, t=35, b=30),
            title=dict(text="MACD (12,26,9)", font=dict(size=12), x=0),
            legend=dict(orientation="h", yanchor="top", y=-0.15, x=0),
            barmode="relative",
            xaxis=dict(type="category", tickangle=-45, nticks=12),
        )
        st.plotly_chart(fig_macd, use_container_width=True)

    # RSI 子图
    if len(close) >= 15:
        period = 14
        rsi_vals, rsi_dates = [], []
        for i in range(period, len(close)):
            diffs = np.diff(close[i - period: i + 1])
            g = np.where(diffs > 0, diffs, 0.0).mean()
            l = np.where(diffs < 0, -diffs, 0.0).mean()
            rs = g / l if l > 0 else 100
            rsi_vals.append(100 - 100 / (1 + rs))
            rsi_dates.append(dates[i])

        fig_rsi = go.Figure()
        fig_rsi.add_trace(go.Scatter(x=rsi_dates, y=rsi_vals, name="RSI(14)",
                                      line=dict(color=_BLUE, width=2)))
        # 水平参考线：仅画线，标注用 layout.annotations 固定在右侧，不与标题重叠
        x_range = [rsi_dates[0], rsi_dates[-1]] if rsi_dates else [0, 1]
        fig_rsi.add_trace(go.Scatter(
            x=x_range, y=[70, 70], mode="lines",
            line=dict(color=_RED, dash="dash", width=1),
            showlegend=False, hoverinfo="skip",
        ))
        fig_rsi.add_trace(go.Scatter(
            x=x_range, y=[30, 30], mode="lines",
            line=dict(color=_GREEN, dash="dash", width=1),
            showlegend=False, hoverinfo="skip",
        ))
        fig_rsi.update_layout(
            height=175, margin=dict(l=40, r=80, t=25, b=30),
            title=dict(text="RSI (14)", font=dict(size=12), x=0),
            yaxis=dict(range=[0, 100]),
            xaxis=dict(type="category", tickangle=-45, nticks=12),
            # 标注固定在图表右边缘，xref/yref="paper" 不依赖数据坐标
            annotations=[
                dict(
                    x=1.01, y=70/100, xref="paper", yref="paper",
                    text="超买(70)", showarrow=False,
                    font=dict(color=_RED, size=10),
                    xanchor="left", yanchor="middle",
                ),
                dict(
                    x=1.01, y=30/100, xref="paper", yref="paper",
                    text="超卖(30)", showarrow=False,
                    font=dict(color=_GREEN, size=10),
                    xanchor="left", yanchor="middle",
                ),
            ],
        )
        st.plotly_chart(fig_rsi, use_container_width=True)


def _render_history(code: str, name: str, hist: dict):
    """历史上榜胜率 KPI + 走势图"""
    count = hist.get("count", 0)
    if count == 0:
        st.info("该股暂无历史上榜价格记录。每次浏览「龙虎榜盈亏分析」页面后，数据会逐步积累。")
        return

    hc1, hc2, hc3, hc4 = st.columns(4)
    hc1.metric("历史上榜次数", f"{count} 次")
    hc2.metric("T+2 胜率", f"{hist.get('t2_win', '—')}%" if hist.get("t2_win") else "—")
    hc3.metric("T+5 胜率", f"{hist.get('t5_win', '—')}%" if hist.get("t5_win") else "—")
    hc4.metric("T+5 平均收益", f"{hist.get('t5_avg', '—')}%" if hist.get("t5_avg") else "—")

    try:
        df_all = storage.read("lhb_top10_prices")
        if not df_all.empty:
            code_norm = str(code).lstrip("0")
            sub = df_all[df_all["代码"].astype(str).str.lstrip("0") == code_norm]
            sub = sub[sub["T+1买入价"].notna()].sort_values("trade_date")
            if not sub.empty:
                fig = go.Figure()
                for n, color in [(1, _BLUE), (2, _GOLD), (5, _RED), (10, "#AA44FF")]:
                    col_n = f"T+{n}收盘收益%"
                    if col_n in sub.columns:
                        vals = sub[col_n].dropna()
                        if len(vals) > 0:
                            # 把 "20251021" → "2025.10.21"，避免 Plotly 显示英文月份
                            x_td = sub.loc[sub[col_n].notna(), "trade_date"].astype(str)
                            x_td = x_td.apply(lambda d: f"{d[:4]}.{d[4:6]}.{d[6:]}" if len(d)==8 else d)
                            fig.add_trace(go.Scatter(
                                x=x_td,
                                y=vals, name=f"T+{n}",
                                mode="lines+markers",
                                line=dict(color=color, width=2),
                            ))
                # 零线：用 Scatter 模拟
                x_vals_raw = sub["trade_date"].astype(str).tolist()
                x_vals = [f"{d[:4]}.{d[4:6]}.{d[6:]}" if len(d)==8 else d for d in x_vals_raw]
                if x_vals:
                    fig.add_trace(go.Scatter(
                        x=[x_vals[0], x_vals[-1]], y=[0, 0], mode="lines",
                        line=dict(color=_GRAY, dash="dash", width=1),
                        showlegend=False, hoverinfo="skip",
                    ))
                fig.update_layout(
                    title=f"{name} 历次上榜后 T+N 收益率",
                    height=270, margin=dict(l=40, r=40, t=40, b=30),
                    legend=dict(orientation="h", yanchor="bottom", y=1.02),
                    yaxis_title="收益率 (%)",
                    xaxis=dict(type="category", tickangle=-45),
                )
                st.plotly_chart(fig, use_container_width=True)
    except Exception:
        pass


def _render_stoploss(code: str, trade_date: str):
    """止盈止损参考位"""
    st.markdown("**📌 止盈止损参考**")
    try:
        df_all = storage.read("lhb_top10_prices")
        if df_all.empty:
            st.info("历史数据不足。")
            return

        code_norm = str(code).lstrip("0")
        sub = df_all[df_all["代码"].astype(str).str.lstrip("0") == code_norm]
        sub = sub[sub["T+1买入价"].notna()]

        price_row = storage.query_by_date("lhb_top10_prices", trade_date)
        buy_price = None
        if not price_row.empty and "T+1买入价" in price_row.columns:
            code_p = price_row[price_row["代码"].astype(str).str.lstrip("0") == code_norm]
            if not code_p.empty:
                buy_price = float(code_p.iloc[0]["T+1买入价"])

        cfg = STRATEGY_CONFIG
        if buy_price:
            st.caption(f"T+1 开盘买入价：**¥{buy_price:.3f}**  |  止损：**{cfg['stop_loss']:.0%}**  |  止盈：**+{cfg['stop_profit']:.0%}**")
            sl_price = round(buy_price * (1 + cfg["stop_loss"]), 3)
            sp_price = round(buy_price * (1 + cfg["stop_profit"]), 3)
            st.markdown(
                f"<table style='width:100%;font-size:.9em'>"
                f"<tr><td>🔴 止损触发价</td><td><b>¥{sl_price}</b>（{cfg['stop_loss']:.0%}）</td></tr>"
                f"<tr><td>🟢 止盈触发价</td><td><b>¥{sp_price}</b>（+{cfg['stop_profit']:.0%}）</td></tr>"
                f"<tr><td>⏱️ 最大持有</td><td><b>{cfg['max_hold_days']} 个交易日</b></td></tr>"
                f"</table>",
                unsafe_allow_html=True,
            )
        else:
            st.caption("当日买入价暂无，以下为基于历史数据的分位参考")

        # 基于历史的分位参考
        if not sub.empty:
            rows = []
            for n in [1, 2, 5]:
                col_r = f"T+{n}收盘收益%"
                if col_r in sub.columns:
                    vals = sub[col_r].dropna()
                    if len(vals) >= 3:
                        p25 = float(np.percentile(vals, 25))
                        p50 = float(np.percentile(vals, 50))
                        p75 = float(np.percentile(vals, 75))
                        stop = p25 if p25 < 0 else -5.0
                        rows.append({
                            "持仓": f"T+{n}",
                            "止损参考": f"{stop:+.1f}%",
                            "中位目标": f"{p50:+.1f}%",
                            "乐观目标": f"{p75:+.1f}%",
                            "n": len(vals),
                        })
            if rows:
                st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
                st.caption("⚠️ 基于历史统计，不代表未来表现。")
    except Exception:
        st.info("止盈止损计算暂时不可用。")


def _render_op_checklist(name: str, code: str, advice: dict):
    """每日操作清单（盘后选股 + 集合竞价决策）"""
    st.markdown("**📋 操作清单**")
    cfg = STRATEGY_CONFIG
    lu  = advice.get("mkt_lu")
    ld  = advice.get("mkt_ld")
    pos = advice.get("position", "正常仓位")

    st.markdown(
        f"""
<div style='font-size:.92em;line-height:1.8'>

**📅 盘后选股（18:00~20:00）**
- ① 市场情绪确认：涨停≥{cfg['min_limit_up']}家 且 跌停≤{cfg['max_limit_down']}家
  - 当日：涨停 **{lu or '—'}** / 跌停 **{ld or '—'}** → **{pos}**
- ② 本股综合评分：**{advice['total']}/100** → {advice['action']}
- ③ 净买入额 > {cfg['min_net_buy']/1e7:.0f}百万，席位含机构/知名游资
- ④ 近20日涨幅 < {cfg['max_gain_20d']:.0%}（避免追高）

**⏰ 集合竞价（09:15~09:25）**
- 高开 >7% → 放弃
- 高开 5~7% → 减半仓位
- 高开 2~5% → 正常买入
- 平开/低开 → 正常或加仓

**📌 持仓执行**
- 止损：触发 **{cfg['stop_loss']:.0%}** → 立即卖出
- 止盈：触发 **+{cfg['stop_profit']:.0%}** → 分批减仓
- 最长持有：**{cfg['max_hold_days']} 个交易日**
- 每日最多：**{cfg['top_n']} 只**，单只不超总资金 **30%**

</div>
""",
        unsafe_allow_html=True,
    )


# ============================================================
# 批量评分视图
# ============================================================

def _render_batch(codes: list, names: list, trade_date: str, top10: pd.DataFrame, mkt: dict,
                  lianban_map: dict | None = None):
    st.divider()
    st.subheader("③ Top10 批量评分对比")

    results  = []
    progress = st.progress(0, text="批量评分中...")
    for i, (code, name) in enumerate(zip(codes, names)):
        progress.progress((i + 1) / len(codes), text=f"评分 {name}（{i+1}/{len(codes)}）...")
        kline    = _get_kline(code, trade_date)
        fund_flow = _get_fund_flow(code)
        seat_df  = _get_lhb_seat_detail(code, trade_date)
        hist     = _get_history_winrate(code)
        lhb_row  = top10.iloc[i] if i < len(top10) else pd.Series()

        lianban_count = (lianban_map or {}).get(code, 0)
        t_r = _score_tech(kline, trade_date, lianban_count=lianban_count)
        f_r = _score_fund(lhb_row, fund_flow, seat_df)
        h_r = _score_history(hist)
        tot = t_r["score"] + f_r["score"] + h_r["score"]
        adv = _gen_advice(tot, t_r, f_r, hist)
        adv = _apply_market_filter(adv, mkt)

        jg_tag = f"机构{f_r['jigou_buy']}席" if f_r.get("jigou_buy") else ""
        fy_tag = "/".join(f_r.get("famous_buy", []))
        seat_summary = " | ".join(filter(None, [jg_tag, fy_tag])) or "—"

        # 净买入强度：优先从 f_r 取（fetch_signal 已计算），降级从 lhb_row 估算
        _strength = f_r.get("net_buy_strength_pct")
        if _strength is None:
            _net_buy    = lhb_row.get("龙虎榜净买额") or 0
            _circ_val   = lhb_row.get("流通市值") or lhb_row.get("流通股本") or 0
            try:
                _strength = round(float(_net_buy) / float(_circ_val) * 100, 3) if float(_circ_val) > 0 else None
            except Exception:
                _strength = None
        _strength_str = f"{_strength:.2f}%" if _strength is not None else "—"

        results.append({
            "排名": i + 1,
            "股票":     f"{name}（{code}）",
            "综合评分": tot,
            "技术面":   t_r["score"],
            "资金面":   f_r["score"],
            "历史胜率": h_r["score"],
            "净买强度":  _strength_str,
            "量比":     f"{t_r.get('volume_ratio'):.2f}" if t_r.get("volume_ratio") is not None else "—",
            "近20日涨幅": f"{t_r.get('gain20', 0):+.1f}%" if t_r.get("gain20") is not None else "—",
            "连板":     f"{lianban_count}板" if lianban_count > 0 else "—",
            "席位亮点": seat_summary,
            "操作建议": adv["action"],
        })
        time.sleep(0.4)

    progress.empty()

    df_res = pd.DataFrame(results).sort_values("综合评分", ascending=False)

    # 堆积条形图
    names_bar = [r["股票"].split("（")[0] for r in results]
    fig = go.Figure()
    fig.add_trace(go.Bar(name="技术面", x=names_bar,
                          y=[r["技术面"] for r in results], marker_color=_BLUE))
    fig.add_trace(go.Bar(name="资金面", x=names_bar,
                          y=[r["资金面"] for r in results], marker_color=_GOLD))
    fig.add_trace(go.Bar(name="历史胜率", x=names_bar,
                          y=[r["历史胜率"] for r in results], marker_color=_RED))
    fig.update_layout(
        barmode="stack", height=360,
        title="Top10 综合评分对比（按龙虎榜排名顺序）",
        margin=dict(l=40, r=40, t=50, b=80),
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
        yaxis_title="评分",
    )
    st.plotly_chart(fig, use_container_width=True)

    # 汇总表格（降序）
    def _fmt(v):
        if v >= 80: return f"🔴 {v}"
        if v >= 65: return f"🟡 {v}"
        if v >= 50: return f"🟠 {v}"
        return f"🟢 {v}"

    df_show = df_res.copy()
    df_show["综合评分"] = df_show["综合评分"].map(_fmt)
    st.dataframe(df_show, use_container_width=True, hide_index=True)
    st.caption("🔴≥80重点关注  🟡65-79谨慎参与  🟠50-64观望  🟢<50回避")
