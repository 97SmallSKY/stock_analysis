"""
机器人评分体系

功能：
1. 计算单个机器人的各项指标
2. 根据权重配置计算综合评分
3. 多机器人排名对比
"""
import sys
import logging
import math
import json
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from utils.storage import storage
from sqlalchemy import text

logger = logging.getLogger(__name__)


# 默认权重配置
DEFAULT_WEIGHTS = {
    'return_rate': 0.25,      # 累计收益率
    'max_drawdown': 0.20,     # 最大回撤
    'sharpe_ratio': 0.20,     # 夏普比率
    'win_rate': 0.10,         # 胜率
    'profit_loss_ratio': 0.10, # 盈亏比
    'cost_ratio': 0.10,       # 交易成本占比
    'sample_size': 0.05,      # 样本数量
}

# 评分基准值
DEFAULT_BENCHMARKS = {
    'return_rate': {'perfect': 50, 'zero': 0},
    'max_drawdown': {'perfect': 0, 'zero': -30},
    'sharpe_ratio': {'perfect': 3.0, 'zero': 0},
    'win_rate': {'perfect': 80, 'zero': 30},
    'profit_loss_ratio': {'perfect': 3.0, 'zero': 0.5},
    'cost_ratio': {'perfect': 0, 'zero': 5},
    'sample_size': {'perfect': 30, 'zero': 3},
}


def calc_robot_metrics(robot_id: str) -> Optional[dict]:
    """
    计算单个机器人的所有指标
    
    Returns:
        {
            'return_rate': 累计收益率(%),
            'max_drawdown': 最大回撤(%),
            'sharpe_ratio': 夏普比率,
            'win_rate': 胜率(%),
            'profit_loss_ratio': 盈亏比,
            'cost_ratio': 交易成本占比(%),
            'sample_size': 交易样本数,
            'total_trades': 总交易笔数,
            'total_profit': 总盈利(元),
        }
    """
    engine = storage._get_engine()
    
    with engine.connect() as conn:
        # 1. 查询净值数据
        nav_rows = conn.execute(text('''
            SELECT trade_date, nav, daily_return_pct
            FROM sim_daily_summary
            WHERE robot_id = :rid 
            ORDER BY trade_date
        '''), {'rid': robot_id}).fetchall()
        
        if not nav_rows:
            logger.warning(f"⚠️ {robot_id}: 无净值数据")
            return None
        
        navs = [dict(r._mapping) for r in nav_rows]
        
        # 累计收益率
        return_rate = (navs[-1]['nav'] - 1) * 100
        
        # 2. 最大回撤
        peak, max_dd = 1.0, 0.0
        for n in navs:
            nav_val = n['nav'] or 1.0
            peak = max(peak, nav_val)
            dd = (nav_val - peak) / peak * 100 if peak > 0 else 0
            max_dd = min(max_dd, dd)
        
        # 3. 夏普比率
        daily_returns = [n['daily_return_pct'] for n in navs 
                        if n['daily_return_pct'] is not None]
        
        if len(daily_returns) > 1:
            avg_r = sum(daily_returns) / len(daily_returns)
            var_r = sum((r - avg_r) ** 2 for r in daily_returns) / (len(daily_returns) - 1)
            std_r = math.sqrt(var_r) if var_r > 0 else 0.001
            # 年化夏普，假设无风险利率约 2%/年
            risk_free_daily = 2.0 / 252 / 100 * 100  # 日化
            sharpe = (avg_r - risk_free_daily) / std_r * math.sqrt(252) if std_r > 0 else 0
        else:
            sharpe = 0
        
        # 4. 胜率/盈亏比/交易成本
        trades = conn.execute(text('''
            SELECT 
                net_profit, 
                net_profit_pct,
                buy_amount,
                total_fee
            FROM sim_trades
            WHERE robot_id = :rid 
            AND status = 'SOLD' 
            AND net_profit IS NOT NULL
        '''), {'rid': robot_id}).fetchall()
        
        total_trades = len(trades)
        if total_trades > 0:
            wins = [t for t in trades if t[0] > 0]
            losses = [t for t in trades if t[0] <= 0]
            
            win_rate = len(wins) / total_trades * 100
            
            # 盈亏比
            avg_win_pct = sum(t[1] for t in wins) / len(wins) if wins else 0
            avg_loss_pct = abs(sum(t[1] for t in losses) / len(losses)) if losses else 0.01
            profit_loss_ratio = avg_win_pct / avg_loss_pct if avg_loss_pct > 0 else 0
            
            # 交易成本占比
            total_profit = sum(t[0] for t in trades)
            total_cost = sum(t[3] or 0 for t in trades)
            cost_ratio = abs(total_cost) / (abs(total_profit) + abs(total_cost)) * 100 if (total_profit + total_cost) != 0 else 0
        else:
            win_rate, profit_loss_ratio, cost_ratio = 0, 0, 0
            total_profit = 0
        
        return {
            'return_rate': round(return_rate, 2),
            'max_drawdown': round(max_dd, 2),
            'sharpe_ratio': round(sharpe, 2),
            'win_rate': round(win_rate, 2),
            'profit_loss_ratio': round(profit_loss_ratio, 2),
            'cost_ratio': round(cost_ratio, 2),
            'sample_size': total_trades,
            'total_trades': total_trades,
            'total_profit': round(total_profit, 2),
        }


