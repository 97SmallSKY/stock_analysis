"""
Metrics — 回测统计分析
KPI / 回撤 / 滚动胜率 / 基准对比 / Exit 分布
"""
import pandas as pd
import numpy as np
from loguru import logger


def analyze(df: pd.DataFrame) -> dict:
    """
    核心统计分析。

    Args:
        df: 回测结果 DataFrame，必须含 return_rate 列

    Returns:
        dict 含 total_trades / win_rate / avg_return / drawdown_curve / etc.
    """
    if df.empty or "return_rate" not in df.columns:
        return {
            "total_trades": 0, "win_count": 0, "loss_count": 0,
            "win_rate": 0, "avg_return": 0, "median_return": 0,
            "max_return": 0, "min_return": 0, "avg_win": 0, "avg_loss": 0,
            "pl_ratio": 0, "sharpe": 0, "max_consecutive_loss": 0,
            "max_drawdown": 0, "calmar": 0, "avg_hold_days": 0,
            "cumulative_curve": [], "drawdown_curve": [],
            "rolling_win_rate": [], "exit_distribution": [],
            "monthly_stats": [],
        }

    rates = df["return_rate"].dropna().astype(float)
    if len(rates) == 0:
        return analyze(pd.DataFrame())

    total = len(rates)
    win_mask = rates > 0
    win_count = int(win_mask.sum())
    loss_count = total - win_count
    win_rate = win_count / total if total > 0 else 0
    avg_return = float(rates.mean())
    median_return = float(rates.median())
    max_return = float(rates.max())
    min_return = float(rates.min())

    wins = rates[win_mask]
    losses = rates[~win_mask]
    avg_win = float(wins.mean()) if len(wins) > 0 else 0
    avg_loss = float(losses.mean()) if len(losses) > 0 else 0
    pl_ratio = abs(avg_win / avg_loss) if avg_loss != 0 else 0
    sharpe = float(avg_return / rates.std()) if rates.std() > 0 else 0

    # 最大连续亏损
    max_consec = 0
    cur_consec = 0
    for r in rates:
        if r <= 0:
            cur_consec += 1
            max_consec = max(max_consec, cur_consec)
        else:
            cur_consec = 0

    # 平均持有天数
    avg_hold = float(df["hold_days"].mean()) if "hold_days" in df.columns else 0

    # 累计收益曲线
    cum_curve = _cumulative_curve(df)

    # 最大回撤
    max_dd, dd_curve = _drawdown_curve(cum_curve)
    calmar = abs(avg_return / max_dd) if max_dd > 0 else 0

    # 滚动胜率
    roll_wr = _rolling_win_rate(rates, window=20)

    # Exit 分布
    exit_dist = _exit_distribution(df)

    # 月度统计
    monthly = _monthly_stats(df)

    result = {
        "total_trades": total, "win_count": win_count, "loss_count": loss_count,
        "win_rate": win_rate, "avg_return": avg_return, "median_return": median_return,
        "max_return": max_return, "min_return": min_return,
        "avg_win": avg_win, "avg_loss": avg_loss, "pl_ratio": pl_ratio,
        "sharpe": sharpe, "max_consecutive_loss": max_consec,
        "max_drawdown": max_dd, "calmar": calmar, "avg_hold_days": avg_hold,
        "cumulative_curve": cum_curve, "drawdown_curve": dd_curve,
        "rolling_win_rate": roll_wr, "exit_distribution": exit_dist,
        "monthly_stats": monthly,
    }

    # 机构相关（如果有 has_institution 列）
    if "has_institution" in df.columns:
        inst = df[df["has_institution"] == True]
        result["inst_win_rate"] = float((inst["return_rate"] > 0).mean()) if len(inst) > 0 else None
        result["inst_avg_return"] = float(inst["return_rate"].mean()) if len(inst) > 0 else None

    return result


