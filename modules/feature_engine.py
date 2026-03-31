"""
量化特征计算引擎
================
功能：
  1. 从新浪日K计算龙虎榜候选股的技术指标
  2. 输出标准化的 lhb_features 表行（每日一次，盘后运行）
  3. 与 daily_update.py Step 8 对接

依赖：
  - akshare（新浪日K接口，VPS上稳定可用）
  - utils/storage.py（写 lhb_features 表，PG/Excel 双模式兼容）
  - utils/retry.py（safe_akshare_call 重试保护）

数据来源说明：
  - K线数据：ak.stock_zh_a_daily(symbol, adjust="qfq")   ← 新浪前复权日K
  - 量比代理：从连续日 K 线获取换手率计算近5日均换手，彻底解决量比依赖东财接口及龙虎榜历史缺失的问题
  - 板块数据：Phase 2 实现，本文件预留 null 占位

特征表主键：(trade_date, code)
"""

import time
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from loguru import logger

import akshare as ak

from utils.storage import storage
from utils.retry import safe_akshare_call


# ============================================================
# 低级数学计算（纯 numpy，无外部依赖）
# ============================================================

def _sma(arr: np.ndarray, n: int) -> np.ndarray:
    """简单移动平均（与 analyzer.py 对齐，结尾对齐）"""
    result = np.full(len(arr), np.nan)
    if len(arr) < n:
        return result
    for i in range(n - 1, len(arr)):
        result[i] = arr[i - n + 1: i + 1].mean()
    return result


def _ema(arr: np.ndarray, n: int) -> np.ndarray:
    """指数移动平均（Wilder 风格起始值）"""
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


def _rsi_wilder(close: np.ndarray, period: int = 14) -> np.ndarray:
    """
    Wilder RSI（与主流 RSI 指标一致）
    Returns ndarray，对应 close 的 RSI 值，前 period 行为 nan
    """
    result = np.full(len(close), np.nan)
    if len(close) < period + 1:
        return result
    delta = np.diff(close.astype(float))
    gains = np.where(delta > 0, delta, 0.0)
    losses = np.where(delta < 0, -delta, 0.0)
    avg_gain = float(gains[:period].mean())
    avg_loss = float(losses[:period].mean())
    for i in range(period, len(close)):
        idx = i - 1
        avg_gain = (avg_gain * (period - 1) + gains[idx]) / period
        avg_loss = (avg_loss * (period - 1) + losses[idx]) / period
        rs = avg_gain / avg_loss if avg_loss > 0 else 1e9
        result[i] = 100.0 - 100.0 / (1.0 + rs)
    return result


def _atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, n: int = 14) -> np.ndarray:
    """
    ATR（Average True Range），使用 SMA 平滑
    """
    result = np.full(len(close), np.nan)
    if len(close) < n + 1:
        return result
    prev_close = np.roll(close, 1)
    prev_close[0] = close[0]
    tr = np.maximum(
        high - low,
        np.maximum(np.abs(high - prev_close), np.abs(low - prev_close)),
    )
    atr_arr = _sma(tr, n)
    return atr_arr


def _macd(close: np.ndarray, fast: int = 12, slow: int = 26, sig: int = 9):
    """
    MACD (DIF, DEA, HIST=Bar)
    Returns: (dif, dea, hist) 均为 ndarray
    """
    ema_fast = _ema(close, fast)
    ema_slow = _ema(close, slow)
    dif = ema_fast - ema_slow
    dea = _ema(dif, sig)
    hist = (dif - dea) * 2
    return dif, dea, hist


# ============================================================
# K 线数据获取
# ============================================================

