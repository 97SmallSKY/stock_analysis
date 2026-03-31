"""
策略分析模块
- 技术面评分
- 席位质量评分
- 综合选股
- 止盈止损模拟
- 回测框架
"""
import time
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from loguru import logger
from typing import List, Optional

import akshare as ak

from config.settings import FAMOUS_TRADERS, STRATEGY_CONFIG
from utils.storage import storage

# ============================================================
# 进程级 K 线缓存（回测时避免重复拉 API）
# ============================================================
# 结构: { stock_code: DataFrame(日期, 开盘, 最高, 最低, 收盘, 成交量) }
# 每次 run_backtest 调用 _kline_cache_clear() 重置
_KLINE_CACHE: dict = {}


def _kline_cache_clear():
    """清空进程级 K 线缓存（每次回测前调用）"""
    _KLINE_CACHE.clear()
    logger.info("K 线缓存已清空")


def _kline_get(stock_code: str, start_dt: str, end_dt: str) -> pd.DataFrame:
    """
    先查进程缓存，没有则拉 AKShare 后写入缓存。
    返回 DataFrame(日期 as str, 收盘, 最高, 最低, 成交量)
    """
    if stock_code in _KLINE_CACHE:
        df = _KLINE_CACHE[stock_code]
        # 按请求日期范围过滤
        if "日期" in df.columns:
            df = df[df["日期"].astype(str) <= end_dt]
        return df

    from utils.retry import safe_akshare_call
    raw = safe_akshare_call(
        ak.stock_zh_a_hist,
        symbol=stock_code,
        period="daily",
        start_date=start_dt,
        end_date=end_dt,
        adjust="qfq",
        max_retries=3,
        base_delay=2.0,
        fallback=None,
    )
    if raw is not None and len(raw) > 5:
        # 统一列名
        raw = raw.rename(columns={"date": "日期", "open": "开盘", "high": "最高",
                                   "low": "最低", "close": "收盘", "volume": "成交量"})
        if "日期" not in raw.columns and raw.index.name == "日期":
            raw = raw.reset_index()
        _KLINE_CACHE[stock_code] = raw.copy()
        return raw
    return pd.DataFrame()

# 尝试导入 TA-Lib，若未安装则使用 pandas_ta 替代
try:
    import talib
    HAS_TALIB = True
except ImportError:
    HAS_TALIB = False
    try:
        import pandas_ta as ta
        HAS_PANDAS_TA = True
    except ImportError:
        HAS_PANDAS_TA = False
    logger.warning("TA-Lib 未安装，使用 pandas 自计算技术指标")


# ============================================================
# 技术面评分
# ============================================================

def _sma(series: np.ndarray, period: int) -> np.ndarray:
    """简单移动平均"""
    if HAS_TALIB:
        return talib.SMA(series, period)
    result = np.full_like(series, np.nan)
    for i in range(period - 1, len(series)):
        result[i] = series[i - period + 1: i + 1].mean()
    return result


def _macd(close: np.ndarray, fast=12, slow=26, signal=9):
    """MACD 计算"""
    if HAS_TALIB:
        return talib.MACD(close, fast, slow, signal)
    
    def ema(arr, n):
        result = np.full_like(arr, np.nan)
        k = 2 / (n + 1)
        for i in range(len(arr)):
            if np.isnan(arr[i]):
                continue
            if np.isnan(result[i - 1]) if i > 0 else True:
                result[i] = arr[i]
            else:
                result[i] = arr[i] * k + result[i - 1] * (1 - k)
        return result
    
    ema_fast = ema(close, fast)
    ema_slow = ema(close, slow)
    macd_line = ema_fast - ema_slow
    signal_line = ema(macd_line, signal)
    hist = macd_line - signal_line
    return macd_line, signal_line, hist


