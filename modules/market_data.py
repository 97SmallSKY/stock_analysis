"""
modules/market_data.py — 统一行情获取层
=========================================

将原本散落在 modules/analyzer.py 与 skills/stock_sim_robot/trade_runner.py
中的重复行情获取逻辑统一到此模块，作为唯一的行情数据出口。

公开接口：
    get_kline(code, trade_date, lookback=60, adjust="qfq") → pd.DataFrame
    get_kline_raw(code, start_date, days, adjust="") → pd.DataFrame | None
    get_t2_prices(code, trade_date) → dict | None

规则（来自总控 Prompt）：
    - 回测场景：get_kline 必须基于 trade_date 截断，禁止泄漏未来数据
    - T+N 日期：必须使用交易日历，禁止自然日 +N
    - 不复权价格（adjust=""）用于交易，前复权（adjust="qfq"）用于技术分析

变更历史：
    2026-04-01  新建，合并 analyzer._kline_get 与 trade_runner._fetch_kline/_get_t2_prices
"""

import time
from datetime import date as _date
from functools import lru_cache

import akshare as ak
import pandas as pd
from loguru import logger

from utils.retry import safe_akshare_call


# ── 进程级 K 线内存缓存 ──────────────────────────────────────────────────
# 结构: { (code, adjust): DataFrame(日期/开盘/最高/最低/收盘/成交量) }
_KLINE_CACHE: dict = {}


def kline_cache_clear():
    """清空进程级 K 线缓存（每次回测前调用，与旧版 _kline_cache_clear 等效）"""
    _KLINE_CACHE.clear()
    logger.info("market_data: K 线缓存已清空")


# ── 交易日历辅助 ──────────────────────────────────────────────────────────

def _get_nth_trading_day(base_date: str, n: int) -> str:
    """
    获取 base_date 之后（或之前）第 |n| 个交易日日期
    n > 0: 之后，n < 0: 之前，n == 0: 当日（不验证是否为交易日）

    注意：此函数使用 AKShare 交易日历，如网络不可用则降级为自然日估算（不推荐生产使用）。

    Args:
        base_date: YYYYMMDD 格式
        n: 偏移量（正数=往后）

    Returns:
        YYYYMMDD 格式的目标交易日
    """
    if n == 0:
        return base_date
    try:
        cal = safe_akshare_call(
            ak.tool_trade_date_hist_sina,
            max_retries=2, base_delay=1.0, fallback=None,
        )
        if cal is not None and not cal.empty:
            dates = sorted(
                d.strftime("%Y%m%d") if hasattr(d, "strftime") else str(d).replace("-", "")
                for d in cal.iloc[:, 0]
            )
            try:
                idx = dates.index(base_date)
            except ValueError:
                # base_date 本身不是交易日，找最近交易日
                idx = max((i for i, d in enumerate(dates) if d <= base_date), default=0)
            target_idx = idx + n
            target_idx = max(0, min(target_idx, len(dates) - 1))
            return dates[target_idx]
    except Exception as e:
        logger.warning(f"market_data._get_nth_trading_day: 交易日历获取失败，降级自然日估算: {e}")

    # 降级：自然日估算（仅在接口不可用时使用）
    from datetime import timedelta
    base_dt = _date(int(base_date[:4]), int(base_date[4:6]), int(base_date[6:]))
    offset  = 0
    count   = 0
    step    = 1 if n > 0 else -1
    abs_n   = abs(n)
    while count < abs_n:
        offset += step
        candidate = base_dt + timedelta(days=offset)
        if candidate.weekday() < 5:  # 排除周末，不排除节假日
            count += 1
    result = base_dt + timedelta(days=offset)
    return result.strftime("%Y%m%d")


# ── 统一 K 线获取 ─────────────────────────────────────────────────────────

