"""
回测框架

回测引擎：历史数据回放，调用 SignalEngine + TradingEngine
指标计算：年化收益、夏普比率、最大回撤、胜率计算
报告生成：生成回测报告（JSON + HTML）

参考：docs/lhb-quant-v2/TRADING_FLOW.md
"""
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional
from dataclasses import dataclass, field
import json

from loguru import logger


@dataclass
class BacktestMetrics:
    """回测指标"""
    # 基础指标
    start_date: date
    end_date: date
    trading_days: int
    initial_capital: float
    final_capital: float
    
    # 收益指标
    total_return: float  # 总收益率
    annual_return: float  # 年化收益率
    daily_return_avg: float  # 日均收益率
    
    # 风险指标
    max_drawdown: float  # 最大回撤
    sharpe_ratio: float  # 夏普比率
    sortino_ratio: float  # 索提诺比率
    volatility: float  # 年化波动率
    
    # 交易指标
    total_trades: int  # 总交易数
    win_trades: int  # 盈利交易数
    lose_trades: int  # 亏损交易数
    win_rate: float  # 胜率
    profit_loss_ratio: float  # 盈亏比
    
    # 持仓指标
    avg_hold_days: float  # 平均持仓天数
    max_hold_days: int  # 最大持仓天数
    min_hold_days: int  # 最小持仓天数
    
    def to_dict(self) -> dict:
        """转换为字典"""
        return {
            'start_date': str(self.start_date),
            'end_date': str(self.end_date),
            'trading_days': self.trading_days,
            'initial_capital': self.initial_capital,
            'final_capital': self.final_capital,
            'total_return': self.total_return,
            'annual_return': self.annual_return,
            'daily_return_avg': self.daily_return_avg,
            'max_drawdown': self.max_drawdown,
            'sharpe_ratio': self.sharpe_ratio,
            'sortino_ratio': self.sortino_ratio,
            'volatility': self.volatility,
            'total_trades': self.total_trades,
            'win_trades': self.win_trades,
            'lose_trades': self.lose_trades,
            'win_rate': self.win_rate,
            'profit_loss_ratio': self.profit_loss_ratio,
            'avg_hold_days': self.avg_hold_days,
            'max_hold_days': self.max_hold_days,
            'min_hold_days': self.min_hold_days,
        }


def calculate_metrics(
    nav_series: list[float],
    trade_results: list[dict],
    risk_free_rate: float = 0.03  # 无风险利率 3%
) -> BacktestMetrics:
    """
    计算回测指标
    
    Args:
        nav_series: 净值序列（按日期排序）
        trade_results: 交易结果列表
        risk_free_rate: 无风险利率
    
    Returns:
        BacktestMetrics: 计算后的指标
    """
    if len(nav_series) < 2:
        raise ValueError("净值序列太短，至少需要2个数据点")
    
    # 基础数据
    initial_capital = nav_series[0]
    final_capital = nav_series[-1]
    trading_days = len(nav_series)
    
    # 收益率
    total_return = (final_capital - initial_capital) / initial_capital
    annual_return = (final_capital / initial_capital) ** (252 / trading_days) - 1
    
    # 日收益率序列
    daily_returns = [
        (nav_series[i] - nav_series[i-1]) / nav_series[i-1]
        for i in range(1, len(nav_series))
    ]
    daily_return_avg = sum(daily_returns) / len(daily_returns) if daily_returns else 0
    
    # 波动率（年化）
    if len(daily_returns) > 1:
        variance = sum((r - daily_return_avg) ** 2 for r in daily_returns) / (len(daily_returns) - 1)
        volatility = (variance ** 0.5) * (252 ** 0.5)  # 年化
    else:
        volatility = 0
    
    # 最大回撤
    peak = nav_series[0]
    max_dd = 0
    for nav in nav_series:
        if nav > peak:
            peak = nav
        dd = (peak - nav) / peak
        max_dd = max(max_dd, dd)
    
    # 夏普比率
    if volatility > 0:
        sharpe_ratio = (annual_return - risk_free_rate) / volatility
    else:
        sharpe_ratio = 0
    
    # 索提诺比率（只考虑下行风险）
    negative_returns = [r for r in daily_returns if r < 0]
    if negative_returns:
        downside_variance = sum(r ** 2 for r in negative_returns) / len(negative_returns)
        downside_risk = (downside_variance ** 0.5) * (252 ** 0.5)
        sortino_ratio = (annual_return - risk_free_rate) / downside_risk if downside_risk > 0 else 0
    else:
        sortino_ratio = float('inf') if annual_return > risk_free_rate else 0
    
    # 交易统计
    total_trades = len(trade_results)
    win_trades = sum(1 for t in trade_results if t.get('pnl', 0) > 0)
    lose_trades = sum(1 for t in trade_results if t.get('pnl', 0) <= 0)
    win_rate = win_trades / total_trades if total_trades > 0 else 0
    
    # 盈亏比
    total_profit = sum(t.get('pnl', 0) for t in trade_results if t.get('pnl', 0) > 0)
    total_loss = abs(sum(t.get('pnl', 0) for t in trade_results if t.get('pnl', 0) < 0))
    profit_loss_ratio = total_profit / total_loss if total_loss > 0 else float('inf')
    
    # 持仓天数统计
    hold_days_list = [t.get('hold_days', 1) for t in trade_results]
    avg_hold_days = sum(hold_days_list) / len(hold_days_list) if hold_days_list else 0
    max_hold_days = max(hold_days_list) if hold_days_list else 0
    min_hold_days = min(hold_days_list) if hold_days_list else 0
    
    return BacktestMetrics(
        start_date=date.today() - timedelta(days=trading_days),
        end_date=date.today(),
        trading_days=trading_days,
        initial_capital=initial_capital,
        final_capital=final_capital,
        total_return=total_return,
        annual_return=annual_return,
        daily_return_avg=daily_return_avg,
        max_drawdown=max_dd,
        sharpe_ratio=sharpe_ratio,
        sortino_ratio=sortino_ratio,
        volatility=volatility,
        total_trades=total_trades,
        win_trades=win_trades,
        lose_trades=lose_trades,
        win_rate=win_rate,
        profit_loss_ratio=profit_loss_ratio,
        avg_hold_days=avg_hold_days,
        max_hold_days=max_hold_days,
        min_hold_days=min_hold_days,
    )