def _rsi(close: np.ndarray, period: int = 14) -> np.ndarray:
    """RSI 计算"""
    if HAS_TALIB:
        return talib.RSI(close, period)
    
    result = np.full_like(close, np.nan)
    if len(close) < period + 1:
        return result
    
    delta = np.diff(close)
    gain = np.where(delta > 0, delta, 0)
    loss = np.where(delta < 0, -delta, 0)
    
    avg_gain = np.mean(gain[:period])
    avg_loss = np.mean(loss[:period])
    
    for i in range(period, len(close)):
        idx = i - 1
        avg_gain = (avg_gain * (period - 1) + gain[idx]) / period
        avg_loss = (avg_loss * (period - 1) + loss[idx]) / period
        rs = avg_gain / avg_loss if avg_loss > 0 else 100
        result[i] = 100 - 100 / (1 + rs)
    
    return result


def calculate_technical_score(stock_code: str, trade_date: str) -> dict:
    """
    技术面打分系统（0~100分），返回 dict 以支持细项展示。
    向下兼容：调用方可用 result["score"] 取总分，或直接 int(result) 如需旧接口。

    评分维度：
    - 趋势位置（30分）：收盘 > MA5/MA10/MA20/MA60
    - 多头排列奖励（+5分）：MA5 > MA10 > MA20 严格多头
    - 量能配合（20分）：量比 2~5 最佳
    - 涨幅位置（20分）：近20日涨幅是否在合理区间
    - 创20日新高奖励（+5分）：收盘为近20日最高收盘
    - K线形态（15分）：MACD金叉、RSI健康
    - RSI强势区奖励（+5分）：RSI 50~70
    - 基础分（15分）：默认给分

    优先使用进程级 _KLINE_CACHE（回测时避免重复拉接口）。

    Returns:
        dict with keys:
          score(int 0~100), ma_bullish(bool), above_ma20(bool), above_ma60(bool),
          new_high_20d(bool), macd_golden(bool), rsi_val(float), vol_ratio(float)
    """
    _default = {
        "score": 50, "ma_bullish": False, "above_ma20": False, "above_ma60": False,
        "new_high_20d": False, "macd_golden": False, "rsi_val": 50.0, "vol_ratio": 1.0,
    }
    try:
        start_dt = (pd.to_datetime(trade_date) - timedelta(days=120)).strftime("%Y%m%d")
        # ── 优先走缓存 ──
        df = _kline_get(stock_code, start_dt, trade_date)
        if df.empty:
            return _default

        if len(df) < 30:
            return _default  # 数据不足时给中性分
        # 截止到 trade_date 当天（缓存里可能含更新数据）
        if "日期" in df.columns:
            df = df[df["日期"].astype(str) <= trade_date]
        if len(df) < 30:
            return _default

        close  = df["收盘"].values.astype(float)
        volume = df["成交量"].values.astype(float)

        score = 0
        detail = dict(_default)

        # ===== 因子1：趋势位置（30分）+ 多头排列奖励（+5）=====
        ma5  = _sma(close, 5)
        ma10 = _sma(close, 10)
        ma20 = _sma(close, 20)
        ma60 = _sma(close, min(60, len(close)))

        cur = close[-1]
        if not np.isnan(ma5[-1])  and cur > ma5[-1]:   score += 5
        if not np.isnan(ma10[-1]) and cur > ma10[-1]:  score += 5
        if not np.isnan(ma20[-1]) and cur > ma20[-1]:
            score += 10
            detail["above_ma20"] = True
        if not np.isnan(ma60[-1]) and cur > ma60[-1]:
            score += 10
            detail["above_ma60"] = True

        # 多头排列：MA5 > MA10 > MA20
        if (not np.isnan(ma5[-1]) and not np.isnan(ma10[-1]) and not np.isnan(ma20[-1])
                and ma5[-1] > ma10[-1] > ma20[-1]):
            score += 5
            detail["ma_bullish"] = True

        # ===== 因子2：量能配合（20分）=====
        vol_ma5  = _sma(volume, 5)
        vol_ma20 = _sma(volume, 20)
        vol_ratio = 1.0

        if (not np.isnan(vol_ma5[-1]) and not np.isnan(vol_ma20[-1])
                and vol_ma20[-1] > 0):
            vol_ratio = volume[-1] / vol_ma20[-1]
            detail["vol_ratio"] = round(float(vol_ratio), 2)
            if vol_ma5[-1] > vol_ma20[-1] * 1.5:
                score += 10
            elif vol_ma5[-1] > vol_ma20[-1] * 1.2:
                score += 5

            if 2 < vol_ratio < 5:
                score += 10  # 适度放量
            elif 1.5 < vol_ratio <= 2:
                score += 5

        # ===== 因子3：涨幅位置（20分）+ 创20日新高奖励（+5）=====
        if len(close) >= 20:
            gain_20d = (close[-1] / close[-20] - 1)
            if 0 < gain_20d < 0.15:
                score += 20
            elif 0.15 <= gain_20d < 0.30:
                score += 10
            elif gain_20d >= 0.50:
                score -= 10

            # 创20日新高：今日收盘 = 近20日最高收盘
            high_20d = close[-20:].max() if len(close) >= 20 else close[-1]
            if close[-1] >= high_20d * 0.999:   # 容差 0.1%
                score += 5
                detail["new_high_20d"] = True

        # ===== 因子4：K线形态（15分）+ RSI强势奖励（+5）=====
        _, _, hist = _macd(close, 12, 26, 9)
        if hist is not None and len(hist) >= 2:
            if not np.isnan(hist[-1]) and not np.isnan(hist[-2]):
                if hist[-1] > 0 and hist[-2] <= 0:
                    score += 10  # 刚金叉
                    detail["macd_golden"] = True
                elif hist[-1] > hist[-2] > 0:
                    score += 5   # MACD 柱放大

        rsi = _rsi(close, 14)
        rsi_val = 50.0
        if not np.isnan(rsi[-1]):
            rsi_val = float(rsi[-1])
            detail["rsi_val"] = round(rsi_val, 1)
            if 50 < rsi_val <= 70:   # 强势区（含奖励）
                score += 5
            elif 40 < rsi_val <= 50:
                pass  # 中性，不加分
            elif rsi_val > 80:
                score -= 5  # 超买扣分

        # ===== 基础分（15分）=====
        score += 15

        detail["score"] = int(max(0, min(100, score)))
        return detail

    except Exception as e:
        logger.debug(f"技术面评分失败 {stock_code}: {e}")
        return _default


