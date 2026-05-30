"""
策略回测页面（后台线程版 — 切换页面不中断）
单股聚焦 + 可插拔策略 + Exit 组件 UI
"""
import streamlit as st
import pandas as pd
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from config.settings import HALF_YEAR_START
from modules.strategies import get_strategy, list_strategies, list_strategy_meta, BaseStrategy
from modules.backtest import DataFeed, Executor, BacktestEngine, BacktestContext, BacktestDiagnostic


# ============================================================
# 线程安全的 Job Store
# ============================================================
@dataclass
class BacktestJob:
    job_type: str = ""  # single | batch
    status: str = "running"  # running | success | failed
    progress: float = 0.0
    message: str = ""
    result: object = None  # 单股: (df, diag)；批量: (df, diag)
    error: str = None
    # 任务上下文（与当前表单解绑，避免 UI 残留误导）
    ts_code: str = ""
    strategy_name: str = ""
    start: str = ""
    end: str = ""
    roundtrip_cost: float = 0.0025
    benchmark_code: str = ""


_JOBS: dict = {}
_JOBS_LOCK = threading.Lock()


def _job_update(job_id: str, **kwargs):
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if job:
            for k, v in kwargs.items():
                setattr(job, k, v)


def _job_get(job_id: str) -> BacktestJob | None:
    with _JOBS_LOCK:
        return _JOBS.get(job_id)


def _render_params_form(param_schema: dict, prefix: str = "p", exclude: set = None) -> dict:
    """根据 param_schema 渲染表单控件"""
    exclude = exclude or set()
    params = {}
    for key, meta in param_schema.items():
        if key in exclude:
            continue
        field_type = meta.get("type", "str")
        label = meta.get("label", key)
        default = meta.get("default", 0)
        field_key = f"{prefix}_{key}"

        if field_type == "bool":
            params[key] = st.checkbox(label, value=bool(default), key=field_key)
        elif field_type == "int":
            params[key] = st.number_input(label, value=int(default), key=field_key)
        elif field_type == "float":
            if key in ("stop_loss",):
                params[key] = st.slider(label, -30, 0, int(default * 100), key=field_key) / 100
            elif key in ("stop_profit",):
                params[key] = st.slider(label, 1, 50, int(default * 100), key=field_key) / 100
            elif key == "max_buy_gap":
                params[key] = st.slider(label, 0, 15, int(default * 100), key=field_key) / 100
            elif key in ("max_gain_20d",):
                params[key] = st.slider(label, 0.0, 1.0, float(default), 0.01, key=field_key)
            elif key == "atr_mult":
                params[key] = st.slider(label, 0.5, 5.0, float(default), 0.1, key=field_key)
            elif key == "trail_activate":
                params[key] = st.slider(label, 0.0, 0.3, float(default), 0.01, key=field_key)
            elif key == "trail_giveback":
                params[key] = st.slider(label, 0.1, 0.8, float(default), 0.05, key=field_key)
            else:
                params[key] = st.number_input(label, value=float(default), key=field_key)
        else:
            params[key] = st.text_input(label, value=str(default), key=field_key)
    return params


def _code_to_ts_code(code_raw: str) -> str:
    """6位代码 → ts_code"""
    code_clean = str(code_raw).replace(".SZ", "").replace(".SH", "").replace(".BJ", "").zfill(6)
    if code_clean.startswith(("6", "9")):
        return f"{code_clean}.SH"
    elif code_clean.startswith(("0", "3")):
        return f"{code_clean}.SZ"
    else:
        return f"{code_clean}.BJ"


def _make_strategy_instance(name: str, params: dict) -> BaseStrategy:
    cls = get_strategy(name)
    return cls(params)


# ============================================================
# 后台线程
# ============================================================
def _bt_runner(job_id: str, ts_code: str, sd: str, ed: str, strategy: BaseStrategy,
               roundtrip_cost: float = 0.0025, benchmark_code: str = ""):
    try:
        _job_update(job_id, progress=0, message="加载数据...")

        feed = DataFeed()
        engine = BacktestEngine(feed, roundtrip_cost=roundtrip_cost)
        ctx = BacktestContext(data_feed=feed, code=ts_code, start=sd, end=ed,
                              strategy_params=strategy.params,
                              benchmark_code=benchmark_code or "000300.SH")

        _job_update(job_id, progress=10, message="扫描信号...")
        df, diag = engine.run(ts_code, sd, ed, strategy, ctx)

        _job_update(job_id, status="success", progress=100,
                    message=f"完成，共 {len(df)} 笔", result=(df, diag))
    except Exception as e:
        _job_update(job_id, status="failed", progress=0, message="回测失败", error=str(e))


