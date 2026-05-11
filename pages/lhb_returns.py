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
import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from datetime import datetime, timedelta

from utils.storage import storage
from utils.cache import ss_get, ss_set
from utils.formatters import fmt_pct
from utils.helpers import fmt_date, fmt_date_slash
from modules.data_fetcher import fetch_lhb_netbuy_by_date
from config.theme import Color
from config.constants import TTL

_RED   = Color.RED
_GREEN = Color.GREEN
_GRAY  = Color.GRAY


def _ret_color(val) -> str:
    if pd.isna(val):
        return f"color:{_GRAY}"
    return f"color:{_RED};font-weight:bold" if val > 0 else f"color:{_GREEN};font-weight:bold"


def _style_ret_series(s):
    return [_ret_color(v) for v in s]




# ============================================================
# 数据加载函数（纯 Python 函数，无 @st.cache_data）
# ============================================================

def _fetch_stock_hist(code: str, trade_date: str, days: int = 15):
    """
    拉取 trade_date 起往后 days 个交易日的日 K（前复权），含最高/最低/开盘/收盘。
    优先用新浪接口，失败再用腾讯接口（东方财富日K接口有时被封）。
    """
    import akshare as ak
    # 统一日期为 YYYYMMDD 格式（兼容 2026-04-29 / 20260429 两种输入）
    td = str(trade_date).replace("-", "").replace("/", "")[:8]
    end_dt = (pd.to_datetime(td) + timedelta(days=days + 15)).strftime("%Y%m%d")

    # 清理代码：去掉市场后缀，统一为6位数字
    code_clean = str(code).replace(".SZ", "").replace(".SH", "").replace(".BJ", "").zfill(6)
    # 上交所用 sh 前缀，深交所/创业板/科创板用 sz
    prefix = "sh" if code_clean.startswith(("6", "9")) else "sz"
    sina_code = f"{prefix}{code_clean}"

    hist = None

    # ── 1. 优先：新浪财经接口 ──────────────────────────────────
    try:
        raw = ak.stock_zh_a_daily(symbol=sina_code, adjust="qfq",
                                   start_date=td, end_date=end_dt)
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
                                         start_date=td, end_date=end_dt)
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
            raw = ak.stock_zh_a_hist(symbol=code_clean, period="daily",
                                      start_date=td, end_date=end_dt, adjust="qfq")
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

    if hist is None:
        return None
    if len(hist) < 2:
        # 只有上榜日当天数据，尚无 T+1（新股或最近上市）
        return None
    return hist


def _calc_returns_from_hist(code: str, trade_date: str):
    """从数据库 kline_daily + lhb_tn_price 直接计算 T+1~T+10 收益率，无需 AKShare"""
    cache_key = f"calc_ret_{code}_{trade_date}"
    cached = ss_get(cache_key, max_age=TTL.LONG)
    if cached is not None:
        return cached

    try:
        from sqlalchemy import text
        engine = storage._get_engine()
        td_date = str(trade_date)[:10].replace("-", "").replace("/", "")
        td_date = f"{td_date[:4]}-{td_date[4:6]}-{td_date[6:]}"

        # 清理代码格式（统一为 ts_code 格式，如 000001.SZ）
        code_clean = str(code).replace(".SZ", "").replace(".SH", "").replace(".BJ", "").zfill(6)
        # 推断后缀
        if code_clean.startswith(("6", "9")):
            ts_code = f"{code_clean}.SH"
        elif code_clean.startswith(("0", "3")):
            ts_code = f"{code_clean}.SZ"
        else:
            ts_code = f"{code_clean}.BJ"

        with engine.connect() as conn:
            # 1. T+1 买入价：上榜次日的开盘价
            r = conn.execute(text("""
                SELECT open FROM kline_daily
                WHERE ts_code = :ts AND trade_date > :td
                ORDER BY trade_date ASC LIMIT 1
            """), {"ts": ts_code, "td": td_date}).fetchone()

            if r is None or r[0] is None:
                return None
            buy_price = float(r[0])
            if buy_price <= 0:
                return None

            # 2. T+1~T+10 价格（从 lhb_tn_price，已预计算）
            tn_rows = conn.execute(text("""
                SELECT n_day, target_date, close_price, high_price, low_price, pnl_pct
                FROM lhb_tn_price
                WHERE ts_code = :ts AND trade_date = :td AND n_day BETWEEN 1 AND 10
                ORDER BY n_day
            """), {"ts": ts_code, "td": td_date}).fetchall()

            if not tn_rows:
                # 降级：直接从 kline_daily 读取
                k_rows = conn.execute(text("""
                    SELECT trade_date, open, high, low, close
                    FROM kline_daily
                    WHERE ts_code = :ts AND trade_date > :td
                    ORDER BY trade_date ASC
                    LIMIT 10
                """), {"ts": ts_code, "td": td_date}).fetchall()

                rows = []
                for i, kr in enumerate(k_rows):
                    d = i + 1
                    sell_price = float(kr[4])
                    high_price = float(kr[2]) if kr[2] else None
                    low_price = float(kr[3]) if kr[3] else None
                    ret = (sell_price / buy_price - 1) * 100
                    high_ret = (high_price / buy_price - 1) * 100 if high_price else None
                    rows.append({
                        "day": d, "label": f"T+{d}",
                        "卖出日期": str(kr[0]),
                        "最高价": round(high_price, 3) if high_price else None,
                        "最低价": round(low_price, 3) if low_price else None,
                        "收盘价": round(sell_price, 3),
                        "收盘收益率(%)": round(ret, 2),
                        "最高收益率(%)": round(high_ret, 2) if high_ret is not None else None,
                        "盈亏": "盈" if ret > 0 else ("亏" if ret < 0 else "平"),
                    })
            else:
                rows = []
                for tn in tn_rows:
                    d = tn[0]
                    sell_price = float(tn[2]) if tn[2] else None
                    high_price = float(tn[3]) if tn[3] else None
                    low_price = float(tn[4]) if tn[4] else None
                    if sell_price is None:
                        continue
                    ret = (sell_price / buy_price - 1) * 100
                    high_ret = (high_price / buy_price - 1) * 100 if high_price else None
                    rows.append({
                        "day": d, "label": f"T+{d}",
                        "卖出日期": str(tn[1]),
                        "最高价": round(high_price, 3) if high_price else None,
                        "最低价": round(low_price, 3) if low_price else None,
                        "收盘价": round(sell_price, 3),
                        "收盘收益率(%)": round(ret, 2),
                        "最高收益率(%)": round(high_ret, 2) if high_ret is not None else None,
                        "盈亏": "盈" if ret > 0 else ("亏" if ret < 0 else "平"),
                    })

            if not rows:
                return None

            result = {"buy_price": round(buy_price, 3), "rows": rows}
            ss_set(cache_key, result)
            return result

    except Exception:
        return None