# ============================================================
# 席位质量评分
# ============================================================

def calculate_seat_score(stock_detail: pd.DataFrame, jigou_df: pd.DataFrame = None) -> dict:
    """
    计算席位质量评分

    支持两种数据格式：
    1. 东方财富格式（含「买方营业郥1」等席位列）→ 直接识别机构、知名游资
    2. 新浪格式（只有股票代码/名称/成交额/指标）→ 使用 jigou_df 追踪机构席位

    Args:
        stock_detail: 单只股票的龙虎榜明细行（可能来自新浪或东方财富）
        jigou_df: 机构席位汇总 DataFrame（新浪 接口返回，列：股票代码、累积买入额、买入次数、净额等）

    Returns:
        包含评分详情的字典
    """
    result = {
        "seat_score": 0,
        "has_institution": False,
        "inst_selling": False,
        "famous_count": 0,
        "famous_names": [],
    }

    # 方式1：东方财富格式（含营业郥列）
    buy_col = None
    sell_col = None
    for c in stock_detail.columns:
        if "买" in c and "营业部" in c:
            buy_col = c
        if "卖" in c and "营业部" in c:
            sell_col = c

    if buy_col:
        result["has_institution"] = stock_detail[buy_col].str.contains(
            "机构专用", na=False
        ).any()
        famous_names = []
        for dept in stock_detail[buy_col].dropna():
            for name_key, name_val in FAMOUS_TRADERS.items():
                if name_key in str(dept) and name_val not in famous_names:
                    famous_names.append(name_val)
        result["famous_count"] = len(famous_names)
        result["famous_names"] = famous_names

    if sell_col:
        result["inst_selling"] = stock_detail[sell_col].str.contains(
            "机构专用", na=False
        ).any()

    # 方式2：新浪格式（无营业郥列），通过 jigou_df 追踪机构
    if not buy_col and jigou_df is not None and not jigou_df.empty:
        code_col = "股票代码" if "股票代码" in jigou_df.columns else "代码"
        code_val = None
        for col in ["代码", "股票代码"]:
            if col in stock_detail.columns:
                codes = stock_detail[col].dropna().unique()
                if len(codes) > 0:
                    code_val = str(codes[0])
                    break
        if code_val and code_col in jigou_df.columns:
            match = jigou_df[jigou_df[code_col].astype(str) == code_val]
            if not match.empty:
                net_val = float(match["净额"].iloc[0]) if "净额" in match.columns else 0
                buy_cnt = float(match["买入次数"].iloc[0]) if "买入次数" in match.columns else 0
                sell_cnt = float(match["卖出次数"].iloc[0]) if "卖出次数" in match.columns else 0
                result["has_institution"] = buy_cnt > 0
                result["inst_selling"]    = sell_cnt > buy_cnt

    # 计算席位评分
    score = 0
    if result["has_institution"] and not result["inst_selling"]:
        score += 40
    elif result["has_institution"] and result["inst_selling"]:
        score += 10

    if result["famous_count"] >= 2:
        score += 30
    elif result["famous_count"] == 1:
        score += 15

    if result["inst_selling"] and not result["has_institution"]:
        score -= 20

    # 新浪接口无法识别营业部时，给一个基础分，不让全部被过滤
    if not buy_col and score == 0:
        score = 20  # 新浪接口默认分（不知道席位）

    result["seat_score"] = score
    return result


