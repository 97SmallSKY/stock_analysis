"""通用格式化函数（消除各页面 3+ 处重复的 _fmt_pct 定义）。"""
import math


def fmt_pct(v, decimals: int = 2, default: str = "—") -> str:
    """格式化为带符号百分比字符串，支持 None / NaN / 非数值。"""
    try:
        f = float(v)
        if math.isnan(f):
            return default
        return f"{f:+.{decimals}f}%"
    except (TypeError, ValueError):
        return default


def fmt_price(v, decimals: int = 2, default: str = "—") -> str:
    """格式化为价格字符串（¥x.xx）。"""
    try:
        f = float(v)
        if math.isnan(f) or f <= 0:
            return default
        return f"¥{f:.{decimals}f}"
    except (TypeError, ValueError):
        return default
