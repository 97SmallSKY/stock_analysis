"""
数据管理页面 - 后台线程版本

核心机制说明
-----------
Streamlit 每次用户交互（点击按钮、切换菜单）都会重新执行当前页面脚本。
如果数据拉取任务直接运行在 Streamlit 主线程，切换页面会立即中断任务。

解决方案：
  1. 点击按钮 → 把任务提交到独立的后台 Python 线程
  2. 任务状态（进度、日志）写入 st.session_state（线程安全的共享状态）
  3. 无论用户在哪个页面，后台线程持续运行，数据写入完成后停止
  4. 回到数据管理页时自动轮询状态，实时显示进度
"""
import time
import threading
import streamlit as st
import pandas as pd
from datetime import datetime
import os

from config.settings import HALF_YEAR_START, DATA_END_DATE
from utils.storage import storage
from modules.data_fetcher import (
    fetch_lhb_detail, fetch_lhb_jigou, fetch_lhb_ggtj,
    fetch_market_sentiment, initialize_data,
)


# ============================================================
# 全局任务状态（跨 Streamlit 重绘持久化）
# ============================================================

# 用模块级变量存储后台任务状态，所有 session 共享
# （因为 Streamlit 多用户时各自独立，单用户场景下此方案足够）
_TASK_STATE: dict = {
    "running":   False,
    "task_name": "",
    "progress":  0,       # 0~100
    "logs":      [],      # 日志列表
    "result":    None,    # 完成后的结果摘要
    "error":     None,
    "start_time": None,
    "end_time":   None,
}
_TASK_LOCK = threading.Lock()


def _get_state() -> dict:
    with _TASK_LOCK:
        return dict(_TASK_STATE)


def _update_state(**kwargs):
    with _TASK_LOCK:
        _TASK_STATE.update(kwargs)