# ============================================================
# 综合选股
# ============================================================

def select_stocks_daily(
    lhb_df: pd.DataFrame,
    trade_date: str,
    top_n: int = None,
    min_net_buy: float = None,
    min_tech_score: int = None,
    max_gain_20d: float = None,
    jigou_df: pd.DataFrame = None,
) -> List[dict]:
    """
    对指定日期进行综合选股

    支持新浪接口格式（无营业部列）和东方财富格式（含营业部列）。
    新浪格式时需传入 jigou_df 进行机构席位判断。

    Args:
        lhb_df: 龙虎榜数据
        trade_date: 交易日期
        top_n: 最多选几只
        min_net_buy: 最小净买入额（万元）
        min_tech_score: 最小技术面评分
        max_gain_20d: 最大 20 日涨幅
        jigou_df: 机构席位汇总（新浪 stock_lhb_jgzz_sina 返回）

    Returns:
        选出的股票列表（按综合评分排序）
    """
    cfg = STRATEGY_CONFIG
    top_n = top_n or cfg["top_n"]
    min_net_buy = min_net_buy or cfg["min_net_buy"]
    min_tech_score = min_tech_score or cfg["min_tech_score"]
    max_gain_20d = max_gain_20d or cfg["max_gain_20d"]
    
    # 取当日数据
    date_col = "trade_date" if "trade_date" in lhb_df.columns else "上榜日期"
    if date_col not in lhb_df.columns:
        logger.warning(f"找不到日期列，可用列: {list(lhb_df.columns)}")
        return []
    
    day_df = lhb_df[lhb_df[date_col].astype(str) == trade_date].copy()
    if day_df.empty:
        return []
    
    # 排除 ST 股
    if "名称" in day_df.columns:
        day_df = day_df[~day_df["名称"].str.contains("ST|st", na=False)]
    
    # 按股票代码汇总净买入
    code_col = "代码" if "代码" in day_df.columns else "股票代码"
    name_col = "名称" if "名称" in day_df.columns else "股票名称"
    
    if code_col not in day_df.columns:
        logger.warning(f"找不到代码列，可用列: {list(day_df.columns)}")
        return []
    
    agg_dict = {}
    if name_col in day_df.columns:
        agg_dict[name_col] = "first"
    if "买入额" in day_df.columns:
        agg_dict["买入额"] = "sum"
    if "卖出额" in day_df.columns:
        agg_dict["卖出额"] = "sum"
    # 新浪格式：成交额(万元)列
    if "成交额(万元)" in day_df.columns:
        agg_dict["成交额(万元)"] = "sum"

    if not agg_dict:
        return []

    stock_agg = day_df.groupby(code_col).agg(agg_dict).reset_index()

    if "买入额" in stock_agg.columns and "卖出额" in stock_agg.columns:
        stock_agg["净买入额"] = stock_agg["买入额"] - stock_agg["卖出额"]
    elif "净买入额" not in stock_agg.columns:
        # 新浪格式：用 jigou_df 的净额，或用成交额作为近似排序
        if jigou_df is not None and not jigou_df.empty and "净额" in jigou_df.columns:
            code_c = "股票代码" if "股票代码" in jigou_df.columns else "代码"
            net_map = dict(zip(jigou_df[code_c].astype(str), jigou_df["净额"]))
            stock_agg["净买入额"] = stock_agg[code_col].astype(str).map(net_map).fillna(0)
        elif "成交额(万元)" in stock_agg.columns:
            stock_agg["净买入额"] = stock_agg["成交额(万元)"]
        else:
            # 完全无法计算净买入额，放宽过滤条件
            stock_agg["净买入额"] = 0

    # 净买入额过滤（新浪格式无净买入时跳过此过滤）
    effective_min_net_buy = min_net_buy if "买入额" in day_df.columns else 0
    stock_agg = stock_agg[stock_agg["净买入额"] >= effective_min_net_buy]
    
    if stock_agg.empty:
        return []
    
    qualified_stocks = []
    
    for _, row in stock_agg.iterrows():
        code = row[code_col]
        name = row.get(name_col, "")
        net_buy = row["净买入额"]
        
        # 席位评分（传入 jigou_df 支持新浪格式）
        stock_detail = day_df[day_df[code_col] == code]
        seat_info = calculate_seat_score(stock_detail, jigou_df=jigou_df)
        
        if seat_info["seat_score"] < 15:
            continue

        # 获取20日涨幅 ── 走缓存，避免重复拉接口
        try:
            start = (pd.to_datetime(trade_date) - timedelta(days=120)).strftime("%Y%m%d")
            hist = _kline_get(code, start, trade_date)
            if not hist.empty and "收盘" in hist.columns:
                hcut = hist[hist["日期"].astype(str) <= trade_date] if "日期" in hist.columns else hist
                if len(hcut) >= 20:
                    gain_20d = float(hcut["收盘"].iloc[-1] / hcut["收盘"].iloc[-20] - 1)
                else:
                    gain_20d = 0.0
            else:
                gain_20d = 0.0
        except Exception:
            gain_20d = 0.0
        
        if gain_20d >= max_gain_20d:
            continue
        
        # 技术面评分（返回 dict，取 score 字段）
        tech_result = calculate_technical_score(code, trade_date)
        tech_score = tech_result["score"] if isinstance(tech_result, dict) else int(tech_result)
        if tech_score < min_tech_score:
            continue
        
        qualified_stocks.append({
            "code": code,
            "name": name,
            "net_buy": net_buy,
            "tech_score": tech_score,
            "ma_bullish": tech_result.get("ma_bullish", False) if isinstance(tech_result, dict) else False,
            "new_high_20d": tech_result.get("new_high_20d", False) if isinstance(tech_result, dict) else False,
            "rsi_val": tech_result.get("rsi_val", 50.0) if isinstance(tech_result, dict) else 50.0,
            "gain_20d": gain_20d,
            **seat_info,
        })
        
        time.sleep(0.3)
    
    if not qualified_stocks:
        return []
    
    # 综合评分
    max_net = max(s["net_buy"] for s in qualified_stocks)
    min_net = min(s["net_buy"] for s in qualified_stocks)
    
    for stock in qualified_stocks:
        net_score = (
            (stock["net_buy"] - min_net) / (max_net - min_net) * 100
            if max_net > min_net else 50
        )
        stock["final_score"] = (
            net_score * 0.25
            + stock["seat_score"] * 0.35
            + stock["tech_score"] * 0.30
            + (30 - stock["gain_20d"] * 100) * 0.10
        )
    
    qualified_stocks.sort(key=lambda x: x["final_score"], reverse=True)
    return qualified_stocks[:top_n]