def _bt_batch_runner(job_id: str, sd: str, ed: str, strategy: BaseStrategy,
                     roundtrip_cost: float = 0.0025):
    try:
        _job_update(job_id, progress=0, message="加载数据...")
        engine = BacktestEngine(DataFeed(), roundtrip_cost=roundtrip_cost)
        df, diag = engine.run_batch(sd, ed, strategy, save=True)
        _job_update(job_id, status="success", progress=100,
                    message=f"完成，共 {len(df)} 笔", result=(df, diag))
    except Exception as e:
        _job_update(job_id, status="failed", progress=0, message="失败", error=str(e))


# ============================================================
# 页面渲染
# ============================================================
def render():
    st.title("🔬 策略回测")
    st.info(
        "📌 **单股逐笔信号回测**，不考虑资金占用和持仓重叠限制。"
        "T+1 开盘买入，一字涨停自动过滤，高开超阈值放弃。"
    )

    # 初始化 session_state
    for _key in ("_bt_job_id", "_bt_strategy_name", "_bt_ts_code"):
        if _key not in st.session_state:
            st.session_state[_key] = None

    # ── 策略选择 ──
    strategy_meta_list = list_strategy_meta()
    strategy_names = [m["name"] for m in strategy_meta_list]
    strategy_labels = [f"{m['name']} — {m['description']}" for m in strategy_meta_list]

    col_code, col_strat, col_date = st.columns([2, 3, 3])
    with col_code:
        code_raw = st.text_input("股票代码", value="000001", key="bt_code",
                                 help="6位数字或 ts_code，如 000001、688981.SH").strip()
        ts_code = _code_to_ts_code(code_raw)
        st.caption(f"→ {ts_code}")

    with col_strat:
        selected_idx = st.selectbox(
            "选择策略", range(len(strategy_names)),
            format_func=lambda i: strategy_labels[i],
            key="bt_strat_idx"
        )
        selected_strategy_name = strategy_names[selected_idx]
        strategy_cls = get_strategy(selected_strategy_name)

    with col_date:
        sd = st.date_input("开始日期", key="bt_sd", value=datetime(2024, 1, 1)).strftime("%Y%m%d")
        ed = st.date_input("结束日期", key="bt_ed", value=datetime.now()).strftime("%Y%m%d")

    # 策略模式提示
    mode = getattr(strategy_cls, "mode", "event")
    deps = getattr(strategy_cls, "data_requirements", {})
    mode_hint = f"模式: **{mode}** | 数据需求: K线={'✓' if deps.get('kline') else '✗'} LHB={'✓' if deps.get('lhb') else '✗'} 情绪={'✓' if deps.get('sentiment') else '✗'}"
    st.caption(mode_hint)

    # ── 策略参数 ──
    with st.expander("⚙️ 策略参数", expanded=True):
        strategy_params = _render_params_form(strategy_cls.param_schema, prefix="bt",
                                              exclude={"stock_code"})
        if "stock_code" in strategy_cls.param_schema:
            strategy_params["stock_code"] = ts_code

    # ── 基准选择 ──
    with st.expander("📊 基准 & 高级选项", expanded=False):
        col_b1, col_b2 = st.columns(2)
        with col_b1:
            benchmark = st.selectbox("对比基准", ["000300.SH 沪深300", "000905.SH 中证500", "无"],
                                     index=0, key="bt_bench")
        with col_b2:
            roundtrip_cost = st.number_input("双边手续费", 0.0, 0.01, 0.0025, 0.0005,
                                             format="%.3f", key="bt_cost")

    # ── 运行 ──
    job_id = st.session_state.get("_bt_job_id")
    job = _job_get(job_id) if job_id else None
    is_running = job is not None and job.status == "running"

    col_btn1, _ = st.columns([1, 3])
    with col_btn1:
        if st.button("🚀 开始回测", type="primary", disabled=is_running, key="bt_run"):
            new_id = str(uuid.uuid4())[:8]
            bench_code = benchmark.split()[0] if benchmark != "无" else ""
            with _JOBS_LOCK:
                _JOBS[new_id] = BacktestJob(
                    job_type="single",
                    ts_code=ts_code,
                    strategy_name=selected_strategy_name,
                    start=sd, end=ed,
                    roundtrip_cost=roundtrip_cost,
                    benchmark_code=bench_code,
                )
            st.session_state["_bt_job_id"] = new_id
            st.session_state["_bt_strategy_name"] = selected_strategy_name
            st.session_state["_bt_ts_code"] = ts_code
            strategy = _make_strategy_instance(selected_strategy_name, strategy_params)
            threading.Thread(target=_bt_runner, args=(
                new_id, ts_code, sd, ed, strategy, roundtrip_cost, bench_code,
            ), daemon=True).start()
            st.rerun()

    if is_running:
        st.progress(int(job.progress), text=job.message)
        import time as _t;
        _t.sleep(1.5);
        st.rerun()

    if job and job.status == "failed":
        st.error(f"回测出错: {job.error}")
    if job and job.status == "success" and job.result is not None:
        # 解构结果（新格式：(df, diag)；老格式兼容：单 df）
        if isinstance(job.result, tuple) and len(job.result) == 2:
            df, diag = job.result
        else:
            df, diag = job.result, None
        if df.empty:
            # 用 job 自带的上下文，不用当前表单
            ctx_label = f"{job.ts_code} | {job.strategy_name} | {job.start}~{job.end}"
            if diag is not None and diag.empty_reason:
                st.warning(f"⚠️ {ctx_label}：{diag.empty_reason}")
            else:
                st.warning(f"⚠️ {ctx_label}：在区间内无信号")
        else:
            n = len(df)
            st.success(f"✅ {job.ts_code} | {job.strategy_name} → {n} 笔交易")
            if diag is not None and diag.skip_stats:
                st.caption(f"📋 跳过: " + " | ".join(f"{k}:{v}" for k, v in diag.skip_stats.items()))
            if n < 10:
                st.caption(f"⚠️ 样本仅 {n} 笔，统计结果稳定性较弱。")
            elif n < 30:
                st.caption(f"📌 样本 {n} 笔，参考价值有限，建议扩大日期范围。")
            _show_result(df)
            _save_section(df, job.strategy_name)

    st.divider()

    # ── 批量回测（折叠）──
    with st.expander("📊 批量自动选股回测（LHB 策略专用）", expanded=False):
        _render_batch_section()


