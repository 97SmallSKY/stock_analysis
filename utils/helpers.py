"""
工具函数
"""
import re
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from loguru import logger


def fmt_date(d: str, sep: str = "-") -> str:
    """统一日期格式化：兼容 20260429 / 2026-04-29 / 2026/04/29 → 2026-04-29"""
    s = str(d).strip()
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}{sep}{s[4:6]}{sep}{s[6:]}"
    s = re.sub(r"[-/.]", "-", s)
    return s


def fmt_date_slash(d: str) -> str:
    """格式化为 2026/04/29"""
    return fmt_date(d, "/")


def fmt_date_dot(d: str) -> str:
    """格式化为 2026.04.29"""
    return fmt_date(d, ".")


def format_number(value: float, unit: str = "元") -> str:
    """格式化数字为易读形式"""
    if pd.isna(value):
        return "—"
    if abs(value) >= 1e8:
        return f"{value/1e8:.2f}亿{unit}"
    elif abs(value) >= 1e4:
        return f"{value/1e4:.0f}万{unit}"
    else:
        return f"{value:.2f}{unit}"


def get_trade_dates(start_date: str, end_date: str) -> list:
    """获取两个日期间的工作日列表"""
    dates = pd.date_range(start=start_date, end=end_date, freq="B")
    return dates.tolist()


def calc_annualized_return(total_return: float, days: int) -> float:
    """计算年化收益率"""
    if days <= 0:
        return 0
    return (1 + total_return) ** (252 / days) - 1


def calc_max_drawdown(returns: pd.Series) -> float:
    """计算最大回撤"""
    cumulative = (1 + returns).cumprod()
    rolling_max = cumulative.cummax()
    drawdown = (cumulative - rolling_max) / rolling_max
    return float(drawdown.min())


def is_trading_day(date: str = None) -> bool:
    """判断是否为交易日（简单工作日判断）"""
    if date is None:
        date = datetime.now().strftime("%Y%m%d")
    dt = pd.to_datetime(date)
    return dt.dayofweek < 5  # 0~4 = 周一~周五


def normalize_stock_code(code: str) -> str:
    """标准化股票代码（去除市场前缀）"""
    code = str(code).strip()
    for prefix in ["sh", "sz", "SH", "SZ"]:
        if code.startswith(prefix):
            code = code[2:]
    return code.zfill(6)


def get_stock_market(code: str) -> str:
    """根据股票代码判断市场"""
    code = normalize_stock_code(code)
    if code.startswith("6"):
        return "SH"
    elif code.startswith(("0", "3")):
        return "SZ"
    elif code.startswith("8"):
        return "BJ"
    return "SH"
