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
from modules.strategies.base import SellSignal

# ============================================================
# 进程级 K 线缓存（回测时避免重复查 DB）
# ============================================================
# 结构: { ts_code: DataFrame(日期, 开盘, 最高, 最低, 收盘, 成交量) }
# key 为 ts_code (如 "000001.SZ")，value 为该股最宽日期范围的 K 线
# 每次 run_backtest 调用 _kline_cache_clear() 重置
_KLINE_CACHE: dict = {}


def _normalize_ts_code(stock_code: str) -> str:
    """将任意股票代码格式统一为 'XXXXXX.SZ/SH/BJ'"""
    code_clean = str(stock_code).replace(".SZ", "").replace(".SH", "").replace(".BJ", "").zfill(6)
    if code_clean.startswith(("6", "9")):
        return f"{code_clean}.SH"
    elif code_clean.startswith(("0", "3")):
        return f"{code_clean}.SZ"
    else:
        return f"{code_clean}.BJ"


def _kline_cache_clear():
    """清空进程级 K 线缓存（每次回测前调用）"""
    _KLINE_CACHE.clear()
    try:
        from modules.market_data import kline_cache_clear
        kline_cache_clear()
    except ImportError:
        pass
    logger.info("K 线缓存已清空")


def _kline_get(stock_code: str, start_dt: str, end_dt: str) -> pd.DataFrame:
    """
    K 线获取 — 从数据库 kline_daily 直读，进程级缓存（按 ts_code）
    """
    ts_code = _normalize_ts_code(stock_code)
    sd = str(start_dt)[:10]
    ed = str(end_dt)[:10]

    # 命中缓存 → 切片返回（统一日期格式 YYYYMMDD 再比较）
    sd_norm = sd.replace("-", "")
    ed_norm = ed.replace("-", "")
    if ts_code in _KLINE_CACHE:
        cached = _KLINE_CACHE[ts_code]
        if "日期" in cached.columns and not cached.empty:
            cached_dates_norm = cached["日期"].astype(str).str.replace("-", "").str[:8]
            cached_min, cached_max = cached_dates_norm.min(), cached_dates_norm.max()
            if sd_norm >= cached_min and ed_norm <= cached_max:
                mask = (cached_dates_norm >= sd_norm) & (cached_dates_norm <= ed_norm)
                return cached[mask].copy()

    # 未命中或范围不够 → 查 DB
    try:
        from utils.storage import storage as _st
        from sqlalchemy import text
        engine = _st._get_engine()

        with engine.connect() as conn:
            df = pd.read_sql_query(
                text("""
                    SELECT trade_date AS "日期", open AS "开盘", high AS "最高",
                           low AS "最低", close AS "收盘", vol AS "成交量"
                    FROM kline_daily
                    WHERE ts_code = :ts AND trade_date BETWEEN :sd AND :ed
                    ORDER BY trade_date ASC
                """),
                conn, params={"ts": ts_code, "sd": sd, "ed": ed}
            )

        if df.empty:
            if ts_code in _KLINE_CACHE:
                cached = _KLINE_CACHE[ts_code]
                if "日期" in cached.columns:
                    mask = cached["日期"].astype(str).str.replace("-", "").str[:8] <= ed_norm
                    return cached[mask].copy()
            return pd.DataFrame()

        # 合并到缓存（保持最宽范围）
        if ts_code in _KLINE_CACHE:
            existing = _KLINE_CACHE[ts_code]
            merged = pd.concat([existing, df]).drop_duplicates(subset=["日期"]).sort_values("日期").reset_index(drop=True)
            _KLINE_CACHE[ts_code] = merged
        else:
            _KLINE_CACHE[ts_code] = df.copy()

        # 按请求范围切片返回（统一格式 YYYYMMDD）
        if "日期" in df.columns:
            dnorm = df["日期"].astype(str).str.replace("-", "").str[:8]
            return df[(dnorm >= sd_norm) & (dnorm <= ed_norm)]
        return df
    except Exception as e:
        logger.debug(f"_kline_get from DB failed ({stock_code}): {e}")

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

