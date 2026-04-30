"""技术指标纯函数计算 (STO-115).

所有函数:
  - 输入: close prices 列表 (float), 升序 (最旧在前)
  - 输出: 与输入等长数组, 前 N 个不足时为 None
  - 不依赖外部库, 纯 Python 浮点运算
"""
from __future__ import annotations

from typing import Optional


def _ma(closes: list[float], n: int, i: int) -> Optional[float]:
    if i < n - 1:
        return None
    return sum(closes[i - n + 1: i + 1]) / n


def calc_dma(
    closes: list[float],
    n: int = 10,
    m: int = 50,
) -> dict[str, list[Optional[float]]]:
    """DMA 指标: 返回 MA(n) 和 MA(m) 两条均线.

    返回: {"ma10": [...], "ma50": [...]}, 长度等于 closes.
    前 n-1 个 ma10 为 None, 前 m-1 个 ma50 为 None.
    """
    size = len(closes)
    ma_n = [_ma(closes, n, i) for i in range(size)]
    ma_m = [_ma(closes, m, i) for i in range(size)]
    return {f"ma{n}": ma_n, f"ma{m}": ma_m}


def calc_macd(
    closes: list[float],
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> dict[str, list[Optional[float]]]:
    """MACD 指标.

    返回: {"diff": [...], "dea": [...], "bar": [...]}, 长度等于 closes.
    前 slow-1 个 diff 为 None, 前 slow+signal-2 个 bar 为 None.
    """
    size = len(closes)
    if size == 0:
        return {"diff": [], "dea": [], "bar": []}

    # EMA 计算
    def ema_series(vals: list[float], period: int) -> list[Optional[float]]:
        result: list[Optional[float]] = [None] * size
        # 用 SMA 初始化第一个有效点
        if size < period:
            return result
        init = sum(vals[:period]) / period
        result[period - 1] = init
        k = 2 / (period + 1)
        for i in range(period, size):
            result[i] = vals[i] * k + result[i - 1] * (1 - k)  # type: ignore[operator]
        return result

    ema_fast = ema_series(closes, fast)
    ema_slow = ema_series(closes, slow)

    # DIF = EMA(fast) - EMA(slow)
    diff: list[Optional[float]] = []
    for f, s in zip(ema_fast, ema_slow):
        if f is None or s is None:
            diff.append(None)
        else:
            diff.append(f - s)

    # DEA = EMA(DIF, signal)
    diff_vals_only = [v for v in diff if v is not None]
    first_valid = next((i for i, v in enumerate(diff) if v is not None), None)
    dea: list[Optional[float]] = [None] * size
    if first_valid is not None and len(diff_vals_only) >= signal:
        # 找到 diff 中第一个有效值的起点，从那里计算 EMA(signal)
        ema_signal = [None] * size
        # 初始化: 取 diff 中前 signal 个有效点的均值
        init_end = first_valid + signal - 1
        if init_end < size:
            init_val = sum(diff_vals_only[:signal]) / signal  # type: ignore[arg-type]
            ema_signal[init_end] = init_val
            k = 2 / (signal + 1)
            for i in range(init_end + 1, size):
                if diff[i] is not None:
                    ema_signal[i] = diff[i] * k + ema_signal[i - 1] * (1 - k)  # type: ignore[operator]
                else:
                    ema_signal[i] = ema_signal[i - 1]
            dea = ema_signal  # type: ignore[assignment]

    # BAR = 2 * (DIF - DEA)
    bar: list[Optional[float]] = []
    for d, e in zip(diff, dea):
        if d is None or e is None:
            bar.append(None)
        else:
            bar.append(2 * (d - e))

    return {"diff": diff, "dea": dea, "bar": bar}
