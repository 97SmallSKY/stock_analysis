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

from config.settings import FAMOUS_TRADERS, STRATEGY_CONFIG, HALF_YEAR_START, DATA_END_DATE
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


def calculate_technical_score(stock_code: str, trade_date: str) -> int:
    """
    技术面打分系统（0~100分）
    
    评分维度：
    - 趋势位置（30分）：MA5/MA10/MA20/MA60 多头排列
    - 量能配合（20分）：量比是否合适
    - 涨幅位置（20分）：近20日涨幅是否在合理区间
    - K线形态（15分）：MACD金叉、RSI健康
    - 基础分（15分）：默认给分

    优先使用进程级 _KLINE_CACHE（回测时避免重复拉接口）。
    
    Returns:
        0~100 的整数评分
    """
    try:
        start_dt = (pd.to_datetime(trade_date) - timedelta(days=120)).strftime("%Y%m%d")
        # ── 优先走缓存 ──
        df = _kline_get(stock_code, start_dt, trade_date)
        if df.empty:
            return 50
        
        if len(df) < 30:
            return 50  # 数据不足时给中性分
        # 截止到 trade_date 当天（缓存里可能含更新数据）
        if "日期" in df.columns:
            df = df[df["日期"].astype(str) <= trade_date]
        if len(df) < 30:
            return 50

        close = df["收盘"].values.astype(float)
        volume = df["成交量"].values.astype(float)
        
        score = 0
        
        # ===== 因子1：趋势位置（30分）=====
        ma5 = _sma(close, 5)
        ma10 = _sma(close, 10)
        ma20 = _sma(close, 20)
        ma60 = _sma(close, min(60, len(close)))
        
        cur = close[-1]
        if not np.isnan(ma5[-1]) and cur > ma5[-1]:
            score += 5
        if not np.isnan(ma10[-1]) and cur > ma10[-1]:
            score += 5
        if not np.isnan(ma20[-1]) and cur > ma20[-1]:
            score += 10
        if not np.isnan(ma60[-1]) and cur > ma60[-1]:
            score += 10
        
        # ===== 因子2：量能配合（20分）=====
        vol_ma5 = _sma(volume, 5)
        vol_ma20 = _sma(volume, 20)
        
        if (not np.isnan(vol_ma5[-1]) and not np.isnan(vol_ma20[-1])
                and vol_ma20[-1] > 0):
            vol_ratio = volume[-1] / vol_ma20[-1]
            if vol_ma5[-1] > vol_ma20[-1] * 1.5:
                score += 10
            elif vol_ma5[-1] > vol_ma20[-1] * 1.2:
                score += 5
            
            if 2 < vol_ratio < 5:
                score += 10  # 适度放量
            elif 1.5 < vol_ratio <= 2:
                score += 5
        
        # ===== 因子3：涨幅位置（20分）=====
        if len(close) >= 20:
            gain_20d = (close[-1] / close[-20] - 1)
            if 0 < gain_20d < 0.15:
                score += 20
            elif 0.15 <= gain_20d < 0.30:
                score += 10
            elif gain_20d >= 0.50:
                score -= 10
        
        # ===== 因子4：K线形态（15分）=====
        _, _, hist = _macd(close, 12, 26, 9)
        if hist is not None and len(hist) >= 2:
            if not np.isnan(hist[-1]) and not np.isnan(hist[-2]):
                if hist[-1] > 0 and hist[-2] <= 0:
                    score += 10  # 刚金叉
                elif hist[-1] > hist[-2] > 0:
                    score += 5   # MACD 柱放大
        
        rsi = _rsi(close, 14)
        if not np.isnan(rsi[-1]):
            if 40 < rsi[-1] < 70:
                score += 5
            elif rsi[-1] > 80:
                score -= 5
        
        # ===== 基础分（15分）=====
        score += 15
        
        return int(max(0, min(100, score)))
    
    except Exception as e:
        logger.debug(f"技术面评分失败 {stock_code}: {e}")
        return 50


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
# P1/P2 辅助函数：对倒检测 / 上榜原因评分 / 流通市值查询
#                  买卖集中度不对称 / 连续上榜频率
# ============================================================