from modules.indicators import sma as _sma  # noqa: E402


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
    向下兼容：调用方可用 result["score"] 取总分。

    v2.2 起：委托给 modules/scorer.score_tech()，统一回测与模拟盘评分逻辑。
    - scorer.score_tech 满分 40 → 线性映射到 100 分
    - 保留旧 key：ma_bullish / above_ma20 / above_ma60 / new_high_20d /
                   macd_golden / rsi_val / vol_ratio

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
        df = _kline_get(stock_code, start_dt, trade_date)
        if df.empty or len(df) < 30:
            return _default
        if "日期" in df.columns:
            df = df[df["日期"].astype(str) <= trade_date]
        if len(df) < 30:
            return _default

        # ── 委托给统一评分引擎 ────────────────────────────────────────
        from modules.scorer import score_tech
        res = score_tech(df, trade_date, lianban_count=0)
        # score_tech 满分 40 → 线性映射到 0~100
        raw_score_100 = int(round(res["score"] / 40 * 100))

        # ── 填充旧兼容字段（供 select_stocks_daily 等调用方使用）──────
        close  = df["收盘"].values.astype(float)
        volume = df["成交量"].values.astype(float) if "成交量" in df.columns else None

        ma5   = _sma(close, 5)
        ma10  = _sma(close, 10)
        ma20  = _sma(close, 20)
        ma60  = _sma(close, min(60, len(close)))

        ma_bullish  = (
            not np.isnan(ma5[-1]) and not np.isnan(ma10[-1]) and not np.isnan(ma20[-1])
            and ma5[-1] > ma10[-1] > ma20[-1]
        )
        above_ma20  = not np.isnan(ma20[-1]) and close[-1] > ma20[-1]
        above_ma60  = not np.isnan(ma60[-1]) and close[-1] > ma60[-1]
        new_high_20d = (
            len(close) >= 20
            and close[-1] >= close[-20:].max() * 0.999
        )
        macd_golden = res.get("macd_score", 0) >= 10   # scorer 中金叉=10分
        rsi_val     = res.get("rsi") or 50.0
        vol_ratio   = res.get("volume_ratio") or 1.0

        return {
            "score":        raw_score_100,
            "ma_bullish":   ma_bullish,
            "above_ma20":   above_ma20,
            "above_ma60":   above_ma60,
            "new_high_20d": new_high_20d,
            "macd_golden":  macd_golden,
            "rsi_val":      round(float(rsi_val), 1),
            "vol_ratio":    round(float(vol_ratio), 2),
            # 透传 scorer 的详细分项（可选用）
            "detail":       res.get("detail", {}),
            "gain20":       res.get("gain20"),
            "ma5":          res.get("ma5"),
            "ma10":         res.get("ma10"),
            "ma20":         res.get("ma20"),
        }

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
        "seat_available": False,  # 是否有真正的营业部级别席位明细
    }

    # 方式1：东方财富格式（含营业部列）
    buy_col = None
    sell_col = None
    for c in stock_detail.columns:
        if "买" in c and "营业部" in c:
            buy_col = c
        if "卖" in c and "营业部" in c:
            sell_col = c

    if buy_col:
        result["seat_available"] = True

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
    # 字段名兼容：新表用"龙虎榜买入额/卖出额/净买额"，老表用"买入额/卖出额"
    buy_col = "买入额" if "买入额" in day_df.columns else (
              "龙虎榜买入额" if "龙虎榜买入额" in day_df.columns else None)
    sell_col = "卖出额" if "卖出额" in day_df.columns else (
               "龙虎榜卖出额" if "龙虎榜卖出额" in day_df.columns else None)
    net_col = "净买入额" if "净买入额" in day_df.columns else (
              "龙虎榜净买额" if "龙虎榜净买额" in day_df.columns else None)
    if buy_col:
        agg_dict[buy_col] = "sum"
    if sell_col:
        agg_dict[sell_col] = "sum"
    if net_col:
        agg_dict[net_col] = "sum"
    # 新浪格式：成交额(万元)列
    if "成交额(万元)" in day_df.columns:
        agg_dict["成交额(万元)"] = "sum"

    if not agg_dict:
        return []

    stock_agg = day_df.groupby(code_col).agg(agg_dict).reset_index()

    # 统一净买入额列：优先用 net_col 直接给的，再用 buy-sell，再退化
    if net_col and net_col in stock_agg.columns:
        stock_agg["净买入额"] = stock_agg[net_col]
    elif buy_col and sell_col and buy_col in stock_agg.columns and sell_col in stock_agg.columns:
        stock_agg["净买入额"] = stock_agg[buy_col] - stock_agg[sell_col]
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

    # 净买入额过滤（无净买入数据时跳过此过滤）
    effective_min_net_buy = min_net_buy if (buy_col or net_col) else 0
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
        # DB 直读不再需要 AKShare 限流等待

    if not qualified_stocks:
        return []
    
    # 综合评分（席位缺失时动态调整权重）
    max_net = max(s["net_buy"] for s in qualified_stocks)
    min_net = min(s["net_buy"] for s in qualified_stocks)
    # 检查是否有任何股票的席位数据可用
    any_seat_available = any(s.get("seat_available", False) for s in qualified_stocks)

    for stock in qualified_stocks:
        net_score = (
            (stock["net_buy"] - min_net) / (max_net - min_net) * 100
            if max_net > min_net else 50
        )
        seat_ok = stock.get("seat_available", False) and any_seat_available
        if seat_ok:
            # 完整三维评分：净买入25% + 席位35% + 技术面30% + 涨幅10%
            stock["final_score"] = (
                net_score * 0.25
                + stock["seat_score"] * 0.35
                + stock["tech_score"] * 0.30
                + max(0, 30 - stock["gain_20d"] * 100) * 0.10
            )
        else:
            # 席位缺失：净买入40% + 技术面50% + 涨幅10%（动态重分配35%席位权重）
            stock["final_score"] = (
                net_score * 0.40
                + stock.get("inst_bonus", 0) * 0.10
                + stock["tech_score"] * 0.40
                + max(0, 30 - stock["gain_20d"] * 100) * 0.10
            )
            stock["seat_score_note"] = "席位明细缺失，权重已动态调整"

    qualified_stocks.sort(key=lambda x: x["final_score"], reverse=True)
    return qualified_stocks[:top_n]