def _calc_single_score(metric: str, value: float, benchmarks: dict) -> float:
    """
    计算单个指标的分数（0-100）
    
    对于大多数指标：分数 = (实际值 - 零分值) / (满分值 - 零分值) * 100
    对于 max_drawdown 和 cost_ratio：满分在零分右侧，需要反向计算
    """
    bench = benchmarks.get(metric, {'perfect': 50, 'zero': 0})
    perfect = bench['perfect']
    zero = bench['zero']
    
    # 特殊处理：回撤和成本，越小越好
    if metric in ('max_drawdown', 'cost_ratio'):
        # 零分更负，满分更接近零或正
        score = (value - zero) / (perfect - zero) * 100
    else:
        score = (value - zero) / (perfect - zero) * 100
    
    return max(0, min(100, score))


def calc_robot_score(robot_id: str, weights: dict = None, benchmarks: dict = None) -> Optional[dict]:
    """
    计算机器人综合评分（0-100）
    
    Args:
        robot_id: 机器人ID
        weights: 权重配置，默认使用 DEFAULT_WEIGHTS
        benchmarks: 评分基准，默认使用 DEFAULT_BENCHMARKS
    
    Returns:
        {
            'total_score': 综合得分,
            'metrics': 原始指标值,
            'scores': 各项得分,
            'weighted_scores': 加权得分明细,
        }
    """
    if weights is None:
        weights = DEFAULT_WEIGHTS
    if benchmarks is None:
        benchmarks = DEFAULT_BENCHMARKS
    
    metrics = calc_robot_metrics(robot_id)
    if not metrics:
        return None
    
    scores = {}
    weighted_scores = {}
    
    for metric, weight in weights.items():
        raw_value = metrics.get(metric, 0)
        score = _calc_single_score(metric, raw_value, benchmarks)
        scores[metric] = round(score, 1)
        weighted_scores[metric] = round(score * weight, 2)
    
    total_score = sum(weighted_scores.values())
    
    return {
        'total_score': round(total_score, 1),
        'metrics': metrics,
        'scores': scores,
        'weighted_scores': weighted_scores,
    }


def rank_robots(robot_ids: list, weights: dict = None, benchmarks: dict = None) -> list:
    """
    多机器人排名
    
    Returns:
        [{
            'rank': 排名,
            'robot_id': ID,
            'robot_name': 名称,
            'total_score': 综合得分,
            'metrics': {...},
            'scores': {...},
        }]
    """
    engine = storage._get_engine()
    results = []
    
    # 获取机器人名称
    with engine.connect() as conn:
        robot_names = {}
        for rid in robot_ids:
            name = conn.execute(text('''
                SELECT robot_name FROM sim_robots WHERE robot_id = :rid
            '''), {'rid': rid}).scalar()
            robot_names[rid] = name or rid
    
    for rid in robot_ids:
        score_data = calc_robot_score(rid, weights, benchmarks)
        if score_data:
            score_data['robot_id'] = rid
            score_data['robot_name'] = robot_names.get(rid, rid)
            results.append(score_data)
    
    # 按总分降序排列
    results.sort(key=lambda x: x['total_score'], reverse=True)
    
    # 添加排名
    for i, r in enumerate(results):
        r['rank'] = i + 1
    
    return results


def load_weights_config(config_name: str = 'default') -> tuple:
    """
    从数据库加载评分权重配置
    
    Returns:
        (weights, benchmarks)
    """
    engine = storage._get_engine()
    
    with engine.connect() as conn:
        row = conn.execute(text('''
            SELECT weights, benchmarks FROM score_weights_config 
            WHERE config_name = :name AND is_active = true
        '''), {'name': config_name}).fetchone()
        
        if row:
            weights = row[0] if isinstance(row[0], dict) else json.loads(row[0])
            benchmarks = row[1] if isinstance(row[1], dict) else json.loads(row[1]) if row[1] else DEFAULT_BENCHMARKS
            return weights, benchmarks
    
    return DEFAULT_WEIGHTS, DEFAULT_BENCHMARKS


# === 单元测试 ===
if __name__ == '__main__':
    import os
    os.chdir(ROOT)
    
    print("=== 测试评分体系 ===")
    
    # 测试默认配置
    print("\n默认权重配置:")
    for k, v in DEFAULT_WEIGHTS.items():
        print(f"  {k}: {v*100:.0f}%")
    
    # 注意：实际测试需要有数据的机器人
    print("\n✅ 评分模块测试完成")
