"""
DataFeed — 统一数据访问层
封装 K线/ATR/情绪/LHB 数据访问，策略一律通过此层读取数据。
"""
import pandas as pd
import numpy as np
from datetime import datetime
from loguru import logger
from utils.storage import storage


# 进程级 K 线缓存
_KLINE_CACHE: dict = {}


def _normalize_code(stock_code: str) -> str:
    """将任意代码格式统一为 XXXXXX.SZ/SH/BJ"""
    code_clean = str(stock_code).replace(".SZ", "").replace(".SH", "").replace(".BJ", "").zfill(6)
    if code_clean.startswith(("6", "9")):
        return f"{code_clean}.SH"
    elif code_clean.startswith(("0", "3")):
        return f"{code_clean}.SZ"
    else:
        return f"{code_clean}.BJ"


class DataFeed:
    """统一数据访问层，策略通过 ctx.data_feed.* 调用"""

    def __init__(self, cache: dict = None):
        self._cache = cache if cache is not None else _KLINE_CACHE

    # ═══ K 线 ═══

    def kline(self, code: str, start: str, end: str) -> pd.DataFrame:
        """获取日 K 线 DataFrame（列: 日期/开盘/最高/最低/收盘/成交量）"""
        ts_code = _normalize_code(code)
        sd, ed = _norm_date(start), _norm_date(end)

        cached = self._cache.get(ts_code)
        if cached is not None:
            cached_dates = pd.to_datetime(cached["日期"])
            req_start = pd.Timestamp(sd)
            req_end = pd.Timestamp(ed)
            if cached_dates.min().date() <= req_start.date() and cached_dates.max().date() >= req_end.date():
                return cached[(cached_dates >= req_start) & (cached_dates <= req_end)].copy().reset_index(drop=True)

        from sqlalchemy import text
        engine = storage._get_engine()
        with engine.connect() as conn:
            df = pd.read_sql_query(
                text("""
                    SELECT trade_date::date AS "日期", open AS "开盘", high AS "最高",
                           low AS "最低", close AS "收盘", vol AS "成交量"
                    FROM kline_daily
                    WHERE ts_code = :ts AND trade_date BETWEEN :sd AND :ed
                    ORDER BY trade_date
                """),
                conn, params={"ts": ts_code, "sd": sd, "ed": ed}
            )
        if df.empty:
            return df

        # 合并到缓存
        df["日期"] = pd.to_datetime(df["日期"])
        existing = self._cache.get(ts_code)
        if existing is not None:
            self._cache[ts_code] = pd.concat([existing, df]).drop_duplicates(subset="日期").sort_values("日期").reset_index(drop=True)
        else:
            self._cache[ts_code] = df.sort_values("日期").reset_index(drop=True)
        return df.copy()

    def kline_raw(self, code: str, start: str, end: str) -> pd.DataFrame:
        """同 kline 但不使用缓存，直接查 DB"""
        return self.kline(code, start, end)

    # ═══ ATR ═══

    def atr(self, code: str, date: str, period: int = 14) -> float:
        """计算截至 date 日的 ATR(period) 值"""
        from datetime import timedelta
        start = (pd.Timestamp(date) - timedelta(days=period * 3)).strftime("%Y%m%d")
        df = self.kline(code, start, date)
        if len(df) < period + 1:
            return 0.0
        high = df["最高"].values
        low = df["最低"].values
        close = df["收盘"].values
        tr = np.maximum(high[1:] - low[1:],
                        np.maximum(np.abs(high[1:] - close[:-1]),
                                   np.abs(low[1:] - close[:-1])))
        # SMA smoothing
        atr_vals = np.zeros(len(tr))
        atr_vals[period - 1] = tr[:period].mean()
        for i in range(period, len(tr)):
            atr_vals[i] = (atr_vals[i - 1] * (period - 1) + tr[i]) / period
        return float(atr_vals[-1]) if len(atr_vals) > 0 else 0.0

    # ═══ 情绪 ═══

    def sentiment(self, date: str) -> dict | None:
        """读取 market_sentiment 表中最近的记录。

        trend_flip_to_bearish 的语义是【真正的翻转】：前一日 bullish → 今日 bearish。
        前一日缺数据时为 False（保守，不误触发情绪反转 exit）。
        """
        try:
            df = storage.read("market_sentiment")
            if df.empty or "trade_date" not in df.columns:
                return None
            df["trade_date"] = pd.to_datetime(df["trade_date"])
            target = pd.Timestamp(_norm_date(date))
            past = df[df["trade_date"] <= target].sort_values("trade_date")
            if past.empty:
                return None
            row = past.iloc[-1]
            cur_trend = str(row.get("index_trend", "neutral"))
            prev_trend = str(past.iloc[-2].get("index_trend", "")) if len(past) >= 2 else ""
            return {
                "trade_date": str(row.get("trade_date", ""))[:10],
                "temperature": float(row.get("temperature", 50)),
                "index_trend": cur_trend,
                "prev_trend": prev_trend,
                # 真正的翻转：从 bullish 切到 bearish
                "trend_flip_to_bearish": (prev_trend == "bullish" and cur_trend == "bearish"),
                "trade_signal": str(row.get("trade_signal", "normal")),
            }
        except Exception:
            return None

    # ═══ LHB ═══

    def lhb_events(self, code: str, start: str, end: str) -> pd.DataFrame:
        """查询 lhb_stock_daily 表中某只股票的上榜记录"""
        from sqlalchemy import text
        ts_code = _normalize_code(code)
        sd, ed = _norm_date(start), _norm_date(end)
        engine = storage._get_engine()
        with engine.connect() as conn:
            return pd.read_sql_query(
                text("""
                    SELECT trade_date::text, ts_code, close_price, pct_change,
                           l_net, reason, name
                    FROM lhb_stock_daily
                    WHERE ts_code = :ts AND trade_date BETWEEN :sd AND :ed
                    ORDER BY trade_date
                """),
                conn, params={"ts": ts_code, "sd": sd, "ed": ed}
            )

    # ═══ 涨跌停限制 ═══

    def limit_pct(self, code: str) -> float:
        """返回涨跌停限制比例"""
        code_clean = str(code).replace(".SZ", "").replace(".SH", "").replace(".BJ", "").zfill(6)
        if code_clean.startswith("300") or code_clean.startswith("301"):
            return 0.20
        if code_clean.startswith("688"):
            return 0.20
        if code_clean.startswith(("8", "4")):
            return 0.30
        # ST 检测
        try:
            df = self.kline(code, (datetime.now() - pd.Timedelta(days=20)).strftime("%Y%m%d"), datetime.now().strftime("%Y%m%d"))
            if not df.empty and "ST" in str(df.iloc[-1].get("名称", "")):
                return 0.05
        except Exception:
            pass
        return 0.10

    # ═══ 缓存管理 ═══

    def clear_cache(self):
        self._cache.clear()

    def prefetch(self, codes: list[str], start: str, end: str):
        """批量预取 K 线到缓存"""
        from sqlalchemy import text
        if not codes:
            return
        sd, ed = _norm_date(start), _norm_date(end)
        ts_codes = [_normalize_code(c) for c in codes]
        engine = storage._get_engine()
        with engine.connect() as conn:
            df = pd.read_sql_query(
                text("""
                    SELECT ts_code, trade_date::date AS "日期", open AS "开盘", high AS "最高",
                           low AS "最低", close AS "收盘", vol AS "成交量"
                    FROM kline_daily
                    WHERE ts_code = ANY(:codes) AND trade_date BETWEEN :sd AND :ed
                    ORDER BY ts_code, trade_date
                """),
                conn, params={"codes": ts_codes, "sd": sd, "ed": ed}
            )
        if df.empty:
            return
        df["日期"] = pd.to_datetime(df["日期"])
        for ts_code, gdf in df.groupby("ts_code"):
            existing = self._cache.get(ts_code)
            if existing is not None:
                self._cache[ts_code] = pd.concat([existing, gdf]).drop_duplicates(subset="日期").sort_values("日期").reset_index(drop=True)
            else:
                self._cache[ts_code] = gdf.sort_values("日期").reset_index(drop=True)


def _norm_date(d: str) -> str:
    """统一日期格式为 YYYYMMDD"""
    return str(d).replace("-", "")[:8]