def _load_top10(trade_date: str) -> pd.DataFrame:
    """获取当日 Top10（session_state 缓存，TTL.DEFAULT）"""
    cache_key = f"top10_{trade_date}"
    cached = ss_get(cache_key, max_age=TTL.DEFAULT)
    if cached is not None:
        return cached

    try:
        df = fetch_lhb_netbuy_by_date(trade_date, top_n=10)
    except Exception:
        df = pd.DataFrame()

    ss_set(cache_key, df)
    return df


def _load_all_prices() -> pd.DataFrame:
    """读取全部价格缓存（session_state 缓存，TTL.MEDIUM）"""
    cache_key = "all_prices"
    cached = ss_get(cache_key, max_age=TTL.MEDIUM)
    if cached is not None:
        return cached

    try:
        df = storage.read_recent("lhb_top10_prices", days=90)
        if not df.empty:
            df = df[df["T+1买入价"].notna()].copy()
    except Exception:
        df = pd.DataFrame()

    ss_set(cache_key, df)
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
    通过 AKShare 拉取价格（session_state 缓存，TTL.DEFAULT）。
    codes_key 是缓存键，codes 是实际股票代码列表。
    只拉取 codes 里的股票，调用方负责过滤掉 DB 已有的。
    """
    cache_key = f"prices_api_{codes_key}_{trade_date}"
    cached = ss_get(cache_key, max_age=TTL.DEFAULT)
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
        ss_set(cache_key, result)
        return result

    new_df = pd.DataFrame(new_rows)
    # 写入 DB
    try:
        storage.write(new_df, "lhb_top10_prices", mode="append")
    except Exception as e:
        import logging
        logging.warning(f"存价格缓存失败: {e}")

    ss_set(cache_key, new_df)
    return new_df


def _render_quick_from_db(code: str, name: str, trade_date: str) -> bool:
    """从 lhb_top10_prices 快速展示缓存数据，无 API 调用，毫秒级。有数据返回 True。"""
    try:
        row_df = _read_prices_from_db(trade_date)
        if row_df is None or row_df.empty:
            return False
        code_norm = str(code).replace(".SZ", "").replace(".SH", "").replace(".BJ", "").lstrip("0")
        sub = row_df[row_df["代码"].astype(str).str.replace(".SZ", "").str.replace(".SH", "").str.replace(".BJ", "").str.lstrip("0") == code_norm]
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
    # 直接 SQL 查询去重日期，并过滤交易日（跳过法定节假日）
    _date_set = set()
    try:
        from sqlalchemy import text
        engine = storage._get_engine()
        with engine.connect() as conn:
            # 获取交易日历白名单
            cal_dates = set()
            try:
                result = conn.execute(text(
                    "SELECT cal_date::text FROM trade_calendar WHERE is_open = true AND cal_date >= '2024-01-01'"
                ))
                cal_dates = {r[0] for r in result.fetchall()}
            except Exception:
                pass
            for tbl in ["lhb_top10", "lhb_top10_prices"]:
                try:
                    result = conn.execute(text(f'SELECT DISTINCT trade_date FROM "{tbl}" ORDER BY trade_date DESC'))
                    for r in result.fetchall():
                        d = str(r[0])[:10]
                        if not cal_dates or d in cal_dates:  # 有交易日历时过滤，否则全放行
                            _date_set.add(d)
                except Exception:
                    pass
    except Exception:
        pass
    local_dates = sorted(_date_set, reverse=True)
    today_str = datetime.now().strftime("%Y-%m-%d")
    # 默认选最近有数据的日期
    all_options = list(dict.fromkeys(local_dates + ([today_str] if today_str not in local_dates else [])))
    if not all_options:
        all_options = [today_str]

    # 日期选择（简化 session_state 管理）
    if "lhb_sel_date" not in st.session_state or st.session_state["lhb_sel_date"] not in all_options:
        st.session_state["lhb_sel_date"] = local_dates[0] if local_dates else today_str

    col_l, col_r = st.columns([2, 3])
    with col_l:
        sel_idx = all_options.index(st.session_state["lhb_sel_date"])
        sel_date = st.selectbox(
            "选择日期",
            options=all_options,
            index=sel_idx,
            format_func=lambda d: f"{fmt_date_slash(d)}  {'📡 今日' if d == today_str else ''}",
        )
        # 同步选择到 session_state
        if sel_date != st.session_state.get("lhb_sel_date"):
            st.session_state["lhb_sel_date"] = sel_date

        # 快捷日期按钮
        c1, c2, c3 = st.columns(3)
        cur_idx = all_options.index(sel_date)
        with c1:
            prev_idx = min(cur_idx + 1, len(all_options) - 1)
            if st.button("⬅ 上一交易日", use_container_width=True, key="lhb_prev_date2",
                         disabled=cur_idx >= len(all_options) - 1):
                st.session_state["lhb_sel_date"] = all_options[prev_idx]
                st.rerun()
        with c2:
            if st.button("📅 最近交易日", use_container_width=True, key="lhb_latest_date2"):
                st.session_state["lhb_sel_date"] = local_dates[0] if local_dates else today_str
                st.rerun()
        with c3:
            next_idx = max(cur_idx - 1, 0)
            if st.button("下一交易日 ➡", use_container_width=True, key="lhb_next_date2",
                         disabled=cur_idx <= 0):
                st.session_state["lhb_sel_date"] = all_options[next_idx]
                st.rerun()
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
    sel_date_display = fmt_date(sel_date)
    with st.spinner(f"获取 {sel_date_display} 数据..."):
        top10 = _load_top10(sel_date)

    if top10.empty:
        if sel_date == today_str and datetime.now().hour < 15:
            st.info(
                f"📅 **{sel_date_display}** 龙虎榜数据尚未发布。\n\n"
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
        def _code_key(c):
            return str(c).replace(".SZ","").replace(".SH","").replace(".BJ","").lstrip("0")
        top10_m["_code_key"]  = top10_m["代码"].astype(str).apply(_code_key)
        price_df2 = price_df.copy()
        price_df2["_code_key"] = price_df2["代码"].astype(str).apply(_code_key)
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
        fmt[c] = fmt_pct
    if has_prices:
        price_num_cols = ["T+1买入价"] + [f"T+{d}{s}" for d in [1,2,5,10]
                                           for s in ["最高价","最低价","收盘价"]]
        for c in price_num_cols:
            if c in disp_df.columns:
                fmt[c] = lambda v: f"¥{v:.3f}" if pd.notna(v) else "—"
        pct_cols = [f"T+{d}{s}" for d in [1,2,5,10] for s in ["收盘收益%","最高收益%"]]
        for c in pct_cols:
            if c in disp_df.columns:
                fmt[c] = fmt_pct

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

    ret_data = None
    fetch_failed = False

    if not st.session_state[load_key]:
        col_b1, col_b2 = st.columns([3, 1])
        with col_b1:
            st.caption(f"点击右侧按钮加载 **{sel_name}** 的 T+1~T+10 走势数据")
        with col_b2:
            if st.button(f"📈 加载走势", key=f"btn_hist_{sel_code}"):
                st.session_state[load_key] = True
                st.rerun()
        # 尝试从 DB 缓存快速展示（无需 AKShare API）
        _quick = _render_quick_from_db(sel_code, sel_name, sel_date)
        if not _quick:
            st.info("📊 点击上方按钮加载个股详细走势图（从数据库直接读取，秒级响应）")
        # 未点击按钮时不显示错误
    else:
        with st.spinner(f"加载 {sel_name} 走势（从行情接口拉取约需5秒）..."):
            ret_data = _calc_returns_from_hist(sel_code, sel_date)
            if ret_data is None:
                fetch_failed = True
                st.session_state[load_key] = False  # 允许重试

    if fetch_failed:
        td = str(sel_date).replace("-", "")
        st.warning(
            f"⚠️ **{sel_name}**（{sel_code}）无法获取 {sel_date} 之后的 K 线数据。\n\n"
            f"**可能原因：**\n"
            f"1. 该股 **{sel_date}** 刚上市，尚无 T+1 交易日数据（需等下一个交易日）\n"
            f"2. AKShare 行情接口暂时不可用（可稍后重试）\n"
            f"3. 股票已退市或代码变更\n\n"
            f"💡 若为原因 1，可在下一个交易日后再查看。"
        )
    elif ret_data is not None:
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
