# P3 阶段：对比看板 + 评分体系

> **前置条件**：P2 完成（每日净值数据）
> **预计时间**：2-3天
> **文档版本**：v1.0

---

## 目标

1. 建立机器人评分体系
2. 实现6个对比图表
3. 建立评分权重配置表

---

## 评分体系

### 默认权重

| 指标 | 默认权重 | 说明 |
|------|---------|------|
| 累计收益率 | 25% | 核心盈利能力 |
| 最大回撤 | 20% | 风险控制 |
| 夏普比率 | 20% | 风险调整后收益 |
| 胜率 | 10% | 交易准确度 |
| 盈亏比 | 10% | 盈利质量 |
| 交易成本占比 | 10% | 策略效率 |
| 样本数量 | 5% | 统计可信度 |

### 评分标准

| 指标 | 满分参考值 | 零分参考值 |
|------|-----------|-----------|
| 累计收益率 | 50% | 0% |
| 最大回撤 | 0% | -30% |
| 夏普比率 | 3.0 | 0 |
| 胜率 | 80% | 30% |
| 盈亏比 | 3.0 | 0.5 |
| 交易成本占比 | 0% | 5% |
| 样本数量 | 30笔 | 3笔 |

---

## 数据库设计

```sql
CREATE TABLE IF NOT EXISTS score_weights_config (
    id              SERIAL PRIMARY KEY,
    config_name     VARCHAR(50) UNIQUE DEFAULT 'default',
    weights         JSONB NOT NULL,
    description     TEXT,
    is_active       BOOLEAN DEFAULT true,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

---

## 核心模块

### 1. 评分模块

**文件路径**：`skills/stock_sim_robot/scorer.py`

```python
"""
机器人评分体系
"""
import logging
import math
from utils.storage import storage
from sqlalchemy import text

logger = logging.getLogger(__name__)

DEFAULT_WEIGHTS = {
    'return_rate': 0.25,
    'max_drawdown': 0.20,
    'sharpe_ratio': 0.20,
    'win_rate': 0.10,
    'profit_loss_ratio': 0.10,
    'cost_ratio': 0.10,
    'sample_size': 0.05,
}

SCORING_BENCHMARKS = {
    'return_rate': {'perfect': 50, 'zero': 0},
    'max_drawdown': {'perfect': 0, 'zero': -30},
    'sharpe_ratio': {'perfect': 3.0, 'zero': 0},
    'win_rate': {'perfect': 80, 'zero': 30},
    'profit_loss_ratio': {'perfect': 3.0, 'zero': 0.5},
    'cost_ratio': {'perfect': 0, 'zero': 5},
    'sample_size': {'perfect': 30, 'zero': 3},
}


def calc_robot_metrics(robot_id: str) -> dict:
    """计算单个机器人的所有指标"""
    engine = storage._get_engine()
    
    with engine.connect() as conn:
        # 查询净值数据
        nav_rows = conn.execute(text('''
            SELECT trade_date, nav, daily_return_pct
            FROM sim_daily_summary
            WHERE robot_id = :rid ORDER BY trade_date
        '''), {'rid': robot_id}).fetchall()
        
        if not nav_rows:
            return None
        
        navs = [dict(r._mapping) for r in nav_rows]
        return_rate = (navs[-1]['nav'] - 1) * 100
        
        # 最大回撤
        peak, max_dd = 1.0, 0.0
        for n in navs:
            peak = max(peak, n['nav'])
            dd = (n['nav'] - peak) / peak * 100
            max_dd = min(max_dd, dd)
        
        # 夏普比率
        daily_returns = [n['daily_return_pct'] for n in navs if n['daily_return_pct'] is not None]
        if len(daily_returns) > 1:
            avg = sum(daily_returns) / len(daily_returns)
            std = math.sqrt(sum((r - avg) ** 2 for r in daily_returns) / (len(daily_returns) - 1))
            sharpe = (avg - 2.0/252) / std * math.sqrt(252) if std > 0 else 0
        else:
            sharpe = 0
        
        # 胜率/盈亏比
        trades = conn.execute(text('''
            SELECT net_profit, net_profit_pct FROM sim_trades
            WHERE robot_id = :rid AND status = 'SOLD' AND net_profit IS NOT NULL
        '''), {'rid': robot_id}).fetchall()
        
        total = len(trades)
        if total > 0:
            wins = [t for t in trades if t[0] > 0]
            win_rate = len(wins) / total * 100
            avg_win = sum(t[1] for t in wins) / len(wins) if wins else 0
            avg_loss = abs(sum(t[1] for t in trades if t[0] <= 0) / (total - len(wins))) if total > len(wins) else 1
            profit_loss_ratio = avg_win / avg_loss if avg_loss > 0 else 0
        else:
            win_rate, profit_loss_ratio = 0, 0
        
        return {
            'return_rate': round(return_rate, 2),
            'max_drawdown': round(max_dd, 2),
            'sharpe_ratio': round(sharpe, 2),
            'win_rate': round(win_rate, 2),
            'profit_loss_ratio': round(profit_loss_ratio, 2),
            'sample_size': total,
        }