# ============================================================
# 止盈止损模拟
# ============================================================

def simulate_with_stoploss(
    stock_code: str,
    trade_date: str,
    stop_loss: float = -0.05,
    stop_profit: float = 0.08,
    max_hold_days: int = 3,
) -> Optional[dict]:
    """
    模拟带止盈止损的交易
    
    Returns:
        包含交易结果的字典
    """
    try:
        end_dt = pd.to_datetime(trade_date) + timedelta(days=max_hold_days + 15)
        # ── 优先走缓存，回测时完全不触发接口 ──
        raw = _kline_get(stock_code, trade_date, end_dt.strftime("%Y%m%d"))
        if raw.empty:
            return None
        # 截取 trade_date 当日及之后的数据
        if "日期" in raw.columns:
            df = raw[raw["日期"].astype(str) >= trade_date].reset_index(drop=True)
        else:
            df = raw.reset_index(drop=True)

        if len(df) < 2:
            return None
        
        buy_price = float(df.iloc[1]["开盘"])
        
        for day_idx in range(1, min(len(df), max_hold_days + 2)):
            if day_idx > max_hold_days:
                sell_price = float(df.iloc[day_idx]["收盘"])
                return {
                    "buy_price": buy_price, "sell_price": sell_price,
                    "return_rate": (sell_price - buy_price) / buy_price,
                    "hold_days": day_idx, "exit_reason": "到期卖出",
                }
            
            day_open = float(df.iloc[day_idx]["开盘"])
            day_high = float(df.iloc[day_idx]["最高"])
            day_low = float(df.iloc[day_idx]["最低"])
            day_close = float(df.iloc[day_idx]["收盘"])
            
            max_return = (day_high - buy_price) / buy_price
            min_return = (day_low - buy_price) / buy_price
            open_return = (day_open - buy_price) / buy_price
            
            if day_idx > 1 and open_return <= stop_loss:
                return {"buy_price": buy_price, "sell_price": day_open,
                        "return_rate": open_return, "hold_days": day_idx,
                        "exit_reason": "开盘止损"}
            if open_return >= stop_profit:
                return {"buy_price": buy_price, "sell_price": day_open,
                        "return_rate": open_return, "hold_days": day_idx,
                        "exit_reason": "开盘止盈"}
            if min_return <= stop_loss:
                return {"buy_price": buy_price,
                        "sell_price": buy_price * (1 + stop_loss),
                        "return_rate": stop_loss, "hold_days": day_idx,
                        "exit_reason": "盘中止损"}
            if max_return >= stop_profit:
                return {"buy_price": buy_price,
                        "sell_price": buy_price * (1 + stop_profit),
                        "return_rate": stop_profit, "hold_days": day_idx,
                        "exit_reason": "盘中止盈"}
        
        sell_price = float(df.iloc[-1]["收盘"])
        return {"buy_price": buy_price, "sell_price": sell_price,
                "return_rate": (sell_price - buy_price) / buy_price,
                "hold_days": len(df) - 1, "exit_reason": "数据截止"}
    
    except Exception as e:
        logger.debug(f"止盈止损模拟失败 {stock_code}: {e}")
        return None


