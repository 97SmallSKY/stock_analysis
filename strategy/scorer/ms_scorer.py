"""
MS 动能评分模块（Momentum Score）
===================================
MS = V_score × 0.40 + P_score × 0.35 + B_score × 0.25

三大维度：
  V_score (Volume/量能):  量比评分，反映主力资金参与热度
  P_score (Price/价格):   价格结构评分，反映当前趋势强弱
  B_score (Board/盘口):   盘口评分，反映封单/挂单结构（无 L2 时降级）

评分规则（0~100）：
  MS ≥ 60 → 继续持有，移动止盈线跟随
  MS 40~59 → 准备减仓 50%
  MS < 40 → 立即清仓

降级模式（数据不完整时自动降级）：
  MS_FULL:          所有维度可用，正常计算
  MS_DEGRADED_NO_L2: B_score 不可用，权重重新归一化到 V/P
  MS_DEGRADED_NO_VOL: V_score 不可用，权重归一化到 P/B
  MS_PRICE_ONLY:    只有 P_score，清仓阈值上移 10 分（更保守）

ATR 移动止盈线：
  基于 Chandelier Exit（吊灯止盈）公式：
  stop_line = Highest_Close - K × ATR(N)
  K 默认 2.0，N 默认 5（最近5根K线）
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from datetime import datetime, time
from enum import Enum
from typing import Optional
from loguru import logger


# ══════════════════════════════════════════════════════════════
# 数据结构定义
# ══════════════════════════════════════════════════════════════

class MSMode(str, Enum):
    FULL           = "MS_FULL"           # 正常模式
    DEGRADED_NO_L2 = "MS_DEGRADED_NO_L2"  # 无封单数据
    DEGRADED_NO_VOL= "MS_DEGRADED_NO_VOL" # 无量能数据
    PRICE_ONLY     = "MS_PRICE_ONLY"     # 只有价格
    UNAVAILABLE    = "MS_UNAVAILABLE"    # 完全不可用


class SceneType(str, Enum):
    HOLD            = "hold"         # MS≥60，继续持有
    REDUCE          = "reduce"       # MS 40~59，准备减仓
    EXIT_WEAK       = "exit_weak"    # MS<40，立即清仓
    LIMIT_UP_SEALED = "limit_up"     # 涨停封板
    HIGH_OPEN_FAIL  = "high_open_fail"  # 高开低走
    NEAR_STOPLOSS   = "near_stop"    # 接近止损
    DATA_STALE      = "data_stale"   # 数据延迟
    CLOSED          = "closed"       # 已收盘/非交易时段


@dataclass
class MarketSnapshot:
    """单只股票的市场快照（从 realtime_fetcher 获取后填充）"""
    code:         str   = ""
    name:         str   = ""
    buy_price:    float = 0.0     # T+1 开盘买入价
    current:      float = 0.0     # 当前最新价
    high_today:   float = 0.0     # 今日最高价
    low_today:    float = 0.0     # 今日最低价
    open_today:   float = 0.0     # 今日开盘价
    prev_close:   float = 0.0     # 昨收价
    volume:       float = 0.0     # 当前累计成交量（手）
    amount:       float = 0.0     # 当前累计成交额（元）
    volume_ratio: float = 1.0     # 量比（可能是自算值）
    chg_pct:      float = 0.0     # 涨跌幅 %
    turnover:     float = 0.0     # 换手率 %
    bid1_vol:     float = 0.0     # 买一挂单量（L2，可能为 0）
    ask1_vol:     float = 0.0     # 卖一挂单量（L2，可能为 0）
    limit_up_pct: float = 10.0    # 涨幅限制（%）：主板10/创业板20/北交所30
    limit_down_pct: float = 10.0  # 跌幅限制（%）
    is_limit_up:  bool  = False   # 是否涨停
    is_limit_down: bool = False   # 是否跌停
    # 历史数据（用于 ATR/P_score 计算）
    recent_closes: list = field(default_factory=list)  # 最近N根收盘价
    recent_highs:  list = field(default_factory=list)  # 最近N根最高价
    recent_lows:   list = field(default_factory=list)  # 最近N根最低价
    # 元信息
    data_source:  str   = "unknown"   # 数据来源级别
    fetched_at:   Optional[datetime] = None
    vol_ratio_stale_minutes: float = 0.0  # 量比数据陈旧度（分钟）


@dataclass
class MSResult:
    """MS 评分计算结果"""
    ms:       float = 50.0   # 综合 MS（0~100）
    v_score:  float = 50.0   # 量能维度
    p_score:  float = 50.0   # 价格维度
    b_score:  float = 50.0   # 盘口维度
    mode:     MSMode = MSMode.FULL
    scene:    SceneType = SceneType.HOLD
    # 止盈止损线
    trail_stop_price: float = 0.0    # 移动止盈线价格
    hard_stop_price:  float = 0.0    # 硬止损价格（买入价×(1-stop_pct)）
    highest_since_buy: float = 0.0   # 买入后最高价（用于 ATR 止盈）
    atr:              float = 0.0    # ATR(5)
    # 操作建议文字
    action_text:  str = ""
    est_sell_price: float = 0.0  # 预计卖出价
    detail:       dict = field(default_factory=dict)


# ══════════════════════════════════════════════════════════════
# 核心计算函数
# ══════════════════════════════════════════════════════════════

def _get_price_limit(code: str) -> float:
    """
    根据股票代码返回涨幅限制（%）
    规则（2025年最新）：
      主板(沪/深)：±10%
      创业板(30x)：±20%
      科创板(688)：±20%
      北交所(8x/4x)：±30%
      ST股（名称含ST）：±5%（此函数不处理，由调用方传入）
    """
    c = str(code).zfill(6)
    if c.startswith("688"):     return 20.0   # 科创板
    if c.startswith("3"):       return 20.0   # 创业板
    if c[0] in ("8", "4"):      return 30.0   # 北交所
    if c[:2] == "68":           return 20.0   # 科创板变体
    return 10.0  # 主板（沪/深）


def _calc_atr(highs: list, lows: list, closes: list, period: int = 5) -> float:
    """
    计算 ATR（真实波幅均值）
    TR = max(H-L, |H-prev_close|, |L-prev_close|)
    ATR = SMA(TR, period)
    """
    h = np.array(highs, dtype=float)
    l = np.array(lows,  dtype=float)
    c = np.array(closes,dtype=float)

    if len(h) < 2:
        return (h[-1] - l[-1]) if len(h) > 0 else 0.0

    tr_list = []
    for i in range(1, len(h)):
        tr = max(h[i] - l[i], abs(h[i] - c[i-1]), abs(l[i] - c[i-1]))
        tr_list.append(tr)

    tr_arr = np.array(tr_list)
    n = min(period, len(tr_arr))
    return float(tr_arr[-n:].mean()) if n > 0 else 0.0


def _calc_v_score(snap: MarketSnapshot, stale_minutes: float = 0.0) -> float:
    """
    量能评分 V_score（0~100）
    基于量比判断主力热度：
      量比 > 3.0 → 100（极强放量）
      量比 2.0~3.0 → 80
      量比 1.2~2.0 → 60（温和放量）
      量比 0.8~1.2 → 40（量能正常）
      量比 < 0.8 → 20（缩量）
    若量比数据陈旧（>15min），强制返回 50（中性）
    """
    if stale_minutes > 15:
        logger.debug(f"[VOLUME_RATIO_UNAVAILABLE] {snap.code} 量比陈旧 {stale_minutes:.0f}min，设为中性50")
        return 50.0

    vr = snap.volume_ratio
    if vr >= 3.0:   return 100.0
    if vr >= 2.0:   return 80.0
    if vr >= 1.5:   return 70.0
    if vr >= 1.2:   return 60.0
    if vr >= 0.8:   return 40.0
    if vr >= 0.5:   return 25.0
    return 15.0


def _calc_p_score(snap: MarketSnapshot) -> float:
    """
    价格结构评分 P_score（0~100）
    综合考察：
      1. 相对买入价的涨幅位置（40分）
      2. 日内走势形态（30分）：高开低走 / 稳步上涨 / 横盘
      3. 相对今日高低点位置（30分）
    """
    if snap.buy_price <= 0 or snap.current <= 0:
        return 50.0

    score = 0.0
    bp = snap.buy_price
    cur = snap.current
    open_p = snap.open_today if snap.open_today > 0 else cur
    high = snap.high_today if snap.high_today > 0 else cur
    low = snap.low_today if snap.low_today > 0 else cur

    # 1. 相对买入价涨幅（40分）
    pnl = (cur - bp) / bp
    if pnl >= 0.05:   score += 40.0
    elif pnl >= 0.03: score += 35.0
    elif pnl >= 0.01: score += 28.0
    elif pnl >= 0:    score += 20.0
    elif pnl >= -0.02: score += 10.0
    else:              score += 0.0

    # 2. 日内走势形态（30分）
    day_chg = (cur - open_p) / open_p if open_p > 0 else 0
    open_gap = (open_p - snap.prev_close) / snap.prev_close if snap.prev_close > 0 else 0

    # 高开低走判断：今日高开（>2%）且当前价 < 开盘价
    if open_gap > 0.02 and cur < open_p:
        score += 0.0   # 高开低走，很差
    elif day_chg > 0.02:
        score += 30.0  # 盘中稳步上涨
    elif day_chg > 0:
        score += 22.0  # 小幅上涨
    elif day_chg > -0.01:
        score += 15.0  # 基本横盘
    else:
        score += 5.0   # 下跌

    # 3. 相对今日高低点（30分）：当前价在当日振幅中的相对位置
    if high > low:
        pos = (cur - low) / (high - low)  # 0=低点, 1=高点
        score += pos * 30.0
    else:
        score += 15.0  # 无振幅，中性

    return round(min(100.0, max(0.0, score)), 1)


def _calc_b_score(snap: MarketSnapshot) -> tuple[float, bool]:
    """
    盘口评分 B_score（0~100），返回 (分数, 是否有L2数据)
    有 L2 数据时：
      - 涨停封板：封单比 = bid1_vol / (ask1_vol + 1)
    无 L2 时：
      - 仅依赖涨停状态和价格位置做间接判断
    """
    has_l2 = snap.bid1_vol > 0 or snap.ask1_vol > 0

    if snap.is_limit_up:
        if has_l2:
            # 封单比评分
            seal_ratio = snap.bid1_vol / (snap.ask1_vol + 1)
            if seal_ratio > 5:    return 100.0, True  # 极强封单
            if seal_ratio > 2:    return 85.0,  True
            if seal_ratio > 1:    return 70.0,  True
            return 40.0, True  # 封单薄弱，可能开板
        else:
            # 无 L2，保守估计
            return 65.0, False

    if snap.is_limit_down:
        return 0.0, has_l2

    # 非涨停/跌停：用买卖盘挂单比判断
    if has_l2 and snap.bid1_vol > 0:
        bid_ask_ratio = snap.bid1_vol / (snap.ask1_vol + snap.bid1_vol + 1)
        return round(bid_ask_ratio * 100.0, 1), True

    # 无 L2 数据：用价格位置间接评估
    if snap.high_today > snap.low_today:
        pos = (snap.current - snap.low_today) / (snap.high_today - snap.low_today)
        return round(pos * 80.0 + 10.0, 1), False  # [10, 90]

    return 50.0, False


def _calc_trail_stop(
    snap: MarketSnapshot,
    highest_since_buy: float,
    atr_k: float = 2.0,
    atr_n: int = 5,
) -> tuple[float, float]:
    """
    计算 ATR 移动止盈线（Chandelier Exit）和 ATR 值
    stop_line = max(历史收盘最高价) - k × ATR(n)
    """
    # 优先用收盘价列表计算 ATR
    if len(snap.recent_closes) >= 3 and len(snap.recent_highs) >= 3:
        atr = _calc_atr(snap.recent_highs, snap.recent_lows, snap.recent_closes, period=atr_n)
    else:
        # 退化：用今日振幅估算
        atr = (snap.high_today - snap.low_today) if snap.high_today > snap.low_today else snap.current * 0.02

    highest = max(highest_since_buy, snap.high_today, snap.current)
    stop_line = highest - atr_k * atr
    # 止盈线不能低于买入价（否则毫无意义）
    stop_line = max(stop_line, snap.buy_price * 0.97)  # 最低给 -3% 的保护

    return round(stop_line, 3), round(atr, 3)


def _is_high_open_fail(snap: MarketSnapshot) -> bool:
    """判断是否高开低走模式"""
    if snap.prev_close <= 0:
        return False
    open_gap = (snap.open_today - snap.prev_close) / snap.prev_close
    return open_gap > 0.02 and snap.current < snap.open_today


def _scene_and_action(
    ms: float,
    snap: MarketSnapshot,
    trail_stop: float,
    hard_stop: float,
    mode: MSMode,
) -> tuple[SceneType, str, float]:
    """
    根据 MS 分数和市场快照判断场景，返回（场景类型, 操作建议文字, 预计卖出价）
    
    参数：
      ms          - 综合 MS 分数
      snap        - 市场快照
      trail_stop  - 移动止盈线价格
      hard_stop   - 硬止损价格
      mode        - MS 计算模式（影响清仓阈值）
    """
    cur = snap.current
    bp  = snap.buy_price

    # 1. 数据陈旧
    if snap.fetched_at:
        age = (datetime.now() - snap.fetched_at).total_seconds()
        if age > 120:  # 数据超过 2 分钟
            return (
                SceneType.DATA_STALE,
                f"⚪ 数据延迟 {age:.0f}s，最后已知价 {cur:.3f}",
                cur,
            )

    # 2. 非交易时段
    now_t = datetime.now().time()
    if not (_is_trading_time(now_t)):
        return SceneType.CLOSED, "💤 非交易时段", cur

    # 3. 接近硬止损
    if cur <= hard_stop * 1.01:
        return (
            SceneType.NEAR_STOPLOSS,
            f"🔴 接近止损！建议卖出价 ≈ {hard_stop:.3f}（买入价 ×97%）",
            hard_stop,
        )

    # 4. 跌破移动止盈线
    if cur <= trail_stop and bp > 0 and cur > bp * 0.97:
        pnl = (cur - bp) / bp * 100
        return (
            SceneType.REDUCE,
            f"⚠️ 跌破止盈线 {trail_stop:.3f}，建议减仓（当前盈亏 {pnl:+.1f}%）",
            cur,
        )

    # 5. 涨停封板
    if snap.is_limit_up:
        return (
            SceneType.LIMIT_UP_SEALED,
            "🔒 涨停封板中，不卖出（若开板→快速卖出）",
            0.0,
        )

    # 6. 高开低走
    if _is_high_open_fail(snap):
        vwap = snap.amount / snap.volume / 100 if snap.volume > 0 else cur
        return (
            SceneType.HIGH_OPEN_FAIL,
            f"⚠️ 高开低走，建议减仓 50%，预计卖出价 ≈ {vwap:.3f}（VWAP）",
            vwap,
        )

    # 7. 清仓阈值（更保守模式上移10分）
    exit_threshold = 50 if mode == MSMode.PRICE_ONLY else 40

    if ms < exit_threshold:
        return (
            SceneType.EXIT_WEAK,
            f"🔴 MS={ms:.0f} < {exit_threshold}，动能衰竭，建议清仓",
            cur,
        )

    # 8. 准备减仓
    if ms < 60:
        pnl = (cur - bp) / bp * 100
        return (
            SceneType.REDUCE,
            f"⚠️ MS={ms:.0f}（40~60），建议减仓50%，预计卖出价 ≈ {cur:.3f}（盈亏 {pnl:+.1f}%）",
            cur,
        )

    # 9. 继续持有
    pnl = (cur - bp) / bp * 100
    return (
        SceneType.HOLD,
        f"✅ 继续持有（MS={ms:.0f}，盈亏 {pnl:+.1f}%），止盈线 {trail_stop:.3f}",
        trail_stop,
    )


def _is_trading_time(t: time) -> bool:
    """是否在交易时段（9:30~11:30, 13:00~15:00）"""
    morning = time(9, 30) <= t <= time(11, 30)
    afternoon = time(13, 0) <= t <= time(15, 0)
    return morning or afternoon


# ══════════════════════════════════════════════════════════════
# 主计算入口
# ══════════════════════════════════════════════════════════════

def calculate_ms(
    snap: MarketSnapshot,
    highest_since_buy: float = 0.0,
    atr_k: float = 2.0,
    stop_loss_pct: float = 0.03,
) -> MSResult:
    """
    计算 MS 动能评分及所有相关指标
    
    参数：
      snap              - 市场快照
      highest_since_buy - 买入以来的最高价（用于 ATR 止盈线）
      atr_k             - ATR 倍数（Chandelier Exit 参数）
      stop_loss_pct     - 硬止损百分比（0.03 = -3%）
    
    返回：
      MSResult
    """
    result = MSResult()

    # ── 计算各维度 ──────────────────────────────────────────────
    v_score = _calc_v_score(snap, snap.vol_ratio_stale_minutes)
    p_score = _calc_p_score(snap)
    b_score, has_l2 = _calc_b_score(snap)

    # ── 确定降级模式 ──────────────────────────────────────────
    vol_unavailable = snap.vol_ratio_stale_minutes > 15
    l2_unavailable = not has_l2

    if vol_unavailable and l2_unavailable:
        mode = MSMode.PRICE_ONLY
        ms = p_score
        v_score = 50.0  # 中性
        b_score = 50.0
        logger.debug(f"[MS_PRICE_ONLY] {snap.code} 仅价格数据可用")
    elif vol_unavailable:
        mode = MSMode.DEGRADED_NO_VOL
        v_score = 50.0
        # 重新归一化 P + B 权重
        ms = 50.0 * 0.40 + p_score * (0.35 / 0.60) + b_score * (0.25 / 0.60)
        logger.debug(f"[MS_DEGRADED_NO_VOL] {snap.code}")
    elif l2_unavailable:
        mode = MSMode.DEGRADED_NO_L2
        b_score = 50.0
        # 重新归一化 V + P 权重
        ms = v_score * (0.40 / 0.75) + p_score * (0.35 / 0.75) + 50.0 * 0.25
        logger.debug(f"[MS_DEGRADED_NO_L2] {snap.code}")
    else:
        mode = MSMode.FULL
        ms = v_score * 0.40 + p_score * 0.35 + b_score * 0.25

    ms = round(min(100.0, max(0.0, ms)), 1)

    # ── 止盈止损线计算 ─────────────────────────────────────────
    h_since_buy = highest_since_buy if highest_since_buy > 0 else snap.buy_price
    trail_stop, atr = _calc_trail_stop(snap, h_since_buy, atr_k=atr_k)
    hard_stop = snap.buy_price * (1 - stop_loss_pct) if snap.buy_price > 0 else 0.0

    # ── 场景判断 ──────────────────────────────────────────────
    scene, action_text, est_sell = _scene_and_action(ms, snap, trail_stop, hard_stop, mode)

    result.ms             = ms
    result.v_score        = round(v_score, 1)
    result.p_score        = round(p_score, 1)
    result.b_score        = round(b_score, 1)
    result.mode           = mode
    result.scene          = scene
    result.trail_stop_price  = trail_stop
    result.hard_stop_price   = round(hard_stop, 3)
    result.highest_since_buy = round(h_since_buy, 3)
    result.atr            = atr
    result.action_text    = action_text
    result.est_sell_price = round(est_sell, 3) if est_sell else 0.0
    result.detail = {
        "v_score": v_score,
        "p_score": p_score,
        "b_score": b_score,
        "has_l2":  has_l2,
        "vol_ratio": snap.volume_ratio,
        "pnl_pct": round((snap.current - snap.buy_price) / snap.buy_price * 100, 2)
                   if snap.buy_price > 0 else 0.0,
        "is_limit_up": snap.is_limit_up,
        "is_high_open_fail": _is_high_open_fail(snap),
        "atr": atr,
        "trail_stop": trail_stop,
        "hard_stop": hard_stop,
    }

    return result


def ms_bar(ms: float, width: int = 10) -> str:
    """生成 MS 进度条字符串，如 ▰▰▰▰▰▰▰▱▱▱"""
    filled = round(ms / 100 * width)
    return "▰" * filled + "▱" * (width - filled)


def ms_level_label(ms: float) -> str:
    """MS 分数转档位文字"""
    if ms >= 80: return "极强"
    if ms >= 60: return "强"
    if ms >= 40: return "中性"
    if ms >= 20: return "弱"
    return "极弱"
