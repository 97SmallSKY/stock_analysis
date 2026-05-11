"""技术指标统一实现（消除 _sma/_ema/_macd 在 5 个文件中的重复定义）。"""
import numpy as np


def sma(arr: np.ndarray, n: int) -> np.ndarray:
    """简单移动平均，前 n-1 个位置为 NaN。"""
    arr = np.asarray(arr, dtype=float)
    result = np.full(len(arr), np.nan)
    if len(arr) < n:
        return result
    for i in range(n - 1, len(arr)):
        result[i] = arr[i - n + 1:i + 1].mean()
    return result


def ema(arr: np.ndarray, n: int) -> np.ndarray:
    """指数移动平均（首值用原始值初始化，跳过 NaN）。"""
    arr = np.asarray(arr, dtype=float)
    result = np.full(len(arr), np.nan)
    k = 2.0 / (n + 1)
    for i in range(len(arr)):
        if np.isnan(arr[i]):
            continue
        if i == 0 or np.isnan(result[i - 1]):
            result[i] = arr[i]
        else:
            result[i] = arr[i] * k + result[i - 1] * (1 - k)
    return result


def macd(close: np.ndarray, fast: int = 12, slow: int = 26, signal: int = 9):
    """标准 MACD，返回 (dif, dea, hist)，hist = (dif - dea) × 2。"""
    dif  = ema(close, fast) - ema(close, slow)
    dea  = ema(dif, signal)
    hist = (dif - dea) * 2
    return dif, dea, hist