# ============================================================
# 涨跌停判断
# ============================================================

def _get_limit_pct(code: str, name: str = "") -> float:
    """返回涨停幅度（小数），覆盖主板/双创/ST/北交所"""
    c = str(code).lstrip("'").strip()
    n = str(name).upper()
    if "ST" in n:
        return 0.05
    if c.startswith(("300", "301")):
        return 0.20
    if c.startswith("688"):
        return 0.20
    if c.startswith(("8", "4")):
        return 0.30
    return 0.10


def _is_one_word_limit_up(row, prev_close: float, limit_pct: float, eps: float = 0.001) -> bool:
    """判断是否为 T+1 一字涨停（开盘=最高=最低≈涨停价），实际无法买入"""
    limit_price = prev_close * (1 + limit_pct)
    op = float(row["开盘"])
    hi = float(row["最高"])
    lo = float(row["最低"])
    at_limit = op >= limit_price * (1 - eps)
    flat_line = abs(op - hi) < 1e-4 and abs(op - lo) < 1e-4
    return at_limit and flat_line


def _make_skip(reason: str) -> dict:
    return {"return_rate": None, "hold_days": 0, "exit_reason": reason, "skipped": True}


# ============================================================
# 止盈止损模拟
# ============================================================

def _default_sell_check(position: dict, stop_loss: float, stop_profit: float, max_hold_days: int) -> dict | None:
    """回退止盈止损判断（无 strategy 时使用），保持原有优先级"""
    if position["hold_days"] > 1 and position["open_return"] <= stop_loss:
        return {"reason": "开盘止损", "price_type": "open"}
    if position["open_return"] >= stop_profit:
        return {"reason": "开盘止盈", "price_type": "open"}
    if position["min_return"] <= stop_loss:
        return {"reason": "盘中止损", "price_type": "close"}
    if position["max_return"] >= stop_profit:
        return {"reason": "盘中止盈", "price_type": "close"}
    if position["hold_days"] >= max_hold_days:
        return {"reason": "到期卖出", "price_type": "close"}
    return None