def fetch_kline_for_features(
    code: str,
    trade_date: str,
    lookback: int = 90,
) -> pd.DataFrame:
    """
    拉取 trade_date 前 lookback 个自然日的日K（前复权，新浪接口）

    Args:
        code:       6位股票代码（如 '600519'）
        trade_date: YYYYMMDD
        lookback:   向前取多少自然日，默认90（约60个交易日，足够计算所有技术指标）

    Returns:
        DataFrame 含 [日期, 开盘, 最高, 最低, 收盘, 成交量]，按日期升序
        失败时返回空 DataFrame
    """
    code = str(code).zfill(6)
    prefix = "sh" if code.startswith(("6", "9")) else "sz"
    start_dt = (pd.Timestamp(trade_date) - pd.Timedelta(days=lookback + 20)).strftime("%Y%m%d")
    end_dt   = trade_date  # 只取到上榜日当天

    try:
        raw = safe_akshare_call(
            ak.stock_zh_a_daily,
            symbol=f"{prefix}{code}",
            adjust="qfq",
            start_date=start_dt,
            end_date=end_dt,
            max_retries=3,
            base_delay=2.0,
            fallback=None,
        )
        if raw is None or len(raw) < 5:
            logger.debug(f"[feature] {code} K线不足5行，跳过")
            return pd.DataFrame()

        raw = raw.rename(columns={
            "date": "日期", "open": "开盘", "high": "最高",
            "low": "最低", "close": "收盘", "volume": "成交量",
            "turnover": "换手率"
        })
        need_cols = ["日期", "开盘", "最高", "最低", "收盘", "成交量", "换手率"]
        raw = raw[[c for c in need_cols if c in raw.columns]].sort_values("日期").reset_index(drop=True)
        return raw

    except Exception as e:
        logger.warning(f"[feature] {code} 拉取K线失败: {type(e).__name__}: {e}")
        return pd.DataFrame()


# ============================================================
# 单股特征计算
# ============================================================

