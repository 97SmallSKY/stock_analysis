"""
龙虎榜净买入 Top10 持仓模拟

数据来源：东方财富 stock_lhb_detail_em 接口（含「龙虎榜净买额」和「上榜后N日涨跌幅」）
  - 历史日期：直接使用接口自带的上榜后1/2/5/10日涨跌幅
  - 当日查询：拉取当天数据，上榜后N日需后续更新才有数据

操作流程：
  1. 选择交易日
  2. 系统显示当日龙虎榜净买入 Top10（按「龙虎榜净买额」降序）
  3. 选择某只股票，查看：
     - T+1 开盘买入，T+1/T+2/.../T+10 收盘卖出的盈亏情况
     - 接口自带的上榜后涨跌幅（即上榜日收盘为基准）
  4. 批量查看：所有 Top10 的上榜后收益汇总表

颜色规则（中国A股惯例）：
  红色 = 涨 = 收益率 > 0
  绿色 = 跌 = 收益率 < 0

注：st.cache_data 在 Python 3.13 下存在 semaphore 泄漏 bug，
    所有缓存均改用 st.session_state 手动管理。
"""
import time
import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from datetime import datetime, timedelta

from utils.storage import storage
from modules.data_fetcher import fetch_lhb_netbuy_by_date

# 颜色（中国惯例）
_RED   = "#E84040"
_GREEN = "#33CC66"
_GRAY  = "#888888"


def _ret_color(val) -> str:
    if pd.isna(val):
        return f"color:{_GRAY}"
    return f"color:{_RED};font-weight:bold" if val > 0 else f"color:{_GREEN};font-weight:bold"


def _style_ret_series(s):
    return [_ret_color(v) for v in s]


def _fmt_pct(v):
    if pd.isna(v):
        return "—"
    return f"{v:+.2f}%"


# ============================================================
# Session State 缓存工具（替代 @st.cache_data，避免 Python 3.13 semaphore 泄漏）
# ============================================================

def _ss_get(key: str, max_age: int = 300):
    """从 session_state 取缓存值，超时返回 None。max_age 单位：秒"""
    import time as _t
    cache = st.session_state.get("_cache", {})
    entry = cache.get(key)
    if entry is None:
        return None
    if _t.time() - entry["ts"] > max_age:
        return None
    return entry["val"]


def _ss_set(key: str, val):
    """写入 session_state 缓存"""
    import time as _t
    if "_cache" not in st.session_state:
        st.session_state["_cache"] = {}
    st.session_state["_cache"][key] = {"ts": _t.time(), "val": val}


# ============================================================
# 数据加载函数（纯 Python 函数，无 @st.cache_data）
# ============================================================

