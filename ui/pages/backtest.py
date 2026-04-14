"""
策略回测页面
"""
import streamlit as st
import pandas as pd
from datetime import datetime, timedelta

from config.settings import HALF_YEAR_START, DATA_END_DATE, STRATEGY_CONFIG
from modules.analyzer import run_backtest, analyze_backtest
from utils.storage import storage


def render():
    st.title("🔬 策略回测")
    st.caption("基于龙虎榜多因子增强策略，自动筛选机构/知名游资买入股票并计算收益")
    
    # ============================================================
    # 参数配置
    # ============================================================
    with st.expander("⚙️ 回测参数配置", expanded=True):
        col1, col2, col3 = st.columns(3)
        
        with col1:
            st.subheader("📅 时间范围")
            start_date = st.date_input(
                "开始日期",
                value=datetime.strptime(HALF_YEAR_START, "%Y%m%d"),
            ).strftime("%Y%m%d")
            
            end_date = st.date_input(
                "结束日期",
                value=datetime.now(),
            ).strftime("%Y%m%d")
        
        with col2:
            st.subheader("📌 选股参数")
            top_n = st.number_input("每日最多选股", 1, 10, STRATEGY_CONFIG["top_n"])
            min_net_buy = st.number_input(
                "净买入额阈值（万元）",
                1000, 50000,
                int(STRATEGY_CONFIG["min_net_buy"] / 10000),
                step=1000,
            ) * 10000
            min_tech = st.slider("最低技术面评分", 0, 100, STRATEGY_CONFIG["min_tech_score"])
        
        with col3:
            st.subheader("💰 止盈止损")
            use_sl = st.toggle("启用止盈止损", value=True)
            
            if use_sl:
                stop_loss = st.slider("止损比例 (%)", -20, -1, -5) / 100
                stop_profit = st.slider("止盈比例 (%)", 2, 30, 8) / 100
                max_hold_days = st.number_input("最大持有天数", 1, 10, 3)
            else:
                hold_days = st.number_input("持有天数", 1, 10, 1)
                stop_loss = -0.05
                stop_profit = 0.08
                max_hold_days = 3
    
    # ============================================================
    # 运行回测
    # ============================================================
    col_btn1, col_btn2 = st.columns([1, 4])
    with col_btn1:
        run_btn = st.button("🚀 开始回测", type="primary", use_container_width=True)
    with col_btn2:
        st.info("⏱️ 回测过程需要逐日请求行情数据，根据数据量约需 5~30 分钟，请耐心等待")
    
    if run_btn:
        progress_bar = st.progress(0, text="初始化中...")
        status_text = st.empty()
        
        with st.spinner("正在运行回测，请勿关闭页面..."):
            try:
                results_df = run_backtest(
                    start_date=start_date,
                    end_date=end_date,
                    top_n=int(top_n),
                    hold_days=int(hold_days) if not use_sl else 1,
                    use_stoploss=use_sl,
                    stop_loss=stop_loss,
                    stop_profit=stop_profit,
                    max_hold_days=int(max_hold_days),
                    min_net_buy=float(min_net_buy),
                    min_tech_score=int(min_tech),
                    save=True,
                )
                progress_bar.progress(100, text="回测完成！")
                
                if results_df.empty:
                    st.warning("回测结果为空，可能是筛选条件过严或数据不足")
                else:
                    st.success(f"✅ 回测完成，共产生 **{len(results_df)}** 笔交易")
                    st.info("请前往「📊 回测分析」页面查看详细结果")
                    
                    # 快速预览
                    st.subheader("🔍 快速预览（前20条）")
                    preview_cols = [c for c in [
                        "trade_date", "name", "code", "return_rate",
                        "exit_reason", "hold_days", "tech_score", "seat_score",
                    ] if c in results_df.columns]
                    
                    df_show = results_df[preview_cols].head(20).copy()
                    if "return_rate" in df_show.columns:
                        df_show["return_rate"] = df_show["return_rate"].apply(
                            lambda x: f"{x:.2%}"
                        )
                    st.dataframe(df_show, use_container_width=True)
                    
            except Exception as e:
                st.error(f"回测出错: {e}")
                st.exception(e)
    
    # ============================================================
    # 已有回测结果
    # ============================================================
    st.divider()
    st.subheader("💾 已保存的回测结果")
    
    bt_df = storage.read("backtest_results")
    if bt_df.empty:
        st.info("暂无保存的回测结果")
    else:
        st.success(f"已保存 **{len(bt_df)}** 条回测记录")
        
        if "trade_date" in bt_df.columns:
            min_d = bt_df["trade_date"].min()
            max_d = bt_df["trade_date"].max()
            st.caption(f"时间范围: {min_d} ~ {max_d}")
        
        if st.button("📊 前往查看详细分析", use_container_width=True):
            if "_backtest_analysis_page" in st.session_state:
                st.switch_page(st.session_state["_backtest_analysis_page"])
            else:
                st.switch_page("pages/backtest_analysis.py")