def _detect_wash_trade(
    code: str,
    trade_date: str,
    seat_detail_df: pd.DataFrame,
) -> bool:
    """
    对倒检测：检查同一只股票的买方和卖方席位中是否存在相同营业部。

    Args:
        code:           股票代码
        trade_date:     交易日期（YYYYMMDD）
        seat_detail_df: lhb_seat_detail 表，含买方席位1~5 / 卖方席位1~5

    Returns:
        True = 存在对倒嫌疑（直接剔除），False = 无对倒
    """
    try:
        code_norm = str(code).zfill(6)
        mask = (
            (seat_detail_df["代码"].astype(str).str.zfill(6) == code_norm) &
            (seat_detail_df["trade_date"].astype(str) == trade_date)
        )
        rows = seat_detail_df[mask]
        if rows.empty:
            return False

        # 收集买方席位名称
        buy_seats = set()
        for col in [f"买方席位{i}" for i in range(1, 6)]:
            if col in rows.columns:
                vals = rows[col].dropna().astype(str).str.strip()
                buy_seats.update(v for v in vals if v and v != "nan")

        # 收集卖方席位名称
        sell_seats = set()
        for col in [f"卖方席位{i}" for i in range(1, 6)]:
            if col in rows.columns:
                vals = rows[col].dropna().astype(str).str.strip()
                sell_seats.update(v for v in vals if v and v != "nan")

        # 买卖双方有交集 = 对倒
        intersection = buy_seats & sell_seats
        if intersection:
            logger.debug(f"[对倒检测] {code} 重叠席位: {intersection}")
            return True
    except Exception as e:
        logger.debug(f"[对倒检测] {code} 检测失败（兼容降级）: {e}")
    return False


def _calc_reason_score(
    code: str,
    trade_date: str,
    lhb_df: pd.DataFrame,
) -> int:
    """
    上榜原因评分（0~20）:
      - 涨幅偏离（涨停板/大涨）→ 20
      - 换手率偏离              → 10
      - 振幅偏离（高低分歧）    → 0
      - 其他/未知               → 10（中性）

    数据来自 lhb_detail 的「上榜原因」字段（新浪格式）。
    """
    try:
        code_norm = str(code).zfill(6)
        date_col  = "trade_date" if "trade_date" in lhb_df.columns else "上榜日期"
        col_col   = "代码"       if "代码"       in lhb_df.columns else "股票代码"
        mask = (
            (lhb_df[col_col].astype(str).str.zfill(6) == code_norm) &
            (lhb_df[date_col].astype(str) == trade_date)
        )
        rows = lhb_df[mask]
        if rows.empty or "上榜原因" not in rows.columns:
            return 10  # 兼容降级：无数据给中性分

        reasons = " ".join(rows["上榜原因"].dropna().astype(str))
        if "涨幅" in reasons or "涨停" in reasons:
            return 20
        if "振幅" in reasons:
            return 0
        if "换手" in reasons:
            return 10
        return 10
    except Exception:
        return 10


def _get_float_mcap(
    code: str,
    trade_date: str,
    lhb_top10_df: pd.DataFrame = None,
) -> float:
    """
    获取股票流通市值（元）。
    优先从 lhb_top10_df 的「流通市值」字段中查询，
    找不到时返回 0（降级方案：net_intensity 将为 0，不阻断流程）。
    """
    if lhb_top10_df is None or lhb_top10_df.empty:
        return 0.0
    if "流通市值" not in lhb_top10_df.columns:
        return 0.0
    try:
        code_norm = str(code).zfill(6)
        date_col  = "trade_date" if "trade_date" in lhb_top10_df.columns else "上榜日"
        code_c    = "代码"       if "代码"       in lhb_top10_df.columns else "股票代码"
        mask = (
            (lhb_top10_df[code_c].astype(str).str.zfill(6) == code_norm) &
            (lhb_top10_df[date_col].astype(str) == trade_date)
        )
        sub = lhb_top10_df[mask]
        if not sub.empty:
            v = pd.to_numeric(sub["流通市值"].iloc[0], errors="coerce")
            return float(v) if pd.notna(v) and v > 0 else 0.0
    except Exception:
        pass
    return 0.0


# ============================================================
# P2-1: 买卖集中度不对称因子
# ============================================================