def calc_robot_score(robot_id: str, weights: dict = None) -> dict:
    """计算机器人综合评分（0-100）"""
    if weights is None:
        weights = DEFAULT_WEIGHTS
    
    metrics = calc_robot_metrics(robot_id)
    if not metrics:
        return None
    
    scores = {}
    for metric, weight in weights.items():
        raw = metrics.get(metric, 0)
        bench = SCORING_BENCHMARKS.get(metric, {'perfect': 50, 'zero': 0})
        score = (raw - bench['zero']) / (bench['perfect'] - bench['zero']) * 100
        score = max(0, min(100, score))
        scores[metric] = round(score, 1)
    
    total = sum(scores[m] * w for m, w in weights.items())
    
    return {
        'total_score': round(total, 1),
        'metrics': metrics,
        'scores': scores,
    }


def rank_robots(robot_ids: list, weights: dict = None) -> list:
    """多机器人排名"""
    results = []
    for rid in robot_ids:
        score = calc_robot_score(rid, weights)
        if score:
            score['robot_id'] = rid
            results.append(score)
    
    results.sort(key=lambda x: x['total_score'], reverse=True)
    for i, r in enumerate(results):
        r['rank'] = i + 1
    
    return results
```

### 2. 图表模块

**文件路径**：`skills/stock_sim_robot/charts.py`

```python
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
import pandas as pd
import plotly.graph_objects as go
from utils.storage import storage
from sqlalchemy import text

COLORS = ['#FF6B6B', '#4ECDC4', '#45B7D1', '#96CEB4', '#FFEAA7']


def chart_nav_comparison(robot_ids: list, benchmark_codes: list = None) -> go.Figure:
    """净值曲线对比图"""
    fig = go.Figure()
    engine = storage._get_engine()
    
    with engine.connect() as conn:
        for i, rid in enumerate(robot_ids):
            df = pd.read_sql(text('''
                SELECT trade_date, nav FROM sim_daily_summary
                WHERE robot_id = :rid ORDER BY trade_date
            '''), conn, params={'rid': rid})
            
            if df.empty:
                continue
            
            name = conn.execute(text(
                'SELECT robot_name FROM sim_robots WHERE robot_id = :rid'
            ), {'rid': rid}).scalar() or rid
            
            fig.add_trace(go.Scatter(
                x=df['trade_date'], y=df['nav'],
                mode='lines', name=name,
                line=dict(color=COLORS[i % len(COLORS)], width=2.5),
            ))
    
    fig.update_layout(
        title="📈 策略净值曲线对比",
        xaxis_title="日期", yaxis_title="净值",
        height=500, template='plotly_white',
    )
    
    return fig


def chart_drawdown_comparison(robot_ids: list) -> go.Figure:
    """回撤曲线对比"""
    pass  # 实现见完整方案


def chart_sharpe_comparison(robot_ids: list) -> go.Figure:
    """夏普比率柱状图"""
    from .scorer import calc_robot_metrics
    
    fig = go.Figure()
    names, sharpes = [], []
    
    for rid in robot_ids:
        metrics = calc_robot_metrics(rid)
        if metrics:
            names.append(rid)
            sharpes.append(metrics['sharpe_ratio'])
    
    fig.add_trace(go.Bar(x=names, y=sharpes, marker_color=COLORS[:len(names)]))
    fig.update_layout(title="📊 夏普比率对比", height=400)
    
    return fig


def chart_trade_scatter(robot_ids: list) -> go.Figure:
    """单笔交易散点图（持仓天数 vs 收益率）"""
    pass  # 实现见完整方案
```

---

## 文件清单

```
P3 完整文件清单
├── 新增
│   ├── scripts/migrate_p3.py
│   ├── skills/stock_sim_robot/scorer.py
│   └── skills/stock_sim_robot/charts.py
│
└── 前端页面（可选）
    └── pages/strategy_comparison.py
```

---

## 下一步

P3 完成后，继续执行 [P4: 实时监控 + 盘中止损](./phase-P4-realtime-monitor.md)