def _cumulative_curve(df: pd.DataFrame) -> list[dict]:
    """计算逐笔信号累乘收益曲线"""
    if "trade_date" not in df.columns:
        return []
    df_sorted = df.sort_values("trade_date").copy()
    rates = df_sorted["return_rate"].astype(float).values
    cum = np.cumprod(1 + rates) - 1
    return [
        {"trade_date": str(d), "cumulative_return": float(c)}
        for d, c in zip(df_sorted["trade_date"].values, cum)
    ]


def _drawdown_curve(cum_curve: list[dict]) -> tuple[float, list[dict]]:
    """从累计曲线计算最大回撤和水下曲线"""
    if not cum_curve:
        return 0.0, []
    values = np.array([p["cumulative_return"] for p in cum_curve])
    peak = np.maximum.accumulate(values)
    dd = np.where(peak > -1, (values - peak) / (1 + peak), 0)
    max_dd = float(abs(dd.min()))

    curve = []
    for i, d in enumerate(cum_curve):
        curve.append({
            "trade_date": d["trade_date"],
            "drawdown": float(dd[i]),
            "peak": float(peak[i]),
        })
    return max_dd, curve


def _rolling_win_rate(rates: pd.Series, window: int = 20) -> list[dict]:
    """滚动窗口胜率"""
    if len(rates) < window:
        return []
    result = []
    for i in range(window - 1, len(rates)):
        wr = (rates.iloc[i - window + 1 : i + 1] > 0).mean()
        result.append({"trade_index": i + 1, "win_rate": float(wr)})
    return result


def _exit_distribution(df: pd.DataFrame) -> list[dict]:
    """Exit 原因分布"""
    if "exit_reason" not in df.columns:
        return []
    dist = []
    for reason, grp in df.groupby("exit_reason"):
        rates = grp["return_rate"].astype(float)
        dist.append({
            "exit_reason": reason,
            "count": len(grp),
            "avg_return": float(rates.mean()),
            "win_rate": float((rates > 0).mean()),
        })
    return sorted(dist, key=lambda x: x["count"], reverse=True)


def _monthly_stats(df: pd.DataFrame) -> list[dict]:
    """月度统计"""
    if "trade_date" not in df.columns:
        return []
    df_m = df.copy()
    df_m["trade_date"] = pd.to_datetime(df_m["trade_date"].astype(str))
    df_m["month"] = df_m["trade_date"].dt.to_period("M")
    result = []
    for month, grp in df_m.groupby("month"):
        rates = grp["return_rate"].astype(float)
        result.append({
            "month": str(month),
            "trades": len(grp),
            "avg_return": float(rates.mean()),
            "win_rate": float((rates > 0).mean()),
            "total_return": float(np.prod(1 + rates) - 1 if len(rates) > 0 else 0),
        })
    return sorted(result, key=lambda x: x["month"])


def benchmark_compare(
    df: pd.DataFrame, benchmark_code: str = "000300.SH",
    data_feed=None,
) -> dict:
    """
    对比策略收益与基准（买入持有/指数）。

    Returns:
        dict 含 benchmark_return, excess_return
    """
    if df.empty or data_feed is None:
        return {}

    if "trade_date" not in df.columns:
        return {}

    dates = sorted(df["trade_date"].astype(str).unique())
    if not dates:
        return {}

    start, end = dates[0], dates[-1]
    bench_df = data_feed.kline(benchmark_code, start, end)
    if bench_df.empty:
        return {}

    bench_df["日期"] = pd.to_datetime(bench_df["日期"])
    first_close = float(bench_df.iloc[0]["收盘"])
    last_close = float(bench_df.iloc[-1]["收盘"])
    bench_return = (last_close - first_close) / first_close

    strategy_total = float(np.prod(1 + df["return_rate"].astype(float)) - 1)

    return {
        "benchmark_return": bench_return,
        "benchmark_code": benchmark_code,
        "strategy_total_return": strategy_total,
        "excess_return": strategy_total - bench_return,
    }
