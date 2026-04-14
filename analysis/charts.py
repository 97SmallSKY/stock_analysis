"""
策略对比图表模块

图表清单：
1. 净值曲线对比（含基准线）
2. 回撤曲线对比
3. 夏普比率柱状图
4. 胜率/盈亏比表格
5. 月度收益热力图
6. 单笔交易散点图
"""
import sys
import logging
from pathlib import Path
from typing import List, Optional
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from utils.storage import storage
from sqlalchemy import text

logger = logging.getLogger(__name__)

# 配色方案
COLORS = ['#FF6B6B', '#4ECDC4', '#45B7D1', '#96CEB4', '#FFEAA7', '#DDA0DD', '#98D8C8', '#F7DC6F']


def chart_nav_comparison(
    robot_ids: List[str],
    benchmark_code: Optional[str] = '000001',
    show_benchmark: bool = True
) -> go.Figure:
    """
    净值曲线对比图
    
    Args:
        robot_ids: 机器人ID列表
        benchmark_code: 基准指数代码（默认上证指数）
        show_benchmark: 是否显示基准线
    """
    fig = go.Figure()
    engine = storage._get_engine()
    
    with engine.connect() as conn:
        for i, rid in enumerate(robot_ids):
            # 查询净值数据
            df = pd.read_sql(text('''
                SELECT trade_date, nav 
                FROM simDaily_summary
                WHERE robot_id = :rid 
                ORDER BY trade_date
            '''), conn, params={'rid': rid})
            
            if df.empty:
                continue
            
            # 获取机器人名称
            name = conn.execute(text('''
                SELECT robot_name FROM sim_robots WHERE robot_id = :rid
            '''), {'rid': rid}).scalar() or rid
            
            fig.add_trace(go.Scatter(
                x=df['trade_date'],
                y=df['nav'],
                mode='lines',
                name=name,
                line=dict(color=COLORS[i % len(COLORS)], width=2.5),
                hovertemplate='%{y:.4f}<extra></extra>',
            ))
        
        # 添加基准指数
        if show_benchmark and benchmark_code:
            bench_df = pd.read_sql(text('''
                SELECT trade_date, nav 
                FROM benchmark_daily
                WHERE index_code = :code
                ORDER BY trade_date
            '''), conn, params={'code': benchmark_code})
            
            if not bench_df.empty:
                # 归一化基准净值
                bench_df['nav'] = bench_df['nav'] / bench_df['nav'].iloc[0] if bench_df['nav'].iloc[0] > 0 else 1
                
                fig.add_trace(go.Scatter(
                    x=bench_df['trade_date'],
                    y=bench_df['nav'],
                    mode='lines',
                    name=f'基准({benchmark_code})',
                    line=dict(color='#888888', width=1.5, dash='dash'),
                    hovertemplate='%{y:.4f}<extra></extra>',
                ))
    
    fig.update_layout(
        title="📈 策略净值曲线对比",
        xaxis_title="日期",
        yaxis_title="净值",
        height=500,
        template='plotly_white',
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="right",
            x=1
        ),
        hovermode='x unified',
    )
    
    return fig


def chart_drawdown_comparison(robot_ids: List[str]) -> go.Figure:
    """回撤曲线对比图"""
    fig = go.Figure()
    engine = storage._get_engine()
    
    with engine.connect() as conn:
        for i, rid in enumerate(robot_ids):
            df = pd.read_sql(text('''
                SELECT trade_date, nav 
                FROM simDaily_summary
                WHERE robot_id = :rid 
                ORDER BY trade_date
            '''), conn, params={'rid': rid})
            
            if df.empty:
                continue
            
            # 计算回撤
            peak = df['nav'].expanding().max()
            drawdown = (df['nav'] - peak) / peak * 100
            
            name = conn.execute(text('''
                SELECT robot_name FROM sim_robots WHERE robot_id = :rid
            '''), {'rid': rid}).scalar() or rid
            
            fig.add_trace(go.Scatter(
                x=df['trade_date'],
                y=drawdown,
                mode='lines',
                name=name,
                line=dict(color=COLORS[i % len(COLORS)], width=2),
                fill='tozeroy',
                hovertemplate='回撤: %{y:.2f}%<extra></extra>',
            ))
    
    fig.update_layout(
        title="📉 最大回撤对比",
        xaxis_title="日期",
        yaxis_title="回撤(%)",
        height=400,
        template='plotly_white',
    )
    
    return fig


def chart_sharpe_comparison(robot_ids: List[str]) -> go.Figure:
    """夏普比率柱状图"""
    from strategy.scorer import calc_robot_metrics
    
    fig = go.Figure()
    names, sharpes = [], []
    engine = storage._get_engine()
    
    with engine.connect() as conn:
        for rid in robot_ids:
            metrics = calc_robot_metrics(rid)
            if metrics:
                name = conn.execute(text('''
                    SELECT robot_name FROM sim_robots WHERE robot_id = :rid
                '''), {'rid': rid}).scalar() or rid
                names.append(name)
                sharpes.append(metrics['sharpe_ratio'])
    
    fig.add_trace(go.Bar(
        x=names,
        y=sharpes,
        marker_color=COLORS[:len(names)],
        text=[f'{s:.2f}' for s in sharpes],
        textposition='outside',
    ))
    
    fig.update_layout(
        title="📊 夏普比率对比",
        xaxis_title="策略",
        yaxis_title="夏普比率",
        height=400,
        template='plotly_white',
    )
    
    return fig