def simulate_with_stoploss(
    stock_code: str,
    trade_date: str,
    stop_loss: float = -0.05,
    stop_profit: float = 0.08,
    max_hold_days: int = 3,
    roundtrip_cost: float = 0.0025,
    max_buy_gap: float = 0.095,
    filter_one_word: bool = True,
    strategy=None,
) -> Optional[dict]:
    """
    模拟带止盈止损的交易（委托给 modules.backtest.executor.Executor）

    Args:
        stop_loss / stop_profit / max_hold_days: 回退模式参数（strategy=None 时使用）
        max_buy_gap: T+1 开盘最大允许高开幅度
        filter_one_word: 是否过滤 T+1 一字涨停
        strategy: BaseStrategy 实例，提供 should_sell() 判断

    Returns:
        包含交易结果的字典（skipped=True 表示未实际成交）
    """
    from modules.backtest.executor import Executor, BacktestContext
    from modules.backtest.data_feed import DataFeed

    # 若传入 strategy，从 strategy.params 提取参数
    if strategy is not None:
        stop_loss = strategy.params.get("stop_loss", stop_loss)
        stop_profit = strategy.params.get("stop_profit", stop_profit)
        max_hold_days = strategy.params.get("max_hold_days", max_hold_days)
        max_buy_gap = strategy.params.get("max_buy_gap", max_buy_gap)
        filter_one_word = strategy.params.get("filter_one_word", filter_one_word)

    feed = DataFeed()
    executor = Executor(feed, roundtrip_cost=roundtrip_cost)
    ctx = BacktestContext(
        data_feed=feed, code=stock_code,
        strategy_params={
            "stop_loss": stop_loss, "stop_profit": stop_profit,
            "max_hold_days": max_hold_days, "max_buy_gap": max_buy_gap,
            "filter_one_word": filter_one_word,
        }
    )
    return executor.execute_trade(stock_code, trade_date, ctx, strategy=strategy)


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
    roundtrip_cost: float = 0.0025,
    filter_one_word: bool = True,
    max_buy_gap: float = 0.095,
    strategy=None,  # BaseStrategy 实例（可选）
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
    
    # ── 预拉 K 线缓存（prefetch batch）────────────────────────
    # 1 次 SQL 拉取所有候选股 K 线 → 按 ts_code 分组写入进程缓存
    all_codes = lhb_df["代码"].astype(str).unique().tolist() if "代码" in lhb_df.columns else []
    all_ts_codes = [_normalize_ts_code(c) for c in all_codes]
    prefetch_start = (pd.to_datetime(start_date) - timedelta(days=130)).strftime("%Y%m%d")
    prefetch_end   = (pd.to_datetime(end_date)   + timedelta(days=20)).strftime("%Y%m%d")
    logger.info(f"预拉 K 线缓存：{len(all_ts_codes)} 只股票，范围 {prefetch_start}~{prefetch_end}")

    if all_ts_codes:
        try:
            from utils.storage import storage as _st
            from sqlalchemy import text
            engine = _st._get_engine()
            # 分批查询避免 IN 列表过长（每批 500 只）
            batch_size = 500
            total_fetched = 0
            for batch_start in range(0, len(all_ts_codes), batch_size):
                batch_codes = all_ts_codes[batch_start:batch_start + batch_size]
                with engine.connect() as conn:
                    df_batch = pd.read_sql_query(
                        text("""
                            SELECT ts_code, trade_date AS "日期", open AS "开盘", high AS "最高",
                                   low AS "最低", close AS "收盘", vol AS "成交量"
                            FROM kline_daily
                            WHERE ts_code = ANY(:codes) AND trade_date BETWEEN :sd AND :ed
                            ORDER BY ts_code, trade_date ASC
                        """),
                        conn, params={
                            "codes": batch_codes,
                            "sd": prefetch_start,
                            "ed": prefetch_end,
                        }
                    )
                for ts_code, grp in df_batch.groupby("ts_code"):
                    grip_sorted = grp.sort_values("日期").reset_index(drop=True)
                    if ts_code in _KLINE_CACHE:
                        merged = pd.concat([_KLINE_CACHE[ts_code], grip_sorted]).drop_duplicates(subset=["日期"]).sort_values("日期").reset_index(drop=True)
                        _KLINE_CACHE[ts_code] = merged
                    else:
                        _KLINE_CACHE[ts_code] = grip_sorted
                total_fetched += len(df_batch)
                logger.info(f"  prefetch batch [{batch_start//batch_size + 1}/{(len(all_ts_codes)-1)//batch_size + 1}]: {len(batch_codes)} 只 → {len(df_batch)} 行")
            logger.info(f"K 线预拉完成，共缓存 {len(_KLINE_CACHE)} 只，{total_fetched} 行")
        except Exception as e:
            logger.warning(f"批量预拉 K 线失败，回退到逐只查询: {e}")
            for ci, code in enumerate(all_codes):
                ts_code = _normalize_ts_code(code)
                if ts_code not in _KLINE_CACHE:
                    _kline_get(code, prefetch_start, prefetch_end)
                if ci % 20 == 0:
                    logger.info(f"  prefetch [{ci+1}/{len(all_codes)}] {code}")
            logger.info(f"K 线预拉完成（fallback），共缓存 {len(_KLINE_CACHE)} 只")

    results = []
    skip_stats = {}  # 统计跳过原因
    total_dates = len(trade_dates)

    for i, date_str in enumerate(trade_dates):
        logger.info(f"[{i+1}/{total_dates}] 回测日期: {date_str}")

        # 选股：优先使用策略，回退到旧路径
        if strategy is not None:
            buy_orders = strategy.select(date_str, lhb_df, _KLINE_CACHE)
            selected = [{"code": o.code, **(o.meta if hasattr(o, 'meta') else {})} for o in buy_orders]
        else:
            stocks = select_stocks_daily(
                lhb_df, date_str,
                top_n=top_n,
                min_net_buy=min_net_buy,
                min_tech_score=min_tech_score,
            )
            selected = [{"code": s["code"], **s} for s in stocks]

        if not selected:
            continue

        for stock in selected:
            if use_stoploss:
                ret = simulate_with_stoploss(
                    stock["code"], date_str,
                    stop_loss=stop_loss,
                    stop_profit=stop_profit,
                    max_hold_days=max_hold_days,
                    roundtrip_cost=roundtrip_cost,
                    filter_one_word=filter_one_word,
                    max_buy_gap=max_buy_gap,
                    strategy=strategy,
                )
            else:
                from modules.data_fetcher import get_next_day_return
                ret = get_next_day_return(stock["code"], date_str, hold_days)

            if ret is None:
                continue
            if ret.get("skipped"):
                reason = ret.get("exit_reason", "未知")
                skip_stats[reason] = skip_stats.get(reason, 0) + 1
                continue

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

    if skip_stats:
        logger.info(f"跳过交易统计: {skip_stats}")
        print(f"\n📊 成交约束过滤统计:")
        for reason, cnt in sorted(skip_stats.items(), key=lambda x: -x[1]):
            print(f"  {reason}: {cnt} 笔")
    
    results_df = pd.DataFrame(results)

    if not results_df.empty and save:
        import uuid as _uuid
        from datetime import datetime as _dt
        run_id = _uuid.uuid4().hex[:12]
        strategy_name = strategy.name if strategy else "legacy"
        run_type = "signal_backtest"
        now = _dt.now().strftime("%Y-%m-%d %H:%M:%S")

        results_df["run_id"] = run_id
        results_df["run_type"] = run_type
        results_df["strategy_name"] = strategy_name
        results_df["created_at"] = now

        storage.write(results_df, "backtest_results", mode="append")

        # 记录 run 元信息
        run_meta = pd.DataFrame([{
            "run_id": run_id, "run_type": run_type,
            "strategy_name": strategy_name,
            "start_date": start_date, "end_date": end_date,
            "trade_count": len(results_df),
            "created_at": now,
        }])
        storage.write(run_meta, "backtest_runs", mode="append")
        logger.info(f"回测结果保存完成 run={run_id} 共 {len(results_df)} 条")

    return results_df


# ============================================================
# 统计分析
# ============================================================

def analyze_backtest(df: pd.DataFrame) -> dict:
    """
    对回测结果进行统计分析（委托给 modules.backtest.metrics.analyze）
    """
    from modules.backtest.metrics import analyze as _analyze
    return _analyze(df)