def get_kline(
    code: str,
    trade_date: str,
    lookback: int = 60,
    adjust: str = "qfq",
) -> pd.DataFrame:
    """
    获取 trade_date 前 lookback 根 K 线（前复权，用于技术分析）

    带进程级内存缓存（回测场景可显著减少 API 调用）。
    结果严格截断到 trade_date，确保不包含未来数据。

    Args:
        code:       股票代码（6位，如 "600000"）
        trade_date: 截止日期（YYYYMMDD），结果不含该日之后的数据
        lookback:   最大返回 K 线数
        adjust:     复权方式（"qfq"=前复权, ""=不复权, "hfq"=后复权）

    Returns:
        DataFrame，含 日期/开盘/最高/最低/收盘/成交量，按日期升序
        K 线不足时返回空 DataFrame
    """
    cache_key = (code, adjust)
    if cache_key in _KLINE_CACHE:
        df = _KLINE_CACHE[cache_key]
    else:
        # 用 stock_zh_a_hist 拉历史数据（支持复权参数）
        # 估算开始日期：lookback * 1.5 个自然日，兼顾停牌空白
        from datetime import timedelta
        base_dt   = _date(int(trade_date[:4]), int(trade_date[4:6]), int(trade_date[6:]))
        start_dt  = base_dt - timedelta(days=int(lookback * 2.2))
        start_str = start_dt.strftime("%Y%m%d")

        raw = safe_akshare_call(
            ak.stock_zh_a_hist,
            symbol=code,
            period="daily",
            start_date=start_str,
            end_date="20991231",  # 拉到最新，由截断控制边界
            adjust=adjust,
            max_retries=3,
            base_delay=2.0,
            fallback=None,
        )
        if raw is None or raw.empty or len(raw) < 5:
            # 尝试降级：用 stock_zh_a_daily（兼容旧版 fetch_signal 路径）
            prefix = "sh" if code.startswith(("6", "9")) else "sz"
            raw = safe_akshare_call(
                ak.stock_zh_a_daily,
                symbol=f"{prefix}{code}", adjust=adjust,
                max_retries=2, base_delay=1.5, fallback=None,
            )
            if raw is not None and not raw.empty:
                raw = raw.rename(columns={
                    "date": "日期", "open": "开盘",
                    "high": "最高", "low": "最低",
                    "close": "收盘", "volume": "成交量",
                })

        if raw is not None and not raw.empty:
            # 统一列名（stock_zh_a_hist 使用中文列）
            col_map = {
                "date": "日期", "open": "开盘", "high": "最高",
                "low": "最低", "close": "收盘", "volume": "成交量",
                "日期": "日期", "开盘": "开盘", "最高": "最高",
                "最低": "最低", "收盘": "收盘", "成交量": "成交量",
            }
            raw = raw.rename(columns={k: v for k, v in col_map.items() if k in raw.columns})
            if "日期" not in raw.columns and raw.index.name == "日期":
                raw = raw.reset_index()
            raw = raw.sort_values("日期").reset_index(drop=True)
            _KLINE_CACHE[cache_key] = raw.copy()
            df = raw
        else:
            logger.warning(f"market_data.get_kline: [{code}] 无法获取 K 线数据")
            return pd.DataFrame()

    # 严格截断：不含 trade_date 之后的数据（防止回测未来函数）
    if "日期" in df.columns:
        df = df[df["日期"].astype(str).str.replace("-", "") <= trade_date].copy()

    return df.tail(lookback).reset_index(drop=True)