def _fetch_stock_hist(code: str, trade_date: str, days: int = 15):
    """
    拉取 trade_date 起往后 days 个交易日的日 K（前复权），含最高/最低/开盘/收盘。
    优先用新浪接口，失败再用腾讯接口（东方财富日K接口有时被封）。
    """
    import akshare as ak
    end_dt = (pd.to_datetime(trade_date) + timedelta(days=days + 15)).strftime("%Y%m%d")

    # 上交所用 sh 前缀，深交所/创业板/科创板用 sz
    prefix = "sh" if code.startswith(("6", "9")) else "sz"
    sina_code = f"{prefix}{code}"

    hist = None

    # ── 1. 优先：新浪财经接口 ──────────────────────────────────
    try:
        raw = ak.stock_zh_a_daily(symbol=sina_code, adjust="qfq",
                                   start_date=trade_date, end_date=end_dt)
        if raw is not None and len(raw) >= 2:
            col_map = {"date": "日期", "open": "开盘", "high": "最高",
                       "low": "最低", "close": "收盘"}
            raw = raw.rename(columns=col_map)
            if all(c in raw.columns for c in ["日期", "开盘", "最高", "最低", "收盘"]):
                hist = raw[["日期", "开盘", "最高", "最低", "收盘"]].sort_values("日期").reset_index(drop=True)
    except Exception:
        pass

    # ── 2. 备选：腾讯接口 ─────────────────────────────────────
    if hist is None:
        try:
            raw = ak.stock_zh_a_hist_tx(symbol=sina_code, adjust="qfq",
                                         start_date=trade_date, end_date=end_dt)
            if raw is not None and len(raw) >= 2:
                col_map = {"date": "日期", "open": "开盘", "high": "最高",
                           "low": "最低", "close": "收盘"}
                raw = raw.rename(columns=col_map)
                if all(c in raw.columns for c in ["日期", "开盘", "最高", "最低", "收盘"]):
                    hist = raw[["日期", "开盘", "最高", "最低", "收盘"]].sort_values("日期").reset_index(drop=True)
        except Exception:
            pass

    # ── 3. 最后备选：东方财富接口 ──────────────────────────────
    if hist is None:
        try:
            raw = ak.stock_zh_a_hist(symbol=code, period="daily",
                                      start_date=trade_date, end_date=end_dt, adjust="qfq")
            if raw is not None and len(raw) >= 2:
                col = {}
                for c in raw.columns:
                    if "日期" in c:             col["日期"] = c
                    elif c in ("开盘", "open"):  col["开盘"] = c
                    elif c in ("收盘", "close"): col["收盘"] = c
                    elif c in ("最高", "high"):  col["最高"] = c
                    elif c in ("最低", "low"):   col["最低"] = c
                if all(k in col for k in ["日期", "开盘", "收盘", "最高", "最低"]):
                    raw = raw[[col[k] for k in ["日期", "开盘", "收盘", "最高", "最低"]]].sort_values(col["日期"]).reset_index(drop=True)
                    raw.columns = ["日期", "开盘", "收盘", "最高", "最低"]
                    hist = raw[["日期", "开盘", "最高", "最低", "收盘"]].copy()
                    hist.columns = ["日期", "开盘", "最高", "最低", "收盘"]
        except Exception:
            pass

    if hist is None or len(hist) < 2:
        return None
    return hist


def _calc_returns_from_hist(code: str, trade_date: str):
    """计算某只股票的 T+1~T+10 收益率（session_state 缓存，ttl=600s）"""
    cache_key = f"calc_ret_{code}_{trade_date}"
    cached = _ss_get(cache_key, max_age=600)
    if cached is not None:
        return cached

    try:
        hist = _fetch_stock_hist(code, trade_date)
        if hist is None:
            return None
        buy_price = float(hist.iloc[1]["开盘"])
        if buy_price <= 0:
            return None
        rows = []
        for d in range(1, 11):
            if d >= len(hist):
                break
            row_h = hist.iloc[d]
            sell_price = float(row_h["收盘"])
            high_price = float(row_h["最高"]) if "最高" in hist.columns else None
            low_price  = float(row_h["最低"]) if "最低" in hist.columns else None
            sell_date  = str(row_h["日期"])[:10]
            ret        = (sell_price / buy_price - 1) * 100
            high_ret   = (high_price / buy_price - 1) * 100 if high_price and buy_price > 0 else None
            rows.append({
                "day":           d,
                "label":         f"T+{d}",
                "卖出日期":      sell_date,
                "最高价":        round(high_price, 3) if high_price else None,
                "最低价":        round(low_price, 3)  if low_price  else None,
                "收盘价":        round(sell_price, 3),
                "收盘收益率(%)": round(ret, 2),
                "最高收益率(%)": round(high_ret, 2) if high_ret is not None else None,
                "盈亏":          "盈" if ret > 0 else ("亏" if ret < 0 else "平"),
            })
        result = {"buy_price": round(buy_price, 3), "rows": rows}
        _ss_set(cache_key, result)
        return result
    except Exception:
        return None


def _load_top10(trade_date: str) -> pd.DataFrame:
    """获取当日 Top10（session_state 缓存，ttl=300s）"""
    cache_key = f"top10_{trade_date}"
    cached = _ss_get(cache_key, max_age=300)
    if cached is not None:
        return cached

    try:
        df = fetch_lhb_netbuy_by_date(trade_date, top_n=10)
    except Exception:
        df = pd.DataFrame()

    _ss_set(cache_key, df)
    return df


