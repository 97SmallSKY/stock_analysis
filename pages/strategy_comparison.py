"""
策略对比看板页面

功能：
1. 选择多个机器人进行对比
2. 显示6个核心图表
3. 显示评分排名表
"""
import streamlit as st
from pathlib import Path
import sys

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))

from utils.storage import storage
from sqlalchemy import text
from strategy.scorer import rank_robots, load_weights_config
from analysis.charts import (
    chart_nav_comparison,
    chart_drawdown_comparison,
    chart_sharpe_comparison,
    chart_metrics_table,
    chart_trade_scatter,
    chart_score_radar,
)


def render():
    """渲染策略对比看板"""
    st.title("🏆 策略对比看板")
    
    # === 侧边栏：选择机器人 ===
    with st.sidebar:
        st.header("📊 选择对比策略")
        
        # 获取所有机器人列表
        engine = storage._get_engine()
        with engine.connect() as conn:
            robots = conn.execute(text('''
                SELECT robot_id, robot_name, strategy_name, status, initial_capital
                FROM sim_robots
                ORDER BY created_at DESC
            ''')).fetchall()
        
        if not robots:
            st.warning("⚠️ 暂无机器人数据，请先创建机器人")
            return
        
        robot_options = {f"{r[1]} ({r[2]})": r[0] for r in robots}
        
        selected_names = st.multiselect(
            "选择要对比的机器人（最多5个）",
            options=list(robot_options.keys()),
            max_selections=5,
        )
        
        if len(selected_names) < 2:
            st.info("👆 请至少选择2个机器人进行对比")
            return
        
        selected_ids = [robot_options[name] for name in selected_names]
        
        # 基准指数选择
        st.divider()
        st.subheader("📈 基准设置")
        show_benchmark = st.checkbox("显示基准指数", value=True)
        benchmark_code = st.selectbox(
            "选择基准指数",
            options=[
                ('000001', '上证指数'),
                ('000300', '沪深300'),
                ('000905', '中证500'),
                ('399006', '创业板指'),
            ],
            format_func=lambda x: x[1],
        )[0]
        
        # 评分权重配置
        st.divider()
        st.subheader("⚖️ 评分权重")
        config_name = st.selectbox(
            "选择权重配置",
            options=['default'],
            format_func=lambda x: '默认配置' if x == 'default' else x,
        )
    
    # === 主区域：图表展示 ===
    st.subheader("📊 综合评分排名")
    
    # 加载权重配置
    weights, benchmarks = load_weights_config(config_name)
    
    # 计算排名
    ranking = rank_robots(selected_ids, weights, benchmarks)
    
    if not ranking:
        st.warning("⚠️ 无法计算评分，可能缺少净值数据")
        return
    
    # 显示排名表
    rank_df = [{
        '排名': r['rank'],
        '策略名称': r['robot_name'],
        '综合得分': r['total_score'],
        '累计收益': f"{r['metrics']['return_rate']:.2f}%",
        '最大回撤': f"{r['metrics']['max_drawdown']:.2f}%",
        '夏普比率': f"{r['metrics']['sharpe_ratio']:.2f}",
        '胜率': f"{r['metrics']['win_rate']:.1f}%",
        '交易笔数': r['metrics']['sample_size'],
    } for r in ranking]
    
    st.dataframe(rank_df, use_container_width=True, hide_index=True)
    
    st.divider()
    
    # === 图表区域 ===
    col1, col2 = st.columns(2)
    
    with col1:
        # 净值曲线对比
        fig_nav = chart_nav_comparison(selected_ids, benchmark_code, show_benchmark)
        st.plotly_chart(fig_nav, use_container_width=True)
        
        # 夏普比率对比
        fig_sharpe = chart_sharpe_comparison(selected_ids)
        st.plotly_chart(fig_sharpe, use_container_width=True)
        
        # 交易散点图
        fig_scatter = chart_trade_scatter(selected_ids)
        st.plotly_chart(fig_scatter, use_container_width=True)
    
    with col2:
        # 回撤曲线对比
        fig_dd = chart_drawdown_comparison(selected_ids)
        st.plotly_chart(fig_dd, use_container_width=True)
        
        # 雷达图
        fig_radar = chart_score_radar(selected_ids)
        st.plotly_chart(fig_radar, use_container_width=True)
        
        # 核心指标表格
        fig_table = chart_metrics_table(selected_ids)
        st.plotly_chart(fig_table, use_container_width=True)
    
    # === 权重配置说明 ===
    with st.expander("📖 评分权重说明"):
        st.markdown("""
        | 指标 | 权重 | 说明 |
        |------|------|------|
        | 累计收益率 | 25% | 核心盈利能力 |
        | 最大回撤 | 20% | 风险控制能力 |
        | 夏普比率 | 20% | 风险调整后收益 |
        | 胜率 | 10% | 交易准确度 |
        | 盈亏比 | 10% | 盈利质量 |
        | 交易成本占比 | 10% | 策略效率 |
        | 样本数量 | 5% | 统计可信度 |
        """)
        
        st.info("""
        💡 **评分机制说明**：
        - 每项指标单独评分（0-100分）
        - 根据权重加权计算综合得分
        - 满分100分，分数越高表现越好
        """)


if __name__ == '__main__':
    render()