def get_kline_raw(
    code: str,
    start_date: str,
    days: int = 5,
    adjust: str = "",
) -> pd.DataFrame | None:
    """
    拉取 start_date 起 days 根 K 线（不使用缓存，用于实时/交易场景）

    与 trade_runner._fetch_kline 等效。
    adjust="" 表示不复权（拿真实成交价，用于买入/卖出价计算）。

    Args:
        code:       股票代码（6位）
        start_date: 开始日期（YYYYMMDD）
        days:       请求天数（自然日窗口 = days*2+5，确保拿到足够交易日）
        adjust:     复权方式（默认""不复权）

    Returns:
        DataFrame(日期/开盘/最高/最低/收盘) 或 None（获取失败）
    """
    prefix = "sh" if code.startswith(("6", "9")) else "sz"
    end_dt = (pd.Timestamp(start_date) + pd.Timedelta(days=days * 2 + 5)).strftime("%Y%m%d")
    symbol = f"{prefix}{code}"
    source = f"AKShare stock_zh_a_daily ({symbol})"

    try:
        raw = safe_akshare_call(
            ak.stock_zh_a_daily,
            symbol=symbol, adjust=adjust,
            start_date=start_date, end_date=end_dt,
            max_retries=2, base_delay=2.0, fallback=None,
        )
        if raw is not None and not raw.empty:
            raw = raw.rename(columns={
                "date":   "日期",
                "open":   "开盘",
                "high":   "最高",
                "low":    "最低",
                "close":  "收盘",
                "volume": "成交量",
                "amount": "成交额",
            })
            return raw.sort_values("日期").reset_index(drop=True)
    except Exception as e:
        import requests
        if isinstance(e, requests.exceptions.ConnectionError):
            logger.warning(f"[{source}] 网络连接失败: {type(e).__name__}")
        elif isinstance(e, requests.exceptions.Timeout):
            logger.warning(f"[{source}] 请求超时: {type(e).__name__}")
        elif "429" in str(e) or "Too Many Requests" in str(e):
            logger.warning(f"[{source}] 接口限流 HTTP 429")
        else:
            logger.warning(f"[{source}] 未知错误: {type(e).__name__}: {e}")
    return None


def get_t2_prices(
    code: str,
    trade_date: str,
    max_retries: int = 3,
) -> dict | None:
    """
    获取 T+2 日价格（用于结算）

    T+2 = trade_date 之后的第 2 个交易日，通过交易日历精确定位，
    禁止使用 kline.iloc[N]（违反"T+N 不得使用自然日偏移"规则）。

    Args:
        code:        股票代码（6位）
        trade_date:  T 日（YYYYMMDD）
        max_retries: 重试次数（默认3次）

    Returns:
        {date, open, high, low, close} 或 None（全部失败）
    """
    last_err = None
    for attempt in range(1, max_retries + 1):
        try:
            result = _get_t2_prices_once(code, trade_date)
            if result is not None:
                if attempt > 1:
                    logger.info(f"  {code} T+2价格第{attempt}次获取成功")
                return result
        except Exception as e:
            last_err = e
        if attempt < max_retries:
            wait = attempt * 2
            logger.warning(f"  {code} T+2价格第{attempt}次失败，{wait}s后重试...")
            time.sleep(wait)
    logger.error(f"  {code} T+2价格全部重试失败，最后错误: {last_err}")
    return None


def _get_t2_prices_once(code: str, trade_date: str) -> dict | None:
    """内部单次尝试获取 T+2 价格"""
    t2_date = _get_nth_trading_day(trade_date, 2)
    kline   = get_kline_raw(code, t2_date, days=3)
    if kline is None or kline.empty:
        return None
    t2_rows = kline[kline["日期"].astype(str).str.replace("-", "") == t2_date]
    if not t2_rows.empty:
        row = t2_rows.iloc[0]
        return {
            "date":  t2_date,
            "open":  round(float(row["开盘"]), 3),
            "high":  round(float(row["最高"]), 3),
            "low":   round(float(row["最低"]), 3),
            "close": round(float(row["收盘"]), 3),
        }
    # 日期精确匹配失败时 fallback 取第一行
    if not kline.empty:
        row = kline.iloc[0]
        actual_date = str(row["日期"]).replace("-", "")
        logger.warning(
            f"  {code} T+2={t2_date} 精确匹配失败，fallback 取 kline[0]={actual_date}"
        )
        return {
            "date":  actual_date,
            "open":  round(float(row["开盘"]), 3),
            "high":  round(float(row["最高"]), 3),
            "low":   round(float(row["最低"]), 3),
            "close": round(float(row["收盘"]), 3),
        }
    return None