def _calc_concentration_score(
    code: str,
    trade_date: str,
    seat_detail_df: pd.DataFrame,
    lhb_top10_df: pd.DataFrame = None,
) -> float:
    """
    P2-1: 买卖集中度不对称因子。

    定义（改进方案 2.2.2）：
      买入集中度 = 买方前5席位合计买入额 / 当日总成交额
      卖出集中度 = 卖方前5席位合计卖出额 / 当日总成交额
      集中度差值 = 买入集中度 - 卖出集中度

    返回值范围约 -1 ~ +1，正值越大说明主力资金收集筹码越明确。
    兼容降级：若数据缺失返回 0（中性）。
    """
    try:
        code_norm = str(code).zfill(6)

        # ── 总成交额：优先从 lhb_top10_df 获取 ──
        total_vol = 0.0
        if lhb_top10_df is not None and not lhb_top10_df.empty:
            date_c = "trade_date" if "trade_date" in lhb_top10_df.columns else "上榜日"
            code_c = "代码"       if "代码"       in lhb_top10_df.columns else "股票代码"
            sub10 = lhb_top10_df[
                (lhb_top10_df[code_c].astype(str).str.zfill(6) == code_norm) &
                (lhb_top10_df[date_c].astype(str) == trade_date)
            ]
            if not sub10.empty and "市场总成交额" in sub10.columns:
                v = pd.to_numeric(sub10["市场总成交额"].iloc[0], errors="coerce")
                total_vol = float(v) if pd.notna(v) and v > 0 else 0.0

        if seat_detail_df is None or seat_detail_df.empty:
            return 0.0

        # ── 席位明细 ──
        date_c2 = "trade_date" if "trade_date" in seat_detail_df.columns else "上榜日期"
        code_c2 = "代码"       if "代码"       in seat_detail_df.columns else "股票代码"
        mask = (
            (seat_detail_df[code_c2].astype(str).str.zfill(6) == code_norm) &
            (seat_detail_df[date_c2].astype(str) == trade_date)
        )
        rows = seat_detail_df[mask]
        if rows.empty:
            return 0.0

        # 合计买方 / 卖方金额
        buy_total = 0.0
        for col in [f"买方金额{i}" for i in range(1, 6)]:
            if col in rows.columns:
                v = pd.to_numeric(rows[col], errors="coerce").fillna(0).sum()
                buy_total += float(v)

        sell_total = 0.0
        for col in [f"卖方金额{i}" for i in range(1, 6)]:
            if col in rows.columns:
                v = pd.to_numeric(rows[col], errors="coerce").fillna(0).sum()
                sell_total += float(v)

        # 若 lhb_top10 无总成交额，用买+卖估算（保守降级）
        if total_vol <= 0:
            total_vol = buy_total + sell_total
        if total_vol <= 0:
            return 0.0

        buy_conc  = buy_total  / total_vol
        sell_conc = sell_total / total_vol
        return float(buy_conc - sell_conc)

    except Exception as e:
        logger.debug(f"[集中度因子] {code} 计算失败: {e}")
        return 0.0


# ============================================================
# P2-2: 连续上榜频率因子
# ============================================================