def _load_all_prices() -> pd.DataFrame:
    """读取全部价格缓存（session_state 缓存，ttl=120s）"""
    cache_key = "all_prices"
    cached = _ss_get(cache_key, max_age=120)
    if cached is not None:
        return cached

    try:
        df = storage.read("lhb_top10_prices")
        if not df.empty:
            df = df[df["T+1买入价"].notna()].copy()
    except Exception:
        df = pd.DataFrame()

    _ss_set(cache_key, df)
    return df


def _read_prices_from_db(trade_date: str) -> pd.DataFrame:
    """直接从 DB 读取指定日期的价格（不走缓存，始终最新）"""
    try:
        df = storage.query_by_date("lhb_top10_prices", trade_date)
        if not df.empty and "T+1买入价" in df.columns:
            df = df[df["T+1买入价"].notna()]
        return df
    except Exception:
        return pd.DataFrame()


def _fetch_prices_via_api(codes_key: str, trade_date: str, codes: tuple) -> pd.DataFrame:
    """
    通过 AKShare 拉取价格（session_state 缓存，ttl=300s）。
    codes_key 是缓存键，codes 是实际股票代码列表。
    只拉取 codes 里的股票，调用方负责过滤掉 DB 已有的。
    """
    cache_key = f"prices_api_{codes_key}_{trade_date}"
    cached = _ss_get(cache_key, max_age=300)
    if cached is not None:
        return cached

    _DAYS = [1, 2, 5, 10]
    new_rows = []

    for code in codes:
        code_str = str(code).zfill(6)
        hist = _fetch_stock_hist(code_str, trade_date, days=12)

        if hist is None or len(hist) < 2:
            continue

        rec = {"代码": code_str, "trade_date": trade_date}
        buy_price = float(hist.iloc[1]["开盘"])
        rec["T+1买入价"] = round(buy_price, 3)

        for d in _DAYS:
            for suffix in ["最高价", "最低价", "收盘价", "收盘收益%", "最高收益%"]:
                rec[f"T+{d}{suffix}"] = None
            if d < len(hist):
                row_h = hist.iloc[d]
                hp = float(row_h["最高"]) if "最高" in hist.columns else None
                lp = float(row_h["最低"]) if "最低" in hist.columns else None
                cp = float(row_h["收盘"])
                rec[f"T+{d}最高价"] = round(hp, 3) if hp is not None else None
                rec[f"T+{d}最低价"] = round(lp, 3) if lp is not None else None
                rec[f"T+{d}收盘价"] = round(cp, 3)
                if buy_price > 0:
                    rec[f"T+{d}收盘收益%"] = round((cp / buy_price - 1) * 100, 2)
                    if hp is not None:
                        rec[f"T+{d}最高收益%"] = round((hp / buy_price - 1) * 100, 2)

        new_rows.append(rec)
        time.sleep(0.25)

    if not new_rows:
        result = pd.DataFrame()
        _ss_set(cache_key, result)
        return result

    new_df = pd.DataFrame(new_rows)
    # 写入 DB
    try:
        storage.write(new_df, "lhb_top10_prices", mode="append")
    except Exception as e:
        import logging
        logging.warning(f"存价格缓存失败: {e}")

    _ss_set(cache_key, new_df)
    return new_df