# ============================================================
# 结果展示
# ============================================================
def _show_result(df: pd.DataFrame):
    display_cols = [c for c in [
        "trade_date", "buy_date", "return_rate", "exit_reason", "hold_days",
        "buy_price", "sell_price", "signal_close", "t1_gap",
    ] if c in df.columns]
    ds = df[display_cols].copy()
    for c in ["return_rate", "t1_gap"]:
        if c in ds.columns:
            ds[c] = ds[c].apply(lambda x: f"{x * 100:+.2f}%" if pd.notna(x) else "—")
    for c in ["buy_price", "sell_price", "signal_close"]:
        if c in ds.columns:
            ds[c] = ds[c].apply(lambda x: f"¥{x:.2f}" if pd.notna(x) else "—")
    if "trade_date" in ds.columns:
        ds["trade_date"] = ds["trade_date"].apply(
            lambda x: f"{str(x)[:4]}-{str(x)[4:6]}-{str(x)[6:]}" if len(str(x)) == 8 else str(x))
    if "buy_date" in ds.columns:
        ds["buy_date"] = ds["buy_date"].apply(
            lambda x: f"{str(x)[:4]}-{str(x)[4:6]}-{str(x)[6:]}" if len(str(x)) == 8 else str(x))
    st.dataframe(ds, use_container_width=True, hide_index=True)

    if "return_rate" in df.columns:
        rates = df["return_rate"].dropna().astype(float)
        if len(rates) > 0:
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("交易次数", len(rates))
            c2.metric("胜率", f"{(rates > 0).mean() * 100:.1f}%")
            c3.metric("平均收益", f"{rates.mean() * 100:+.2f}%")
            pos, neg = rates[rates > 0], rates[rates < 0]
            c4.metric("盈亏比", f"{pos.mean() / abs(neg.mean()):.1f}" if len(pos) and len(neg) else "—")
    ## K线买卖图
    ts_code = df["code"].iloc[0]
    if ts_code is None:
        return
    _render_kline_chart(df, ts_code)


