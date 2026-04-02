"""
模拟炒股 — 交易记录与绩效分析页面
====================================
展示：
  1. 统计卡片（总交易/胜率/累计盈亏/平均盈亏%）
  2. 交易记录明细表（含4维度选股理由展开）
  3. 累计净值曲线（资金变化折线图）
  4. 每笔盈亏柱状图
  5. 按周汇总表
"""

import json
from datetime import datetime, date, timedelta

import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
import streamlit as st

# ─── 路径修复 ────────────────────────────────────────────────────────────
import sys
import os
from pathlib import Path

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))
os.chdir(_ROOT)

from utils.storage import storage


# ─── session_state 缓存工具（替代 @st.cache_data，避免 Python 3.13 semaphore 泄漏）──

import time as _time


def _ss_get(key: str, ttl: int):
    """从 session_state 取缓存，过期返回 None"""
    val = st.session_state.get(f"__cache_{key}")
    ts  = st.session_state.get(f"__cache_ts_{key}", 0)
    if val is not None and (_time.time() - ts) < ttl:
        return val
    return None


def _ss_set(key: str, val):
    """写入 session_state 缓存"""
    st.session_state[f"__cache_{key}"] = val
    st.session_state[f"__cache_ts_{key}"] = _time.time()


# ─── 数据加载（提升到模块顶层，避免在 render() 内重复定义）────────────────────────

def _get_capital_info() -> dict:
    """账户资金信息（session_state 缓存60s）"""
    cached = _ss_get("sim_capital", 60)
    if cached is not None:
        return cached
    try:
        from skills.stock_sim_robot.sim_trade_db import get_available_capital
        result = get_available_capital()
    except Exception:
        result = {"total": 100000.0, "invested": 0.0, "available": 100000.0, "open_count": 0}
    _ss_set("sim_capital", result)
    return result


def _get_health_data() -> tuple:
    """账户健康 + 熔断数据（session_state 缓存120s）"""
    cached = _ss_get("sim_health", 120)
    if cached is not None:
        return cached
    try:
        from skills.stock_sim_robot.sim_trade_db import (
            get_performance_stats, calc_account_health_score, check_circuit_breaker
        )
        stats = get_performance_stats()
        ahs   = calc_account_health_score(stats)
        cb    = check_circuit_breaker(stats=stats)
        result = (stats, ahs, cb)
    except Exception:
        result = ({}, 60, {"allow_open": True, "level": "ok", "reason": "", "ahs": 60})
    _ss_set("sim_health", result)
    return result


def _get_trade_events() -> "pd.DataFrame":
    """交易流水（session_state 缓存60s）"""
    cached = _ss_get("sim_trade_events", 60)
    if cached is not None:
        return cached
    try:
        from skills.stock_sim_robot.sim_trade_db import get_trade_events
        result = get_trade_events(limit=200)
    except Exception:
        result = pd.DataFrame()
    _ss_set("sim_trade_events", result)
    return result


def _get_selection_log_all() -> "pd.DataFrame":
    """选股理由日志（session_state 缓存300s）"""
    cached = _ss_get("sim_selection_log", 300)
    if cached is not None:
        return cached
    try:
        from skills.stock_sim_robot.sim_trade_db import get_selection_log
        result = get_selection_log()
    except Exception:
        result = pd.DataFrame()
    _ss_set("sim_selection_log", result)
    return result


def _get_lhb_top10_for_compare() -> "pd.DataFrame":
    """龙虎榜完整数据（用于同期对比，缓存300s）"""
    cached = _ss_get("sim_lhb_top10", 300)
    if cached is not None:
        return cached
    try:
        result = storage.read("lhb_top10")
    except Exception:
        result = pd.DataFrame()
    _ss_set("sim_lhb_top10", result)
    return result


# ─── 策略加载 / 保存（复用 sim_trade_db 函数）──────────────────────────────

def _load_strategy() -> dict:
    """从 DB 加载策略配置，失败时返回默认值"""
    try:
        from skills.stock_sim_robot.sim_trade_db import load_strategy
        return load_strategy("default")
    except Exception as e:
        st.warning(f"加载策略失败，使用默认值: {e}")
        return {
            "strategy_key":  "default",
            "stop_loss_pct": -5.0,
            "sell1_pct":     3.0,  "sell1_ratio": 1/3,
            "sell2_pct":     5.0,  "sell2_ratio": 1/3,
            "sell3_pct":     8.0,
            "description":   "三档分批卖出策略（默认值）",
            "updated_at":    "",
            "updated_by":    "",
        }


def _save_strategy(strategy: dict) -> bool:
    """将策略保存到 DB"""
    try:
        from skills.stock_sim_robot.sim_trade_db import save_strategy
        return save_strategy(strategy, updated_by="frontend")
    except Exception as e:
        st.error(f"保存策略失败: {e}")
        return False


# ─── 数据加载 ────────────────────────────────────────────────────────────

def _load_trades(limit: int = 800) -> pd.DataFrame:
    """从 PostgreSQL 加载 sim_trades（默认限制最近 N 条）"""
    try:
        engine = storage._get_engine()
        from sqlalchemy import text
        sql = text("""
            SELECT *
            FROM sim_trades
            ORDER BY created_at DESC
            LIMIT :limit
        """)
        df = pd.read_sql(sql, engine, params={"limit": int(limit)})
        return df
    except Exception as e:
        st.error(f"加载交易记录失败: {e}")
        return pd.DataFrame()


def _load_summary() -> pd.DataFrame:
    """从 PostgreSQL 加载 sim_summary"""
    try:
        engine = storage._get_engine()
        from sqlalchemy import text
        sql = text("SELECT * FROM sim_summary ORDER BY week_tag DESC")
        df = pd.read_sql(sql, engine)
        return df
    except Exception as e:
        return pd.DataFrame()


def _fmt_pct(v) -> str:
    if v is None or pd.isna(v):
        return "—"
    return f"{v:+.2f}%"


def _fmt_price(v) -> str:
    if v is None or pd.isna(v):
        return "—"
    return f"¥{v:.3f}"


def _source_label(src: str) -> str:
    mapping = {
        "akshare_spot":       "东财实时",
        "akshare_min":        "分钟K线",
        "akshare_daily_hist": "历史日K",
        "akshare_daily":      "历史日K",
        "failed":             "获取失败",
    }
    return mapping.get(str(src) if src else "", src or "—")


# ─── 同天竞品实时对比组件 ────────────────────────────────────────────────

def _render_same_day_competitor_widget(open_df: "pd.DataFrame") -> None:
    """
    在持仓列表下方显示「同天竞品」实时对比组件。
    展示当前持仓股 vs 同天未选股票的今日涨幅对比。
    若未选股票涨幅领先持仓股 >3%，橙色高亮提示。
    """
    try:
        from modules.comparison_analyzer import build_comparison_table

        with st.expander("🔍 同天候选股对比（当前持仓 vs 同天未买入股票）", expanded=False):
            any_data = False
            for _, pos_row in open_df.iterrows():
                trade_date = str(pos_row.get("trade_date", ""))
                held_code  = str(pos_row.get("code", ""))
                held_name  = str(pos_row.get("name", ""))
                if not trade_date:
                    continue

                cache_key = f"sim_competitor_{trade_date}"
                if cache_key not in st.session_state:
                    table = build_comparison_table(trade_date)
                    st.session_state[cache_key] = table
                else:
                    table = st.session_state[cache_key]

                if table.empty:
                    continue

                any_data = True
                st.markdown(f"**{trade_date} — 持仓股: {held_name}({held_code})**")

                # 获取已持仓股的 T+5 收益
                held_row = table[table["代码"] == held_code]
                held_t5  = float(held_row.iloc[0]["T+5收益%"]) if not held_row.empty and pd.notna(held_row.iloc[0].get("T+5收益%")) else None

                others = table[table["代码"] != held_code].copy()
                if others.empty:
                    st.caption("无其他候选股数据")
                    continue

                display_cols = [c for c in ["代码", "名称", "评分", "T+1收益%", "T+5收益%", "未选主因"] if c in others.columns]
                others_display = others[display_cols].copy()

                # 检查是否有候选股 T+5 收益明显领先（>3%）
                if held_t5 is not None and "T+5收益%" in others_display.columns:
                    leading = others_display[
                        others_display["T+5收益%"].fillna(-999) > held_t5 + 3
                    ]
                    if not leading.empty:
                        st.warning(
                            f"⚠️ {len(leading)} 只同天候选股 T+5 收益领先持仓股 >3%："
                            f" {', '.join(leading['代码'].tolist())}"
                        )

                st.dataframe(others_display, use_container_width=True, hide_index=True)
                st.caption(f"持仓股 {held_code} T+5收益: {held_t5:+.2f}%" if held_t5 is not None else f"持仓股 {held_code} T+5收益: 尚未填充")

            if not any_data:
                st.info("暂无同天候选对比数据（等待 daily_update step10 填充 T+5 收益）")
                if st.button("快速跳转到「选股决策复盘」页面"):
                    st.switch_page("pages/trade_comparison.py")

    except Exception as e:
        # 不阻断主页面渲染
        import logging
        logging.getLogger(__name__).debug(f"_render_same_day_competitor_widget 非致命: {e}")


# ─── 主渲染函数 ──────────────────────────────────────────────────────────