def generate_report(metrics: BacktestMetrics, output_path: str) -> None:
    """
    生成回测报告
    
    Args:
        metrics: 回测指标
        output_path: 输出路径（JSON格式）
    """
    report = {
        'metrics': metrics.to_dict(),
        'generated_at': str(date.today()),
    }
    
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    
    logger.info(f"[Backtest] 报告已生成: {output_path}")


def generate_html_report(metrics: BacktestMetrics, output_path: str) -> None:
    """
    生成HTML格式的回测报告
    
    Args:
        metrics: 回测指标
        output_path: 输出路径
    """
    html_template = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>回测报告</title>
    <style>
        body { font-family: Arial, sans-serif; max-width: 800px; margin: 0 auto; padding: 20px; }
        h1 { color: #333; border-bottom: 2px solid #007bff; padding-bottom: 10px; }
        h2 { color: #555; margin-top: 30px; }
        table { width: 100%; border-collapse: collapse; margin: 20px 0; }
        th, td { padding: 12px; text-align: left; border-bottom: 1px solid #ddd; }
        th { background-color: #007bff; color: white; }
        tr:hover { background-color: #f5f5f5; }
        .positive { color: green; }
        .negative { color: red; }
        .summary { background: #f8f9fa; padding: 15px; border-radius: 5px; margin: 20px 0; }
    </style>
</head>
<body>
    <h1>📊 回测报告</h1>
    
    <div class="summary">
        <h2>时间范围</h2>
        <p><strong>开始日期:</strong> {start_date}</p>
        <p><strong>结束日期:</strong> {end_date}</p>
        <p><strong>交易天数:</strong> {trading_days}</p>
    </div>
    
    <h2>💰 收益指标</h2>
    <table>
        <tr><th>指标</th><th>数值</th></tr>
        <tr><td>初始资金</td><td>¥{initial_capital:,.2f}</td></tr>
        <tr><td>最终资金</td><td>¥{final_capital:,.2f}</td></tr>
        <tr><td>总收益率</td><td class="{return_class}">{total_return:.2%}</td></tr>
        <tr><td>年化收益率</td><td class="{return_class}">{annual_return:.2%}</td></tr>
    </table>
    
    <h2>⚠️ 风险指标</h2>
    <table>
        <tr><th>指标</th><th>数值</th></tr>
        <tr><td>最大回撤</td><td class="negative">{max_drawdown:.2%}</td></tr>
        <tr><td>夏普比率</td><td>{sharpe_ratio:.2f}</td></tr>
        <tr><td>年化波动率</td><td>{volatility:.2%}</td></tr>
    </table>
    
    <h2>📈 交易统计</h2>
    <table>
        <tr><th>指标</th><th>数值</th></tr>
        <tr><td>总交易数</td><td>{total_trades}</td></tr>
        <tr><td>盈利交易</td><td class="positive">{win_trades}</td></tr>
        <tr><td>亏损交易</td><td class="negative">{lose_trades}</td></tr>
        <tr><td>胜率</td><td>{win_rate:.2%}</td></tr>
        <tr><td>盈亏比</td><td>{profit_loss_ratio:.2f}</td></tr>
        <tr><td>平均持仓天数</td><td>{avg_hold_days:.1f}</td></tr>
    </table>
    
    <footer style="margin-top: 40px; color: #666; text-align: center;">
        <p>报告生成时间: {generated_at}</p>
    </footer>
</body>
</html>
"""
    
    return_class = "positive" if metrics.total_return >= 0 else "negative"
    
    html_content = html_template.format(
        start_date=metrics.start_date,
        end_date=metrics.end_date,
        trading_days=metrics.trading_days,
        initial_capital=metrics.initial_capital,
        final_capital=metrics.final_capital,
        total_return=metrics.total_return,
        annual_return=metrics.annual_return,
        max_drawdown=metrics.max_drawdown,
        sharpe_ratio=metrics.sharpe_ratio,
        volatility=metrics.volatility,
        total_trades=metrics.total_trades,
        win_trades=metrics.win_trades,
        lose_trades=metrics.lose_trades,
        win_rate=metrics.win_rate,
        profit_loss_ratio=metrics.profit_loss_ratio,
        avg_hold_days=metrics.avg_hold_days,
        return_class=return_class,
        generated_at=date.today(),
    )
    
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html_content)
    
    logger.info(f"[Backtest] HTML报告已生成: {output_path}")