def _save_section(df: pd.DataFrame, strategy_name: str):
    """保存到数据库"""
    if st.button("💾 保存到回测结果表", key="bt_save"):
        from utils.storage import storage
        import json
        run_id = uuid.uuid4().hex[:12]
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ts_code = st.session_state.get("_bt_ts_code", "?")

        df_to_save = df.copy()
        df_to_save["run_id"] = run_id
        df_to_save["run_type"] = "single_stock"
        df_to_save["strategy_name"] = strategy_name
        df_to_save["created_at"] = now
        if "signal_meta" in df_to_save.columns:
            df_to_save["signal_meta"] = df_to_save["signal_meta"].apply(
                lambda x: json.dumps(x, ensure_ascii=False) if isinstance(x, dict) else str(x)
            )

        ok_results = storage.write(df_to_save, "backtest_results", mode="append")
        run_meta = pd.DataFrame([{
            "run_id": run_id, "task_id": run_id,
            "strategy_name": strategy_name, "status": "SUCCESS",
            "start_date": "", "end_date": "",
            "trade_count": len(df_to_save),
            "config": json.dumps({"code": ts_code}),
            "created_at": now,
        }])
        ok_runs = storage.write(run_meta, "backtest_runs", mode="append")

        if ok_results and ok_runs:
            st.success(f"✅ 已保存 {len(df_to_save)} 条 (run={run_id})")
        else:
            st.error(
                f"❌ 保存失败：results={ok_results} runs={ok_runs}。\n\n"
                f"通常是 PG 表 schema 缺列，请联系 Ops 在 jd 上执行：\n"
                f"`psql ... -f scripts/migrate_20260508b_backtest_schema_fix.sql`\n\n"
                f"详细错误见 logs/streamlit_*.log"
            )