def chart_metrics_table(robot_ids: List[str]) -> go.Figure:
    """
    指标对比表格
    
    显示：胜率、盈亏比、累计收益、最大回撤、夏普比率
    """
    from strategy.scorer import calc_robot_metrics
    
    engine = storage._get_engine()
    
    headers = ['策略', '累计收益', '最大回撤', '夏普比率', '胜率', '盈亏比', '交易笔数']
    cells = []
    
    with engine.connect() as conn:
        for rid in robot_ids:
            metrics = calc_robot_metrics(rid)
            if metrics:
                name = conn.execute(text('''
                    SELECT robot_name FROM sim_robots WHERE robot_id = :rid
                '''), {'rid': rid}).scalar() or rid
                
                cells.append([
                    name,
                    f"{metrics['return_rate']:.2f}%",
                    f"{metrics['max_drawdown']:.2f}%",
                    f"{metrics['sharpe_ratio']:.2f}",
                    f"{metrics['win_rate']:.1f}%",
                    f"{metrics['profit_loss_ratio']:.2f}",
                    str(metrics['sample_size']),
                ])
    
    fig = go.Figure(data=[go.Table(
        header=dict(
            values=headers,
            fill_color='#45B7D1',
            font=dict(color='white', size=12),
            align='center',
        ),
        cells=dict(
            values=list(zip(*cells)) if cells else [[]],
            fill_color='white',
            font=dict(size=11),
            align='center',
        )
    )])
    
    fig.update_layout(
        title="📋 核心指标对比",
        height=300,
        margin=dict(l=10, r=10, t=50, b=10),
    )
    
    return fig


def chart_trade_scatter(robot_ids: List[str]) -> go.Figure:
    """
    单笔交易散点图
    
    X轴：持仓天数
    Y轴：收益率(%)
    """
    fig = go.Figure()
    engine = storage._get_engine()
    
    with engine.connect() as conn:
        for i, rid in enumerate(robot_ids):
            df = pd.read_sql(text('''
                SELECT 
                    stock_code, stock_name,
                    buy_date, sell_date,
                    hold_days, net_profit_pct
                FROM sim_trades
                WHERE robot_id = :rid 
                AND status = 'SOLD'
                AND net_profit_pct IS NOT NULL
            '''), conn, params={'rid': rid})
            
            if df.empty:
                continue
            
            name = conn.execute(text('''
                SELECT robot_name FROM sim_robots WHERE robot_id = :rid
            '''), {'rid': rid}).scalar() or rid
            
            # 添加散点
            fig.add_trace(go.Scatter(
                x=df['hold_days'],
                y=df['net_profit_pct'],
                mode='markers',
                name=name,
                marker=dict(
                    color=COLORS[i % len(COLORS)],
                    size=8,
                    opacity=0.7,
                ),
                customdata=df[['stock_code', 'stock_name']],
                hovertemplate=(
                    '<b>%{customdata[1]}</b> (%{customdata[0]})<br>'
                    '持仓: %{x}天<br>'
                    '收益: %{y:.2f}%'
                    '<extra></extra>'
                ),
            ))
    
    # 添加零线
    fig.add_hline(y=0, line_dash='dash', line_color='gray', opacity=0.5)
    
    fig.update_layout(
        title="🎯 单笔交易散点图（持仓天数 vs 收益率）",
        xaxis_title="持仓天数",
        yaxis_title="收益率(%)",
        height=450,
        template='plotly_white',
        hovermode='closest',
    )
    
    return fig


def chart_score_radar(robot_ids: List[str]) -> go.Figure:
    """
    雷达图：多维度评分对比
    
    维度：收益率、回撤控制、风险调整收益、胜率、盈亏比、交易效率
    """
    from strategy.scorer import calc_robot_score
    
    fig = go.Figure()
    engine = storage._get_engine()
    
    categories = ['收益率', '回撤控制', '风险调整', '胜率', '盈亏比', '交易效率']
    
    with engine.connect() as conn:
        for i, rid in enumerate(robot_ids):
            score_data = calc_robot_score(rid)
            if not score_data:
                continue
            
            name = conn.execute(text('''
                SELECT robot_name FROM sim_robots WHERE robot_id = :rid
            '''), {'rid': rid}).scalar() or rid
            
            # 提取各维度分数
            s = score_data['scores']
            values = [
                s.get('return_rate', 0),
                s.get('max_drawdown', 0),
                s.get('sharpe_ratio', 0),
                s.get('win_rate', 0),
                s.get('profit_loss_ratio', 0),
                100 - s.get('cost_ratio', 0),  # 成本占比越小越好
            ]
            
            fig.add_trace(go.Scatterpolar(
                r=values,
                theta=categories,
                fill='toself',
                name=name,
                line=dict(color=COLORS[i % len(COLORS)]),
            ))
    
    fig.update_layout(
        title="🎯 多维度评分雷达图",
        polar=dict(
            radialaxis=dict(
                visible=True,
                range=[0, 100]
            )
        ),
        showlegend=True,
        height=450,
    )
    
    return fig


# === 单元测试 ===
if __name__ == '__main__':
    import os
    os.chdir(ROOT)
    
    print("=== 测试图表模块 ===")
    
    # 测试空数据
    fig = chart_nav_comparison(['test_robot'])
    print(f"✅ 净值曲线图创建成功")
    
    fig = chart_sharpe_comparison(['test_robot'])
    print(f"✅ 夏普比率图创建成功")
    
    print("\n✅ 图表模块测试完成")