# ============================================================
# 回测框架
# ============================================================

def run_backtest(
    start_date: str = STRATEGY_CONFIG.get("backtest_start", ""),
    end_date: str = "",
    top_n: int = 3,
    hold_days: int = 1,
    use_stoploss: bool = True,
    stop_loss: float = -0.05,
    stop_profit: float = 0.08,
    max_hold_days: int = 3,
    min_net_buy: float = 50_000_000,
    min_tech_score: int = 50,
    save: bool = True,
) -> pd.DataFrame:
    """
    完整策略回测（已优化：进程级 K 线缓存，同一只股只拉一次 API）

    性能优化说明：
      - 使用 _KLINE_CACHE 进程缓存 K 线，避免同一只股重复拉接口
      - 回测前统一预拉所有候选股 K 线（prefetch），接口调用次数从
        「交易日 × 每日选股数」降至「独立股票数」
      - time.sleep 从 0.5s/日 改为仅 prefetch 阶段按需休眠

    Args:
        start_date: 回测开始日期
        end_date: 回测结束日期
        top_n: 每日最多选股数
        hold_days: 持有天数（不用止盈止损时）
        use_stoploss: 是否使用止盈止损
        stop_loss / stop_profit / max_hold_days: 止盈止损参数

    Returns:
        回测结果 DataFrame
    """
    if not start_date:
        start_date = HALF_YEAR_START
    if not end_date:
        end_date = DATA_END_DATE

    logger.info(f"开始回测: {start_date} ~ {end_date}")
    # 每次回测前清空 K 线缓存，避免跨回测污染
    _kline_cache_clear()
    
    # 加载龙虎榜数据
    from modules.data_fetcher import load_lhb_detail, fetch_lhb_detail
    lhb_df = load_lhb_detail(start_date, end_date)
    
    if lhb_df.empty:
        logger.info("本地无数据，开始拉取...")
        lhb_df = fetch_lhb_detail(start_date, end_date, save=True)
    
    if lhb_df.empty:
        logger.error("无法获取龙虎榜数据，回测终止")
        return pd.DataFrame()
    
    # 获取所有交易日
    date_col = "trade_date" if "trade_date" in lhb_df.columns else "上榜日期"
    trade_dates = sorted(lhb_df[date_col].astype(str).unique())
    
    # ── 预拉 K 线缓存（prefetch）────────────────────────────────
    # 提前批量拉取所有出现过的股票 K 线，避免回测内逐只实时拉取
    date_col2 = "trade_date" if "trade_date" in lhb_df.columns else "上榜日期"
    all_codes = lhb_df["代码"].astype(str).unique().tolist() if "代码" in lhb_df.columns else []
    prefetch_start = (pd.to_datetime(start_date) - timedelta(days=130)).strftime("%Y%m%d")
    prefetch_end   = (pd.to_datetime(end_date)   + timedelta(days=20)).strftime("%Y%m%d")
    logger.info(f"预拉 K 线缓存：{len(all_codes)} 只股票，范围 {prefetch_start}~{prefetch_end}")
    for ci, code in enumerate(all_codes):
        if code not in _KLINE_CACHE:
            _kline_get(code, prefetch_start, prefetch_end)
            if ci % 10 == 0:
                logger.info(f"  prefetch [{ci+1}/{len(all_codes)}] {code}")
            time.sleep(0.3)   # 保护接口，仅 prefetch 阶段节流
    logger.info(f"K 线预拉完成，共缓存 {len(_KLINE_CACHE)} 只")

    results = []
    total_dates = len(trade_dates)

    for i, date_str in enumerate(trade_dates):
        logger.info(f"[{i+1}/{total_dates}] 回测日期: {date_str}")

        # 选股（此时 K 线已在缓存，不再触发 API）
        selected = select_stocks_daily(
            lhb_df, date_str,
            top_n=top_n,
            min_net_buy=min_net_buy,
            min_tech_score=min_tech_score,
        )

        if not selected:
            continue

        for stock in selected:
            if use_stoploss:
                ret = simulate_with_stoploss(
                    stock["code"], date_str,
                    stop_loss=stop_loss,
                    stop_profit=stop_profit,
                    max_hold_days=max_hold_days,
                )
            else:
                from modules.data_fetcher import get_next_day_return
                ret = get_next_day_return(stock["code"], date_str, hold_days)

            if ret:
                results.append({
                    "trade_date": date_str,
                    "code": stock["code"],
                    "name": stock.get("name", ""),
                    "net_buy": stock.get("net_buy", 0),
                    "seat_score": stock.get("seat_score", 0),
                    "tech_score": stock.get("tech_score", 0),
                    "final_score": stock.get("final_score", 0),
                    "has_institution": stock.get("has_institution", False),
                    "famous_count": stock.get("famous_count", 0),
                    "gain_20d": stock.get("gain_20d", 0),
                    **ret,
                })
    
    results_df = pd.DataFrame(results)
    
    if not results_df.empty and save:
        storage.write(results_df, "backtest_results", mode="replace")
        logger.info(f"回测结果保存完成，共 {len(results_df)} 条记录")
    
    return results_df


