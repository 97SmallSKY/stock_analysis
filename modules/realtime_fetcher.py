"""
实时行情获取模块（三级兜底体系）
================================
Level 1: 券商 API（QMT/PTrade）—— 当前项目不接真实券商，跳过
Level 2: AKShare stock_zh_a_spot_em() —— 主要使用
Level 3: 新浪财经 HTTP 接口（hq.sinajs.cn）—— 兜底

量比计算（当接口不返回量比时自算）：
  量比 = 当前成交量 / (过去5日均日量 / 240 × 当日已开盘分钟数)
  过去5日均日量从 AKShare 历史K线中取

数据格式：统一返回 MarketSnapshot 对象（见 modules/ms_scorer.py）
"""
from __future__ import annotations

import time
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Optional, Dict
from loguru import logger

from modules.ms_scorer import MarketSnapshot, _get_price_limit


# ══════════════════════════════════════════════════════════════
# 进程级缓存（避免每次请求都全量拉行情）
# ══════════════════════════════════════════════════════════════

# 结构: { code: (snapshot, fetched_at) }
_SNAPSHOT_CACHE: Dict[str, tuple[MarketSnapshot, datetime]] = {}
# 历史日量缓存: { code: avg_daily_vol_5d(float) }
_DAILY_VOL_CACHE: Dict[str, float] = {}
# 全量行情缓存（stock_zh_a_spot_em 一次拉全部）
_SPOT_CACHE: Optional[pd.DataFrame] = None
_SPOT_CACHE_AT: Optional[datetime] = None
_SPOT_CACHE_TTL_SEC: int = 12  # 12 秒内复用


def _elapsed_minutes() -> float:
    """当日已开盘交易分钟数（9:30~11:30 + 13:00~当前，最大240）"""
    now = datetime.now()
    t = now.time()
    import datetime as dt_module

    morning_start = dt_module.time(9, 30)
    morning_end   = dt_module.time(11, 30)
    afternoon_start = dt_module.time(13, 0)

    total = 0.0
    if t < morning_start:
        return 0.0
    elif t <= morning_end:
        total = (now.hour * 60 + now.minute) - (9 * 60 + 30)
    elif t < afternoon_start:
        total = 120.0  # 上午全部
    else:
        total = 120.0 + (now.hour * 60 + now.minute) - (13 * 60)

    return max(1.0, min(240.0, total))


def _calc_vol_ratio_self(code: str, current_volume: float) -> tuple[float, float]:
    """
    自算量比，返回 (量比值, 陈旧分钟数)
    若计算失败返回 (1.0, 999)
    """
    avg_daily = _DAILY_VOL_CACHE.get(code, 0.0)
    if avg_daily <= 0:
        return 1.0, 999.0

    elapsed = _elapsed_minutes()
    if elapsed <= 0:
        return 1.0, 0.0

    avg_per_min = avg_daily / 240.0
    expected_vol = avg_per_min * elapsed
    if expected_vol <= 0:
        return 1.0, 0.0

    ratio = current_volume / expected_vol
    return round(ratio, 2), 0.0  # 自算的陈旧度=0


def _fetch_daily_vol_5d(code: str) -> float:
    """
    拉取近5个交易日平均成交量（手），结果写入缓存
    失败时返回 0.0
    """
    if code in _DAILY_VOL_CACHE:
        return _DAILY_VOL_CACHE[code]
    try:
        import akshare as ak
        end_dt = datetime.now().strftime("%Y%m%d")
        start_dt = (datetime.now() - timedelta(days=15)).strftime("%Y%m%d")
        df = ak.stock_zh_a_hist(
            symbol=code, period="daily",
            start_date=start_dt, end_date=end_dt, adjust=""
        )
        if df is not None and not df.empty:
            vol_col = "成交量" if "成交量" in df.columns else df.columns[-3]
            avg_vol = float(df[vol_col].tail(5).mean())
            _DAILY_VOL_CACHE[code] = avg_vol
            return avg_vol
    except Exception as e:
        logger.debug(f"[VOL_5D_FAIL] {code}: {e}")
    return 0.0


def _get_spot_df() -> Optional[pd.DataFrame]:
    """获取全量 A 股实时行情，带 12 秒缓存"""
    global _SPOT_CACHE, _SPOT_CACHE_AT
    now = datetime.now()
    if (_SPOT_CACHE is not None and _SPOT_CACHE_AT is not None
            and (now - _SPOT_CACHE_AT).total_seconds() < _SPOT_CACHE_TTL_SEC):
        return _SPOT_CACHE

    try:
        import akshare as ak
        df = ak.stock_zh_a_spot_em()
        if df is not None and not df.empty:
            _SPOT_CACHE = df
            _SPOT_CACHE_AT = now
            return df
    except Exception as e:
        logger.warning(f"[LEVEL2_SPOT_FAIL] AKShare spot_em: {e}")
    return _SPOT_CACHE  # 返回旧缓存（可能为 None）