def _calc_continuous_lhb_score(
    code: str,
    trade_date: str,
    lhb_df: pd.DataFrame,
    lookback_days: int = 5,
) -> int:
    """
    P2-2: 连续上榜频率因子（改进方案 2.2.3）。

    逻辑：
      在过去 lookback_days 个交易日（不含当日）内查找同一股票的历史上榜记录：
      - 同方向净买入连续 2~3 次 → +10（强势跟进信号）
      - 高频交替（买卖交替出现超过 1 次） → -10（分歧/对倒信号）
      - 单次首次上榜或中性 → 0

    数据来自 lhb_detail（含 trade_date 字段）。
    返回值范围：-10 ~ +10，加入综合评分。
    """
    try:
        date_col = "trade_date" if "trade_date" in lhb_df.columns else "上榜日期"
        code_col = "代码"       if "代码"       in lhb_df.columns else "股票代码"

        # 取过去 N 日范围
        td = pd.to_datetime(trade_date)
        start_dt = td - timedelta(days=lookback_days * 2)  # 宽松范围，避免节假日
        start_str = start_dt.strftime("%Y%m%d")

        hist = lhb_df[
            (lhb_df[code_col].astype(str).str.zfill(6) == str(code).zfill(6)) &
            (lhb_df[date_col].astype(str) >= start_str) &
            (lhb_df[date_col].astype(str) < trade_date)
        ].copy()

        if hist.empty:
            return 0  # 首次上榜：中性

        # 按日期去重，取最近 lookback_days 个交易日
        hist_dates = sorted(hist[date_col].astype(str).unique(), reverse=True)[:lookback_days]

        # 判断每次上榜的净买入方向（需 买入额/卖出额 或 净买入额 字段）
        directions: list = []
        for d in hist_dates:
            day_rows = hist[hist[date_col].astype(str) == d]
            if "买入额" in day_rows.columns and "卖出额" in day_rows.columns:
                net = day_rows["买入额"].sum() - day_rows["卖出额"].sum()
            elif "净买入额" in day_rows.columns:
                net = day_rows["净买入额"].sum()
            elif "成交额(万元)" in day_rows.columns:
                # 新浪格式：无净买入，只有成交额，视为中性
                net = 0
            else:
                net = 0
            directions.append(1 if net > 0 else (-1 if net < 0 else 0))

        if not directions:
            return 0

        # 检测连续同方向（最近 2 次都是净买入）
        if len(directions) >= 2 and all(d == 1 for d in directions[:2]):
            return 10  # 同方向连续净买入
        if len(directions) >= 2 and all(d == -1 for d in directions[:2]):
            return -5  # 连续净卖出：稍微减分

        # 检测交替信号（买→卖或卖→买频繁出现）
        alternations = sum(
            1 for i in range(len(directions) - 1)
            if directions[i] != directions[i + 1] and directions[i] != 0 and directions[i + 1] != 0
        )
        if alternations >= 2:
            return -10  # 高频交替：强烈减分

        return 0  # 中性

    except Exception as e:
        logger.debug(f"[连续上榜因子] {code} 计算失败: {e}")
        return 0


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
    lhb_top10_df: pd.DataFrame = None,
    seat_detail_df: pd.DataFrame = None,
) -> List[dict]:
    """
    对指定日期进行综合选股

    支持新浪接口格式（无营业部列）和东方财富格式（含营业部列）。
    新浪格式时需传入 jigou_df 进行机构席位判断。

    Args:
        lhb_df: 龙虎榜数据（lhb_detail 格式）
        trade_date: 交易日期
        top_n: 最多选几只
        min_net_buy: 最小净买入额（万元）
        min_tech_score: 最小技术面评分
        max_gain_20d: 最大 20 日涨幅
        jigou_df: 机构席位汇总（新浪 stock_lhb_jgzz_sina 返回）
        lhb_top10_df: 龙虎榜 Top10 数据（含流通市值字段，用于净买入强度标准化）
        seat_detail_df: 当日席位明细（lhb_seat_detail，用于对倒检测）

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
        
        # 技术面评分
        tech_score = calculate_technical_score(code, trade_date)
        if tech_score < min_tech_score:
            continue

        # P1：对倒检测 — 买卖双方出现同一营业部则剔除
        if seat_detail_df is not None and not seat_detail_df.empty:
            if _detect_wash_trade(code, trade_date, seat_detail_df):
                logger.info(f"[对倒检测] {code} 买卖双方存在同一营业部，已剔除")
                continue

        # P1：上榜原因评分（涨幅偏离=20, 换手率偏离=10, 振幅偏离=0）
        reason_score = _calc_reason_score(code, trade_date, lhb_df)

        # P1：取流通市值（优先从 lhb_top10_df 中查）
        mcap = _get_float_mcap(code, trade_date, lhb_top10_df)

        # P2-1：买卖集中度不对称因子
        conc_score = _calc_concentration_score(code, trade_date, seat_detail_df, lhb_top10_df)

        # P2-2：连续上榜频率因子
        cont_score = _calc_continuous_lhb_score(code, trade_date, lhb_df)

        qualified_stocks.append({
            "code": code,
            "name": name,
            "net_buy": net_buy,
            "tech_score": tech_score,
            "gain_20d": gain_20d,
            "float_mcap": mcap,
            "reason_score": reason_score,
            "conc_score": conc_score,    # P2-1: 集中度差值 -1~+1
            "cont_score": cont_score,    # P2-2: 连续上榜 -10~+10
            **seat_info,
        })
        
        time.sleep(0.3)
    
    if not qualified_stocks:
        return []
    
    # 综合评分
    # P1 改进：净买入强度 = 净买入额 / 流通市值（标准化），替代绝对额排名
    for stock in qualified_stocks:
        mcap = float(stock.get("float_mcap", 0) or 0)
        net = float(stock.get("net_buy", 0) or 0)
        if mcap > 0:
            stock["net_intensity"] = net / mcap * 100  # 百分比，相对流通市值
        else:
            stock["net_intensity"] = 0.0

    max_intensity = max(s["net_intensity"] for s in qualified_stocks)
    min_intensity = min(s["net_intensity"] for s in qualified_stocks)

    for stock in qualified_stocks:
        intensity_score = (
            (stock["net_intensity"] - min_intensity) / (max_intensity - min_intensity) * 100
            if max_intensity > min_intensity else 50
        )
        # reason_score: 0~20 → 归一化到 0~10
        reason_bonus = stock.get("reason_score", 10) / 20.0 * 10
        # P2-1: conc_score -1~+1 → 映射到 0~10 加分
        conc_bonus   = (stock.get("conc_score", 0.0) + 1.0) / 2.0 * 10  # 0~10
        # P2-2: cont_score -10~+10 直接加入（已是百分级别可加可减）
        cont_bonus   = stock.get("cont_score", 0)
        stock["final_score"] = (
            intensity_score * 0.22
            + stock["seat_score"] * 0.33
            + stock["tech_score"] * 0.28
            + (30 - stock["gain_20d"] * 100) * 0.04
            + reason_bonus * 0.05
            + conc_bonus   * 0.05
            + cont_bonus   * 0.03  # 小权重，防止因子噪声过大
        )
    
    qualified_stocks.sort(key=lambda x: x["final_score"], reverse=True)
    return qualified_stocks[:top_n]


# ============================================================
# 止盈止损模拟
# ============================================================

# P1-4: 交易成本常量（买卖佣金 + 印花税 + 滑点，单次往返约 0.25%）
_TRADE_COST_BUY  = 0.00125            # 买入：佣金0.025% + 滑点0.10%
_TRADE_COST_SELL = 0.00175            # 卖出：佣金0.025% + 印花税0.05% + 滑点0.10%
# 单次往返总成本 ≈ 0.30%（保守估）


def _apply_trade_cost(gross_return: float, is_stop: bool = False) -> float:
    """
    P1-4：对毛收益扣除完整交易成本（买卖佣金+印花税+滑点）

    实际收益 = (卖出价 × (1 - sell_cost)) / (买入价 × (1 + buy_cost)) - 1
    简化公式：net ≈ gross - buy_cost - sell_cost（误差 <0.01%，可接受）

    Args:
        gross_return: 毛收益率（浮点数，如 0.05 = 5%）
        is_stop:      是否为止损退出（印花税按卖出价算，止损时卖价较低，但成本一样）

    Returns:
        扣除成本后的净收益率
    """
    return gross_return - _TRADE_COST_BUY - _TRADE_COST_SELL


def simulate_with_stoploss(
    stock_code: str,
    trade_date: str,
    stop_loss: float = -0.05,
    stop_profit: float = 0.08,
    max_hold_days: int = 3,
) -> Optional[dict]:
    """
    模拟带止盈止损的交易（P1-4: 已扣除完整交易成本）

    成本模型：
      买入成本：佣金 0.025% + 买入滑点 0.10% = 0.125%
      卖出成本：佣金 0.025% + 印花税 0.05% + 卖出滑点 0.05% = 0.125%
      单次往返总成本 ≈ 0.25%

    Returns:
        包含交易结果的字典（return_rate 为扣除成本后的净收益率）
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

        # T+1 开盘买入（已含滑点，但这是实际开盘价，不再叠加）
        buy_price = float(df.iloc[1]["开盘"])

        for day_idx in range(1, min(len(df), max_hold_days + 2)):
            if day_idx > max_hold_days:
                sell_price = float(df.iloc[day_idx]["收盘"])
                gross = (sell_price - buy_price) / buy_price
                return {
                    "buy_price": buy_price, "sell_price": sell_price,
                    "return_rate": _apply_trade_cost(gross),
                    "gross_return": gross,
                    "hold_days": day_idx, "exit_reason": "到期卖出",
                }

            day_open  = float(df.iloc[day_idx]["开盘"])
            day_high  = float(df.iloc[day_idx]["最高"])
            day_low   = float(df.iloc[day_idx]["最低"])
            day_close = float(df.iloc[day_idx]["收盘"])

            max_return  = (day_high  - buy_price) / buy_price
            min_return  = (day_low   - buy_price) / buy_price
            open_return = (day_open  - buy_price) / buy_price

            if day_idx > 1 and open_return <= stop_loss:
                gross = open_return
                return {"buy_price": buy_price, "sell_price": day_open,
                        "return_rate": _apply_trade_cost(gross, is_stop=True),
                        "gross_return": gross,
                        "hold_days": day_idx, "exit_reason": "开盘止损"}
            if open_return >= stop_profit:
                gross = open_return
                return {"buy_price": buy_price, "sell_price": day_open,
                        "return_rate": _apply_trade_cost(gross),
                        "gross_return": gross,
                        "hold_days": day_idx, "exit_reason": "开盘止盈"}
            if min_return <= stop_loss:
                gross = stop_loss
                return {"buy_price": buy_price,
                        "sell_price": buy_price * (1 + stop_loss),
                        "return_rate": _apply_trade_cost(gross, is_stop=True),
                        "gross_return": gross,
                        "hold_days": day_idx, "exit_reason": "盘中止损"}
            if max_return >= stop_profit:
                gross = stop_profit
                return {"buy_price": buy_price,
                        "sell_price": buy_price * (1 + stop_profit),
                        "return_rate": _apply_trade_cost(gross),
                        "gross_return": gross,
                        "hold_days": day_idx, "exit_reason": "盘中止盈"}

        sell_price = float(df.iloc[-1]["收盘"])
        gross = (sell_price - buy_price) / buy_price
        return {"buy_price": buy_price, "sell_price": sell_price,
                "return_rate": _apply_trade_cost(gross),
                "gross_return": gross,
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

    # P1：加载辅助数据（流通市值 / 席位明细）
    lhb_top10_df   = storage.read("lhb_top10")
    seat_detail_df = storage.read("lhb_seat_detail")

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
            lhb_df=lhb_df,
            trade_date=date_str,
            top_n=top_n,
            min_net_buy=min_net_buy,
            min_tech_score=min_tech_score,
            lhb_top10_df=lhb_top10_df if lhb_top10_df is not None and not lhb_top10_df.empty else None,
            seat_detail_df=seat_detail_df if seat_detail_df is not None and not seat_detail_df.empty else None,
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
    对回测结果进行统计分析（P1-5: 含 t 检验 + Bootstrap 置信区间）

    Returns:
        包含各统计指标的字典，新增：
          t_stat, p_value, is_significant  — 单样本 t 检验
          ci_lower_95, ci_upper_95         — Bootstrap 95% 置信区间
          ir                               — 信息比率
    """
    if df.empty:
        return {}

    returns = df["return_rate"].dropna().values
    total = len(returns)
    win_mask = returns > 0
    win_count = int(win_mask.sum())
    loss_count = total - win_count

    win_rate      = win_count / total
    avg_return    = float(np.mean(returns))
    median_return = float(np.median(returns))
    max_return    = float(np.max(returns))
    min_return    = float(np.min(returns))
    std           = float(np.std(returns, ddof=1)) if total > 1 else 0.0

    avg_win  = float(np.mean(returns[win_mask]))  if win_count  > 0 else 0.0
    avg_loss = float(np.mean(returns[~win_mask])) if loss_count > 0 else 0.0
    pl_ratio = abs(avg_win / avg_loss) if avg_loss != 0 else float("inf")

    sharpe = avg_return / std if std > 0 else 0.0

    # P1-5：单样本 t 检验（H0: 均值收益=0）
    t_stat = p_value = None
    is_significant = False
    try:
        from scipy import stats as _stats
        t_stat, p_value = _stats.ttest_1samp(returns, popmean=0)
        t_stat    = float(t_stat)
        p_value   = float(p_value)
        is_significant = p_value < 0.05
    except ImportError:
        # scipy 未安装时降级：手算 t 统计量
        if total > 1 and std > 0:
            t_stat = float(avg_return / (std / np.sqrt(total)))
            # 简化：无精确 p 值，用 |t| > 2 作为显著性近似
            is_significant = abs(t_stat) > 2.0
        logger.debug("scipy 未安装，t 检验使用近似方法")
    except Exception as e:
        logger.debug(f"t 检验失败: {e}")

    # P1-5：Bootstrap 95% 置信区间（10,000 次重采样）
    ci_lower_95 = ci_upper_95 = None
    try:
        rng = np.random.default_rng(42)
        boot_means = np.array([
            rng.choice(returns, size=total, replace=True).mean()
            for _ in range(10_000)
        ])
        ci_lower_95 = float(np.percentile(boot_means, 2.5))
        ci_upper_95 = float(np.percentile(boot_means, 97.5))
    except Exception as e:
        logger.debug(f"Bootstrap 计算失败: {e}")

    # 信息比率（IR = 均值/标准差，类 Sharpe but 不减无风险利率）
    ir = float(avg_return / std * np.sqrt(252)) if std > 0 else 0.0

    # 最大连续亏损
    max_consec_loss = 0
    cur = 0
    for v in returns:
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
    inst_df = df[df["has_institution"] == True] if "has_institution" in df.columns else pd.DataFrame()

    return {
        "total_trades":         total,
        "win_count":            win_count,
        "loss_count":           int(loss_count),
        "win_rate":             float(win_rate),
        "avg_return":           avg_return,
        "median_return":        median_return,
        "max_return":           max_return,
        "min_return":           min_return,
        "avg_win":              avg_win,
        "avg_loss":             avg_loss,
        "pl_ratio":             float(pl_ratio),
        "sharpe":               float(sharpe),
        "ir":                   ir,
        # P1-5：统计显著性
        "t_stat":               t_stat,
        "p_value":              p_value,
        "is_significant":       is_significant,
        "ci_lower_95":          ci_lower_95,
        "ci_upper_95":          ci_upper_95,
        "max_consecutive_loss": int(max_consec_loss),
        "cumulative_curve":     df_sorted[["trade_date", "cumulative_return"]].to_dict("records"),
        "monthly_stats":        monthly.to_dict("records"),
        "inst_win_rate":        float((inst_df["return_rate"] > 0).mean()) if not inst_df.empty else None,
        "inst_avg_return":      float(inst_df["return_rate"].mean()) if not inst_df.empty else None,
    }


# ============================================================
# P1-6: Walk-Forward 滚动回测
# ============================================================

def walk_forward_backtest(
    start_date: str,
    end_date: str,
    train_months: int = 6,
    test_months: int = 1,
    top_n: int = 3,
    stop_loss: float = -0.05,
    stop_profit: float = 0.08,
    max_hold_days: int = 3,
    min_net_buy: float = 50_000_000,
    min_tech_score: int = 50,
) -> dict:
    """
    P1-6: Walk-Forward 滚动回测框架

    流程：
      第 k 轮：用 [T + k*1月, T + k*1月 + 6月) 的数据作为训练窗口，
               用 [T + k*1月 + 6月, T + k*1月 + 7月) 作为测试窗口，
               记录测试窗口的样本外收益；k 从 0 递增直到 end_date。

    注意：
      本实现「训练窗口」目前仅用于参数验证（未来可在此做因子权重学习）；
      测试窗口使用固定参数运行回测，拼接所有测试结果得到样本外表现。

    Args:
        start_date:    回测起始日期（YYYYMMDD）
        end_date:      回测终止日期（YYYYMMDD）
        train_months:  训练窗口长度（月，默认 6）
        test_months:   测试窗口长度（月，默认 1）
        其余参数同 run_backtest

    Returns:
        dict:
          "oos_results":    样本外全量回测 DataFrame（所有测试窗口拼接）
          "oos_stats":      样本外统计分析（analyze_backtest 结果）
          "window_stats":   每个测试窗口的统计汇总列表
          "total_windows":  滚动窗口数量
    """
    from modules.data_fetcher import load_lhb_detail, fetch_lhb_detail
    from config.settings import HALF_YEAR_START, DATA_END_DATE

    logger.info(f"[Walk-Forward] 开始滚动回测: {start_date} ~ {end_date}")
    logger.info(f"  训练窗口={train_months}月, 测试窗口={test_months}月")

    # 加载全量数据
    lhb_df = load_lhb_detail(start_date, end_date)
    if lhb_df.empty:
        lhb_df = fetch_lhb_detail(start_date, end_date, save=True)
    if lhb_df.empty:
        logger.error("[Walk-Forward] 无龙虎榜数据")
        return {"oos_results": pd.DataFrame(), "oos_stats": {}, "window_stats": [], "total_windows": 0}

    # 加载辅助数据（用于 P1 因子）
    lhb_top10_df = storage.read("lhb_top10")
    seat_detail_df = storage.read("lhb_seat_detail")

    _kline_cache_clear()
    # 预拉 K 线缓存
    all_codes = lhb_df["代码"].astype(str).unique().tolist() if "代码" in lhb_df.columns else []
    prefetch_start = (pd.to_datetime(start_date) - timedelta(days=130)).strftime("%Y%m%d")
    prefetch_end   = (pd.to_datetime(end_date)   + timedelta(days=20)).strftime("%Y%m%d")
    for ci, code in enumerate(all_codes):
        if code not in _KLINE_CACHE:
            _kline_get(code, prefetch_start, prefetch_end)
            if ci % 20 == 0:
                logger.info(f"  [WF prefetch] [{ci+1}/{len(all_codes)}]")
            time.sleep(0.3)
    logger.info(f"[Walk-Forward] K 线缓存完成，共 {len(_KLINE_CACHE)} 只")

    # 构建滚动窗口
    t_start = pd.to_datetime(start_date)
    t_end   = pd.to_datetime(end_date)

    all_oos: list = []
    window_stats: list = []
    window_idx = 0

    # 滑动步长 = test_months 个月
    test_start = t_start + pd.DateOffset(months=train_months)
    while test_start < t_end:
        test_end = min(test_start + pd.DateOffset(months=test_months), t_end)

        ts_str = test_start.strftime("%Y%m%d")
        te_str = test_end.strftime("%Y%m%d")
        window_idx += 1
        logger.info(f"[Walk-Forward] 窗口 {window_idx}: 测试 {ts_str} ~ {te_str}")

        # 过滤测试窗口的 lhb_df
        date_col = "trade_date" if "trade_date" in lhb_df.columns else "上榜日期"
        test_lhb = lhb_df[
            (lhb_df[date_col].astype(str) >= ts_str) &
            (lhb_df[date_col].astype(str) <= te_str)
        ].copy()
        test_dates = sorted(test_lhb[date_col].astype(str).unique())

        window_results = []
        for date_str in test_dates:
            selected = select_stocks_daily(
                lhb_df=lhb_df,
                trade_date=date_str,
                top_n=top_n,
                min_net_buy=min_net_buy,
                min_tech_score=min_tech_score,
                lhb_top10_df=lhb_top10_df if lhb_top10_df is not None and not lhb_top10_df.empty else None,
                seat_detail_df=seat_detail_df if seat_detail_df is not None and not seat_detail_df.empty else None,
            )
            for stock in selected:
                ret = simulate_with_stoploss(
                    stock["code"], date_str,
                    stop_loss=stop_loss,
                    stop_profit=stop_profit,
                    max_hold_days=max_hold_days,
                )
                if ret:
                    window_results.append({
                        "trade_date": date_str,
                        "code": stock["code"],
                        "name": stock.get("name", ""),
                        "window": window_idx,
                        **ret,
                    })

        if window_results:
            wr_df = pd.DataFrame(window_results)
            wstats = analyze_backtest(wr_df)
            window_stats.append({
                "window": window_idx,
                "test_start": ts_str,
                "test_end":   te_str,
                "trades":     wstats.get("total_trades", 0),
                "win_rate":   wstats.get("win_rate", None),
                "avg_return": wstats.get("avg_return", None),
                "p_value":    wstats.get("p_value", None),
                "is_sig":     wstats.get("is_significant", False),
            })
            all_oos.extend(window_results)
            logger.info(f"  窗口 {window_idx} 完成: {len(window_results)} 笔交易")
        else:
            window_stats.append({
                "window": window_idx,
                "test_start": ts_str,
                "test_end":   te_str,
                "trades": 0, "win_rate": None, "avg_return": None,
                "p_value": None, "is_sig": False,
            })

        test_start = test_start + pd.DateOffset(months=test_months)

    oos_df = pd.DataFrame(all_oos) if all_oos else pd.DataFrame()
    oos_stats = analyze_backtest(oos_df) if not oos_df.empty else {}

    logger.info(
        f"[Walk-Forward] 完成: {window_idx} 个窗口, "
        f"样本外总交易 {len(all_oos)} 笔, "
        f"OOS 均值收益 {oos_stats.get('avg_return', 'N/A')}"
    )

    return {
        "oos_results":   oos_df,
        "oos_stats":     oos_stats,
        "window_stats":  window_stats,
        "total_windows": window_idx,
    }


# ============================================================
# P2-4: 凯利公式动态仓位
# ============================================================

def calc_kelly_position(
    backtest_df: pd.DataFrame,
    market_sentiment: dict = None,
    total_capital: float = 100_000.0,
    max_single_position: float = 0.25,
    score_col: str = "final_score",
) -> dict:
    """
    P2-4: 基于凯利公式的动态仓位计算（改进方案 4.2）。

    公式：
      f* = (p × b - q) / b
      实际仓位 = (f* / 2) × 市场环境系数  ← 半凯利，更稳健

    其中：
      p = 历史胜率
      b = 盈亏比（平均盈利 / 平均亏损绝对值）
      q = 1 - p

    市场环境系数（来自 fetch_market_sentiment 的 position_coef）：
      温度 ≥70 → 1.0（全仓系数）
      温度 50~70 → 0.6~1.0
      温度 30~50 → 0.3~0.6
      温度 <30 → 0.0（不交易）

    Args:
        backtest_df:         历史回测结果 DataFrame（含 return_rate 列）
        market_sentiment:    fetch_market_sentiment() 返回的字典，含 temperature/position_coef
        total_capital:       总可用资金（元）
        max_single_position: 单只最大仓位比例（默认 25%）
        score_col:           用于分组的评分列（按高低分分层计算凯利值）

    Returns:
        dict：
          kelly_fraction:  裸凯利值 f*（0~1）
          half_kelly:      半凯利值
          position_coef:   市场环境系数
          recommended_pct: 最终建议仓位比例（半凯利 × 市场系数，上限 max_single_position）
          recommended_amt: 建议买入金额（元）
          win_rate:        历史胜率
          pl_ratio:        历史盈亏比
          temperature:     市场温度计分数
    """
    if backtest_df is None or backtest_df.empty or "return_rate" not in backtest_df.columns:
        return {
            "kelly_fraction": 0.0, "half_kelly": 0.0,
            "position_coef": 0.5,  "recommended_pct": 0.1,
            "recommended_amt": total_capital * 0.1,
            "win_rate": None, "pl_ratio": None,
            "temperature": None,
            "note": "历史数据不足，使用默认保守仓位（10%）",
        }

    returns  = backtest_df["return_rate"].dropna().values
    total    = len(returns)
    win_mask = returns > 0

    p = float(win_mask.sum()) / total if total > 0 else 0.5
    q = 1.0 - p

    win_returns  = returns[win_mask]
    lose_returns = returns[~win_mask]

    avg_win  = float(win_returns.mean())  if len(win_returns)  > 0 else 0.0
    avg_loss = abs(float(lose_returns.mean())) if len(lose_returns) > 0 else 1e-6
    b = avg_win / avg_loss if avg_loss > 0 else 1.0

    # 凯利公式
    kelly_raw = (p * b - q) / b if b > 0 else 0.0
    kelly_raw = max(min(kelly_raw, 1.0), 0.0)   # 截断至 [0, 1]
    half_kelly = kelly_raw / 2.0

    # 市场环境系数（来自 P2-3 温度计）
    pos_coef  = 1.0
    temp_score = None
    if market_sentiment and isinstance(market_sentiment, dict):
        pos_coef   = float(market_sentiment.get("position_coef", 1.0))
        temp_score = market_sentiment.get("temperature", None)

    # 最终建议仓位
    recommended_pct = min(half_kelly * pos_coef, max_single_position)
    recommended_pct = max(recommended_pct, 0.0)
    recommended_amt = total_capital * recommended_pct

    logger.info(
        f"[P2-4 凯利仓位] 胜率={p:.1%}, 盈亏比={b:.2f}, "
        f"f*={kelly_raw:.3f}, 半凯利={half_kelly:.3f}, "
        f"市场系数={pos_coef}, 建议仓位={recommended_pct:.1%}"
    )

    return {
        "kelly_fraction":  round(kelly_raw, 4),
        "half_kelly":      round(half_kelly, 4),
        "position_coef":   round(pos_coef, 2),
        "recommended_pct": round(recommended_pct, 4),
        "recommended_amt": round(recommended_amt, 2),
        "win_rate":        round(p, 4),
        "pl_ratio":        round(b, 4),
        "temperature":     temp_score,
        "total_trades":    int(total),
    }