def _render_quick_from_db(code: str, name: str, trade_date: str) -> bool:
    """从 lhb_top10_prices 快速展示缓存数据，无 API 调用，毫秒级。有数据返回 True。"""
    try:
        row_df = _read_prices_from_db(trade_date)
        if row_df is None or row_df.empty:
            return False
        code_norm = str(code).lstrip("0")
        sub = row_df[row_df["代码"].astype(str).str.lstrip("0") == code_norm]
        if sub.empty or "T+1买入价" not in sub.columns:
            return False
        r = sub.iloc[0]
        buy_price = float(r["T+1买入价"]) if pd.notna(r.get("T+1买入价")) else None
        if buy_price is None:
            return False

        st.metric(label=f"T+1 开盘买入价（{name}）", value=f"¥{buy_price:.3f}",
                  help="上榜次日的开盘价，模拟 T+1 开盘买入成本")

        _DAYS = [1, 2, 5, 10]
        rows = []
        for d in _DAYS:
            cp_col = f"T+{d}收盘价"
            rp_col = f"T+{d}收盘收益%"
            hp_col = f"T+{d}最高收益%"
            if cp_col in r.index and pd.notna(r.get(cp_col)):
                rows.append({
                    "持仓天数":   f"T+{d}",
                    "收盘价":     f"¥{float(r[cp_col]):.3f}",
                    "收盘收益%":  f"{float(r[rp_col]):+.2f}%" if rp_col in r.index and pd.notna(r.get(rp_col)) else "—",
                    "最高收益%":  f"{float(r[hp_col]):+.2f}%" if hp_col in r.index and pd.notna(r.get(hp_col)) else "—",
                })
        if rows:
            import re as _re
            ret_df = pd.DataFrame(rows)
            def _color_pct(val):
                try:
                    v = float(_re.sub(r"[^0-9.+-]", "", str(val)))
                    return f"color:{_RED};font-weight:bold" if v > 0 else (f"color:{_GREEN};font-weight:bold" if v < 0 else "")
                except Exception:
                    return ""
            st.dataframe(
                ret_df.style.applymap(_color_pct, subset=["收盘收益%", "最高收益%"]),
                use_container_width=True, hide_index=True,
            )
            st.caption("📦 数据来自本地缓存 | 点击「📈 加载走势」可拉取完整 K 线图")
            return True
        return False
    except Exception:
        return False


def _enrich_top10_with_prices(trade_date: str, codes: tuple) -> pd.DataFrame:
    """
    先查 DB，DB 缺失的股票才调用 API 拉取，结果写库。
    全程捕获异常，不让任何接口错误透传到页面。
    """
    try:
        db_df = _read_prices_from_db(trade_date)

        if db_df is not None and not db_df.empty:
            existing_codes = set(db_df["代码"].astype(str).str.zfill(6).tolist())
            all_codes_norm = set(str(c).zfill(6) for c in codes)
            missing = tuple(all_codes_norm - existing_codes)
        else:
            missing = tuple(str(c).zfill(6) for c in codes)
            db_df = pd.DataFrame()

        if missing:
            missing_key = "_".join(sorted(missing))
            try:
                api_df = _fetch_prices_via_api(missing_key, trade_date, missing)
                if api_df is not None and not api_df.empty:
                    db_df = pd.concat([db_df, api_df], ignore_index=True) if not db_df.empty else api_df
            except Exception:
                pass

        return db_df
    except Exception:
        return pd.DataFrame()


# ============================================================
# 页面渲染
# ============================================================