def _parse_spot_row(row: pd.Series, code: str) -> Optional[MarketSnapshot]:
    """将 spot_em 的一行解析为 MarketSnapshot"""
    try:
        def _f(col, default=0.0):
            v = row.get(col, default)
            try: return float(v) if v is not None and str(v) not in ("", "nan", "-") else default
            except: return default

        snap = MarketSnapshot()
        snap.code       = str(row.get("代码", code)).zfill(6)
        snap.name       = str(row.get("名称", ""))
        snap.current    = _f("最新价")
        snap.open_today = _f("今开")
        snap.high_today = _f("最高")
        snap.low_today  = _f("最低")
        snap.prev_close = _f("昨收")
        snap.volume     = _f("成交量")   # 单位：手
        snap.amount     = _f("成交额")   # 单位：元
        snap.chg_pct    = _f("涨跌幅")
        snap.turnover   = _f("换手率")

        # 量比（先尝试接口字段，再自算）
        vol_ratio_raw = _f("量比", -1.0)
        if vol_ratio_raw > 0:
            snap.volume_ratio = vol_ratio_raw
            snap.vol_ratio_stale_minutes = 0.0
        else:
            vr, stale = _calc_vol_ratio_self(snap.code, snap.volume)
            snap.volume_ratio = vr
            snap.vol_ratio_stale_minutes = stale

        # 涨跌停限制
        snap.limit_up_pct   = _get_price_limit(snap.code)
        snap.limit_down_pct = snap.limit_up_pct

        # 判断涨停/跌停
        if snap.prev_close > 0:
            limit_up_price  = snap.prev_close * (1 + snap.limit_up_pct / 100)
            limit_down_price = snap.prev_close * (1 - snap.limit_down_pct / 100)
            snap.is_limit_up   = abs(snap.current - limit_up_price) / limit_up_price < 0.001
            snap.is_limit_down = abs(snap.current - limit_down_price) / limit_down_price < 0.001

        snap.data_source = "akshare_spot_em"
        snap.fetched_at  = datetime.now()
        return snap
    except Exception as e:
        logger.debug(f"[PARSE_SPOT_FAIL] {code}: {e}")
        return None


def _fetch_via_sina(code: str) -> Optional[MarketSnapshot]:
    """
    Level 3 兜底：新浪财经 HTTP 接口
    hq.sinajs.cn 返回格式：
    var hq_str_sh600519="贵州茅台,2001.00,1990.00,2035.00,2050.00,1985.00,
    2034.50,2035.00,3452600,6985...";
    字段顺序：名称,今开,昨收,当前价,最高,最低,买一,卖一,成交量(手),成交额,...
    """
    try:
        # 将六位代码转为新浪格式
        c = str(code).zfill(6)
        prefix = "sh" if c.startswith("6") else "sz"
        sina_code = f"{prefix}{c}"

        url = f"https://hq.sinajs.cn/list={sina_code}"
        headers = {"Referer": "https://finance.sina.com.cn"}
        resp = requests.get(url, headers=headers, timeout=5)
        resp.encoding = "gbk"
        text = resp.text

        # 解析
        start = text.find('"') + 1
        end   = text.rfind('"')
        if start <= 0 or end <= start:
            return None

        parts = text[start:end].split(",")
        if len(parts) < 9:
            return None

        def _fp(idx, default=0.0):
            try: return float(parts[idx])
            except: return default

        snap = MarketSnapshot()
        snap.code       = c
        snap.name       = parts[0]
        snap.open_today = _fp(1)
        snap.prev_close = _fp(2)
        snap.current    = _fp(3)
        snap.high_today = _fp(4)
        snap.low_today  = _fp(5)
        snap.volume     = _fp(8)   # 手
        snap.amount     = _fp(9)   # 元

        if snap.prev_close > 0:
            snap.chg_pct = (snap.current - snap.prev_close) / snap.prev_close * 100

        # 量比只能自算
        vr, stale = _calc_vol_ratio_self(c, snap.volume)
        snap.volume_ratio = vr
        snap.vol_ratio_stale_minutes = stale

        snap.limit_up_pct = _get_price_limit(c)
        snap.limit_down_pct = snap.limit_up_pct

        if snap.prev_close > 0:
            lup  = snap.prev_close * (1 + snap.limit_up_pct / 100)
            ldwn = snap.prev_close * (1 - snap.limit_down_pct / 100)
            snap.is_limit_up   = abs(snap.current - lup) / lup < 0.001
            snap.is_limit_down = abs(snap.current - ldwn) / ldwn < 0.001

        snap.data_source = "sina_hq"
        snap.fetched_at  = datetime.now()
        return snap

    except Exception as e:
        logger.warning(f"[LEVEL3_SINA_FAIL] {code}: {e}")
        return None