def calc_features_for_stock(
    code: str,
    trade_date: str,
    kline_df: pd.DataFrame,
    lhb_row: pd.Series = None,
    lhb_top10_df: pd.DataFrame = None,
) -> dict:
    """
    计算一只股票在 trade_date 的全部 Phase 1 特征

    Args:
        code:           6位股票代码
        trade_date:     YYYYMMDD（上榜日）
        kline_df:       K线数据（fetch_kline_for_features 返回）
        lhb_row:        lhb_top10 中该股当日的行（Series），用于取换手率等字段
        lhb_top10_df:   完整 lhb_top10 表（用于计算近5日均换手率 → 量比代理）

    Returns:
        dict（一行 lhb_features 记录），包含所有字段；失败字段为 None
    """
    row: dict = {
        "trade_date": trade_date,
        "code": str(code).zfill(6),
        "risk_data_missing": True,
        "data_source": "sina_daily_qfq",
        "calc_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }

    # ── 检查 K 线有效性 ──────────────────────────────────────
    if kline_df is None or kline_df.empty:
        logger.debug(f"[feature] {code} K线为空，仅写入 risk_data_missing")
        return row

    # 截取到 trade_date 当天（避免未来数据）
    df = kline_df[kline_df["日期"].astype(str) <= trade_date].copy()
    if len(df) < 10:
        logger.debug(f"[feature] {code} 截取后K线不足10行（{len(df)}）")
        return row

    row["risk_data_missing"] = False

    close  = df["收盘"].astype(float).values
    high   = df["最高"].astype(float).values
    low    = df["最低"].astype(float).values
    open_  = df["开盘"].astype(float).values
    volume = df["成交量"].astype(float).values if "成交量" in df.columns else np.full(len(close), np.nan)

    last = -1  # 最后一行 = trade_date 当日

    # ── 基础价量 ────────────────────────────────────────────
    row["close"]       = round(float(close[last]), 3)
    row["open_price"]  = round(float(open_[last]), 3)
    row["high_price"]  = round(float(high[last]), 3)
    row["low_price"]   = round(float(low[last]), 3)
    row["volume"]      = round(float(volume[last]), 0) if not np.isnan(volume[last]) else None

    # ── 振幅与K线质量 ────────────────────────────────────────
    prev_close = float(close[-2]) if len(close) >= 2 else float(close[0])
    total_range = float(high[last]) - float(low[last])

    if prev_close > 0:
        row["amplitude"] = round((high[last] - low[last]) / prev_close * 100, 2)
    else:
        row["amplitude"] = None

    if total_range > 0:
        row["close_strength"]     = round((close[last] - low[last]) / total_range * 100, 2)
        upper_shadow              = float(high[last]) - max(float(close[last]), float(open_[last]))
        lower_shadow              = min(float(close[last]), float(open_[last])) - float(low[last])
        row["upper_shadow_ratio"] = round(upper_shadow / total_range * 100, 2)
        row["lower_shadow_ratio"] = round(lower_shadow / total_range * 100, 2)
        body                      = abs(float(close[last]) - float(open_[last]))
        row["is_long_upper_shadow"]    = bool(upper_shadow > body * 1.5) if body > 0 else False
        row["is_high_open_low_close"]  = bool(float(open_[last]) > prev_close * 1.01 and float(close[last]) < float(open_[last]))
    else:
        row["close_strength"] = None
        row["upper_shadow_ratio"] = None
        row["lower_shadow_ratio"] = None
        row["is_long_upper_shadow"] = False
        row["is_high_open_low_close"] = False

    # 近5日平均振幅
    if len(close) >= 6 and prev_close > 0:
        amp_arr = []
        for i in range(max(0, len(close)-5), len(close)):
            pc = float(close[i-1]) if i > 0 else float(close[0])
            if pc > 0:
                amp_arr.append((float(high[i]) - float(low[i])) / pc * 100)
        row["amp5d_avg"] = round(float(np.mean(amp_arr)), 2) if amp_arr else None
    else:
        row["amp5d_avg"] = None

    # ── 均线系列 ────────────────────────────────────────────
    def _safe_sma(n):
        arr = _sma(close, n)
        v = arr[last]
        return round(float(v), 3) if not np.isnan(v) else None

    def _safe_sma_prev(n):
        """前一日SMA，用于判断方向"""
        arr = _sma(close, n)
        v = arr[-2] if len(arr) >= 2 else np.nan
        return float(v) if not np.isnan(v) else None

    ma5  = _safe_sma(5)
    ma10 = _safe_sma(10)
    ma20 = _safe_sma(20)
    ma30 = _safe_sma(30)
    ma60 = _safe_sma(60)

    row["ma5"]  = ma5
    row["ma10"] = ma10
    row["ma20"] = ma20
    row["ma30"] = ma30
    row["ma60"] = ma60

    # 价格与均线偏离%
    cur = float(close[last])
    row["price_vs_ma5"]  = round((cur / ma5  - 1) * 100, 2) if ma5  else None
    row["price_vs_ma10"] = round((cur / ma10 - 1) * 100, 2) if ma10 else None
    row["price_vs_ma20"] = round((cur / ma20 - 1) * 100, 2) if ma20 else None
    row["price_vs_ma30"] = round((cur / ma30 - 1) * 100, 2) if ma30 else None
    row["price_vs_ma60"] = round((cur / ma60 - 1) * 100, 2) if ma60 else None

    # 多头排列
    row["ma_bull_3"] = bool(ma5 and ma10 and ma20 and ma5 > ma10 > ma20)
    row["ma_bull_4"] = bool(ma5 and ma10 and ma20 and ma30 and ma5 > ma10 > ma20 > ma30)

    # 均线方向（与前一日比较）
    row["ma5_up"]  = bool(_safe_sma_prev(5)  and ma5  and ma5  > _safe_sma_prev(5))
    row["ma10_up"] = bool(_safe_sma_prev(10) and ma10 and ma10 > _safe_sma_prev(10))
    row["ma20_up"] = bool(_safe_sma_prev(20) and ma20 and ma20 > _safe_sma_prev(20))
    row["ma30_up"] = bool(_safe_sma_prev(30) and ma30 and ma30 > _safe_sma_prev(30))

    # ── MACD ────────────────────────────────────────────────
    if len(close) >= 35:
        dif_arr, dea_arr, hist_arr = _macd(close)
        dif_v  = float(dif_arr[last])  if not np.isnan(dif_arr[last])  else None
        dea_v  = float(dea_arr[last])  if not np.isnan(dea_arr[last])  else None
        hist_v = float(hist_arr[last]) if not np.isnan(hist_arr[last]) else None
        row["dif"]       = round(dif_v,  4) if dif_v  is not None else None
        row["dea"]       = round(dea_v,  4) if dea_v  is not None else None
        row["macd_hist"] = round(hist_v, 4) if hist_v is not None else None

        valid_hist = hist_arr[~np.isnan(hist_arr)]
        if len(valid_hist) >= 2:
            row["macd_golden_cross"] = bool(valid_hist[-2] < 0 <= valid_hist[-1])
            row["macd_dead_cross"]   = bool(valid_hist[-2] > 0 >= valid_hist[-1])
        else:
            row["macd_golden_cross"] = False
            row["macd_dead_cross"]   = False
    else:
        row["dif"] = row["dea"] = row["macd_hist"] = None
        row["macd_golden_cross"] = row["macd_dead_cross"] = False

    # ── RSI ─────────────────────────────────────────────────
    rsi6_arr  = _rsi_wilder(close, 6)
    rsi12_arr = _rsi_wilder(close, 12)
    row["rsi6"]  = round(float(rsi6_arr[last]),  1) if not np.isnan(rsi6_arr[last])  else None
    row["rsi12"] = round(float(rsi12_arr[last]), 1) if not np.isnan(rsi12_arr[last]) else None

    # ── ATR ─────────────────────────────────────────────────
    atr_arr = _atr(high, low, close, 14)
    row["atr14"] = round(float(atr_arr[last]), 3) if not np.isnan(atr_arr[last]) else None

    # ── 是否创新高 ───────────────────────────────────────────
    for window in [10, 20, 30, 60]:
        key = f"high_{window}d"
        if len(close) >= window:
            row[key] = bool(close[last] >= np.max(close[-window:]))
        else:
            row[key] = False

    # ── 量能 ────────────────────────────────────────────────
    if not np.all(np.isnan(volume)) and len(volume) >= 20:
        vol5  = volume[-5:].mean()
        vol20 = volume[-20:].mean()
        row["vol_ratio_5_20"] = round(float(vol5 / vol20), 3) if vol20 > 0 else None
        row["is_vol_up"] = bool(
            close[last] > (close[-2] if len(close) >= 2 else close[last]) 
            and not np.isnan(volume[last]) and volume[last] > vol5
        )
    else:
        row["vol_ratio_5_20"] = None
        row["is_vol_up"] = False

    # ── 量比（降级计算）─────────────────────────────────────
    # 真实量比需要当日分时数据，VPS 上接口不稳定 → 使用代理量比
    row["vol_ratio"] = None  # 暂时 null，Phase 2 补充实时接口

    # 代理量比 = 当日换手率 / 近5日均换手率（从连续日 K 线获取，不依赖东财历史和龙虎榜记录）
    vol_ratio_proxy = None
    vol_ratio_source = "null"

    if "换手率" in kline_df.columns and len(kline_df) >= 6:
        try:
            # 换手率转化为 float Series
            turnovers = pd.to_numeric(kline_df["换手率"], errors="coerce").fillna(0)
            today_turnover = float(turnovers.iloc[-1])
            past_5_turnovers = turnovers.iloc[-6:-1]
            avg_5_turnover = float(past_5_turnovers.mean())
            
            if avg_5_turnover > 0 and today_turnover > 0:
                vol_ratio_proxy = round(today_turnover / avg_5_turnover, 3)
                vol_ratio_source = "proxy"
        except Exception as e:
            logger.debug(f"[feature] {code} 量比代理计算失败: {e}")

    row["vol_ratio_proxy"]  = vol_ratio_proxy
    row["vol_ratio_source"] = vol_ratio_source

    # ── 风险标签 ────────────────────────────────────────────
    row["risk_long_upper_shadow"]  = bool(row.get("is_long_upper_shadow", False))
    row["risk_abnormal_amplitude"] = bool(
        row.get("amplitude") is not None and row["amplitude"] > 10.0
    )
    row["risk_high_vol"] = bool(
        row.get("high_60d", False) and row.get("is_vol_up", False)
    )

    return row


# ============================================================
# 批量计算（当日所有候选股）
# ============================================================

def calc_features_batch(
    codes: list,
    trade_date: str,
    lhb_top10_df: pd.DataFrame = None,
    sleep_between: float = 0.6,
) -> pd.DataFrame:
    """
    批量计算当日所有候选股的特征

    Args:
        codes:          股票代码列表
        trade_date:     YYYYMMDD
        lhb_top10_df:   完整 lhb_top10，用于量比代理计算
        sleep_between:  每只股票拉K线后的休眠时间（保护 API）

    Returns:
        DataFrame，行数 = len(codes)（失败的股票仅有 risk_data_missing=True）
    """
    rows = []
    total = len(codes)

    for i, code in enumerate(codes):
        code = str(code).zfill(6)
        try:
            # 取当日 lhb_top10 行（用于换手率等字段）
            lhb_row = None
            if lhb_top10_df is not None and not lhb_top10_df.empty:
                mask = (
                    (lhb_top10_df["代码"].astype(str).str.zfill(6) == code) &
                    (lhb_top10_df["trade_date"].astype(str) == trade_date)
                )
                sub = lhb_top10_df[mask]
                if not sub.empty:
                    lhb_row = sub.iloc[0]

            # 拉 K 线
            kline = fetch_kline_for_features(code, trade_date)

            # 计算特征
            feat = calc_features_for_stock(
                code=code,
                trade_date=trade_date,
                kline_df=kline,
                lhb_row=lhb_row,
                lhb_top10_df=lhb_top10_df,
            )
            rows.append(feat)

            if (i + 1) % 5 == 0 or i == total - 1:
                logger.info(f"  [feature] [{i+1}/{total}] {code} 完成")

        except Exception as e:
            logger.warning(f"  [feature] {code} 异常: {type(e).__name__}: {e}")
            rows.append({
                "trade_date": trade_date,
                "code": code,
                "risk_data_missing": True,
                "data_source": "error",
                "calc_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            })

        if i < total - 1:
            time.sleep(sleep_between)

    return pd.DataFrame(rows)


# ============================================================
# 主入口：供 daily_update.py Step 8 调用
# ============================================================

def run_feature_update(
    trade_date: str,
    force: bool = False,
    sleep_between: float = 0.6,
) -> dict:
    """
    Step 8 主入口：计算指定日期的量化特征并写入 lhb_features

    幂等：若 lhb_features 中已有该日数据，跳过已计算的 code，
    仅补充缺失的 code（增量模式）。force=True 时强制重算所有。

    Args:
        trade_date:     YYYYMMDD
        force:          是否强制重算（覆盖已有数据）
        sleep_between:  拉 K 线间隔（秒）

    Returns:
        {"success": int, "skip": int, "error": int, "total": int}
    """
    result = {"success": 0, "skip": 0, "error": 0, "total": 0}

    # ── Step 1: 从 lhb_top10 取当日候选股 ───────────────────
    try:
        top10_df = storage.read("lhb_top10")
    except Exception as e:
        logger.error(f"[feature] 读取 lhb_top10 失败: {e}")
        return result

    if top10_df.empty:
        logger.warning("[feature] lhb_top10 为空，跳过特征计算")
        return result

    day_df = top10_df[top10_df["trade_date"].astype(str) == trade_date]
    if day_df.empty:
        logger.warning(f"[feature] {trade_date} 无 lhb_top10 数据（非交易日或未更新）")
        return result

    all_codes = day_df["代码"].astype(str).str.zfill(6).unique().tolist()
    result["total"] = len(all_codes)
    logger.info(f"[feature] {trade_date} 候选股 {len(all_codes)} 只: {all_codes[:5]}...")

    # ── Step 2: 幂等检查（非 force 模式）───────────────────
    pending_codes = all_codes
    if not force:
        try:
            existing = storage.query_by_date("lhb_features", trade_date)
            if not existing.empty and "code" in existing.columns:
                already = set(existing["code"].astype(str).str.zfill(6))
                pending_codes = [c for c in all_codes if c not in already]
                result["skip"] = len(all_codes) - len(pending_codes)
                if result["skip"] > 0:
                    logger.info(f"[feature] 跳过已有数据 {result['skip']} 只，需计算 {len(pending_codes)} 只")
        except Exception as e:
            logger.warning(f"[feature] 幂等检查失败（继续全量计算）: {e}")

    if not pending_codes:
        logger.info(f"[feature] {trade_date} 所有股票已有特征数据，跳过")
        return result

    # ── Step 3: 批量计算 ────────────────────────────────────
    logger.info(f"[feature] 开始计算 {len(pending_codes)} 只股票的量化特征...")
    feat_df = calc_features_batch(
        codes=pending_codes,
        trade_date=trade_date,
        lhb_top10_df=top10_df,
        sleep_between=sleep_between,
    )

    if feat_df.empty:
        logger.warning("[feature] 批量计算结果为空")
        return result

    # ── Step 4: 写库 ────────────────────────────────────────
    success_cnt = int((~feat_df.get("risk_data_missing", pd.Series([True] * len(feat_df)))).sum())
    error_cnt   = len(feat_df) - success_cnt
    result["success"] = success_cnt
    result["error"]   = error_cnt

    try:
        storage.write(feat_df, "lhb_features", mode="append")
        logger.info(
            f"[feature] {trade_date} 写库完成 | "
            f"成功={success_cnt} 跳过={result['skip']} 失败={error_cnt} "
            f"总={result['total']}"
        )
    except Exception as e:
        logger.error(f"[feature] 写库失败: {e}")
        result["error"] = len(feat_df)
        result["success"] = 0

    return result