# ============================================================
# 统计分析
# ============================================================

def analyze_backtest(df: pd.DataFrame) -> dict:
    """
    对回测结果进行统计分析
    
    Returns:
        包含各统计指标的字典
    """
    if df.empty:
        return {}
    
    total = len(df)
    win_mask = df["return_rate"] > 0
    win_count = win_mask.sum()
    loss_count = total - win_count
    
    win_rate = win_count / total
    avg_return = df["return_rate"].mean()
    median_return = df["return_rate"].median()
    max_return = df["return_rate"].max()
    min_return = df["return_rate"].min()
    
    avg_win = df.loc[win_mask, "return_rate"].mean() if win_count > 0 else 0
    avg_loss = df.loc[~win_mask, "return_rate"].mean() if loss_count > 0 else 0
    pl_ratio = abs(avg_win / avg_loss) if avg_loss != 0 else float("inf")
    
    std = df["return_rate"].std()
    sharpe = avg_return / std if std > 0 else 0
    
    # 最大连续亏损
    max_consec_loss = 0
    cur = 0
    for v in df["return_rate"]:
        if v <= 0:
            cur += 1
            max_consec_loss = max(max_consec_loss, cur)
        else:
            cur = 0
    
    # 累计收益曲线（等权）
    df_sorted = df.sort_values("trade_date").copy()
    df_sorted["cumulative_return"] = (1 + df_sorted["return_rate"]).cumprod() - 1
    
    # 按月统计
    df_sorted["month"] = pd.to_datetime(df_sorted["trade_date"]).dt.to_period("M")
    monthly = df_sorted.groupby("month").agg(
        trades=("return_rate", "count"),
        avg_return=("return_rate", "mean"),
        win_rate=("return_rate", lambda x: (x > 0).mean()),
        total_return=("return_rate", "sum"),
    ).reset_index()
    monthly["month"] = monthly["month"].astype(str)
    
    # 按席位分组
    inst_df = df[df.get("has_institution", False) == True] if "has_institution" in df.columns else pd.DataFrame()
    
    return {
        "total_trades": total,
        "win_count": int(win_count),
        "loss_count": int(loss_count),
        "win_rate": float(win_rate),
        "avg_return": float(avg_return),
        "median_return": float(median_return),
        "max_return": float(max_return),
        "min_return": float(min_return),
        "avg_win": float(avg_win),
        "avg_loss": float(avg_loss),
        "pl_ratio": float(pl_ratio),
        "sharpe": float(sharpe),
        "max_consecutive_loss": int(max_consec_loss),
        "cumulative_curve": df_sorted[["trade_date", "cumulative_return"]].to_dict("records"),
        "monthly_stats": monthly.to_dict("records"),
        "inst_win_rate": float((inst_df["return_rate"] > 0).mean()) if not inst_df.empty else None,
        "inst_avg_return": float(inst_df["return_rate"].mean()) if not inst_df.empty else None,
    }