def _append_log(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    with _TASK_LOCK:
        _TASK_STATE["logs"].append(f"[{ts}] {msg}")
        # 只保留最近 200 条
        if len(_TASK_STATE["logs"]) > 200:
            _TASK_STATE["logs"] = _TASK_STATE["logs"][-200:]


# ============================================================
# 后台任务函数
# ============================================================

def _bg_fetch_all(start_date: str, end_date: str, force_refresh: bool):
    """后台线程：拉取全量数据"""
    _update_state(
        running=True, task_name="初始化全部数据",
        progress=0, logs=[], result=None, error=None,
        start_time=datetime.now().strftime("%H:%M:%S"), end_time=None,
    )
    _append_log(f"开始拉取全量数据 {start_date} ~ {end_date}")

    try:
        # ── 1. 龙虎榜明细 ─────────────────────────────────────
        _append_log("拉取龙虎榜明细（新浪接口）...")
        _update_state(progress=5)

        import pandas as pd
        date_range = pd.date_range(start=start_date, end=end_date, freq="B")
        total = len(date_range)
        all_data = []

        import akshare as ak
        from utils.retry import safe_akshare_call
        from modules.data_fetcher import _SINA_LHB_COL_MAP

        for i, date in enumerate(date_range):
            if not _TASK_STATE["running"]:
                _append_log("⚠️ 任务被手动取消")
                return

            date_str = date.strftime("%Y%m%d")
            df = safe_akshare_call(
                ak.stock_lhb_detail_daily_sina,
                date=date_str,
                max_retries=2, base_delay=1.5, fallback=None,
            )
            if df is not None and len(df) > 0:
                df = df.rename(columns={k: v for k, v in _SINA_LHB_COL_MAP.items() if k in df.columns})
                df = df.drop(columns=["序号"], errors="ignore")
                df["trade_date"] = date_str
                all_data.append(df)

            pct = 5 + int((i + 1) / total * 55)
            _update_state(progress=pct)
            if (i + 1) % 10 == 0 or i == total - 1:
                _append_log(f"  龙虎榜 [{i+1}/{total}] {date_str}，已收集 {sum(len(d) for d in all_data)} 条")
            time.sleep(0.3)

        if all_data:
            lhb_df = pd.concat(all_data, ignore_index=True)
            storage.write(lhb_df, "lhb_detail", mode="append")
            _append_log(f"✅ 龙虎榜明细保存完成，共 {len(lhb_df)} 条")
        else:
            lhb_df = pd.DataFrame()
            _append_log("⚠️ 未获取到龙虎榜数据")

        # ── 2. 机构席位追踪 ────────────────────────────────────
        _append_log("拉取机构席位追踪（新浪接口）...")
        _update_state(progress=65)
        from modules.data_fetcher import fetch_lhb_jigou, fetch_lhb_ggtj
        fetch_lhb_jigou(days=20, save=True)
        _append_log("✅ 机构席位追踪完成")
        _update_state(progress=75)

        fetch_lhb_ggtj(days=20, save=True)
        _append_log("✅ 个股上榜统计完成")
        _update_state(progress=85)

        # ── 3. 今日市场情绪 ────────────────────────────────────
        _append_log("拉取今日市场情绪...")
        today = datetime.now().strftime("%Y%m%d")
        fetch_market_sentiment(today, save=True)
        _append_log("✅ 市场情绪完成")
        _update_state(progress=95)

        _update_state(
            progress=100,
            result=f"龙虎榜 {len(lhb_df)} 条，机构追踪完成，市场情绪完成",
            end_time=datetime.now().strftime("%H:%M:%S"),
        )
        _append_log(f"🎉 全部数据拉取完成！共 {len(lhb_df)} 条龙虎榜记录")

    except Exception as e:
        _update_state(error=str(e), end_time=datetime.now().strftime("%H:%M:%S"))
        _append_log(f"❌ 发生错误: {e}")

    finally:
        _update_state(running=False)


def _bg_fetch_lhb(start_date: str, end_date: str):
    """后台线程：仅拉取龙虎榜"""
    _update_state(
        running=True, task_name="拉取龙虎榜明细",
        progress=0, logs=[], result=None, error=None,
        start_time=datetime.now().strftime("%H:%M:%S"), end_time=None,
    )
    _append_log(f"开始拉取龙虎榜 {start_date} ~ {end_date}（新浪接口）")

    try:
        import pandas as pd, akshare as ak
        from utils.retry import safe_akshare_call
        from modules.data_fetcher import _SINA_LHB_COL_MAP

        date_range = pd.date_range(start=start_date, end=end_date, freq="B")
        total = len(date_range)
        all_data = []

        for i, date in enumerate(date_range):
            if not _TASK_STATE["running"]:
                _append_log("⚠️ 任务被取消")
                return

            date_str = date.strftime("%Y%m%d")
            df = safe_akshare_call(
                ak.stock_lhb_detail_daily_sina,
                date=date_str,
                max_retries=2, base_delay=1.5, fallback=None,
            )
            if df is not None and len(df) > 0:
                df = df.rename(columns={k: v for k, v in _SINA_LHB_COL_MAP.items() if k in df.columns})
                df = df.drop(columns=["序号"], errors="ignore")
                df["trade_date"] = date_str
                all_data.append(df)

            pct = int((i + 1) / total * 100)
            _update_state(progress=pct)
            if (i + 1) % 10 == 0 or i == total - 1:
                _append_log(f"  [{i+1}/{total}] {date_str}")
            time.sleep(0.3)

        if all_data:
            result_df = pd.concat(all_data, ignore_index=True)
            storage.write(result_df, "lhb_detail", mode="append")
            _update_state(result=f"共 {len(result_df)} 条")
            _append_log(f"✅ 完成，共 {len(result_df)} 条")
        else:
            _append_log("⚠️ 未获取到数据")

    except Exception as e:
        _update_state(error=str(e))
        _append_log(f"❌ 错误: {e}")

    finally:
        _update_state(running=False, end_time=datetime.now().strftime("%H:%M:%S"))


def _bg_fetch_jigou():
    """后台线程：拉取机构数据"""
    _update_state(
        running=True, task_name="拉取机构席位",
        progress=0, logs=[], result=None, error=None,
        start_time=datetime.now().strftime("%H:%M:%S"), end_time=None,
    )
    _append_log("开始拉取机构席位数据（新浪接口）")
    try:
        from modules.data_fetcher import fetch_lhb_jigou, fetch_lhb_ggtj
        _update_state(progress=30)
        fetch_lhb_jigou(days=20, save=True)
        _append_log("✅ 机构席位追踪完成")
        _update_state(progress=70)
        fetch_lhb_ggtj(days=20, save=True)
        _append_log("✅ 个股上榜统计完成")
        _update_state(progress=100, result="机构席位 + 个股统计完成")
    except Exception as e:
        _update_state(error=str(e))
        _append_log(f"❌ 错误: {e}")
    finally:
        _update_state(running=False, end_time=datetime.now().strftime("%H:%M:%S"))


def _start_task(fn, *args):
    """提交后台任务，若已有任务运行则拒绝"""
    state = _get_state()
    if state["running"]:
        return False
    t = threading.Thread(target=fn, args=args, daemon=True)
    t.start()
    return True


# ============================================================
# 页面渲染
# ============================================================

def render():
    st.title("⚙️ 数据管理")

    # ──────────────────────────────────────────────────────────────
    # 后台任务状态面板（固定在顶部，无论是否有任务都显示）
    # ──────────────────────────────────────────────────────────────
    state = _get_state()

    if state["running"] or state["result"] or state["error"]:
        st.subheader(f"⚙️ 后台任务：{state['task_name']}")

        if state["running"]:
            pct = state["progress"]
            st.progress(pct / 100, text=f"进度 {pct}%（可切换其他页面，任务在后台继续运行）")
            # 自动刷新：每 2 秒重绘一次
            time.sleep(0.5)
            st.rerun()

        elif state["error"]:
            st.error(f"❌ 任务失败：{state['error']}")
        else:
            st.success(f"✅ 任务完成！{state['result']}  ({state['start_time']} → {state['end_time']})")

        # 展示日志
        with st.expander("📋 执行日志", expanded=state["running"]):
            logs = state["logs"]
            log_text = "\n".join(logs[-50:]) if logs else "暂无日志"
            st.code(log_text, language="text")

        if not state["running"]:
            if st.button("🗑️ 清除任务状态"):
                _update_state(running=False, task_name="", progress=0,
                              logs=[], result=None, error=None)
                st.rerun()

        st.divider()

    # ──────────────────────────────────────────────────────────────
    # 数据状态总览
    # ──────────────────────────────────────────────────────────────
    st.subheader("📦 本地数据状态")

    tables = {
        "龙虎榜明细":     "lhb_detail",
        "机构席位追踪":   "lhb_jigou",
        "个股上榜统计":   "lhb_ggtj",
        "今日席位明细":   "lhb_seat_detail",   # 当日买卖方营业部（新）
        "市场情绪":       "market_sentiment",
        "每日选股":       "selected_stocks",
        "回测结果":       "backtest_results",
    }

    status_data = []
    for label, key in tables.items():
        try:
            df = storage.read(key)
            rows = len(df)
            cols = len(df.columns) if not df.empty else 0
            last_date = "—"
            if not df.empty and "trade_date" in df.columns:
                last_date = str(df["trade_date"].max())
            elif not df.empty and "fetch_date" in df.columns:
                last_date = str(df["fetch_date"].max())
            status_data.append({
                "数据表": label,
                "Key": key,
                "状态": "✅ 有数据" if rows > 0 else "📭 空",
                "行数": rows,
                "列数": cols,
                "最新日期": last_date,
            })
        except Exception as e:
            status_data.append({"数据表": label, "Key": key, "状态": f"❌ {e}",
                                 "行数": 0, "列数": 0, "最新日期": "—"})

    st.dataframe(pd.DataFrame(status_data), use_container_width=True, hide_index=True)

    # ── 定时任务说明 ───────────────────────────────────────────
    with st.expander("⏰ 自动更新计划（cron 时间表）", expanded=False):
        st.markdown("""
| 触发时间 | 任务 | 说明 |
|---------|------|------|
| **周一~周五 14:55** | 盘中情绪刷新 | 仅更新涨跌停家数（`--sentiment-only`），不影响其他操作 |
| **周一~周五 18:40** | 盘后完整更新 | Step 1~7 全部执行：Top10 / 情绪 / T+N价格补全 / 龙虎榜明细 / 机构席位 / 个股统计 / 席位明细 |
| **周一~周五 09:05** | 次日T+1补全 | 仅执行 Step 3，补全前一日 T+1 买入价和各期收益 |
| **每5分钟（全天）** | Streamlit保活 | 检测服务是否在线，异常自动重启 |

**为什么今日数据还是旧的？**
- 龙虎榜数据：收盘后 **18:40** 自动拉取（东方财富接口盘后才开放完整数据）  
- 机构席位/个股统计：同上，随 18:40 更新  
- T+N 价格：次日开盘前 **09:05** 补全（需等次日开盘后有收盘价才能计算收益）  
- 手动触发：点击下方「今日席位+机构数据」按钮可立即更新
        """)
    st.divider()

    # ──────────────────────────────────────────────────────────────
    # 数据拉取操作
    # ──────────────────────────────────────────────────────────────
    st.subheader("🔄 数据拉取")

    is_running = _get_state()["running"]
    if is_running:
        st.warning("⏳ 后台任务正在运行，请等待完成后再提交新任务。可切换到其他页面，任务不会中断。")

    col1, col2 = st.columns(2)
    with col1:
        start_input = st.date_input(
            "开始日期",
            value=datetime.strptime(HALF_YEAR_START, "%Y%m%d"),
        ).strftime("%Y%m%d")
    with col2:
        end_input = st.date_input(
            "结束日期",
            value=datetime.now(),
        ).strftime("%Y%m%d")

    force_refresh = st.checkbox("强制刷新（忽略本地缓存，重新拉取所有数据）", value=False)

    col_btn1, col_btn2, col_btn3 = st.columns(3)

    with col_btn1:
        if st.button("📥 初始化全部数据", type="primary",
                     use_container_width=True, disabled=is_running):
            if _start_task(_bg_fetch_all, start_input, end_input, force_refresh):
                st.toast("✅ 后台任务已启动，可切换其他页面，数据拉取不会中断！")
                time.sleep(0.5)
                st.rerun()

    with col_btn2:
        if st.button("📡 仅拉取龙虎榜", use_container_width=True, disabled=is_running):
            if _start_task(_bg_fetch_lhb, start_input, end_input):
                st.toast("✅ 龙虎榜拉取已在后台启动")
                time.sleep(0.5)
                st.rerun()

    with col_btn3:
        if st.button("🏦 仅拉取机构数据", use_container_width=True, disabled=is_running):
            if _start_task(_bg_fetch_jigou):
                st.toast("✅ 机构数据拉取已在后台启动")
                time.sleep(0.5)
                st.rerun()

    col_btn4, col_btn5 = st.columns(2)
    with col_btn4:
        if st.button("📈 更新今日市场情绪", use_container_width=True, disabled=is_running):
            today = datetime.now().strftime("%Y%m%d")
            with st.spinner("正在获取今日市场情绪..."):
                sentiment = fetch_market_sentiment(today, save=True)
                st.success(
                    f"✅ 今日情绪: 涨停={sentiment.get('limit_up', 0)}, "
                    f"跌停={sentiment.get('limit_down', 0)}, "
                    f"信号={sentiment.get('trade_signal', '')}"
                )
    with col_btn5:
        if st.button("🪑 更新今日席位明细", use_container_width=True, disabled=is_running,
                     help="拉取今日龙虎榜各股的买卖方营业部席位，用于席位质量评分"):
            today = datetime.now().strftime("%Y%m%d")
            with st.spinner("正在拉取今日席位明细（东方财富接口）..."):
                try:
                    import akshare as ak
                    from utils.retry import safe_akshare_call
                    raw = safe_akshare_call(
                        ak.stock_lhb_detail_em,
                        start_date=today, end_date=today,
                        max_retries=3, base_delay=2.0, fallback=None,
                    )
                    if raw is not None and not raw.empty:
                        raw["trade_date"] = today
                        storage.write(raw, "lhb_seat_detail", mode="append")
                        st.success(f"✅ 席位明细更新成功：{len(raw)} 条，列: {list(raw.columns[:6])}...")
                    else:
                        st.warning("今日暂无席位明细数据（非交易日或接口暂无）")
                except Exception as e:
                    st.error(f"拉取失败: {e}")

    # 取消按钮
    if is_running:
        st.divider()
        if st.button("⛔ 取消当前任务", type="secondary"):
            _update_state(running=False)
            _append_log("⚠️ 用户手动取消任务")
            st.rerun()

    st.divider()

    # ──────────────────────────────────────────────────────────────
    # 数据预览
    # ──────────────────────────────────────────────────────────────
    st.subheader("🔍 数据预览")

    selected_table = st.selectbox("选择要预览的数据表", list(tables.keys()))

    if selected_table:
        key = tables[selected_table]
        df = storage.read(key)
        if df.empty:
            st.info("该数据表暂无数据")
        else:
            st.caption(f"共 {len(df)} 行 × {len(df.columns)} 列")
            st.dataframe(df.tail(100), use_container_width=True)

            csv = df.to_csv(index=False, encoding="utf-8-sig")
            st.download_button(
                f"📥 下载完整数据（CSV）",
                data=csv.encode("utf-8-sig"),
                file_name=f"{key}_{datetime.now().strftime('%Y%m%d')}.csv",
                mime="text/csv",
            )

    st.divider()

    # ──────────────────────────────────────────────────────────────
    # 危险操作
    # ──────────────────────────────────────────────────────────────
    st.subheader("⚠️ 危险操作")

    with st.expander("🗑️ 清除数据（PostgreSQL 模式会删除表中数据）", expanded=False):
        st.warning("以下操作将永久清空指定数据表，请谨慎操作！")

        clear_table = st.selectbox("选择要清除的数据表", list(tables.keys()), key="clear_table")
        confirm = st.checkbox(f"我确认要清空「{clear_table}」数据")

        if confirm and st.button("🗑️ 确认清空", type="primary"):
            key = tables[clear_table]
            try:
                storage.write(pd.DataFrame(), key, mode="replace")
                st.success(f"✅ 已清空: {key}")
            except Exception as e:
                st.error(f"清空失败: {e}")