def _attach_recent_klines(snap: MarketSnapshot):
    """
    附加近 N 根 K 线（用于 ATR 计算），写入 snap.recent_closes/highs/lows
    先查进程缓存，失败则回退到空列表
    """
    try:
        from modules.analyzer import _kline_get
        code = snap.code
        end_dt = datetime.now().strftime("%Y%m%d")
        start_dt = (datetime.now() - timedelta(days=30)).strftime("%Y%m%d")
        df = _kline_get(code, start_dt, end_dt)
        if df is not None and not df.empty and len(df) >= 5:
            snap.recent_closes = df["收盘"].tail(10).tolist()
            snap.recent_highs  = df["最高"].tail(10).tolist() if "最高" in df.columns else []
            snap.recent_lows   = df["最低"].tail(10).tolist() if "最低" in df.columns else []
    except Exception as e:
        logger.debug(f"[KLINE_ATTACH_FAIL] {snap.code}: {e}")


def fetch_snapshot(
    code: str,
    buy_price: float = 0.0,
    force_refresh: bool = False,
    cache_ttl_sec: int = 12,
) -> MarketSnapshot:
    """
    获取单只股票实时快照（三级兜底）

    参数：
      code           - 股票代码（6位）
      buy_price      - 买入价（填入 snapshot 便于 MS 计算）
      force_refresh  - 强制刷新缓存
      cache_ttl_sec  - 缓存有效期（秒）

    返回：
      MarketSnapshot（即使全部失败也返回空壳对象，不抛异常）
    """
    code = str(code).zfill(6)
    now = datetime.now()

    # ── 检查进程级缓存 ──────────────────────────────────────────────
    if not force_refresh and code in _SNAPSHOT_CACHE:
        cached_snap, cached_at = _SNAPSHOT_CACHE[code]
        age = (now - cached_at).total_seconds()
        if age < cache_ttl_sec:
            if buy_price > 0:
                cached_snap.buy_price = buy_price
            return cached_snap

    # ── Level 2: AKShare 全量实时行情 ──────────────────────────────
    snap = None
    spot_df = _get_spot_df()
    if spot_df is not None and not spot_df.empty:
        code_col = "代码" if "代码" in spot_df.columns else spot_df.columns[0]
        row_df = spot_df[spot_df[code_col].astype(str).str.zfill(6) == code]
        if not row_df.empty:
            snap = _parse_spot_row(row_df.iloc[0], code)

    # ── Level 3: 新浪财经兜底 ──────────────────────────────────────
    if snap is None:
        logger.info(f"[FALLBACK_L3] {code} → 新浪接口")
        snap = _fetch_via_sina(code)

    # ── 全部失败：使用空壳（数据陈旧 999min）──────────────────────────
    if snap is None:
        logger.error(f"[ALL_SOURCES_FAILED] {code}，返回空快照")
        snap = MarketSnapshot()
        snap.code = code
        snap.fetched_at = None
        snap.vol_ratio_stale_minutes = 999.0
        snap.data_source = "none"

    # ── 补充买入价 & K 线 ──────────────────────────────────────────
    if buy_price > 0:
        snap.buy_price = buy_price

    _attach_recent_klines(snap)

    # ── 预热日均量缓存（用于自算量比，后台异步不阻塞）──────────────
    if code not in _DAILY_VOL_CACHE:
        try:
            _fetch_daily_vol_5d(code)
        except Exception:
            pass

    # ── 写入进程缓存 ──────────────────────────────────────────────
    _SNAPSHOT_CACHE[code] = (snap, snap.fetched_at or now)

    return snap


def fetch_snapshots_batch(
    positions: list[dict],
    cache_ttl_sec: int = 12,
) -> dict[str, MarketSnapshot]:
    """
    批量获取多只股票实时快照（复用全量行情 _get_spot_df，避免多次请求）

    参数：
      positions: [{"code": "600519", "buy_price": 2001.0}, ...]

    返回：
      { code: MarketSnapshot }
    """
    result = {}
    # 先刷新全量行情（只刷一次）
    _get_spot_df()

    for pos in positions:
        code = str(pos.get("code", "")).zfill(6)
        bp   = float(pos.get("buy_price", 0.0))
        if not code or code == "000000":
            continue
        result[code] = fetch_snapshot(code, buy_price=bp, cache_ttl_sec=cache_ttl_sec)

    return result


def get_data_freshness_label(snap: MarketSnapshot) -> tuple[str, str]:
    """
    返回 (状态文字, 颜色/表情)
    用于 UI 上的数据状态指示灯
    """
    if snap.fetched_at is None:
        return "🔴 断流", "red"

    age = (datetime.now() - snap.fetched_at).total_seconds()

    if snap.data_source == "none":
        return "🔴 断流", "red"
    elif age > 60:
        return f"🟡 延迟{age:.0f}s", "orange"
    elif snap.data_source == "sina_hq":
        return "🟡 新浪备用", "orange"
    else:
        return "🟢 正常", "green"