def render():
    st.title("🤖 模拟炒股 — 交易记录与绩效")
    st.caption("基于龙虎榜主板策略 | **T日**=上榜日 · **T+1**=次日集合竞价买入 · **T+2**=第2交易日分批卖出 | 总账户资金10万元")

    with st.expander("⚙️ 页面性能设置", expanded=False):
        st.session_state["sim_trading_load_limit"] = st.number_input(
            "交易记录加载上限",
            min_value=200,
            max_value=5000,
            value=int(st.session_state.get("sim_trading_load_limit", 800) or 800),
            step=200,
            help="仅影响首屏加载的数据量，值越大页面越慢。",
        )

    # ── 加载数据 ─────────────────────────────────────────────────────────
    with st.spinner("加载交易数据..."):
        # 先限制查询量，避免首屏全量读表
        load_limit = int(st.session_state.get("sim_trading_load_limit", 800) or 800)
        df = _load_trades(limit=load_limit)
        df_summary = _load_summary()

    if df.empty:
        st.info("暂无模拟交易记录。等待每日18:45盘后 Phase A 自动运行后出现数据。")
        return

    # ── 数据预处理 ────────────────────────────────────────────────────────
    df["trade_date"] = df["trade_date"].astype(str)
    df["created_at"] = pd.to_datetime(df["created_at"])
    df["buy_price"]  = pd.to_numeric(df["buy_price"],  errors="coerce")
    df["sell_price"] = pd.to_numeric(df["sell_price"], errors="coerce")
    df["profit"]     = pd.to_numeric(df["profit"],     errors="coerce")
    df["profit_pct"] = pd.to_numeric(df["profit_pct"], errors="coerce")
    df["signal_score"] = pd.to_numeric(df["signal_score"], errors="coerce")
    df["t2_win_rate"]  = pd.to_numeric(df["t2_win_rate"],  errors="coerce")

    closed = df[df["status"] == "closed"].copy()
    open_  = df[df["status"] == "open"].copy()

    # ── 账户资金卡片（总资金10万制）─────────────────────────────────────
    cap = _get_capital_info()
    total_profit = closed["profit"].sum() if not closed.empty else 0
    nav = 1.0 + total_profit / cap["total"] if cap["total"] > 0 else 1.0

    cap_col1, cap_col2, cap_col3, cap_col4 = st.columns(4)
    cap_col1.metric(
        "🏦 账户总资金",
        f"¥{cap['total']:,.0f}",
        help="模拟账户总本金（固定10万元）",
    )
    cap_col2.metric(
        "💼 已投入仓位",
        f"¥{cap['invested']:,.0f}",
        delta=f"{cap['invested']/cap['total']*100:.0f}% 已用" if cap["total"] > 0 else None,
        help=f"当前 open 持仓合计已用资金（{cap['open_count']} 只）",
    )
    cap_col3.metric(
        "💵 可用余额",
        f"¥{cap['available']:,.0f}",
        delta=f"{cap['available']/cap['total']*100:.0f}% 可用" if cap["total"] > 0 else None,
        delta_color="normal",
        help="可用于下次买入的资金",
    )
    cap_col4.metric(
        "📊 账户净值",
        f"{nav:.4f}",
        delta=f"{(nav-1)*100:+.2f}%" if nav != 1.0 else None,
        delta_color="normal",
        help="累计净值 = 1.0 + 历史总盈亏 / 初始10万",
    )

    # ── 顶部统计卡片 ─────────────────────────────────────────────────────
    total  = len(closed)
    wins   = len(closed[closed["profit_pct"] > 0]) if total > 0 else 0
    win_rate = wins / total * 100 if total > 0 else 0
    total_profit = closed["profit"].sum() if total > 0 else 0
    avg_pct = closed["profit_pct"].mean() if total > 0 else 0

    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("📊 已平仓笔数", total)
    col2.metric("🎯 胜率", f"{win_rate:.1f}%" if total > 0 else "—")
    col3.metric("💰 累计盈亏", f"¥{total_profit:,.0f}",
                delta=f"{total_profit:+,.0f}" if total_profit != 0 else None)
    col4.metric("📈 平均盈亏%", _fmt_pct(avg_pct))
    col5.metric("📌 当前持仓", f"{len(open_)} 笔")

    st.divider()

    # ── 账户健康仪表盘 + 熔断状态告警 ───────────────────────────────────
    _stats, _ahs, _cb = _get_health_data()
    _total_closed = _stats.get("total_closed", 0)

    # ── AHS 图标与颜色 ───────────────────────────────────────────────────
    if _ahs >= 70:
        _ahs_icon, _ahs_color, _ahs_label = "🟢", "#33CC66", "强势期"
    elif _ahs >= 50:
        _ahs_icon, _ahs_color, _ahs_label = "🟡", "#FFCC00", "正常期"
    elif _ahs >= 30:
        _ahs_icon, _ahs_color, _ahs_label = "🟠", "#FFA500", "修复期"
    else:
        _ahs_icon, _ahs_color, _ahs_label = "🔴", "#E84040", "危险期"

    # ── 运行模式横幅（顶置，最显眼位置）─────────────────────────────
    # 判断是否已人工开启 SKIP_CIRCUIT_BREAKER
    import os as _os
    _skip_env = _os.getenv("SKIP_CIRCUIT_BREAKER", "0") == "1"
    # 也支持 session_state 内的临时覆盖（页面开关）
    _skip_session = st.session_state.get("_skip_cb", False)
    _cb_skipped   = _skip_env or _skip_session
    _cb_mode      = _cb.get("mode", "normal")
    _cb_mode_label = _cb.get("mode_label", "🟢 正常")

    if _cb_mode == "crisis" and not _cb_skipped:
        st.markdown(
            "<div style='padding:14px 18px;background:#2d0000;border-left:6px solid #E84040;"
            "border-radius:8px;margin-bottom:12px;'>"
            "<span style='font-size:1.3em;font-weight:bold;color:#E84040;'>🔴 危机模式 — 仓位已降至20%，门槛80分</span><br>"
            f"<span style='color:#ffaaaa;'>{_cb.get('reason','')}</span><br>"
            "<span style='color:#888;font-size:0.9em;'>80分+信号仍可参与；连赢2笔自动恢复警戒模式。如需手动覆盖请在下方操作。</span>"
            "</div>",
            unsafe_allow_html=True,
        )
    elif _cb_mode == "alert" and not _cb_skipped:
        st.markdown(
            "<div style='padding:12px 18px;background:#2b1200;border-left:6px solid #FFA500;"
            "border-radius:8px;margin-bottom:12px;'>"
            "<span style='font-size:1.2em;font-weight:bold;color:#FFA500;'>🟠 警戒模式 — 仓位40%，门槛70分</span><br>"
            f"<span style='color:#ffd580;'>{_cb.get('reason','')}</span>"
            "</div>",
            unsafe_allow_html=True,
        )
    elif _cb_mode == "cautious" and not _cb_skipped:
        st.markdown(
            "<div style='padding:12px 18px;background:#2b2000;border-left:6px solid #FFCC00;"
            "border-radius:8px;margin-bottom:12px;'>"
            "<span style='font-size:1.1em;font-weight:bold;color:#FFCC00;'>🟡 谨慎模式 — 仓位70%，门槛60分</span><br>"
            f"<span style='color:#ffd580;'>{_cb.get('reason','')}</span>"
            "</div>",
            unsafe_allow_html=True,
        )
    elif _cb_skipped:
        st.markdown(
            "<div style='padding:10px 18px;background:#002b1a;border-left:6px solid #33CC66;"
            "border-radius:8px;margin-bottom:12px;'>"
            "✅ <b style='color:#33CC66;'>风控已人工覆盖</b> — Phase A 将以正常模式开仓"
            "</div>",
            unsafe_allow_html=True,
        )

    # ── AHS 仪表盘卡片行 ─────────────────────────────────────────────────
    st.markdown("##### 🏥 账户健康仪表盘")
    _h1, _h2, _h3, _h4, _h5 = st.columns(5)

    _h1.metric(
        f"{_ahs_icon} AHS 健康评分",
        f"{_ahs}/100" if _total_closed >= 3 else "积累中",
        delta=_ahs_label,
        delta_color="off",
        help=(
            "账户健康评分（AHS），基于近10笔实战绩效计算（0~100）。\n\n"
            "评分组成：近10笔胜率（±25分） + 平均盈亏（±15分）+ 连续亏损惩罚（最多-20分）+ 今日盈亏修正（±10分）\n\n"
            "不足5笔时默认60分（中性），随交易积累越来越准确。"
        ),
    )
    _h2.metric(
        "📊 近10笔胜率",
        f"{_stats.get('recent_win_rate', 0):.0f}%" if _total_closed >= 1 else "—",
        delta=f"近{_stats.get('recent_trades', 0)}笔",
        delta_color="off",
        help="近10笔已平仓交易中盈利笔数比例",
    )
    _cl = _stats.get("consecutive_losses", 0)
    _h3.metric(
        "🔴 连续亏损",
        f"{_cl} 笔" if _total_closed >= 1 else "—",
        delta="⚠️ 接近熔断" if _cl == 2 else ("连续盈利" if _cl == 0 else None),
        delta_color="inverse" if _cl >= 2 else "off",
        help="当前连续亏损笔数。连续亏损 ≥ 3 笔将触发熔断，跳过当日开仓。",
    )
    _wpnl = _stats.get("week_pnl", 0.0)
    _h4.metric(
        "📅 本周盈亏",
        f"{_wpnl:+.1f}%" if _total_closed >= 1 else "—",
        delta="⚠️ 接近熔断阈值" if -5.0 < _wpnl < -3.0 else None,
        delta_color="inverse" if _wpnl < -3 else "off",
        help="本周已结算盈亏 / 总资金10万。超过 -5% 触发周熔断。",
    )
    # 运行模式指示器（4档）
    _cb_mode      = _cb.get("mode", "normal")
    _cb_mode_label = _cb.get("mode_label", "🟢 正常")
    _coef         = _cb.get("position_coef", 1.0)
    _min_sc       = _cb.get("min_score", 50)
    if _cb_mode == "normal":
        _cb_display = "🟢 正常"
        _cb_delta   = "仓位100% 门槛50分"
    elif _cb_mode == "cautious":
        _cb_display = "🟡 谨慎"
        _cb_delta   = "仓位70% 门槛60分"
    elif _cb_mode == "alert":
        _cb_display = "🟠 警戒"
        _cb_delta   = "仓位40% 门槛70分"
    else:  # crisis
        _cb_display = "🔴 危机"
        _cb_delta   = "仓位20% 门槛80分"
    _h5.metric(
        "⚡ 运行模式",
        _cb_display,
        delta=_cb_delta,
        delta_color="off",
        help=(
            "分级降速模式（取代原二元熔断）：\n\n"
            "🟢 正常：仓位100%，门槛50分，每日最多5只\n"
            "🟡 谨慎：仓位70%，门槛60分，每日最多3只（连亏3笔/日亏>2%/周亏>3%）\n"
            "🟠 警戒：仓位40%，门槛70分，每日最多1只（连亏5笔/周亏>5%/AHS<45）\n"
            "🔴 危机：仓位20%，门槛80分，每日最多1只（连亏7笔/月亏>8%/回撤>12%）\n\n"
            "自动恢复：连赢2笔自动升一级。危机模式下80分+信号仍可参与。"
        ),
    )

    # ── 手动覆盖开关区 ───────────────────────────────────────────────────
    with st.expander("🔧 风控手动干预（谨慎操作）", expanded=(_cb_mode in ("crisis", "alert") and not _cb_skipped)):
        _sw_col1, _sw_col2 = st.columns([3, 2])
        with _sw_col1:
            if _cb_skipped:
                st.success("✅ 风控已被人工覆盖，Phase A 将以正常模式运行。")
                if st.button("🔒 重新启用风控保护", type="secondary"):
                    st.session_state["_skip_cb"] = False
                    _load_health_data.clear()
                    st.rerun()
            else:
                st.warning(
                    "⚠️ 以下操作将覆盖当前分级降速模式，允许 Phase A 以正常模式执行。\n\n"
                    "**适用场景：** 你已手动评估风险，确认当前市场适合操作（如大盘超跌后反弹机会）。\n\n"
                    "**范围：** 仅本次浏览器 Session 有效，关闭页面后自动失效。"
                )
                if st.button("🔓 手动覆盖风控（按正常模式执行）", type="primary",
                             disabled=(_cb_mode == "normal")):
                    st.session_state["_skip_cb"] = True
                    _load_health_data.clear()
                    st.rerun()
        with _sw_col2:
            st.caption("**当前模式说明**")
            st.caption(_cb.get("reason") or f"当前运行模式：{_cb_mode_label}，无需特殊处理。")
            if _total_closed >= 3:
                st.caption(
                    f"仓位系数：{_coef*100:.0f}%\n\n"
                    f"入场门槛：≥{_min_sc}分\n\n"
                    f"近10笔均盈亏：{_stats.get('recent_avg_pnl',0):+.2f}%\n\n"
                    f"本月盈亏：{_stats.get('month_pnl',0):+.1f}%\n\n"
                    f"最大回撤：{_stats.get('max_drawdown_pct',0):.1f}%"
                )

    st.divider()

    # ── Tabs 布局 ─────────────────────────────────────────────────────────
    tab1, tab2, tab3, tab4, tab5, tab6, tab7, tab8 = st.tabs(
        [
            "📋 交易记录",
            "📈 净值曲线 & 盈亏图",
            "📅 按周汇总",
            "🔍 选股理由详情",
            "🪵 机器人日志",     # 移至 Tab5（原 Tab6）
            "📈 价格轨迹",       # 移至 Tab6（原 Tab7）
            "⚙️ 策略管理",       # 合并 Tab5 策略配置 + 原 Tab8 策略工坊
            "📋 操作审计",       # 移至 Tab8（原 Tab9）
        ]
    )

    # ═══════════════════════════════════════════════════════════════════
    # TAB 1：交易记录表
    # ═══════════════════════════════════════════════════════════════════
    with tab1:
        st.subheader("📋 全部交易记录")

        # 筛选控件
        c1, c2, c3 = st.columns([2, 2, 2])
        status_filter = c1.selectbox("状态", ["全部", "持仓中(open)", "已平仓(closed)"])
        week_list = ["全部"] + sorted(df["week_tag"].dropna().unique().tolist(), reverse=True)
        week_filter = c2.selectbox("按周筛选", week_list)

        disp = df.copy()
        if status_filter == "持仓中(open)":
            disp = disp[disp["status"] == "open"]
        elif status_filter == "已平仓(closed)":
            disp = disp[disp["status"] == "closed"]
        if week_filter != "全部":
            disp = disp[disp["week_tag"] == week_filter]

        # 展示列
        cols_show = [
            "trade_date", "code", "name", "signal_score", "t2_win_rate",
            "buy_price", "buy_source", "list_price",
            "sell_price", "sell_type", "profit", "profit_pct",
            "status", "week_tag",
        ]
        cols_show = [c for c in cols_show if c in disp.columns]
        disp_show = disp[cols_show].copy()

        # 重命名
        col_rename = {
            "trade_date":   "T日",
            "code":         "代码",
            "name":         "名称",
            "signal_score": "综合评分",
            "t2_win_rate":  "T+2胜率%",
            "buy_price":    "买入价",
            "buy_source":   "行情来源",
            "list_price":   "挂牌价",
            "sell_price":   "成交价",
            "sell_type":    "平仓类型",
            "profit":       "盈亏(元)",
            "profit_pct":   "盈亏%",
            "status":       "状态",
            "week_tag":     "周",
        }
        disp_show = disp_show.rename(columns=col_rename)

        # buy_source 翻译
        if "行情来源" in disp_show.columns:
            disp_show["行情来源"] = disp_show["行情来源"].apply(
                lambda x: _source_label(x) if x else "—"
            )

        # ── 价格趋势标识（买入价 → 成交价涨跌方向）────────────────────────
        # 注意：此时 买入价/成交价 还是原始数值（尚未被 _fmt_price_col 格式化），
        # 所以直接用 float() 解析即可，不需要 strip "¥"
        def _trend_badge(row):
            """根据买入价→成交价计算趋势标识（在数值格式化之前调用）"""
            import math
            try:
                bp_raw = row.get("买入价", None)
                sp_raw = row.get("成交价", None)
                # 成交价为空 = 持仓中
                if sp_raw is None or (isinstance(sp_raw, float) and math.isnan(sp_raw)):
                    return "⏳"  # 持仓中，未成交
                if str(sp_raw).strip() in ("", "nan", "None"):
                    return "⏳"
                bp = float(bp_raw) if bp_raw is not None else 0
                sp = float(sp_raw)
                if bp <= 0 or sp <= 0:
                    return "—"
                pct = (sp / bp - 1) * 100
                if pct > 0.5:
                    return f"▲ +{pct:.1f}%"
                elif pct < -0.5:
                    return f"▼ {pct:.1f}%"
                else:
                    return f"→ {pct:+.1f}%"
            except Exception:
                return "—"

        if "买入价" in disp_show.columns:
            disp_show["趋势"] = disp_show.apply(_trend_badge, axis=1)
            # 把趋势列插到成交价前面
            cols_order = list(disp_show.columns)
            if "成交价" in cols_order:
                idx = cols_order.index("成交价")
                cols_order.remove("趋势")
                cols_order.insert(idx, "趋势")
                disp_show = disp_show[cols_order]

        # 颜色渲染与格式化
        def _color_profit(val):
            try:
                v = float(val)
                if v > 0:   return "color: #E84040; font-weight: bold"
                elif v < 0: return "color: #33CC66; font-weight: bold"
            except:
                pass
            return ""

        def _format_status(val):
            if val == "open": return "🟢 持仓中"
            if val == "closed": return "⚪ 已平仓"
            return val

        if "状态" in disp_show.columns:
            disp_show["状态"] = disp_show["状态"].apply(_format_status)

        # 在渲染前，将数值列预格式化为字符串，避免 TextColumn 与 Styler.format() 冲突
        # （Streamlit TextColumn 不支持 Styler 数值格式化，会导致数值显示为 "—"）
        def _fmt_price_col(v):
            import math
            try:
                f = float(v)
                if math.isnan(f):
                    return "—"
                return f"¥{f:.3f}" if f > 0 else "—"
            except (TypeError, ValueError):
                return "—" if (v is None or str(v).strip() in ("", "nan", "None")) else str(v)

        def _fmt_pct_col(v, prefix="+"):
            import math
            try:
                f = float(v)
                if math.isnan(f):  # float('nan') 不抛异常，需显式检查
                    return "—"
                return f"{f:+.2f}%"
            except (TypeError, ValueError):
                return "—" if (v is None or str(v).strip() in ("", "nan", "None")) else str(v)

        def _fmt_money_col(v):
            import math
            try:
                f = float(v)
                if math.isnan(f):
                    return "—"
                return f"¥{f:.2f}"
            except (TypeError, ValueError):
                return "—" if (v is None or str(v).strip() in ("", "nan", "None")) else str(v)

        for _pc in ["买入价", "挂牌价", "成交价"]:
            if _pc in disp_show.columns:
                disp_show[_pc] = disp_show[_pc].apply(_fmt_price_col)
        for _mc in ["盈亏(元)"]:
            if _mc in disp_show.columns:
                disp_show[_mc] = disp_show[_mc].apply(_fmt_money_col)
        for _rc in ["盈亏%"]:
            if _rc in disp_show.columns:
                disp_show[_rc] = disp_show[_rc].apply(_fmt_pct_col)
        if "T+2胜率%" in disp_show.columns:
            disp_show["T+2胜率%"] = disp_show["T+2胜率%"].apply(
                lambda v: f"{float(v):.1f}%" if str(v) not in ("", "nan", "None") else "—"
            )

        # 使用 column_config 进行高级列宽与格式配置
        st.dataframe(
            disp_show.style.map(_color_profit, subset=["盈亏%"] if "盈亏%" in disp_show.columns else [])
                           .map(_color_profit, subset=["盈亏(元)"] if "盈亏(元)" in disp_show.columns else []),
            use_container_width=True, 
            height=450,
            hide_index=True,
            column_config={
                "T日": st.column_config.TextColumn("🗓️ T日",
                    help="信号日（龙虎榜上榜日期 YYYYMMDD）", width="small"),
                "代码": st.column_config.TextColumn("🔢 代码",
                    help="A股股票代码（6位）", width="small"),
                "名称": st.column_config.TextColumn("🏷️ 名称",
                    help="股票名称", width="small"),
                "状态": st.column_config.TextColumn("🚦 状态",
                    help="持仓中=尚未卖出；已平仓=卖出完成", width="small"),
                "综合评分": st.column_config.NumberColumn("🎯 评分",
                    help="龙虎榜综合评分（席位强度 + 资金净买入 + T+2历史胜率加权，满分100）", width="small"),
                "T+2胜率%": st.column_config.TextColumn("📊 T+2胜率%",
                    help="该股过去龙虎榜上榜后 T+2 日收益为正的历史概率", width="small"),
                "买入价": st.column_config.TextColumn("💰 买入价",
                    help="T+1 日（信号日次日）开盘集合竞价成交价，由系统 09:35 自动回填", width="small"),
                "挂牌价": st.column_config.TextColumn("🎯 挂牌价",
                    help="目标卖出价（基于买入价的目标涨幅，T+2日最高价达到时触发止盈）", width="small"),
                "行情来源": st.column_config.TextColumn("📡 行情来源",
                    help="买入价的数据来源：新浪实时/东财实时/分钟K线/历史日K", width="small"),
                "趋势": st.column_config.TextColumn("📈 趋势",
                    help="买入价 → 成交价的涨跌方向和幅度（▲涨/▼跌/→平/⏳持仓中）", width="small"),
                "成交价": st.column_config.TextColumn("💵 成交价",
                    help="实际卖出成交价（止盈=挂牌价，止损/持有到期=T+2收盘价）", width="small"),
                "平仓类型": st.column_config.TextColumn("📋 平仓类型",
                    help="止盈：达到目标价；止损：跌破-5%；持有到期：超过3交易日；超时强制：超过5交易日", width="small"),
                "盈亏(元)": st.column_config.TextColumn("💰 盈亏(元)",
                    help="本笔交易绝对盈亏金额（元），以10万本金计算", width="small"),
                "盈亏%": st.column_config.TextColumn("📈 盈亏%",
                    help="本笔交易盈亏百分比 = (成交价 - 买入价) / 买入价 × 100%", width="small"),
                "周": st.column_config.TextColumn("📅 周",
                    help="交易所在的周（ISO年-周次，如 2026-W13 表示2026年第13周）", width="small"),
            }
        )

        # 持仓中的提示
        if not open_.empty:
            st.info(f"📌 当前持仓中 {len(open_)} 笔，买入价等待 T+1 开盘后 09:35 自动回填。")

        # ── 同天竞品实时对比 ─────────────────────────────────────────────
        if not open_.empty:
            _render_same_day_competitor_widget(open_)

        # ── 交易流水视图（切换Tab）──────────────────────────────────────
        st.divider()
        st.markdown("#### 📜 交易流水（每次买卖各一条记录）")
        st.caption("每笔买入/分批卖出/止损/平仓单独显示一条流水记录（新交易自动记录，旧历史数据无流水）")

        events_df = _get_trade_events()

        if events_df.empty:
            st.info("📭 暂无交易流水记录。流水记录从下次 Phase B 买入回填后开始自动生成。")
        else:
            # 过滤（可选）
            evt_filter_col1, evt_filter_col2 = st.columns([2, 2])
            evt_type_filter = evt_filter_col1.selectbox(
                "按类型过滤",
                ["全部", "BUY", "SELL_T1", "SELL_T2", "SELL_T3", "STOP_LOSS", "EXPIRED", "FORCE_CLOSE"],
                key="evt_type_filter",
            )
            evt_code_filter = evt_filter_col2.text_input("按代码/名称过滤", placeholder="如: 002361", key="evt_code_filter")

            disp_ev = events_df.copy()
            if evt_type_filter != "全部":
                disp_ev = disp_ev[disp_ev["event_type"] == evt_type_filter]
            if evt_code_filter.strip():
                kw = evt_code_filter.strip().lower()
                disp_ev = disp_ev[
                    disp_ev["code"].str.lower().str.contains(kw, na=False)
                    | disp_ev["name"].fillna("").str.lower().str.contains(kw, na=False)
                ]

            # 美化列名
            _EVENT_TYPE_ZH = {
                "BUY":         "💰 买入",
                "SELL_T1":     "🟡 卖出档1",
                "SELL_T2":     "🟡 卖出档2",
                "SELL_T3":     "🟢 卖出档3(清)",
                "STOP_LOSS":   "🔴 止损",
                "EXPIRED":     "⚪ 持有到期",
                "FORCE_CLOSE": "🟠 强制平仓",
            }
            disp_ev["操作类型"] = disp_ev["event_type"].map(
                lambda x: _EVENT_TYPE_ZH.get(str(x), str(x))
            )

            cols_ev = [c for c in [
                "event_date", "操作类型", "name", "code", "price", "shares", "amount",
                "profit", "profit_pct", "note",
            ] if c in ["event_date", "操作类型", "name", "code", "price", "shares", "amount", "profit", "profit_pct", "note"]
                and (c in disp_ev.columns or c == "操作类型")]

            # 格式化
            def _fmt_ev_pct(v):
                try:
                    f = float(v)
                    import math
                    if math.isnan(f): return "—"
                    return f"{f:+.2f}%"
                except: return "—"

            disp_ev2 = disp_ev.copy()
            if "profit_pct" in disp_ev2.columns:
                disp_ev2["profit_pct"] = disp_ev2["profit_pct"].apply(_fmt_ev_pct)
            if "price" in disp_ev2.columns:
                disp_ev2["price"] = disp_ev2["price"].apply(
                    lambda v: f"¥{float(v):.3f}" if v is not None else "—"
                )
            if "amount" in disp_ev2.columns:
                disp_ev2["amount"] = disp_ev2["amount"].apply(
                    lambda v: f"¥{float(v):,.0f}" if v is not None else "—"
                )
            if "profit" in disp_ev2.columns:
                disp_ev2["profit"] = disp_ev2["profit"].apply(
                    lambda v: f"¥{float(v):+,.2f}" if v is not None else "—"
                )

            # 重命名列
            disp_ev2 = disp_ev2.rename(columns={
                "event_date": "日期", "name": "名称", "code": "代码",
                "price": "价格", "shares": "股数", "amount": "金额",
                "profit": "本次盈亏(元)", "profit_pct": "盈亏%", "note": "备注",
            })
            final_cols = [c for c in ["日期", "操作类型", "名称", "代码", "价格", "股数", "金额", "本次盈亏(元)", "盈亏%", "备注"] if c in disp_ev2.columns]

            def _ev_color(val):
                try:
                    if "%" in str(val):
                        f = float(str(val).replace("%", "").replace("+", ""))
                        if f > 0: return "color: #E84040; font-weight: bold"
                        elif f < 0: return "color: #33CC66; font-weight: bold"
                except: pass
                return ""

            st.dataframe(
                disp_ev2[final_cols].style.map(_ev_color, subset=["盈亏%"] if "盈亏%" in final_cols else []),
                use_container_width=True,
                hide_index=True,
                height=400,
                column_config={
                    "日期": st.column_config.TextColumn("🗓️ 日期", width="small"),
                    "操作类型": st.column_config.TextColumn("📋 类型", width="small"),
                    "名称": st.column_config.TextColumn("🏷️ 名称", width="small"),
                    "代码": st.column_config.TextColumn("🔢 代码", width="small"),
                    "价格": st.column_config.TextColumn("💰 成交价", width="small"),
                    "股数": st.column_config.NumberColumn("📊 股数", width="small"),
                    "金额": st.column_config.TextColumn("💵 金额", width="small"),
                    "本次盈亏(元)": st.column_config.TextColumn("🏦 盈亏(元)", width="small"),
                    "盈亏%": st.column_config.TextColumn("📈 盈亏%", width="small"),
                    "备注": st.column_config.TextColumn("📝 备注"),
                }
            )
            st.caption(f"共 {len(disp_ev)} 条流水（已过滤）")

    # ═══════════════════════════════════════════════════════════════════
    # TAB 2：净值曲线 + 每笔盈亏柱状图
    # ═══════════════════════════════════════════════════════════════════
    with tab2:
        if closed.empty:
            st.info("暂无已平仓交易，完成第一笔交易后将显示图表。")
        else:
            closed_sorted = closed.sort_values("created_at").copy()
            closed_sorted["cumulative_profit"] = closed_sorted["profit"].fillna(0).cumsum()
            closed_sorted["nav"] = 1.0 + closed_sorted["cumulative_profit"] / 100000  # 初始10万为基准

            # ── 累计净值曲线 ─────────────────────────────────────────
            st.subheader("📈 累计净值曲线（资金变化）")
            fig_nav = go.Figure()
            fig_nav.add_trace(go.Scatter(
                x=closed_sorted["created_at"],
                y=closed_sorted["nav"],
                mode="lines+markers",
                name="累计净值",
                line=dict(color="#e8553e", width=2),
                marker=dict(size=6),
                hovertemplate="<b>%{x|%Y-%m-%d}</b><br>净值: %{y:.4f}<extra></extra>",
            ))
            fig_nav.add_hline(y=1.0, line_dash="dash", line_color="gray", annotation_text="初始净值=1.00")
            fig_nav.update_layout(
                title="模拟账户累计净值（初始 10 万元）",
                xaxis_title="日期",
                yaxis_title="净值",
                height=360,
                template="plotly_white",
                hovermode="x unified",
            )
            st.plotly_chart(fig_nav, use_container_width=True)

            # ── 每笔盈亏柱状图 ───────────────────────────────────────
            st.subheader("📊 每笔交易盈亏（%）")
            colors = ["#d73027" if v >= 0 else "#1a9641" for v in closed_sorted["profit_pct"].fillna(0)]
            fig_bar = go.Figure()
            fig_bar.add_trace(go.Bar(
                x=[f"{r['name']}({r['trade_date']})" for _, r in closed_sorted.iterrows()],
                y=closed_sorted["profit_pct"].fillna(0),
                marker_color=colors,
                hovertemplate="<b>%{x}</b><br>盈亏: %{y:+.2f}%<extra></extra>",
                name="盈亏%",
            ))
            fig_bar.add_hline(y=0, line_dash="dash", line_color="gray")
            fig_bar.update_layout(
                title="每笔交易盈亏%（红=盈利，绿=亏损）",
                xaxis_title="交易",
                yaxis_title="盈亏%",
                height=320,
                template="plotly_white",
            )
            st.plotly_chart(fig_bar, use_container_width=True)

            # ── 盈亏分布直方图 ───────────────────────────────────────
            st.subheader("📉 盈亏% 分布直方图")
            fig_hist = px.histogram(
                closed_sorted, x="profit_pct", nbins=20,
                title="盈亏% 频率分布",
                color_discrete_sequence=["#e8553e"],
                labels={"profit_pct": "盈亏%"},
                template="plotly_white",
            )
            fig_hist.add_vline(x=0, line_dash="dash", line_color="gray")
            fig_hist.update_layout(height=280)
            st.plotly_chart(fig_hist, use_container_width=True)

    # ═══════════════════════════════════════════════════════════════════
    # TAB 3：按周汇总
    # ═══════════════════════════════════════════════════════════════════
    with tab3:
        st.subheader("📅 按周绩效汇总")
        st.caption("每周为一个统计周期（ISO周次，周一到周五）。盈亏% = 相对买入价的百分比涨跌幅，不含手续费。")

        # 列说明 tooltip 配置（用于 column_config）
        _week_col_config = {
            "周":       st.column_config.TextColumn("📅 周",
                help="ISO周次（如 2026-W13 = 2026年第13周，周一到周五）"),
            "总交易":   st.column_config.NumberColumn("📊 总交易",
                help="本周內已平仓的交易笔数"),
            "盈利笔":   st.column_config.NumberColumn("✅ 盈利笔",
                help="盈亏% > 0 的交易笔数"),
            "亏损笔":   st.column_config.NumberColumn("❌ 亏损笔",
                help="盈亏% < 0 的交易笔数"),
            "胜率%":    st.column_config.NumberColumn("🎯 胜率%",
                format="%.1f%%",
                help="盈利笔 / 总交易 × 100%，越高越好"),
            "总盈亏(元)": st.column_config.NumberColumn("💰 总盈亏(元)",
                format="%.2f",
                help="本周所有已平仓交易的盈亏元额之和（元，以10万/笔为基准）"),
            "平均盈亏%": st.column_config.NumberColumn("📈 平均盈亏%",
                format="%.2f%%",
                help="本周所有交易盈亏%的算术平均値，反映本周整体收益水平"),
            "最大盈利%": st.column_config.NumberColumn("🔥 最大盈利%",
                format="%.2f%%",
                help="本周单笔盈亏%最大的交易（不是元！），反映营业能力上限"),
            "最大亏损%": st.column_config.NumberColumn("💧 最大亏损%",
                format="%.2f%%",
                help="本周单笔盈亏%最小的交易（不是元！），反映最差情况的局面"),
        }

        if df_summary.empty:
            # 自行从 trades 计算
            if not closed.empty:
                g = closed.groupby("week_tag").agg(
                    总交易=("id", "count"),
                    盈利笔=("profit_pct", lambda x: (x > 0).sum()),
                    总盈亏元=("profit", "sum"),
                    平均盈亏=("profit_pct", "mean"),
                    最大盈利=("profit_pct", "max"),
                    最大亏损=("profit_pct", "min"),
                ).reset_index().rename(columns={"week_tag": "周", "总盈亏元": "总盈亏(元)", "平均盈亏": "平均盈亏%", "最大盈利": "最大盈利%", "最大亏损": "最大亏损%"})
                g["胜率%"] = (g["盈利笔"] / g["总交易"] * 100).round(1)
                g = g.sort_values("周", ascending=False)
                _disp_cols = ["周","总交易","盈利笔","胜率%","总盈亏(元)","平均盈亏%","最大盈利%","最大亏损%"]
                st.dataframe(
                    g[[c for c in _disp_cols if c in g.columns]],
                    use_container_width=True,
                    hide_index=True,
                    column_config={k: v for k, v in _week_col_config.items() if k in g.columns},
                )
            else:
                st.info("暂无已平仓记录")
        else:
            disp_s = df_summary.rename(columns={
                "week_tag":       "周",
                "total_trades":   "总交易",
                "win_trades":     "盈利笔",
                "lose_trades":    "亏损笔",
                "win_rate":       "胜率%",
                "total_profit":   "总盈亏(元)",
                "avg_profit_pct": "平均盈亏%",
                "max_profit":     "最大盈利%",   # 已修正：存的是 profit_pct（%）而非元
                "max_loss":       "最大亏损%",   # 已修正：存的是 profit_pct（%）而非元
            })
            disp_cols = [c for c in ["周","总交易","盈利笔","亏损笔","胜率%","总盈亏(元)","平均盈亏%","最大盈利%","最大亏损%"] if c in disp_s.columns]
            st.dataframe(
                disp_s[disp_cols].sort_values("周", ascending=False) if "周" in disp_s.columns else disp_s[disp_cols],
                use_container_width=True,
                hide_index=True,
                column_config={k: v for k, v in _week_col_config.items() if k in disp_s.columns},
            )

    # ═══════════════════════════════════════════════════════════════════
    # TAB 4：选股理由详情（4维度展开 + 同期龙虎榜Top10对比）
    # ═══════════════════════════════════════════════════════════════════
    with tab4:
        st.subheader("🔍 选股理由与交易复盘详情")
        st.caption("点击展开查看每笔交易的 AI 逻辑（技术面/资金面/历史胜率/席位分析）及同期候选对比")

        # ── 加载 sim_selection_log（候选对比数据）───────────────────────────
        # ── 加载 lhb_top10（同期龙虎榜完整数据）──────────────────────────────
        sel_log_df = _get_selection_log_all()
        lhb_top10_all = _get_lhb_top10_for_compare()

        for _, row in df.sort_values("created_at", ascending=False).iterrows():
            is_open = row["status"] == "open"
            status_badge = "🟢 持仓中" if is_open else "⚪ 已平仓"
            
            # 盈亏高亮标识
            pct_val = row.get("profit_pct")
            if is_open or pd.isna(pct_val):
                pct_str = "待结算"
                header_color = "normal"
            else:
                pct_val = float(pct_val)
                pct_str = f"{pct_val:+.2f}%"
                header_color = "red" if pct_val > 0 else ("green" if pct_val < 0 else "gray")

            # 构建带颜色的 Header (Streamlit expander label 不支持 HTML，只能用 Emoji 区分)
            profit_emoji = "🔥" if header_color == "red" else ("💧" if header_color == "green" else "⏳")
            header = (
                f"{status_badge} | {row['name']}（{row['code']}） "
                f"| T日: {row['trade_date']} "
                f"| {profit_emoji} 盈亏: {pct_str} "
                f"| 评分: {int(row['signal_score']) if pd.notna(row.get('signal_score')) else '—'}"
            )
            
            with st.expander(header, expanded=False):
                # 顶部核心数据条
                col_a, col_b, col_c, col_d = st.columns(4)
                col_a.metric("买入价", _fmt_price(row.get("buy_price")))
                col_b.metric("挂牌价", _fmt_price(row.get("list_price")))
                col_c.metric("成交价", _fmt_price(row.get("sell_price")))
                col_d.metric("最终盈亏", pct_str)
                st.divider()

                # ── Tab内子Tab：选股理由 / 候选对比 ──────────────────────
                inner_tab1, inner_tab2, inner_tab3 = st.tabs(
                    ["📌 AI选股理由", "🏆 同期候选对比（选股记录）", "📊 同期龙虎榜Top10"]
                )

                with inner_tab1:
                    c1, c2 = st.columns(2)
                    with c1:
                        st.markdown("##### 📌 AI 综合摘要")
                        st.info(row.get("llm_reason") or row.get("list_reason") or "无记录")

                        st.markdown("##### ⚡ 技术面分析")
                        st.write(row.get("reason_tech") or "—")

                        st.markdown("##### 💰 资金面分析")
                        _r_fund_text = row.get("reason_fund") or "—"
                        # 补充展示净买强度和对倒风险（若 DB 有此字段）
                        _strength_val = row.get("net_buy_strength_pct")
                        _duodao_val   = row.get("duodao_risk")
                        if _strength_val is not None and not pd.isna(_strength_val):
                            st.write(_r_fund_text)
                            _s_icon = "🟢" if float(_strength_val) >= 1.0 else ("🟡" if float(_strength_val) >= 0.5 else "🔴")
                            st.caption(f"{_s_icon} 净买入强度：**{float(_strength_val):.2f}%** 流通市值")
                        else:
                            st.write(_r_fund_text)
                        if _duodao_val:
                            st.warning("⚠️ **对倒风险标记**：入选时系统已识别此股存在疑似对倒特征，需谨慎复盘。")

                    with c2:
                        st.markdown("##### 📊 历史胜率分析")
                        st.write(row.get("reason_history") or "—")

                        st.markdown("##### 🏛 席位深度分析")
                        st.write(row.get("reason_seat") or "—")

                        # 席位标签
                        seat_raw = row.get("seat_tags")
                        if seat_raw:
                            try:
                                tags = json.loads(seat_raw) if isinstance(seat_raw, str) else seat_raw
                                if tags:
                                    st.markdown("##### 🏷 关键席位打标")
                                    st.markdown("`" + "` · `".join(tags) + "`")
                            except Exception:
                                pass

                    if row.get("buy_source"):
                        st.caption(f"行情来源: {_source_label(row['buy_source'])}")

                with inner_tab2:
                    # ── 从 sim_selection_log 取同日候选对比 ───────────────
                    trade_date_str = str(row.get("trade_date", ""))
                    trade_id_val   = row.get("id")

                    # 优先按 trade_id 精确匹配，其次按 trade_date 匹配
                    if not sel_log_df.empty and "trade_date" in sel_log_df.columns:
                        if trade_id_val and "trade_id" in sel_log_df.columns:
                            cand_df = sel_log_df[sel_log_df["trade_id"] == int(trade_id_val)].copy()
                            if cand_df.empty:
                                cand_df = sel_log_df[sel_log_df["trade_date"].astype(str) == trade_date_str].copy()
                        else:
                            cand_df = sel_log_df[sel_log_df["trade_date"].astype(str) == trade_date_str].copy()
                    else:
                        cand_df = pd.DataFrame()

                    if cand_df.empty:
                        st.info("📭 暂无该交易日的候选记录。（旧交易未保存候选对比，新交易会自动记录。）")
                    else:
                        cand_df = cand_df.sort_values("rank")
                        st.caption(f"共 {len(cand_df)} 只候选（排名1=最终入选，其余为落选候选）")
                        for _, cand_row in cand_df.iterrows():
                            is_winner = (cand_row.get("rank") == 1)
                            winner_badge = "✅ **最终入选**" if is_winner else f"❌ 落选（排名 #{cand_row.get('rank', '?')}）"
                            cand_score = cand_row.get("signal_score")
                            cand_t2 = cand_row.get("t2_win_rate")
                            cand_close = cand_row.get("close_price")
                            cand_strength = cand_row.get("net_buy_strength_pct")
                            cand_duodao   = cand_row.get("duodao_risk")
                            score_str = f"{float(cand_score):.0f}分" if cand_score is not None and not pd.isna(cand_score) else "N/A"
                            t2_str_c = f"{float(cand_t2):.1f}%" if cand_t2 is not None and not pd.isna(cand_t2) else "N/A"
                            close_str = f"¥{float(cand_close):.3f}" if cand_close is not None and not pd.isna(cand_close) else "N/A"
                            strength_str_c = (
                                f" | 净买强度={float(cand_strength):.2f}%"
                                if cand_strength is not None and not pd.isna(cand_strength) else ""
                            )
                            duodao_tag_c = " ⚠️[疑似对倒]" if cand_duodao else ""
                            cand_header = (
                                f"{winner_badge}  |  {cand_row.get('name', '?')}（{cand_row.get('code', '?')}）"
                                f"  综合评分={score_str}  T+2胜率={t2_str_c}  T日收盘={close_str}"
                                f"{strength_str_c}{duodao_tag_c}"
                            )
                            with st.expander(cand_header, expanded=is_winner):
                                cc1, cc2 = st.columns(2)
                                with cc1:
                                    st.markdown("**⚡ 技术面**")
                                    st.write(cand_row.get("reason_tech") or "—")
                                    st.markdown("**💰 资金面**")
                                    st.write(cand_row.get("reason_fund") or "—")
                                    if cand_strength is not None and not pd.isna(cand_strength):
                                        _ci = "🟢" if float(cand_strength) >= 1.0 else ("🟡" if float(cand_strength) >= 0.5 else "🔴")
                                        st.caption(f"{_ci} 净买入强度：**{float(cand_strength):.2f}%** 市值")
                                    if cand_duodao:
                                        st.warning("⚠️ 该候选股存在疑似对倒特征")
                                with cc2:
                                    st.markdown("**📊 历史胜率**")
                                    st.write(cand_row.get("reason_history") or "—")
                                    if cand_row.get("llm_rank_reason"):
                                        st.markdown("**🤖 AI排名理由**")
                                        st.info(str(cand_row["llm_rank_reason"]))

                with inner_tab3:
                    # ── 从 lhb_top10 取同日完整龙虎榜（主板过滤）─────────────
                    trade_date_str3 = str(row.get("trade_date", ""))
                    selected_code   = str(row.get("code", ""))

                    if lhb_top10_all is not None and not lhb_top10_all.empty and "trade_date" in lhb_top10_all.columns:
                        day_lhb = lhb_top10_all[lhb_top10_all["trade_date"].astype(str) == trade_date_str3].copy()
                    else:
                        day_lhb = pd.DataFrame()

                    if day_lhb.empty:
                        st.info(f"📭 {trade_date_str3} 暂无龙虎榜Top10数据（可能数据库尚未收录该日）。")
                    else:
                        # 数值清洗
                        for _nc in ["龙虎榜净买额", "流通市值", "换手率", "涨跌幅"]:
                            if _nc in day_lhb.columns:
                                day_lhb[_nc] = pd.to_numeric(day_lhb[_nc], errors="coerce")

                        # 主板标识列
                        def _is_mb(c):
                            c = str(c).zfill(6)
                            if c.startswith("688"): return False
                            if c.startswith("6"): return True
                            if c[:3] in ("000", "001", "002", "003"): return True
                            return False

                        if "代码" in day_lhb.columns:
                            day_lhb["代码"] = day_lhb["代码"].astype(str).str.zfill(6)
                            day_lhb["是主板"] = day_lhb["代码"].apply(_is_mb)
                            day_lhb["是否入选"] = day_lhb["代码"].apply(
                                lambda c: "✅ 本次入选" if c == selected_code else ""
                            )

                        # 排序：按净买入额降序
                        if "龙虎榜净买额" in day_lhb.columns:
                            day_lhb = day_lhb.sort_values("龙虎榜净买额", ascending=False)

                        st.caption(f"{trade_date_str3} 龙虎榜共 {len(day_lhb)} 条记录，其中主板 {day_lhb.get('是主板', pd.Series(dtype=bool)).sum()} 只")

                        # 展示列选择
                        lhb_cols_want = ["是否入选", "代码", "名称", "是主板", "龙虎榜净买额", "流通市值", "换手率", "涨跌幅"]
                        lhb_cols_avail = [c for c in lhb_cols_want if c in day_lhb.columns]
                        disp_lhb = day_lhb[lhb_cols_avail].copy()

                        # 格式化
                        if "龙虎榜净买额" in disp_lhb.columns:
                            disp_lhb["净买入(亿)"] = (disp_lhb["龙虎榜净买额"] / 1e8).round(4)
                            disp_lhb = disp_lhb.drop(columns=["龙虎榜净买额"])
                        if "流通市值" in disp_lhb.columns:
                            disp_lhb["市值(亿)"] = (disp_lhb["流通市值"] / 1e8).round(1)
                            disp_lhb = disp_lhb.drop(columns=["流通市值"])
                        if "涨跌幅" in disp_lhb.columns:
                            disp_lhb["涨跌幅"] = disp_lhb["涨跌幅"].apply(
                                lambda v: f"{v:+.2f}%" if pd.notna(v) else "—"
                            )
                        if "换手率" in disp_lhb.columns:
                            disp_lhb["换手率"] = disp_lhb["换手率"].apply(
                                lambda v: f"{v:.2f}%" if pd.notna(v) else "—"
                            )
                        if "是主板" in disp_lhb.columns:
                            disp_lhb["板块"] = disp_lhb["是主板"].apply(lambda v: "✅主板" if v else "创业/科创")
                            disp_lhb = disp_lhb.drop(columns=["是主板"])

                        # 高亮入选行
                        def _highlight_selected(row_data):
                            if row_data.get("是否入选", "") == "✅ 本次入选":
                                return ["background-color: rgba(232,85,62,0.15)"] * len(row_data)
                            return [""] * len(row_data)

                        st.dataframe(
                            disp_lhb.style.apply(_highlight_selected, axis=1),
                            use_container_width=True,
                            hide_index=True,
                            height=400,
                            column_config={
                                "是否入选": st.column_config.TextColumn("🏆", width="small"),
                                "代码": st.column_config.TextColumn("代码", width="small"),
                                "名称": st.column_config.TextColumn("名称", width="small"),
                                "板块": st.column_config.TextColumn("板块", width="small"),
                                "净买入(亿)": st.column_config.NumberColumn("净买入(亿)", format="%.4f"),
                                "市值(亿)": st.column_config.NumberColumn("市值(亿)", format="%.1f"),
                                "换手率": st.column_config.TextColumn("换手率", width="small"),
                                "涨跌幅": st.column_config.TextColumn("涨跌幅", width="small"),
                            }
                        )

    # ═══════════════════════════════════════════════════════════════════
    # TAB 5：机器人日志查看（含日志级别筛选 + 关键词过滤）
    # ═══════════════════════════════════════════════════════════════════
    with tab5:
        st.subheader("🪵 机器人运行日志")
        st.caption("查看模拟炒股机器人的监控日志（盘中每3分钟检查一次），以及每日定时任务日志。")

        _LOG_DIR = _ROOT / "logs"

        log_options = {
            "📡 盘中实时监控 (monitor_intraday)": str(_LOG_DIR / "monitor_intraday.log"),
            "🤖 机器人主日志 (sim_robot)": str(_LOG_DIR / "sim_robot.log"),
            "📅 每日定时任务 (daily_update)": str(_LOG_DIR / "daily_update.log"),
        }

        ctrl_c1, ctrl_c2, ctrl_c3 = st.columns([3, 2, 1])
        selected_log = ctrl_c1.selectbox("选择日志文件", list(log_options.keys()))
        log_path = log_options[selected_log]
        lines_count = ctrl_c2.number_input("显示最后 N 行", min_value=20, max_value=2000, value=200, step=50)
        auto_refresh = ctrl_c3.checkbox("自动刷新", value=False)
        if auto_refresh:
            import time as _time; _time.sleep(0); st.rerun()

        filter_col1, filter_col2 = st.columns([2, 3])
        selected_levels = filter_col1.multiselect(
            "📊 日志级别（多选）", ["DEBUG", "INFO", "SUCCESS", "WARNING", "ERROR", "CRITICAL"],
            default=[], placeholder="留空=全部级别", key="log_level_filter",
        )
        filter_keyword = filter_col2.text_input(
            "🔍 关键词过滤（留空显示全部）",
            placeholder="例如：止损、买入、002361、卖出...", key="log_filter_kw",
        )

        import os as _os
        if _os.path.exists(log_path):
            file_size = _os.path.getsize(log_path)
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                all_lines = f.readlines()

            total_lines = len(all_lines)
            tail_lines = all_lines[-int(lines_count):]

            if selected_levels:
                def _match_level(line: str) -> bool:
                    return any(f"| {lvl}" in line or f"|{lvl}" in line for lvl in selected_levels)
                tail_lines = [l for l in tail_lines if _match_level(l)]

            if filter_keyword.strip():
                tail_lines = [l for l in tail_lines if filter_keyword.lower() in l.lower()]

            st.caption(
                f"文件大小: {file_size / 1024:.1f} KB | 总行数: {total_lines} 行 | 显示: {len(tail_lines)} 行"
                + (f"（级别: {', '.join(selected_levels)}）" if selected_levels else "")
                + (f"（关键词: {filter_keyword}）" if filter_keyword.strip() else "")
            )

            def _colorize_log(lines: list) -> str:
                colored = []
                for line in lines:
                    line = line.rstrip()
                    if not line:
                        colored.append("")
                        continue
                    if "| ERROR" in line or "| CRITICAL" in line:
                        colored.append(f'<span style="color:#ff4444">{line}</span>')
                    elif "| WARNING" in line:
                        colored.append(f'<span style="color:#ffaa00">{line}</span>')
                    elif "| SUCCESS" in line or "止损" in line or "卖出" in line or "平仓" in line:
                        colored.append(f'<span style="color:#44bb44">{line}</span>')
                    elif "| INFO" in line and ("买入" in line or "选中" in line or "触发" in line):
                        colored.append(f'<span style="color:#66aaff">{line}</span>')
                    else:
                        colored.append(f'<span style="color:#cccccc">{line}</span>')
                return "\n".join(colored)

            log_html = _colorize_log(tail_lines)
            st.markdown(
                f'<div style="background-color:#1e1e1e;color:#cccccc;font-family:monospace;'
                f'font-size:12px;padding:12px;border-radius:6px;height:500px;'
                f'overflow-y:auto;white-space:pre;border:1px solid #333;">'
                f'{log_html}</div>',
                unsafe_allow_html=True,
            )

            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                full_log_content = f.read()
            st.download_button(
                label="⬇️ 下载完整日志", data=full_log_content,
                file_name=_os.path.basename(log_path), mime="text/plain",
            )
        else:
            st.warning(f"日志文件不存在: `{log_path}`")
            st.info("日志文件将在机器人首次运行后自动创建。")

    # ═══════════════════════════════════════════════════════════════════
    # （旧 Tab5 策略配置 逻辑保留，移到 Tab7 策略管理中）
    # 以下是从原 Tab5 保留下来的策略配置逻辑，供 Tab7 引用
    # ───────────────────────────────────────────────────────────────────
    # 注：旧 Tab5 代码需要被跳过（直接跳到 Tab6 价格轨迹）
    if False:  # 占位符，旧 Tab5 策略配置内容已合并到 Tab7，此处不执行
        st.subheader("⚙️ 分批卖出策略配置")
        st.caption(
            "修改后下次盘中监控（每3分钟）执行时自动生效，无需重启任何服务。"
            "**新买入的股票**会按照最新策略初始化卖出价格；**已持仓的股票**需手动重置才能应用新策略。"
        )

        # 加载当前策略
        current = _load_strategy()

        # ── 说明卡片 ────────────────────────────────────────────────────
        with st.expander("📖 策略逻辑说明", expanded=False):
            st.markdown("""
**止损逻辑**
- 每次盘中监控时，获取持仓股票的实时价格
- 若 `(当前价 / 买入价 - 1) × 100 ≤ 止损阈值%`，立即触发止损
- 止损后**全仓清仓**，记录亏损并关闭持仓
- 止损优先级高于分批卖出（先检查止损，再检查分批）

**分批卖出逻辑**
- 三档依次触发（价格超过目标价即触发）：
  - 第1档：涨幅 ≥ `sell1_pct%` 时，卖出 `sell1_ratio` 比例
  - 第2档：涨幅 ≥ `sell2_pct%` 时，再卖 `sell2_ratio` 比例
  - 第3档：涨幅 ≥ `sell3_pct%` 时，清仓剩余全部
- 买入时根据策略初始化目标价：`目标价 = 买入价 × (1 + 涨幅%)`
- **修改策略不影响已初始化的目标价**，如需应用新策略请点击「重新应用策略到持仓」
            """)

        st.divider()

        # ── 策略表单 ─────────────────────────────────────────────────────
        with st.form("strategy_form"):
            st.markdown("##### 🔴 止损线")
            stop_loss_pct = st.number_input(
                "止损触发阈值（%，负数）",
                min_value=-20.0, max_value=-0.5, step=0.5,
                value=float(current["stop_loss_pct"]),
                help="亏损超过此比例时触发止损，如 -5 表示亏损 5% 止损"
            )

            st.markdown("##### 🟡 分批卖出三档")
            col1, col2 = st.columns(2)

            with col1:
                sell1_pct = st.number_input(
                    "第1档 触发涨幅（%）",
                    min_value=0.5, max_value=50.0, step=0.5,
                    value=float(current["sell1_pct"]),
                    help="涨幅达到此值时卖出第1档"
                )
                sell2_pct = st.number_input(
                    "第2档 触发涨幅（%）",
                    min_value=0.5, max_value=50.0, step=0.5,
                    value=float(current["sell2_pct"]),
                    help="涨幅达到此值时卖出第2档"
                )
                sell3_pct = st.number_input(
                    "第3档 触发涨幅（%，清仓）",
                    min_value=0.5, max_value=50.0, step=0.5,
                    value=float(current["sell3_pct"]),
                    help="涨幅达到此值时清仓剩余全部"
                )

            with col2:
                sell1_ratio_pct = st.number_input(
                    "第1档 卖出比例（%）",
                    min_value=1.0, max_value=99.0, step=1.0,
                    value=round(float(current["sell1_ratio"]) * 100, 1),
                    help="第1档卖出占总持仓的比例，如 33 = 卖出 1/3"
                )
                sell2_ratio_pct = st.number_input(
                    "第2档 卖出比例（%）",
                    min_value=1.0, max_value=99.0, step=1.0,
                    value=round(float(current["sell2_ratio"]) * 100, 1),
                    help="第2档再卖占总持仓的比例"
                )
                st.info(f"**第3档比例**：自动 = 100% - {sell1_ratio_pct:.0f}% - {sell2_ratio_pct:.0f}% = **{max(0, 100 - sell1_ratio_pct - sell2_ratio_pct):.0f}%**（剩余全清）")

            description = st.text_area(
                "策略说明（可选）",
                value=current.get("description", ""),
                height=80,
            )

            # ── 实时预览 ─────────────────────────────────────────────────
            st.markdown("##### 📊 策略预览（以买入价 ¥12.90 为例）")
            example_price = 12.90
            ex_c1, ex_c2, ex_c3, ex_c4 = st.columns(4)
            ex_c1.metric("🔴 止损价", f"¥{example_price * (1 + stop_loss_pct/100):.3f}",
                         delta=f"{stop_loss_pct:+.1f}%", delta_color="inverse")
            ex_c2.metric(f"🟡 档1目标", f"¥{example_price * (1 + sell1_pct/100):.3f}",
                         delta=f"+{sell1_pct:.1f}%")
            ex_c3.metric(f"🟡 档2目标", f"¥{example_price * (1 + sell2_pct/100):.3f}",
                         delta=f"+{sell2_pct:.1f}%")
            ex_c4.metric(f"🟢 档3清仓", f"¥{example_price * (1 + sell3_pct/100):.3f}",
                         delta=f"+{sell3_pct:.1f}%")

            submitted = st.form_submit_button("💾 保存策略", type="primary")

        if submitted:
            # 参数校验
            errors = []
            if sell1_pct >= sell2_pct:
                errors.append("第1档涨幅必须 < 第2档涨幅")
            if sell2_pct >= sell3_pct:
                errors.append("第2档涨幅必须 < 第3档涨幅")
            if sell1_ratio_pct + sell2_ratio_pct >= 100:
                errors.append("第1档 + 第2档卖出比例之和必须 < 100%（需要留第3档）")
            if stop_loss_pct >= 0:
                errors.append("止损阈值必须为负数")

            if errors:
                for err in errors:
                    st.error(f"❌ {err}")
            else:
                new_strategy = {
                    "strategy_key":  "default",
                    "stop_loss_pct": stop_loss_pct,
                    "sell1_pct":     sell1_pct,
                    "sell1_ratio":   sell1_ratio_pct / 100,
                    "sell2_pct":     sell2_pct,
                    "sell2_ratio":   sell2_ratio_pct / 100,
                    "sell3_pct":     sell3_pct,
                    "description":   description,
                }
                if _save_strategy(new_strategy):
                    st.success("✅ 策略已保存！下次盘中监控（3分钟内）自动生效。")
                    st.rerun()

        # ── 当前策略信息 ─────────────────────────────────────────────────
        st.divider()
        if current.get("updated_at"):
            st.caption(
                f"最后修改：{current.get('updated_at', '—')} "
                f"by `{current.get('updated_by', '—')}`"
            )

        # ── 重新应用策略到持仓 ─────────────────────────────────────────────
        with st.expander("🔄 重新应用策略到当前持仓（慎用）", expanded=False):
            st.warning(
                "⚠️ 此操作将**覆盖**当前持仓中尚未完成的卖出目标价，"
                "按最新策略重新计算每档触发价和止损线。已触发的档位不受影响。"
            )
            if not open_.empty:
                st.markdown("**当前持仓：**")
                for _, row in open_.iterrows():
                    batch_mode = row.get("batch_mode", False)
                    bp = float(row.get("buy_price") or 0)
                    tag = "✅ 已初始化" if batch_mode and bp > 0 else "❌ 未初始化"
                    st.write(f"- {row['name']}（{row['code']}）买入价 ¥{bp:.3f} | 分批状态: {tag}")

                if st.button("🔄 重新初始化所有持仓的分批计划", type="secondary"):
                    try:
                        from skills.stock_sim_robot.sim_trade_db import init_batch_sell
                        count = 0
                        for _, row in open_.iterrows():
                            bp = float(row.get("buy_price") or 0)
                            bs = int(row.get("buy_shares") or 0)
                            tid = int(row["id"])
                            if bp > 0 and bs > 0:
                                init_batch_sell(tid, bp, bs)
                                count += 1
                        st.success(f"✅ 已重新初始化 {count} 笔持仓的分批计划。")
                        st.rerun()
                    except Exception as e:
                        st.error(f"重新初始化失败: {e}")
            else:
                st.info("当前无持仓。")

    # ═══════════════════════════════════════════════════════════════════
    # TAB 6：价格轨迹
    # ═══════════════════════════════════════════════════════════════════
    with tab6:
        st.subheader("📈 盘中价格轨迹")
        st.caption("每3分钟一个价格快照，显示 effective_price（涨停/跌停已修正），叠加 MA5/MA10 均线。")

        # ── 选择股票 ──────────────────────────────────────────────────
        all_trades = df.copy()
        all_trades["display"] = all_trades.apply(
            lambda r: f"{r['name']}({r['code']}) {r['trade_date']} [{r['status']}]", axis=1
        )
        trade_options = all_trades["display"].tolist()
        if not trade_options:
            st.info("暂无交易记录")
        else:
            selected_trade_label = st.selectbox("选择持仓/交易", trade_options, key="price_log_trade")
            selected_idx = trade_options.index(selected_trade_label)
            selected_trade_row = all_trades.iloc[selected_idx]
            sel_trade_id = int(selected_trade_row["id"])
            sel_code     = selected_trade_row["code"]
            sel_name     = selected_trade_row["name"]
            sel_buy_price = float(selected_trade_row.get("buy_price") or 0)

            # ── 加载价格数据 ───────────────────────────────────────────
            try:
                from skills.stock_sim_robot.sim_trade_db import get_price_log
                price_df = get_price_log(sel_trade_id)
            except Exception as e:
                st.error(f"加载价格数据失败: {e}")
                price_df = pd.DataFrame()

            if price_df.empty:
                st.info("该持仓暂无价格轨迹数据。数据将在下一个盘中监控周期（每3分钟）写入。")
            else:
                price_df["checked_at"] = pd.to_datetime(price_df["checked_at"])
                price_df = price_df.sort_values("checked_at").reset_index(drop=True)

                # ── 从 price_log 数据中提取有效 high/low（过滤掉 high<low 的异常行）──
                # 正常情况下 high >= low，若不满足说明是旧数据（字段修复前写入的）
                has_hldata = (
                    "high" in price_df.columns and "low" in price_df.columns
                    and (price_df["high"] >= price_df["low"]).any()
                )

                # ── 计算 MA5/MA10 ──────────────────────────────────────
                price_df["MA5"]  = price_df["price"].rolling(window=5,  min_periods=1).mean()
                price_df["MA10"] = price_df["price"].rolling(window=10, min_periods=1).mean()

                # ── 绘制 Plotly 图表 ──────────────────────────────────
                # go 已在文件顶部 import，此处无需重复 import（局部 import 会导致 UnboundLocalError）
                fig = go.Figure()

                # high/low 区间填充（只在数据有效时显示）
                if has_hldata:
                    valid_hl = price_df[price_df["high"] >= price_df["low"]]
                    # 高点线（透明，仅用于 fill）
                    fig.add_trace(go.Scatter(
                        x=valid_hl["checked_at"], y=valid_hl["high"],
                        mode="lines", name="今日最高",
                        line=dict(color="rgba(255,80,80,0.25)", width=0.5),
                        fill=None,
                        hovertemplate="最高: %{y:.3f}<extra></extra>",
                    ))
                    # 低点线（填充到高点，形成区间带）
                    fig.add_trace(go.Scatter(
                        x=valid_hl["checked_at"], y=valid_hl["low"],
                        mode="lines", name="今日最低",
                        line=dict(color="rgba(80,180,255,0.25)", width=0.5),
                        fill="tonexty",
                        fillcolor="rgba(150,200,255,0.10)",
                        hovertemplate="最低: %{y:.3f}<extra></extra>",
                    ))

                # 主线：effective_price（当前价）
                fig.add_trace(go.Scatter(
                    x=price_df["checked_at"], y=price_df["price"],
                    mode="lines+markers", name="实时价格",
                    line=dict(color="#4488FF", width=2),
                    marker=dict(size=4),
                    hovertemplate="价格: %{y:.3f}<extra></extra>",
                ))

                # bid1/ask1 对（显示当时盘口）
                if "bid1" in price_df.columns and price_df["bid1"].gt(0).any():
                    fig.add_trace(go.Scatter(
                        x=price_df["checked_at"], y=price_df["bid1"],
                        mode="lines", name="买1价",
                        line=dict(color="#44BB44", width=1, dash="dot"),
                        hovertemplate="买1: %{y:.3f}<extra></extra>",
                    ))

                # MA5 均线
                fig.add_trace(go.Scatter(
                    x=price_df["checked_at"], y=price_df["MA5"],
                    mode="lines", name="MA5",
                    line=dict(color="#FFA500", width=1.5, dash="dot"),
                ))

                # MA10 均线
                fig.add_trace(go.Scatter(
                    x=price_df["checked_at"], y=price_df["MA10"],
                    mode="lines", name="MA10",
                    line=dict(color="#AA44FF", width=1.5, dash="dot"),
                ))

                # 买入价基准线
                if sel_buy_price > 0:
                    fig.add_hline(
                        y=sel_buy_price, line_dash="dash",
                        line_color="#888888", line_width=1.5,
                        annotation_text=f"买入价 {sel_buy_price}",
                        annotation_position="right",
                    )

                # 各档目标价（从第一条数据取快照）
                if not price_df.empty:
                    row0 = price_df.iloc[0]
                    if row0.get("sell1_price") and row0["sell1_price"] > 0:
                        fig.add_hline(
                            y=float(row0["sell1_price"]), line_dash="dashdot",
                            line_color="#FFD700", line_width=1,
                            annotation_text=f"档1 {row0['sell1_price']:.2f}",
                            annotation_position="right",
                        )
                    if row0.get("sell3_price") and row0["sell3_price"] > 0:
                        fig.add_hline(
                            y=float(row0["sell3_price"]), line_dash="dashdot",
                            line_color="#FF8C00", line_width=1,
                            annotation_text=f"档3 {row0['sell3_price']:.2f}",
                            annotation_position="right",
                        )
                    if row0.get("stop_price") and row0["stop_price"] > 0:
                        fig.add_hline(
                            y=float(row0["stop_price"]), line_dash="dash",
                            line_color="#E84040", line_width=1.5,
                            annotation_text=f"止损 {row0['stop_price']:.2f}",
                            annotation_position="right",
                        )

                # 事件标注（触发点）
                events_df = price_df[price_df["event"].notna()]
                if not events_df.empty:
                    fig.add_trace(go.Scatter(
                        x=events_df["checked_at"], y=events_df["price"],
                        mode="markers+text",
                        name="触发事件",
                        marker=dict(symbol="diamond", size=10, color="#E84040"),
                        text=events_df["event"],
                        textposition="top center",
                    ))

                # ── Y 轴范围自动调整（避免 price 全部相同时变成一条直线）──
                _all_prices = []
                for _col in ["price", "bid1", "high", "low"]:
                    if _col in price_df.columns:
                        _vals = price_df[_col].dropna()
                        _vals = _vals[_vals > 0]
                        if not _vals.empty:
                            _all_prices.extend(_vals.tolist())
                if sel_buy_price > 0:
                    _all_prices.append(sel_buy_price)
                    _all_prices.append(sel_buy_price)
                # 从各档目标价也加入
                _first = price_df.iloc[0] if not price_df.empty else None
                for _pk in ["sell1_price", "sell3_price", "stop_price"]:
                    if _first is not None and _first.get(_pk) and float(_first[_pk]) > 0:
                        _all_prices.append(float(_first[_pk]))
                if _all_prices:
                    _y_min = min(_all_prices) * 0.99
                    _y_max = max(_all_prices) * 1.01
                    _pad = max((_y_max - _y_min) * 0.05, 0.5)  # 至少 0.5 元间距
                    _y_range = [_y_min - _pad, _y_max + _pad]
                else:
                    _y_range = None

                fig.update_layout(
                    title=f"{sel_name}（{sel_code}）盘中价格轨迹",
                    xaxis_title="时间",
                    yaxis_title="价格（元）",
                    yaxis=dict(range=_y_range, tickformat=".3f") if _y_range else {},
                    height=520,
                    hovermode="x unified",
                    legend=dict(orientation="h", y=-0.2),
                    plot_bgcolor="rgba(248,250,252,1)",
                    paper_bgcolor="white",
                )
                # 添加网格线
                fig.update_xaxes(showgrid=True, gridcolor="rgba(200,200,200,0.4)")
                fig.update_yaxes(showgrid=True, gridcolor="rgba(200,200,200,0.4)")
                st.plotly_chart(fig, use_container_width=True)

                # ── 提示旧数据说明 ─────────────────────────────────────
                if not has_hldata or not (price_df["high"] >= price_df["low"]).all():
                    st.caption(
                        "⚠️ 部分历史数据 high/low 字段在修复前写入，显示可能不准确；"
                        "修复后新写入的数据将正常显示 K 线区间带。"
                    )

                # ── 数据表格 ────────────────────────────────────────────
                with st.expander("📊 查看原始数据", expanded=False):
                    disp_cols = ["checked_at", "price", "price_raw", "high", "low", "bid1", "ask1", "change_pct", "event"]
                    show_cols = [c for c in disp_cols if c in price_df.columns]
                    st.dataframe(
                        price_df[show_cols].rename(columns={
                            "checked_at": "检查时间", "price": "实时价", "price_raw": "原始last",
                            "high": "今日最高", "low": "今日最低",
                            "bid1": "买1价", "ask1": "卖1价", "change_pct": "涨幅%", "event": "事件"
                        }),
                        use_container_width=True, hide_index=True,
                    )

    # ═══════════════════════════════════════════════════════════════════
    # TAB 7：策略管理（全局三档参数配置 + NL 策略工坊合并）
    # ═══════════════════════════════════════════════════════════════════
    with tab7:
        st.subheader("⚙️ 策略管理")
        st.caption("全局三档参数配置 + 针对当前持仓的自然语言策略（NL 策略）管理")

        strat_subtab1, strat_subtab2 = st.tabs(["⚙️ 全局三档参数", "🤖 NL 策略（单笔持仓）"])

        # ── 子Tab A：全局三档参数配置（原 Tab5 内容）──────────────────────
        with strat_subtab1:
            st.markdown("#### ⚙️ 分批卖出策略配置")
            st.caption(
                "修改后下次盘中监控（每3分钟）执行时自动生效，无需重启任何服务。"
                "**新买入的股票**会按照最新策略初始化卖出价格；**已持仓的股票**需手动重置才能应用新策略。"
            )

            current = _load_strategy()

            # 当前生效规则摘要（人类可读）
            sl_pct_cur  = float(current.get("stop_loss_pct", -5.0))
            s1_pct_cur  = float(current.get("sell1_pct", 3.0))
            s2_pct_cur  = float(current.get("sell2_pct", 5.0))
            s3_pct_cur  = float(current.get("sell3_pct", 8.0))
            s1_r_cur    = float(current.get("sell1_ratio", 1/3))
            s2_r_cur    = float(current.get("sell2_ratio", 1/3))
            _summary_html = (
                "<div style='background:#1a2035;border-left:4px solid #E84040;"
                "padding:10px 14px;border-radius:6px;margin-bottom:10px;font-size:0.92em;'>"
                "<b>&#x1F4CC; 当前默认三档策略（无 NL 策略时生效）</b><br><br>"
                f"&#x1F6D1; <b>硬止损：</b>亏损 {abs(sl_pct_cur):.1f}% 时清仓<br>"
                f"&#x1F7E1; <b>第1档：</b>涨幅 ≥ +{s1_pct_cur:.1f}% → 卖出 {s1_r_cur*100:.0f}%<br>"
                f"&#x1F7E1; <b>第2档：</b>涨幅 ≥ +{s2_pct_cur:.1f}% → 再卖 {s2_r_cur*100:.0f}%<br>"
                f"&#x1F7E2; <b>第3档：</b>涨幅 ≥ +{s3_pct_cur:.1f}% → 清仓剩余<br>"
                "&#x23F0; <b>最大持仓：</b>5个自然日（超期强制平仓）"
                "</div>"
            )
            st.markdown(_summary_html, unsafe_allow_html=True)

            with st.expander("📖 策略逻辑说明", expanded=False):
                st.markdown("""
**止损逻辑**
- 每次盘中监控时，获取持仓股票的实时价格
- 若 `(当前价 / 买入价 - 1) × 100 ≤ 止损阈值%`，立即触发止损
- 止损后**全仓清仓**，记录亏损并关闭持仓

**分批卖出逻辑**
- 三档依次触发（价格超过目标价即触发）：
  - 第1档：涨幅 ≥ `sell1_pct%` 时，卖出 `sell1_ratio` 比例
  - 第2档：涨幅 ≥ `sell2_pct%` 时，再卖 `sell2_ratio` 比例
  - 第3档：涨幅 ≥ `sell3_pct%` 时，清仓剩余全部
- **修改策略不影响已初始化的目标价**
                """)

        # ── 子Tab B：NL 策略工坊（针对单笔持仓）──────────────────────────
        with strat_subtab2:
            st.markdown("#### 🤖 自然语言策略（NL 策略）")
            st.caption(
                "可以用自然语言描述卖出策略（例如：涨5%卖一半，亏3%止损），AI 将自动解析为规则并执行。"
                "NL 策略激活后会**替代**默认三档逻辑，系统硬止损 (-5%) 始终兜底。"
            )

            open_list = open_[["id", "code", "name", "trade_date", "buy_price"]].copy() if not open_.empty else pd.DataFrame()
            if open_list.empty:
                st.info("当前无活跃持仓。策略工坊在开仓后自动生成草拟策略。")
            else:
                open_list["display"] = open_list.apply(
                    lambda r: f"{r['name']}({r['code']}) T日:{r['trade_date']}  买入价:¥{float(r['buy_price'] or 0):.3f}", axis=1
                )
                strat_trade_label = st.selectbox("选择持仓", open_list["display"].tolist(), key="strat_trade_sel")
                strat_idx = open_list["display"].tolist().index(strat_trade_label)
                strat_row = open_list.iloc[strat_idx]
                strat_trade_id = int(strat_row["id"])
                strat_buy_price = float(strat_row.get("buy_price") or 0)
                strat_code = strat_row["code"]
                strat_name = strat_row["name"]

                st.divider()

                try:
                    from skills.stock_sim_robot.sim_trade_db import get_nl_strategies_for_trade, confirm_nl_strategy
                    strat_df = get_nl_strategies_for_trade(strat_trade_id)
                except Exception as e:
                    strat_df = pd.DataFrame()
                    st.error(f"加载策略失败: {e}")

                active_strat = None
                draft_strats  = []
                if not strat_df.empty:
                    for _, sr in strat_df.iterrows():
                        if sr["status"] == "active":
                            active_strat = sr
                        elif sr["status"] == "draft":
                            draft_strats.append(sr)

                cur_cfg = _load_strategy()

                # ── 当前生效策略（人类可读展示）────────────────────────
                if active_strat is not None:
                    import json as _json
                    rules_data = active_strat.get("parsed_rules")
                    if isinstance(rules_data, str):
                        try: rules_data = _json.loads(rules_data)
                        except Exception: rules_data = None

                    confirm_time = str(active_strat.get("confirmed_at", ""))[:16]
                    nl_text_disp = str(active_strat.get("nl_text", ""))
                    hard_stop_val = float(active_strat.get("hard_stop_pct", -5.0))

                    readable_rules = []
                    if rules_data and isinstance(rules_data, dict):
                        for rule in rules_data.get("rules", []):
                            cond = rule.get("condition", {})
                            act  = rule.get("action",    {})
                            cond_parts = []
                            if "change_pct_ge" in cond: cond_parts.append(f"涨幅 ≥ +{cond['change_pct_ge']}%")
                            if "change_pct_le" in cond: cond_parts.append(f"涨幅 ≤ {cond['change_pct_le']}%")
                            if "days_held_ge"  in cond: cond_parts.append(f"持仓 ≥ {cond['days_held_ge']} 天")
                            if "is_limit_up"   in cond: cond_parts.append("涨停时")
                            cond_str = " 且 ".join(cond_parts) or "无条件"
                            act_type = act.get("type", "")
                            if   act_type == "sell_pct":    act_str = f"卖出 {act.get('pct', 0)*100:.0f}%"
                            elif act_type == "sell_all":    act_str = "清仓全部"
                            elif act_type == "sell_shares": act_str = f"卖出 {act.get('shares', 0)} 股"
                            elif act_type == "hold":        act_str = "继续持有"
                            else:                           act_str = str(act)
                            readable_rules.append(f"  规则{rule.get('id','?')}：{cond_str} → {act_str}")

                    rules_html = "<br>".join(readable_rules) if readable_rules else "  \uff08\u65e0\u53ef\u89e3\u6790\u89c4\u5219\uff09"
                    _nl_html = (
                        "<div style='background:#1a2035;border-left:4px solid #33CC66;"
                        "padding:12px 16px;border-radius:6px;margin-bottom:12px;font-size:0.92em;'>"
                        f"&#x2705; <b>\u5f53\u524d\u6fc0\u6d3b\uff1a\u81ea\u7136\u8bed\u8a00\u7b56\u7565</b>\uff08\u786e\u8ba4\u65f6\u95f4 {confirm_time}\uff09<br><br>"
                        f"&#x1F4DD; <b>\u7b56\u7565\u63cf\u8ff0\uff1a</b>{nl_text_disp}<br><br>"
                        f"&#x1F6D1; <b>\u786c\u6b62\u635f\uff1a</b>{hard_stop_val:+.1f}%\uff08\u4e8f\u635f\u8fbe\u6b64\u6bd4\u4f8b\u5f3a\u5236\u6e05\u4ed3\uff0c\u4e0d\u88ab\u4efb\u4f55\u89c4\u5219\u8986\u76d6\uff09<br>"
                        f"&#x1F4CB; <b>\u89e3\u6790\u89c4\u5219\uff1a</b><br>{rules_html}<br>"
                        "&#x23F0; <b>\u6700\u5927\u6301\u4ed3\uff1a</b>5\u4e2a\u81ea\u7136\u65e5\uff08\u8d85\u671f\u5f3a\u5236\u5e73\u4ed3\uff09"
                        "</div>"
                    )
                    st.markdown(_nl_html, unsafe_allow_html=True)
                else:
                    # 无 NL 策略，展示默认三档规则（含实际价格）
                    bp = strat_buy_price
                    sl = float(cur_cfg.get("stop_loss_pct", -5.0))
                    s1 = float(cur_cfg.get("sell1_pct", 3.0))
                    s2 = float(cur_cfg.get("sell2_pct", 5.0))
                    s3 = float(cur_cfg.get("sell3_pct", 8.0))
                    r1 = float(cur_cfg.get("sell1_ratio", 1/3))
                    r2 = float(cur_cfg.get("sell2_ratio", 1/3))
                    if bp > 0:
                        price_row = lambda pct: f"（目标价 ¥{bp*(1+pct/100):.3f}）"
                        sl_hint = f"（止损价 ¥{bp*(1+sl/100):.3f}）"
                    else:
                        price_row = lambda pct: ""
                        sl_hint = ""
                    _def_lines = [
                        "<div style='background:#1a2035;border-left:4px solid #888888;"
                        "padding:12px 16px;border-radius:6px;margin-bottom:12px;font-size:0.92em;'>",
                        f"&#x1F4CC; <b>\u5f53\u524d\u7b56\u7565\uff1a\u9ed8\u8ba4\u4e09\u6863\u5206\u6279\u5356\u51fa</b>\uff08\u4e70\u5165\u4ef7 \u00a5{bp:.3f}\uff0c\u672a\u8bbe\u7f6e NL \u7b56\u7565\uff09<br><br>",
                        f"&#x1F6D1; <b>\u6b62\u635f\uff1a</b>\u4e8f\u635f \u2264 {sl:.1f}% \u2192 \u6e05\u4ed3\u5168\u90e8{sl_hint}<br>",
                        f"&#x1F7E1; <b>\u7b2c1\u6863\uff1a</b>\u6da8\u5e45 \u2265 +{s1:.1f}% \u2192 \u5356\u51fa {r1*100:.0f}%{price_row(s1)}<br>",
                        f"&#x1F7E1; <b>\u7b2c2\u6863\uff1a</b>\u6da8\u5e45 \u2265 +{s2:.1f}% \u2192 \u518d\u5356 {r2*100:.0f}%{price_row(s2)}<br>",
                        f"&#x1F7E2; <b>\u7b2c3\u6863\uff1a</b>\u6da8\u5e45 \u2265 +{s3:.1f}% \u2192 \u6e05\u4ed3\u5269\u4f59{price_row(s3)}<br>",
                        "&#x23F0; <b>\u6700\u5927\u6301\u4ed3\uff1a</b>5\u4e2a\u81ea\u7136\u65e5\uff08\u8d85\u671f\u5f3a\u5236\u5e73\u4ed3\uff09",
                        "</div>",
                    ]
                    st.markdown("".join(_def_lines), unsafe_allow_html=True)

                # ── 展示 draft 策略（LLM 自动草拟）───────────────────
                if draft_strats:
                    st.divider()
                    st.markdown("#### 📝 待确认的 LLM 草拟策略")
                    for dr in draft_strats[:3]:  # 最多展示3条
                        with st.expander(f"策略草稿 ID={dr['id']} （{dr.get('source', 'llm_auto')}，{dr.get('created_at', '')}）", expanded=True):
                            st.markdown("**自然语言描述：**")
                            st.markdown(f"> {dr['nl_text']}" if dr.get("nl_text") else "> （无描述）")

                            if dr.get("parsed_rules"):
                                with st.expander("🔍 查看解析后的 JSON 规则", expanded=False):
                                    import json as _json
                                    rules_data = dr["parsed_rules"]
                                    if isinstance(rules_data, str):
                                        try: rules_data = _json.loads(rules_data)
                                        except Exception: pass
                                    st.json(rules_data)

                            c_confirm, c_skip = st.columns([2, 1])
                            with c_confirm:
                                if st.button(f"✅ 确认启用此策略（ID={dr['id']}）", key=f"confirm_strat_{dr['id']}", type="primary"):
                                    try:
                                        ok = confirm_nl_strategy(int(dr["id"]))
                                        if ok:
                                            st.success("策略已激活！三档逻辑已替换。")
                                            st.rerun()
                                        else:
                                            st.error("激活失败，请检查日志")
                                    except Exception as e:
                                        st.error(f"激活失败: {e}")

                st.divider()
                st.markdown("#### ✏️ 手动输入策略")
                nl_input = st.text_area(
                    "输入自然语言策略",
                    placeholder="例如：涨停后次日+5%止盈，-3%止损；下午2点后涨3%卖一半；RSI>80时全仓卖出",
                    height=120, key="nl_strategy_input",
                )
                hard_stop_input = st.number_input(
                    "硬兜底止损线（%）", min_value=-20.0, max_value=-1.0, value=-5.0, step=0.5,
                    help="此止损线始终生效，即使 LLM 策略未覆盖止损情况", key="hard_stop_input",
                )

                if st.button("🔍 解析策略（LLM）", key="parse_nl_btn", disabled=not nl_input.strip()):
                    if not nl_input.strip():
                        st.warning("请输入策略文本")
                    else:
                        with st.spinner("LLM 解析中…"):
                            try:
                                from skills.stock_sim_robot.llm_select import parse_nl_strategy
                                parse_result = parse_nl_strategy(
                                    nl_text=nl_input, buy_price=strat_buy_price,
                                    code=strat_code, name=strat_name,
                                )
                                st.session_state["_parsed_nl_result"] = parse_result
                                st.session_state["_parsed_nl_text"]   = nl_input
                            except Exception as e:
                                st.error(f"解析失败: {e}")

                if st.session_state.get("_parsed_nl_result"):
                    parse_result = st.session_state["_parsed_nl_result"]
                    if parse_result.get("parse_error"):
                        st.error(f"LLM 解析错误: {parse_result['parse_error']}")
                    else:
                        st.success("✅ 解析成功！")
                        st.markdown("**解析出的 JSON 规则：**")
                        st.json(parse_result.get("parsed_rules", {}))

                        if st.button("✅ 确认启用此策略", key="confirm_manual_strat", type="primary"):
                            try:
                                from skills.stock_sim_robot.sim_trade_db import save_nl_strategy, confirm_nl_strategy
                                new_strat_id = save_nl_strategy(
                                    trade_id=strat_trade_id,
                                    nl_text=st.session_state.get("_parsed_nl_text", nl_input),
                                    parsed_rules=parse_result.get("parsed_rules"),
                                    hard_stop_pct=float(hard_stop_input),
                                    source="user_input",
                                )
                                if new_strat_id:
                                    ok = confirm_nl_strategy(new_strat_id)
                                    if ok:
                                        st.success("策略已激活！三档逻辑已替换，页面刷新后生效。")
                                        st.session_state.pop("_parsed_nl_result", None)
                                        st.rerun()
                                    else:
                                        st.error("激活失败")
                                else:
                                    st.error("保存失败")
                            except Exception as e:
                                st.error(f"操作失败: {e}")

    # ═══════════════════════════════════════════════════════════════════
    # TAB 8：操作审计
    # ═══════════════════════════════════════════════════════════════════
    with tab8:
        st.subheader("📋 操作审计日志")
        st.caption("按时间倒序展示所有重要操作记录（选股/买入/监控/卖出/止损/策略确认），全部可追溯。")

        # ── 过滤控件 ───────────────────────────────────────────────────
        c1, c2, c3, c4 = st.columns([2, 2, 2, 1])
        from datetime import date as _date
        audit_date_val = c1.date_input("日期", value=_date.today(), key="audit_date")
        audit_date_str = audit_date_val.strftime("%Y%m%d") if audit_date_val else None

        event_type_opts = [
            "全部", "phase_a_select", "phase_a_skip", "phase_b_buy", "phase_b_fail",
            "monitor_check", "monitor_sell", "monitor_stop_loss",
            "quote_fail", "strategy_confirm", "strategy_draft", "force_close",
        ]
        audit_event_filter = c2.selectbox("事件类型", event_type_opts, key="audit_event_type")
        audit_trade_filter = c3.text_input("按交易ID过滤（可选）", placeholder="如: 42", key="audit_trade_id")
        audit_limit = c4.number_input("显示条数", min_value=10, max_value=500, value=50, step=10, key="audit_limit")

        if st.button("🔄 刷新日志", key="audit_refresh"):
            st.rerun()

        # ── 加载审计日志 ────────────────────────────────────────────────
        try:
            from skills.stock_sim_robot.sim_trade_db import get_audit_logs
            audit_kwargs = {
                "event_date": audit_date_str if audit_date_str else None,
                "event_type": None if audit_event_filter == "全部" else audit_event_filter,
                "limit": int(audit_limit),
            }
            if audit_trade_filter.strip():
                try:
                    audit_kwargs["trade_id"] = int(audit_trade_filter.strip())
                except ValueError:
                    pass
            audit_df = get_audit_logs(**audit_kwargs)
        except Exception as e:
            st.error(f"加载审计日志失败: {e}")
            audit_df = pd.DataFrame()

        if audit_df.empty:
            st.info("该日期/条件下暂无审计记录。")
        else:
            st.success(f"共 {len(audit_df)} 条记录")

            # 事件类型着色
            _EVENT_COLORS = {
                "monitor_sell":      "🟢",
                "monitor_stop_loss": "🔴",
                "phase_a_select":    "🔵",
                "phase_b_buy":       "🟣",
                "phase_b_fail":      "🟠",
                "quote_fail":        "🟡",
                "strategy_confirm":  "⚪",
                "strategy_draft":    "⚫",
                "force_close":       "🔴",
                "phase_a_skip":      "⬛",
                "monitor_check":     "▪️",
            }

            for _, ar in audit_df.iterrows():
                icon    = _EVENT_COLORS.get(str(ar.get("event_type", "")), "▪️")
                etime   = str(ar.get("event_time", ""))[:19]
                etype   = str(ar.get("event_type", ""))
                summary = str(ar.get("summary", ""))
                result  = str(ar.get("result", ""))
                tid     = ar.get("trade_id")
                code    = ar.get("code", "")

                # 跳过频繁的 monitor_check 记录（太多会淹没有价值的事件）
                if etype == "monitor_check" and audit_event_filter == "全部":
                    continue

                tid_label = f" [T:{tid}]" if tid else ""
                code_label = f" {code}" if code else ""

                with st.expander(
                    f"{icon} `{etime}`{code_label}{tid_label} — {summary[:80]}",
                    expanded=False,
                ):
                    cols = st.columns([1, 1, 1])
                    cols[0].markdown(f"**事件类型：** `{etype}`")
                    cols[1].markdown(f"**结果：** `{result}`")
                    cols[2].markdown(f"**操作者：** `{ar.get('operator', '')}`")

                    if ar.get("detail"):
                        st.markdown("**详细数据：**")
                        import json as _json
                        detail_data = ar["detail"]
                        if isinstance(detail_data, str):
                            try:
                                detail_data = _json.loads(detail_data)
                            except Exception:
                                pass
                        st.json(detail_data)

            # ── monitor_check 统计单独展示 ────────────────────────────
            if audit_event_filter == "全部":
                check_df = audit_df[audit_df["event_type"] == "monitor_check"]
                if not check_df.empty:
                    st.divider()
                    st.caption(f"▪️ monitor_check 共 {len(check_df)} 条（已折叠，展开按类型过滤查看）")