# ============================================================
# 批量回测
# ============================================================
def _render_batch_section():
    st.subheader("📊 批量自动选股回测")

    strategy_meta_list = list_strategy_meta()
    strategy_names = [m["name"] for m in strategy_meta_list]
    strategy_labels = [f"{m['name']} — {m['description']}" for m in strategy_meta_list]
    selected_idx = st.selectbox(
        "选择策略", range(len(strategy_names)),
        format_func=lambda i: strategy_labels[i],
        key="batch_strat_idx"
    )
    selected_strategy_name = strategy_names[selected_idx]
    strategy_cls = get_strategy(selected_strategy_name)

    col_d1, col_d2 = st.columns(2)
    with col_d1:
        b_sd = st.date_input("开始日期", key="b_sd", value=datetime(2024, 1, 1)).strftime("%Y%m%d")
    with col_d2:
        b_ed = st.date_input("结束日期", key="b_ed", value=datetime.now()).strftime("%Y%m%d")

    strategy_params = _render_params_form(strategy_cls.param_schema, prefix="batch")

    use_stoploss = st.toggle("使用止盈止损模式", value=True, key="b_use_sl")

    job_id = st.session_state.get("_batch_bt_job_id")
    job = _job_get(job_id) if job_id else None
    is_running = job is not None and job.status == "running" and job.job_type == "batch"

    col_b1, col_b2 = st.columns([1, 4])
    with col_b1:
        if st.button("🚀 开始批量回测", type="primary", disabled=is_running, key="b_run"):
            new_id = str(uuid.uuid4())[:8]
            with _JOBS_LOCK:
                _JOBS[new_id] = BacktestJob(
                    job_type="batch",
                    strategy_name=selected_strategy_name,
                    start=b_sd, end=b_ed,
                )
            st.session_state["_batch_bt_job_id"] = new_id
            strategy = _make_strategy_instance(selected_strategy_name, strategy_params)
            threading.Thread(target=_bt_batch_runner, args=(
                new_id, b_sd, b_ed, strategy,
            ), daemon=True).start()
            st.rerun()
    with col_b2:
        if is_running:
            st.info(f"⏳ 运行中... {job.message if job else ''}")

    if is_running:
        st.progress(int(job.progress) if job and job.progress else 0,
                    text=job.message if job else "")
        import time as _t;
        _t.sleep(2);
        st.rerun()

    if job and job.status == "failed" and job.error:
        st.error(f"回测出错: {job.error}")

    if job and job.status == "success" and job.result is not None:
        # 解构（兼容老格式）
        if isinstance(job.result, tuple) and len(job.result) == 2:
            df, diag = job.result
        else:
            df, diag = job.result, None
        if df.empty:
            label = f"{job.strategy_name} | {job.start}~{job.end}"
            if diag is not None and diag.empty_reason:
                st.warning(f"⚠️ {label}：{diag.empty_reason}")
            else:
                st.warning(f"⚠️ {label}：回测结果为空")
        else:
            st.success(f"✅ 完成，共 **{len(df)}** 笔交易")
            if diag is not None and diag.skip_stats:
                st.caption(f"📋 跳过: " + " | ".join(f"{k}:{v}" for k, v in diag.skip_stats.items()))
            st.info("请前往「📊 回测分析」页面查看详细结果")
            preview_cols = [c for c in [
                "trade_date", "name", "code", "return_rate", "exit_reason", "hold_days",
            ] if c in df.columns]
            df_show = df[preview_cols].head(20).copy()
            if "return_rate" in df_show.columns:
                df_show["return_rate"] = df_show["return_rate"].apply(
                    lambda x: f"{float(x):.2%}" if pd.notna(x) else "—"
                )
            st.dataframe(df_show, use_container_width=True)

    # 已保存结果
    st.divider()
    st.subheader("💾 已保存的回测结果")
    from utils.storage import storage
    bt_df = storage.read("backtest_results")
    if bt_df.empty:
        st.info("暂无保存的回测结果")
    else:
        st.success(f"已保存 **{len(bt_df)}** 条回测记录")
        if "trade_date" in bt_df.columns:
            st.caption(f"时间范围: {bt_df['trade_date'].min()} ~ {bt_df['trade_date'].max()}")
        if st.button("📊 前往查看详细分析", use_container_width=True, key="b_goto_analysis"):
            if "_backtest_analysis_page" in st.session_state:
                st.switch_page(st.session_state["_backtest_analysis_page"])


