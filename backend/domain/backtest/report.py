"""回测指标报告.

输入: BacktestResult (含 nav_series 原数据 + trades).
输出: BacktestMetrics dataclass, 可直接 asdict() 落 backtest_results 表.

指标:
  - total_return / annual_return
  - max_drawdown / max_drawdown_days
  - volatility (年化标准差) / sharpe_ratio / sortino_ratio / calmar_ratio
  - trade_count / win_count / lose_count / win_rate
  - avg_win / avg_loss / profit_loss_ratio
  - max_consecutive_win / lose
  - avg_hold_days
  - equity_curve / drawdown_curve / monthly_returns

简化假设:
  - 风险无关利率 = 0
  - 交易日数按实际 nav 点数计算, 年化系数 = 252 / n_days
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any, Optional


@dataclass
class BacktestMetrics:
    total_return: float = 0.0
    annual_return: float = 0.0
    max_drawdown: float = 0.0
    max_drawdown_days: int = 0
    volatility: float = 0.0
    downside_volatility: float = 0.0
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    calmar_ratio: float = 0.0
    trade_count: int = 0
    win_count: int = 0
    lose_count: int = 0
    win_rate: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    profit_loss_ratio: float = 0.0
    max_consecutive_win: int = 0
    max_consecutive_lose: int = 0
    avg_hold_days: float = 0.0
    avg_position_pct: float = 0.0
    max_position_pct: float = 0.0
    equity_curve: list = field(default_factory=list)
    drawdown_curve: list = field(default_factory=list)
    monthly_returns: dict = field(default_factory=dict)

    def to_db_row(self) -> dict:
        return asdict(self)


def compute_metrics(result) -> BacktestMetrics:
    """接收 BacktestResult, 返回 BacktestMetrics."""
    m = BacktestMetrics()
    nav_raw = result._nav_raw
    if not nav_raw:
        return m

    init = result.initial_capital
    final = result.final_assets
    n_days = len(nav_raw)

    m.total_return = (final - init) / init if init else 0.0
    # 年化 = (1+R)^(252/n) - 1
    try:
        m.annual_return = (
            (1 + m.total_return) ** (252.0 / n_days) - 1
            if n_days > 0 else 0.0
        )
    except (OverflowError, ValueError):
        m.annual_return = 0.0

    # 日收益序列 (基于 nav 相对变化)
    nav_values = [p.nav for p in nav_raw]
    daily_returns = []
    for i in range(1, len(nav_values)):
        prev = nav_values[i - 1] or 1.0
        daily_returns.append(nav_values[i] / prev - 1)

    # 波动率 (年化)
    if daily_returns:
        mean_ret = sum(daily_returns) / len(daily_returns)
        var = sum((r - mean_ret) ** 2 for r in daily_returns) / len(daily_returns)
        std = math.sqrt(var)
        m.volatility = std * math.sqrt(252)
        # 下行波动率 (只对负收益)
        neg = [r for r in daily_returns if r < 0]
        if neg:
            neg_mean = sum(neg) / len(neg)
            neg_var = sum((r - neg_mean) ** 2 for r in neg) / len(neg)
            m.downside_volatility = math.sqrt(neg_var) * math.sqrt(252)

        # 夏普 = 年化收益 / 年化波动 (风险无关利率=0)
        m.sharpe_ratio = m.annual_return / m.volatility if m.volatility else 0.0
        m.sortino_ratio = (
            m.annual_return / m.downside_volatility
            if m.downside_volatility else 0.0
        )

    # 最大回撤
    peak = nav_values[0]
    peak_date = nav_raw[0].trade_date
    max_dd = 0.0
    max_dd_days = 0
    current_peak_date = peak_date
    drawdown_curve = []
    for p in nav_raw:
        if p.nav > peak:
            peak = p.nav
            current_peak_date = p.trade_date
        dd = (p.nav - peak) / peak if peak else 0.0
        drawdown_curve.append({'date': p.trade_date.isoformat(),
                                'drawdown': round(dd, 6)})
        if dd < max_dd:
            max_dd = dd
            max_dd_days = (p.trade_date - current_peak_date).days
    m.max_drawdown = max_dd   # 负数, 如 -0.12
    m.max_drawdown_days = max_dd_days
    m.drawdown_curve = drawdown_curve
    m.calmar_ratio = (
        m.annual_return / abs(max_dd) if max_dd < 0 else 0.0
    )

    # 交易统计
    trades = [t for t in result._trades_raw if t.direction == 'SELL'
              and t.pnl_amount is not None]
    m.trade_count = len(result._trades_raw)
    m.win_count = sum(1 for t in trades if t.pnl_amount > 0)
    m.lose_count = sum(1 for t in trades if t.pnl_amount < 0)
    closed = m.win_count + m.lose_count
    m.win_rate = m.win_count / closed if closed else 0.0

    wins = [t.pnl_pct for t in trades if t.pnl_pct is not None and t.pnl_pct > 0]
    loses = [t.pnl_pct for t in trades if t.pnl_pct is not None and t.pnl_pct < 0]
    m.avg_win = sum(wins) / len(wins) if wins else 0.0
    m.avg_loss = sum(loses) / len(loses) if loses else 0.0
    m.profit_loss_ratio = (
        abs(m.avg_win / m.avg_loss) if m.avg_loss else 0.0
    )

    # 连胜 / 连亏
    max_w = max_l = cur_w = cur_l = 0
    for t in trades:
        if t.pnl_amount > 0:
            cur_w += 1; cur_l = 0
            max_w = max(max_w, cur_w)
        elif t.pnl_amount < 0:
            cur_l += 1; cur_w = 0
            max_l = max(max_l, cur_l)
    m.max_consecutive_win = max_w
    m.max_consecutive_lose = max_l

    hold_days = [t.hold_days for t in trades if t.hold_days is not None]
    m.avg_hold_days = sum(hold_days) / len(hold_days) if hold_days else 0.0

    # 仓位
    pos_pct = [
        (p.position_value / p.total_assets) if p.total_assets else 0.0
        for p in nav_raw
    ]
    m.avg_position_pct = sum(pos_pct) / len(pos_pct) if pos_pct else 0.0
    m.max_position_pct = max(pos_pct) if pos_pct else 0.0

    # 曲线
    m.equity_curve = [p.to_dict() for p in nav_raw]

    # 月度收益 (按月末 nav 差)
    by_month: dict[str, float] = {}
    for p in nav_raw:
        key = p.trade_date.strftime('%Y-%m')
        by_month[key] = p.nav   # 最新一点覆盖 -> 月末
    monthly_returns: dict[str, float] = {}
    prev_nav = 1.0
    for key in sorted(by_month.keys()):
        nav_m = by_month[key]
        ret = (nav_m - prev_nav) / prev_nav if prev_nav else 0.0
        monthly_returns[key] = round(ret, 6)
        prev_nav = nav_m
    m.monthly_returns = monthly_returns

    return m