def render():
    st.title("📈 龙虎榜净买入 Top10 持仓模拟")
    st.caption(
        "按龙虎榜**净买入额**排名 Top10 | **T 日**=上榜当日 · **T+1**=次日集合竞价买入 · **T+2**=上榜后第2交易日  |  "
        "🔴 红=涨(盈利)  🟢 绿=跌(亏损)"
    )

    # ① 选日期
    st.subheader("① 选择交易日")
    # 从多张表取日期并集，确保即使某张表为空也能显示完整日期列表
    _date_set = set()
    for _tbl in ["lhb_top10", "lhb_detail", "lhb_top10_prices"]:
        try:
            _df = storage.read(_tbl)
            if not _df.empty and "trade_date" in _df.columns:
                _date_set.update(_df["trade_date"].astype(str).unique())
        except Exception:
            pass
    local_dates = sorted(_date_set, reverse=True)
    today_str = datetime.now().strftime("%Y%m%d")
    # 默认优先选最近有历史数据的日期，今日追加到列表末尾（盘后才有数据）
    all_options = list(dict.fromkeys(local_dates + ([today_str] if today_str not in local_dates else [])))
    if not all_options:
        all_options = [today_str]

    col_l, col_r = st.columns([2, 3])
    with col_l:
        sel_date = st.selectbox(
            "选择日期",
            options=all_options,
            format_func=lambda d: f"{d[:4]}/{d[4:6]}/{d[6:]}  {'📡 今日' if d == today_str else ''}",
        )
    with col_r:
        if sel_date == today_str:
            hour_now = datetime.now().hour
            if hour_now < 15:
                st.warning("⏰ 今日龙虎榜数据在 **15:30 收盘后**才发布，当前暂无数据")
            else:
                st.info("📡 今日实时查询")
        else:
            st.info("📦 历史数据（含接口自带上榜后涨跌幅）")

    # ② Top10
    st.subheader("② 当日龙虎榜净买入 Top10")
    with st.spinner(f"获取 {sel_date[:4]}/{sel_date[4:6]}/{sel_date[6:]} 数据..."):
        top10 = _load_top10(sel_date)

    if top10.empty:
        if sel_date == today_str and datetime.now().hour < 15:
            st.info(
                f"📅 **{sel_date[:4]}/{sel_date[4:6]}/{sel_date[6:]}** 龙虎榜数据尚未发布。\n\n"
                "龙虎榜数据在每日 **15:30 收盘后**由交易所公布，请盘后再查询。\n\n"
                "👈 请从左侧下拉框选择**历史日期**查看。"
            )
        else:
            st.error(
                f"⚠️ 获取 **{sel_date}** 数据失败。\n\n"
                "东方财富接口繁忙或当天无龙虎榜数据，请稍后刷新重试。"
            )
        if st.button("🔄 清除缓存重试"):
            if "_cache" in st.session_state:
                st.session_state["_cache"] = {}
            st.rerun()
        return

    from_local = "_from_local" in top10.columns and top10["_from_local"].any()
    if from_local:
        st.warning("⚠️ 接口繁忙，已降级为本地成交额排名（无精确净买额）")

    # ── 价格加载：优先 DB，缺失数据可手动点击补全（避免自动调 API 拖慢首屏）──
    codes_tuple = tuple(top10["代码"].astype(str).str.zfill(6).tolist())

    # 先只查 DB，不发 API 请求，毫秒级响应
    price_df = _read_prices_from_db(sel_date)
    if price_df is None:
        price_df = pd.DataFrame()

    # 如果 DB 有缺失，展示补全按钮
    _has_db_prices = not price_df.empty
    if not _has_db_prices:
        col_p1, col_p2 = st.columns([3, 1])
        with col_p1:
            st.caption("💡 该日价格数据未缓存，点击右侧按钮从行情接口拉取（约需 15~30 秒）")
        with col_p2:
            if st.button("📥 拉取价格数据", key="btn_fetch_prices"):
                with st.spinner("正在拉取各股行情价格，请稍候..."):
                    price_df = _enrich_top10_with_prices(sel_date, codes_tuple)
                st.rerun()

    # 合并价格到 top10（按代码 merge，统一去掉前导零再匹配）
    if price_df is not None and not price_df.empty:
        top10_m   = top10.copy()
        top10_m["_code_key"]  = top10_m["代码"].astype(str).str.lstrip("0")
        price_df2 = price_df.copy()
        price_df2["_code_key"] = price_df2["代码"].astype(str).str.lstrip("0")
        extra_cols = [c for c in price_df2.columns
                      if c not in top10_m.columns or c == "_code_key"]
        display_top10 = top10_m.merge(
            price_df2[["_code_key"] + [c for c in extra_cols if c != "_code_key"]],
            on="_code_key", how="left"
        ).drop(columns=["_code_key"])
    else:
        display_top10 = top10.copy()

    has_prices = price_df is not None and not price_df.empty and "T+1买入价" in price_df.columns

    # ── 展示列 ────────────────────────────────────────────────
    base_cols  = ["排名", "代码", "名称"]
    money_cols = [c for c in ["龙虎榜净买额", "龙虎榜买入额", "龙虎榜卖出额"]
                  if c in display_top10.columns and display_top10[c].notna().any()]

    price_detail_cols = []
    if has_prices:
        if "T+1买入价" in display_top10.columns:
            price_detail_cols.append("T+1买入价")
        for d in [1, 2, 5, 10]:
            for suffix in ["最高价", "最高收益%", "收盘价", "收盘收益%"]:
                c = f"T+{d}{suffix}"
                if c in display_top10.columns and display_top10[c].notna().any():
                    price_detail_cols.append(c)

    after_cols = []
    if not has_prices:
        after_cols = [c for c in ["上榜后1日", "上榜后2日", "上榜后5日", "上榜后10日"]
                      if c in display_top10.columns and display_top10[c].notna().any()]

    disp_cols = base_cols + money_cols + price_detail_cols + after_cols
    disp_df   = display_top10[[c for c in disp_cols if c in display_top10.columns]].copy()

    # ── 格式化 ────────────────────────────────────────────────
    fmt = {}
    for c in money_cols:
        fmt[c] = lambda v: f"{v/1e8:+.2f}亿" if pd.notna(v) else "—"
    for c in after_cols:
        fmt[c] = _fmt_pct
    if has_prices:
        price_num_cols = ["T+1买入价"] + [f"T+{d}{s}" for d in [1,2,5,10]
                                           for s in ["最高价","最低价","收盘价"]]
        for c in price_num_cols:
            if c in disp_df.columns:
                fmt[c] = lambda v: f"¥{v:.3f}" if pd.notna(v) else "—"
        pct_cols = [f"T+{d}{s}" for d in [1,2,5,10] for s in ["收盘收益%","最高收益%"]]
        for c in pct_cols:
            if c in disp_df.columns:
                fmt[c] = _fmt_pct

    # ── 着色（收益率列）─────────────────────────────────────────
    color_cols = [c for c in after_cols + [f"T+{d}{s}" for d in [1,2,5,10]
                  for s in ["收盘收益%","最高收益%"]] if c in disp_df.columns]

    def _apply_fmt(df, fmts):
        df2 = df.copy()
        for col, fn in fmts.items():
            if col in df2.columns:
                df2[col] = df2[col].map(fn)
        return df2

    disp_formatted = _apply_fmt(disp_df, fmt)

    def _color_df(df):
        styles = pd.DataFrame("", index=df.index, columns=df.columns)
        for c in color_cols:
            if c in disp_df.columns:
                styles[c] = disp_df[c].map(_ret_color)
        return styles

    st.dataframe(
        disp_formatted.style.apply(_color_df, axis=None),
        use_container_width=True,
        hide_index=True,
    )

    # ── 说明 ─────────────────────────────────────────────────
    if has_prices:
        st.caption(
            "📌 **T+1买入价** = 上榜次日开盘价  |  **T+N最高/收盘价** = 上榜后第N个交易日最高/收盘价  |  "
            "**收益%** = (卖出价 / T+1买入价 - 1) × 100"
        )
    else:
        st.caption("📌 价格数据加载中或暂无，显示接口自带涨跌幅（以上榜日收盘为基准）")

    # ③ 个股详情
    st.divider()
    st.subheader("③ 个股详细走势")

    names = top10["名称"].tolist() if "名称" in top10.columns else []
    codes = top10["代码"].astype(str).str.zfill(6).tolist()
    options = [f"{r+1}. {n}（{c}）" for r, (n, c) in enumerate(zip(names, codes))]

    selected_opt = st.selectbox("选择股票查看详细走势", options=options)
    idx = options.index(selected_opt)
    sel_code = codes[idx]
    sel_name = names[idx] if names else sel_code

    # 懒加载：点击按钮才触发 API，避免选股时自动拉取拖慢交互
    load_key = f"load_hist_{sel_code}_{sel_date}"
    if load_key not in st.session_state:
        st.session_state[load_key] = False

    if not st.session_state[load_key]:
        col_b1, col_b2 = st.columns([3, 1])
        with col_b1:
            st.caption(f"点击右侧按钮加载 **{sel_name}** 的 T+1~T+10 走势数据")
        with col_b2:
            if st.button(f"📈 加载走势", key=f"btn_hist_{sel_code}"):
                st.session_state[load_key] = True
                st.rerun()
        # 尝试从已缓存的价格表快速渲染（无需 API）
        _quick = _render_quick_from_db(sel_code, sel_name, sel_date)
        if not _quick:
            st.info("📊 点击上方按钮加载个股详细走势图")
        ret_data = None
    else:
        with st.spinner(f"加载 {sel_name} 走势（从行情接口拉取约需5秒）..."):
            ret_data = _calc_returns_from_hist(sel_code, sel_date)

    if ret_data is None:
        st.warning(f"⚠️ {sel_name}（{sel_code}）无法获取 K 线数据，可能是退市或接口暂无数据。")
    else:
        buy_price = ret_data["buy_price"]
        rows = ret_data["rows"]

        st.metric(label=f"T+1 开盘买入价（{sel_name}）", value=f"¥{buy_price:.3f}")

        if rows:
            ret_df = pd.DataFrame(rows)

            # 折线图
            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=ret_df["label"], y=ret_df["收盘收益率(%)"],
                mode="lines+markers+text",
                text=[f"{v:+.1f}%" for v in ret_df["收盘收益率(%)"]],
                textposition="top center",
                name="收盘卖出收益率",
                line=dict(color=_RED, width=2),
                marker=dict(size=8),
            ))
            if "最高收益率(%)" in ret_df.columns and ret_df["最高收益率(%)"].notna().any():
                fig.add_trace(go.Scatter(
                    x=ret_df["label"], y=ret_df["最高收益率(%)"],
                    mode="lines+markers",
                    name="最高价卖出收益率",
                    line=dict(color="#FF8C00", width=2, dash="dot"),
                    marker=dict(size=6),
                ))
            # 零线用 Scatter 模拟，避开 add_hline Plotly bug
            x_labels = ret_df["label"].tolist()
            if x_labels:
                fig.add_trace(go.Scatter(
                    x=[x_labels[0], x_labels[-1]], y=[0, 0], mode="lines",
                    line=dict(color=_GRAY, dash="dash", width=1),
                    showlegend=False, hoverinfo="skip",
                ))
            fig.update_layout(
                title=f"{sel_name}（{sel_code}）T+1 买入后各期收益率",
                xaxis_title="持有天数", yaxis_title="收益率 (%)",
                height=380, margin=dict(l=40, r=40, t=50, b=40),
                legend=dict(orientation="h", yanchor="bottom", y=1.02),
            )
            st.plotly_chart(fig, use_container_width=True)

            # 明细表
            show_cols = ["label", "卖出日期", "最高价", "最低价", "收盘价",
                         "收盘收益率(%)", "最高收益率(%)", "盈亏"]
            show_cols = [c for c in show_cols if c in ret_df.columns]
            ret_display = ret_df[show_cols].rename(columns={"label": "持仓天数"})

            def _color_ret_row(row):
                styles = [""] * len(row)
                for i, col in enumerate(row.index):
                    if "收益率" in col:
                        try:
                            v = float(str(row[col]).replace("%","").replace("+",""))
                            styles[i] = _ret_color(v)
                        except Exception:
                            pass
                return styles

            for c in ["收盘收益率(%)", "最高收益率(%)"]:
                if c in ret_display.columns:
                    ret_display[c] = ret_display[c].map(
                        lambda v: f"{v:+.2f}%" if pd.notna(v) else "—"
                    )
            for c in ["最高价", "最低价", "收盘价"]:
                if c in ret_display.columns:
                    ret_display[c] = ret_display[c].map(
                        lambda v: f"¥{v:.3f}" if pd.notna(v) else "—"
                    )

            st.dataframe(ret_display, use_container_width=True, hide_index=True)
        else:
            st.info("暂无 T+1~T+10 数据（数据不足 2 行）")

    # ④ 汇总结论：跨所有已缓存日期的综合统计
    st.divider()
    st.subheader("④ 历史汇总结论（所有已积累日期）")
    st.caption(
        "统计口径：**T+1 开盘买入**，分别以最高价/收盘价卖出的胜率与平均收益率  |  "
        "样本越多结论越可靠，建议积累 ≥ 20 个交易日"
    )

    all_prices = _load_all_prices()

    if all_prices.empty:
        st.info("暂无跨日期数据（需先浏览多个日期以积累价格缓存）")
    else:
        _DAYS = [1, 2, 5, 10]
        dates_with_data = sorted(all_prices["trade_date"].astype(str).unique())
        st.caption(f"📅 已覆盖 **{len(dates_with_data)}** 个交易日  |  共 **{len(all_prices)}** 条股票记录  |  日期范围：{dates_with_data[0]} ~ {dates_with_data[-1]}")

        # ── KPI 汇总卡片 ──────────────────────────────────────────
        summary_rows = []
        for d in _DAYS:
            cc = f"T+{d}收盘收益%"
            hc = f"T+{d}最高收益%"
            if cc not in all_prices.columns:
                continue
            cs = all_prices[cc].dropna()
            hs = all_prices[hc].dropna() if hc in all_prices.columns else pd.Series(dtype=float)
            if len(cs) == 0:
                continue
            summary_rows.append({
                "持仓天数":     f"T+{d}",
                "样本数":       len(cs),
                "收盘卖出胜率": f"{(cs > 0).mean() * 100:.1f}%",
                "收盘均收益%":  round(cs.mean(), 2),
                "最高卖出胜率": f"{(hs > 0).mean() * 100:.1f}%" if len(hs) else "—",
                "最高均收益%":  round(hs.mean(), 2) if len(hs) else None,
            })

        if summary_rows:
            kpi_cols = st.columns(len(summary_rows))
            for i, row in enumerate(summary_rows):
                with kpi_cols[i]:
                    ret_val = row["收盘均收益%"]
                    color   = _RED if ret_val > 0 else (_GREEN if ret_val < 0 else _GRAY)
                    st.markdown(
                        f"<div style='text-align:center;padding:8px;border:1px solid #ddd;border-radius:6px'>"
                        f"<b>{row['持仓天数']}</b><br/>"
                        f"<span style='font-size:1.3em;color:{color};font-weight:bold'>{ret_val:+.2f}%</span><br/>"
                        f"<small>收盘胜率 {row['收盘卖出胜率']} | n={row['样本数']}</small>"
                        f"</div>",
                        unsafe_allow_html=True,
                    )

            st.write("")
            # 完整汇总表
            sum_df = pd.DataFrame(summary_rows)
            for c in ["收盘均收益%", "最高均收益%"]:
                if c in sum_df.columns:
                    sum_df[c] = sum_df[c].map(
                        lambda v: f"{v:+.2f}%" if pd.notna(v) else "—"
                    )
            st.dataframe(sum_df, use_container_width=True, hide_index=True)

        # ── 收益率分布图 ──────────────────────────────────────────
        st.subheader("收益率分布")
        dist_day = st.radio("选择持仓天数", [f"T+{d}" for d in _DAYS if f"T+{d}收盘收益%" in all_prices.columns],
                            horizontal=True)
        d_num = int(dist_day.replace("T+", ""))
        col_c = f"T+{d_num}收盘收益%"
        col_h = f"T+{d_num}最高收益%"

        if col_c in all_prices.columns:
            fig2 = go.Figure()
            cs2 = all_prices[col_c].dropna()
            fig2.add_trace(go.Histogram(
                x=cs2, nbinsx=30, name="收盘卖出",
                marker_color=_RED, opacity=0.7,
            ))
            if col_h in all_prices.columns:
                hs2 = all_prices[col_h].dropna()
                fig2.add_trace(go.Histogram(
                    x=hs2, nbinsx=30, name="最高价卖出",
                    marker_color="#FF8C00", opacity=0.5,
                ))
            # 零线：用 Scatter 模拟，避开 add_vline Plotly bug
            y_max = max(cs2.max() if not cs2.empty else 0, hs2.max() if col_h in all_prices.columns and not all_prices[col_h].dropna().empty else 0) * 1.1
            y_max = y_max if y_max > 0 else 10
            fig2.add_trace(go.Scatter(
                x=[0, 0], y=[0, y_max], mode="lines",
                line=dict(color=_GRAY, dash="dash", width=1),
                showlegend=False, hoverinfo="skip",
            ))
            fig2.update_layout(
                title=f"{dist_day} 收益率分布（n={len(cs2)}）",
                xaxis_title="收益率 (%)", yaxis_title="频次",
                barmode="overlay", height=350,
                margin=dict(l=40, r=40, t=50, b=40),
            )
            st.plotly_chart(fig2, use_container_width=True)