def _render_kline_chart(df: pd.DataFrame, ts_code: str):
    import numpy as np
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots
    from modules.backtest import DataFeed
    from config.theme import Color

    if df.empty or "buy_date" not in df.columns:
        return

    # ── 1. 加载 K 线数据 ──
    buy_dates = pd.to_datetime(df["buy_date"], format="%Y%m%d", errors="coerce").dropna()
    if buy_dates.empty:
        return

    sell_dates = pd.Series(dtype="datetime64[ns]")
    if "sell_date" in df.columns:
        sell_dates = pd.to_datetime(df["sell_date"], format="%Y%m%d", errors="coerce").dropna()

    sd = (buy_dates.min() - pd.Timedelta(days=15)).strftime("%Y%m%d")
    ed = (
        (sell_dates.max() + pd.Timedelta(days=15)).strftime("%Y%m%d")
        if not sell_dates.empty and datetime.now() > sell_dates.max() + pd.Timedelta(days=15)
        else datetime.now().strftime("%Y%m%d")
    )

    feed = DataFeed()
    kdf = feed.kline(ts_code, sd, ed)
    if kdf.empty:
        st.info(f"无 K 线数据：{ts_code} {sd}~{ed}")
        return

    kdf = kdf.rename(columns={
        "日期": "date", "开盘": "open", "最高": "high",
        "最低": "low", "收盘": "close", "成交量": "volume",
    })
    kdf["date"] = pd.to_datetime(kdf["date"])
    kdf["ma5"]  = kdf["close"].rolling(5).mean()
    kdf["ma20"] = kdf["close"].rolling(20).mean()

    # ── 2. 买卖标记位置（向量化，无 iterrows）──
    trades = df.copy()
    trades["buy_dt"] = pd.to_datetime(trades["buy_date"], format="%Y%m%d", errors="coerce")

    # 买入：合并 kdf 的 low，标记圆圈放在 candle_low 下方 2.5%
    buy_merge = (
        trades.dropna(subset=["buy_dt", "buy_price"])
        .merge(kdf[["date", "low"]].rename(columns={"date": "buy_dt"}), on="buy_dt", how="left")
    )
    buy_merge["marker_y"] = buy_merge["low"].fillna(buy_merge["buy_price"]) * 0.975

    sell_merge = pd.DataFrame()
    if "sell_date" in trades.columns and "sell_price" in trades.columns:
        trades["sell_dt"] = pd.to_datetime(trades["sell_date"], format="%Y%m%d", errors="coerce")
        sell_merge = (
            trades.dropna(subset=["sell_dt", "sell_price"])
            .merge(kdf[["date", "high"]].rename(columns={"date": "sell_dt"}), on="sell_dt", how="left")
        )
        sell_merge["marker_y"] = sell_merge["high"].fillna(sell_merge["sell_price"]) * 1.025

    def _vlines(dt_col: pd.Series, y_from: pd.Series, y_to: pd.Series):
        """生成 Plotly 多段虚线坐标，每段末插 None 以断开。"""
        n = len(dt_col)
        xs = np.empty(n * 3, dtype=object)
        ys = np.empty(n * 3, dtype=object)
        xs[0::3] = xs[1::3] = dt_col.values
        xs[2::3] = None
        ys[0::3] = y_from.values
        ys[1::3] = y_to.values
        ys[2::3] = None
        return xs.tolist(), ys.tolist()

    # ── 3. 标题 ──
    stock_name = df["name"].iloc[0] if "name" in df.columns and df["name"].notna().any() else ""
    n_buy  = len(buy_merge)
    n_sell = len(sell_merge)
    title  = (
        f"日K线图 · {ts_code.split('.')[0]} {stock_name}"
        f"  ↑ 买入×{n_buy}  ↓ 卖出×{n_sell}"
    )

    # ── 4. 构建图表（K线 75% + 成交量 25%）──
    fig = make_subplots(
        rows=2, cols=1,
        shared_xaxes=True,
        vertical_spacing=0.02,
        row_heights=[0.76, 0.24],
    )

    # K 线蜡烛图（红涨绿跌，A股习惯）
    fig.add_trace(go.Candlestick(
        x=kdf["date"],
        open=kdf["open"], high=kdf["high"], low=kdf["low"], close=kdf["close"],
        increasing=dict(line=dict(color=Color.RED, width=1), fillcolor=Color.RED),
        decreasing=dict(line=dict(color=Color.GREEN, width=1), fillcolor=Color.GREEN),
        name="K线", showlegend=False,
        hoverlabel=dict(bgcolor="#222"),
    ), row=1, col=1)

    # MA 均线
    for field, color, label in [("ma5", Color.GOLD, "MA5"), ("ma20", Color.BLUE, "MA20")]:
        fig.add_trace(go.Scatter(
            x=kdf["date"], y=kdf[field],
            line=dict(color=color, width=1.5), name=label,
            hovertemplate=f"{label}: %{{y:.2f}}<extra></extra>",
        ), row=1, col=1)

    # 成交量子图（红涨绿跌）
    vol_colors = [Color.RED if c >= o else Color.GREEN for c, o in zip(kdf["close"], kdf["open"])]
    fig.add_trace(go.Bar(
        x=kdf["date"], y=kdf["volume"],
        marker_color=vol_colors, name="量", showlegend=False,
        hovertemplate="量: %{y:,.0f}<extra></extra>",
    ), row=2, col=1)

    # 买入虚线 + B 圆圈标记
    if not buy_merge.empty:
        bx, by = _vlines(
            buy_merge["buy_dt"],
            buy_merge["marker_y"],
            buy_merge["low"].fillna(buy_merge["buy_price"]),
        )
        fig.add_trace(go.Scatter(
            x=bx, y=by, mode="lines",
            line=dict(color=Color.RED, width=1, dash="dot"),
            showlegend=False, hoverinfo="skip",
        ), row=1, col=1)
        fig.add_trace(go.Scatter(
            x=buy_merge["buy_dt"], y=buy_merge["marker_y"],
            mode="markers+text",
            marker=dict(symbol="circle", size=18, color=Color.RED,
                        line=dict(color="white", width=1.5)),
            text=["B"] * n_buy,
            textfont=dict(color="white", size=9, family="Arial Black"),
            textposition="middle center",
            name=f"买入×{n_buy}",
            customdata=buy_merge[["buy_price"]].values,
            hovertemplate="买入 ¥%{customdata[0]:.2f}  %{x|%Y-%m-%d}<extra></extra>",
        ), row=1, col=1)

    # 卖出虚线 + S 圆圈标记
    if not sell_merge.empty:
        sx, sy = _vlines(
            sell_merge["sell_dt"],
            sell_merge["high"].fillna(sell_merge["sell_price"]),
            sell_merge["marker_y"],
        )
        fig.add_trace(go.Scatter(
            x=sx, y=sy, mode="lines",
            line=dict(color=Color.BLUE, width=1, dash="dot"),
            showlegend=False, hoverinfo="skip",
        ), row=1, col=1)

        has_ret = "return_rate" in sell_merge.columns
        sell_custom = sell_merge[["sell_price", "return_rate"]].values if has_ret else sell_merge[["sell_price"]].values
        sell_tmpl = (
            "卖出 ¥%{customdata[0]:.2f}  收益 %{customdata[1]:.1%}  %{x|%Y-%m-%d}<extra></extra>"
            if has_ret else
            "卖出 ¥%{customdata[0]:.2f}  %{x|%Y-%m-%d}<extra></extra>"
        )
        fig.add_trace(go.Scatter(
            x=sell_merge["sell_dt"], y=sell_merge["marker_y"],
            mode="markers+text",
            marker=dict(symbol="circle", size=18, color=Color.BLUE,
                        line=dict(color="white", width=1.5)),
            text=["S"] * n_sell,
            textfont=dict(color="white", size=9, family="Arial Black"),
            textposition="middle center",
            name=f"卖出×{n_sell}",
            customdata=sell_custom,
            hovertemplate=sell_tmpl,
        ), row=1, col=1)

    # ── 5. 布局与主题 ──
    view_end   = kdf["date"].max()
    view_start = view_end - pd.Timedelta(days=90)

    fig.update_layout(
        plot_bgcolor="#0d1117",
        paper_bgcolor="#161b22",
        font=dict(color="#aaaaaa"),
        title=dict(text=title, font=dict(color="white", size=13), x=0, xanchor="left"),
        height=580,
        margin=dict(t=50, b=20, l=10, r=70),
        legend=dict(
            orientation="h", x=0, y=1.05,
            font=dict(color="#aaaaaa", size=10),
            bgcolor="rgba(0,0,0,0)",
        ),
        hovermode="x unified",
        dragmode="pan",  # 默认拖拽 = 平移（不进入框选/缩放模式）
    )
    _GRID = dict(showgrid=True, gridcolor="#1f2937", gridwidth=0.5, zeroline=False)
    fig.update_xaxes(
        **_GRID,
        range=[view_start.isoformat(), view_end.isoformat()],
        rangeslider=dict(visible=False),
        tickformat="%m月",
        tickfont=dict(color="#777"),
        rangebreaks=[dict(bounds=["sat", "mon"])],
    )
    # fixedrange=True：锁定 Y 轴，彻底禁止纵向缩放/拖拽
    fig.update_yaxes(**_GRID, side="right", tickfont=dict(color="#777"), fixedrange=True)
    fig.update_yaxes(showgrid=False, fixedrange=True, row=2, col=1)

    # 右侧当前收盘价标签
    last_price = float(kdf["close"].iloc[-1])
    last_open  = float(kdf["open"].iloc[-1])
    fig.add_annotation(
        x=1, y=last_price, xref="paper", yref="y",
        text=f"  {last_price:.2f}  ",
        showarrow=False,
        font=dict(color="white", size=10),
        bgcolor=Color.RED if last_price >= last_open else Color.GREEN,
        borderpad=3, xanchor="left",
    )

    st.caption("💡 拖拽左右平移 / 双击还原，默认展示最近 3 个月")
    st.plotly_chart(fig, use_container_width=True, config={
        "scrollZoom": False,       # 禁用滚轮/双指缩放
        "displayModeBar": False,   # 隐藏工具栏，界面更简洁
    })



