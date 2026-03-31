"""
实时监控仪表盘 — 龙虎榜策略盘中实时展示
==========================================
参考：docs/龙虎榜策略_工程化落地方案v2.md 第四章

功能：
  1. 实时行情获取（三级降级：AKShare → 新浪 HTTP → 缓存兜底）
  2. MS 动能评分（V_score × 0.40 + P_score × 0.35 + B_score × 0.25）
  3. 移动止盈线（ATR Chandelier Exit）
  4. 预计卖出价（6种场景自动判断）
  5. 数据状态指示灯（🟢/🟡/🔴）
  6. 分时段差异化刷新频率（集合竞价/盘前/盘中/尾盘/收盘后）
  7. 非交易时段展示今日候选标的

数据源降级链：
  Level 2: AKShare stock_zh_a_spot_em()  → 可用性 ~95%
  Level 3: 新浪 hq.sinajs.cn HTTP        → 简单稳定
  Cache:   最后一次成功数据              → 陈旧数据兜底
"""

import time
import math
import requests
import streamlit as st
import pandas as pd
import numpy as np
from datetime import datetime, date, timedelta
from loguru import logger

from utils.storage import storage

try:
    import akshare as ak
    _HAS_AKSHARE = True
except ImportError:
    _HAS_AKSHARE = False

# ─────────────────────────────────────────────────────────────────────────────
# 工具函数
# ─────────────────────────────────────────────────────────────────────────────

def _price_limit(code: str) -> float:
    """根据股票代码返回涨跌幅限制（小数）"""
    c = str(code).zfill(6)
    if c.startswith("688") or c.startswith("3") or c.startswith("300"):
        return 0.20   # 科创板 / 创业板 ±20%
    if c[0] in ("8", "4"):
        return 0.30   # 北交所 ±30%
    # ST 股票 ±5%（简化判断，实际需要股票名称）
    return 0.10       # 主板 ±10%


def _board_name(code: str) -> str:
    c = str(code).zfill(6)
    if c.startswith("688"):     return "科创板 ±20%"
    if c.startswith("300") or c.startswith("301"): return "创业板 ±20%"
    if c.startswith("3"):       return "创业板 ±20%"
    if c[0] in ("8", "4"):     return "北交所 ±30%"
    if c.startswith("6"):       return "沪主板 ±10%"
    if c[:3] in ("000", "001", "002", "003"): return "深主板 ±10%"
    return "未知 ±10%"


def _ms_bar(ms: int, width: int = 10) -> str:
    """MS 动能评分转进度条文字，如 ▰▰▰▰▰▰▰▱▱▱"""
    filled = round(ms / 100 * width)
    return "▰" * filled + "▱" * (width - filled)


def _ms_label(ms: int) -> str:
    if ms >= 80: return "极强"
    if ms >= 60: return "强"
    if ms >= 40: return "中性"
    if ms >= 20: return "弱"
    return "极弱"


def _ms_color(ms: int) -> str:
    if ms >= 70: return "#E84040"
    if ms >= 50: return "#FFA500"
    if ms >= 35: return "#888888"
    return "#33CC66"


def _is_trading_now() -> bool:
    """当前是否处于 A 股交易时段 9:30~11:30 / 13:00~15:00"""
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    t = now.hour * 60 + now.minute
    return (9 * 60 + 30 <= t <= 11 * 60 + 30) or (13 * 60 <= t <= 15 * 60)


def _is_pre_market() -> bool:
    """集合竞价时段 9:15~9:30"""
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    t = now.hour * 60 + now.minute
    return 9 * 60 + 15 <= t < 9 * 60 + 30


def _is_market_close() -> bool:
    """收盘后（15:00 之后）"""
    now = datetime.now()
    t = now.hour * 60 + now.minute
    return t >= 15 * 60


def _refresh_interval() -> int:
    """按文档2.1节返回推荐刷新间隔（秒）"""
    now = datetime.now()
    t = now.hour * 60 + now.minute
    if 9 * 60 + 15 <= t < 9 * 60 + 30:    return 10   # 集合竞价
    if 9 * 60 + 30 <= t < 9 * 60 + 45:    return 15   # 开盘黄金15分钟
    if t < 11 * 60 + 30:                   return 30   # 上午盘中
    if 11 * 60 + 30 <= t < 13 * 60:        return 120  # 午休
    if 13 * 60 <= t < 14 * 60 + 30:        return 30   # 下午盘中
    if 14 * 60 + 30 <= t < 15 * 60:        return 10   # 尾盘冲刺
    return 300                                          # 非交易时段


# ─────────────────────────────────────────────────────────────────────────────
# 三级实时行情获取（Level 2/3/Cache）
# ─────────────────────────────────────────────────────────────────────────────

# 进程级价格缓存：{code: {price, change_pct, volume, amount, high, low, pre_close, open, ts, source, success_count, fail_count}}
_PRICE_CACHE: dict = {}


def _get_spot_akshare(codes: list) -> dict:
    """Level 2: AKShare 实时行情"""
    result = {}
    try:
        if not _HAS_AKSHARE:
            return result
        df = ak.stock_zh_a_spot_em()
        if df is None or df.empty:
            return result
        df["代码"] = df["代码"].astype(str).str.zfill(6)
        for code in codes:
            row = df[df["代码"] == code]
            if row.empty:
                continue
            r = row.iloc[0]
            result[code] = {
                "price":      float(r.get("最新价", 0) or 0),
                "change_pct": float(r.get("涨跌幅", 0) or 0),
                "volume":     float(r.get("成交量", 0) or 0),
                "amount":     float(r.get("成交额", 0) or 0),
                "high":       float(r.get("最高", 0) or 0),
                "low":        float(r.get("最低", 0) or 0),
                "pre_close":  float(r.get("昨收", 0) or 0),
                "open":       float(r.get("今开", 0) or 0),
                "source":     "AKShare",
            }
    except Exception as e:
        logger.debug(f"AKShare 行情失败: {e}")
    return result


def _get_spot_sina(codes: list) -> dict:
    """Level 3: 新浪 hq.sinajs.cn 实时行情"""
    result = {}
    try:
        # 新浪格式：sh600519 或 sz000001
        sina_codes = []
        for code in codes:
            c = str(code).zfill(6)
            prefix = "sh" if c.startswith("6") else "sz"
            sina_codes.append(f"{prefix}{c}")
        url = "https://hq.sinajs.cn/list=" + ",".join(sina_codes)
        headers = {"Referer": "https://finance.sina.com.cn"}
        resp = requests.get(url, headers=headers, timeout=5)
        resp.encoding = "gbk"
        for line in resp.text.strip().split("\n"):
            line = line.strip()
            if not line or "=" not in line:
                continue
            parts = line.split("=")
            key_part = parts[0].strip()
            val_part = parts[1].strip().strip('"').strip(";")
            if not val_part:
                continue
            # 提取股票代码（sh600519 → 600519）
            code_raw = key_part.replace("var hq_str_", "").replace("sh", "").replace("sz", "").strip()
            code = str(code_raw).zfill(6)
            fields = val_part.split(",")
            if len(fields) < 10:
                continue
            try:
                name      = fields[0]
                open_p    = float(fields[1]) if fields[1] else 0
                pre_close = float(fields[2]) if fields[2] else 0
                price     = float(fields[3]) if fields[3] else 0
                high      = float(fields[4]) if fields[4] else 0
                low       = float(fields[5]) if fields[5] else 0
                amount    = float(fields[9]) if fields[9] else 0  # 成交额
                volume    = float(fields[8]) if fields[8] else 0  # 成交量
                change_pct = (price / pre_close - 1) * 100 if pre_close > 0 else 0
                result[code] = {
                    "name":       name,
                    "price":      price,
                    "change_pct": change_pct,
                    "volume":     volume,
                    "amount":     amount,
                    "high":       high,
                    "low":        low,
                    "pre_close":  pre_close,
                    "open":       open_p,
                    "source":     "新浪",
                }
            except Exception:
                continue
    except Exception as e:
        logger.debug(f"新浪行情失败: {e}")
    return result


def fetch_realtime_prices(codes: list) -> dict:
    """
    三级降级获取实时行情，同时更新进程级缓存。
    返回 {code: {price, change_pct, volume, amount, high, low, pre_close, open,
                 source, stale, stale_seconds, last_ts}}
    """
    ts = datetime.now()
    result = {}

    # Level 2: AKShare
    ak_result = _get_spot_akshare(codes)
    if ak_result:
        for code, data in ak_result.items():
            data["last_ts"] = ts
            data["stale"] = False
            data["stale_seconds"] = 0
            _PRICE_CACHE[code] = data.copy()
            result[code] = data

    # Level 3: 新浪（补充 AKShare 没拿到的）
    missing = [c for c in codes if c not in result]
    if missing:
        sina_result = _get_spot_sina(missing)
        for code, data in sina_result.items():
            data["last_ts"] = ts
            data["stale"] = False
            data["stale_seconds"] = 0
            _PRICE_CACHE[code] = data.copy()
            result[code] = data

    # Cache 兜底（仍然缺失的）
    still_missing = [c for c in codes if c not in result]
    for code in still_missing:
        if code in _PRICE_CACHE:
            cached = _PRICE_CACHE[code].copy()
            stale_secs = (ts - cached["last_ts"]).total_seconds()
            cached["stale"] = True
            cached["stale_seconds"] = int(stale_secs)
            cached["source"] = f"缓存({int(stale_secs//60)}分钟前)"
            result[code] = cached
        else:
            result[code] = {
                "price": 0, "change_pct": 0, "volume": 0, "amount": 0,
                "high": 0, "low": 0, "pre_close": 0, "open": 0,
                "source": "无数据", "stale": True, "stale_seconds": 999999,
                "last_ts": ts,
            }

    return result


# ─────────────────────────────────────────────────────────────────────────────
# MS 动能评分计算
# ─────────────────────────────────────────────────────────────────────────────

def calc_ms_score(code: str, price_data: dict, buy_price: float, buy_date: str) -> dict:
    """
    MS 动能评分（0~100）
    MS = V_score × 0.40 + P_score × 0.35 + B_score × 0.25

    V_score（量能维度，40%）：
      - 量比（当日成交量 / 5日均量，近似用 amount/预估值）
      - 成交额活跃度

    P_score（价格结构，35%）：
      - 相对买入价涨幅位置
      - 高低点位置（High-Low Range）
      - 开盘价趋势（高开/低开 → 高走/低走）

    B_score（盘口，25%）：
      - 涨跌幅相对板块限制的位置
      - 是否接近涨停（> 9.5% / 19%）
      - 简化版封单评估

    兜底：某维度不可用时使用中性值 50 替代（见文档 3.2 节）
    """
    price  = float(price_data.get("price", 0) or 0)
    high   = float(price_data.get("high", 0) or 0)
    low    = float(price_data.get("low", 0) or 0)
    open_p = float(price_data.get("open", 0) or 0)
    vol    = float(price_data.get("volume", 0) or 0)
    amt    = float(price_data.get("amount", 0) or 0)
    pct    = float(price_data.get("change_pct", 0) or 0)
    pre    = float(price_data.get("pre_close", 0) or 0)
    stale  = bool(price_data.get("stale", False))
    limit  = _price_limit(code)

    if price <= 0 or stale:
        return {
            "ms": 50, "v_score": 50, "p_score": 50, "b_score": 50,
            "degraded": True, "mode": "数据不可用",
            "trailing_stop": buy_price * 0.97 if buy_price > 0 else 0,
            "stop_loss": buy_price * 0.97 if buy_price > 0 else 0,
        }

    # ── V_score（量能，40%）──────────────────────────────────────────────────
    # 用成交额判断活跃度（无5日均量时用绝对规模判断）
    v_score = 50.0
    if amt > 0:
        # 简单量能评分：成交额 > 5亿活跃，> 10亿极活跃
        if amt >= 1e9:      v_score = 90.0
        elif amt >= 5e8:    v_score = 75.0
        elif amt >= 2e8:    v_score = 60.0
        elif amt >= 5e7:    v_score = 45.0
        else:               v_score = 30.0

        # 涨幅 × 量能 正向加分（量价齐升）
        if pct > 3 and amt >= 5e8:   v_score = min(100, v_score + 10)
        if pct > 6 and amt >= 2e8:   v_score = min(100, v_score + 5)

    # ── P_score（价格结构，35%）──────────────────────────────────────────────
    p_score = 50.0
    if pre > 0 and high > 0 and low > 0:
        # 1. 相对买入价的涨幅
        if buy_price > 0:
            gain = (price / buy_price - 1) * 100
            if gain >= 5:     p_score += 20
            elif gain >= 3:   p_score += 15
            elif gain >= 1:   p_score += 8
            elif gain >= 0:   p_score += 3
            elif gain >= -2:  p_score -= 10
            else:             p_score -= 20

        # 2. 价格在日内高低点的位置（越靠近最高点越强）
        range_hl = high - low
        if range_hl > 0:
            pos_in_range = (price - low) / range_hl  # 0~1
            p_score += (pos_in_range - 0.5) * 20   # -10 ~ +10

        # 3. 高开低走 vs 低开高走判断
        if open_p > 0:
            if price > open_p and pct > 0:      p_score += 5   # 高走
            elif price < open_p and pct < 0:    p_score -= 10  # 低走

        p_score = max(0, min(100, p_score))

    # ── B_score（盘口简化，25%）──────────────────────────────────────────────
    b_score = 50.0
    limit_pct = limit * 100  # 转成百分比
    if pct >= limit_pct * 0.95:     # 接近或达到涨停
        b_score = 95.0
    elif pct >= limit_pct * 0.80:   # 强势
        b_score = 80.0
    elif pct >= 5:
        b_score = 70.0
    elif pct >= 3:
        b_score = 60.0
    elif pct >= 0:
        b_score = 50.0
    elif pct >= -2:
        b_score = 35.0
    else:
        b_score = 20.0

    # ── 综合 MS ──────────────────────────────────────────────────────────────
    ms = v_score * 0.40 + p_score * 0.35 + b_score * 0.25
    ms = max(0, min(100, round(ms)))

    # ── 移动止盈线（简化 ATR = (High-Low)×1.2，Chandelier K=2）────────────
    atr_proxy = (high - low) if (high > 0 and low > 0) else price * 0.02
    trailing_stop = price - 2 * atr_proxy
    if buy_price > 0:
        trailing_stop = max(trailing_stop, buy_price * 0.97)  # 不低于买入价 -3%

    stop_loss = buy_price * 0.97 if buy_price > 0 else price * 0.97

    # 是否涨停
    is_limit_up = (pct >= limit_pct * 0.99)

    # 高开低走检测
    gap_up = open_p > pre * 1.02 if (open_p > 0 and pre > 0) else False
    fall_from_open = price < open_p if open_p > 0 else False
    high_open_low_walk = gap_up and fall_from_open and pct < 2

    return {
        "ms": ms,
        "v_score": round(v_score, 1),
        "p_score": round(p_score, 1),
        "b_score": round(b_score, 1),
        "degraded": False,
        "is_limit_up": is_limit_up,
        "high_open_low_walk": high_open_low_walk,
        "trailing_stop": round(trailing_stop, 3),
        "stop_loss": round(stop_loss, 3),
        "mode": "正常",
    }


# ─────────────────────────────────────────────────────────────────────────────
# 预计卖出价（6场景判断，参考文档 4.2节）
# ─────────────────────────────────────────────────────────────────────────────

def get_sell_advice(price: float, buy_price: float, ms_data: dict, price_data: dict) -> dict:
    """
    根据当前状态返回预计卖出价和操作建议。
    场景 A/B/C/D/E/F（对应文档 4.2 节六种场景）。
    """
    ms           = ms_data.get("ms", 50)
    trailing_stop = ms_data.get("trailing_stop", 0)
    stop_loss    = ms_data.get("stop_loss", 0)
    is_limit_up  = ms_data.get("is_limit_up", False)
    high_low_walk = ms_data.get("high_open_low_walk", False)
    stale        = price_data.get("stale", False)

    # 场景 F：数据不可用
    if stale or price <= 0:
        cached_price = price_data.get("price", buy_price)
        stale_min = price_data.get("stale_seconds", 0) // 60
        return {
            "scenario": "F",
            "label": f"⚪ 数据延迟，最后已知价 {cached_price:.2f}（{stale_min} 分钟前）",
            "sell_price": cached_price,
            "color": "#888888",
            "bg_color": "#3a3a00",
            "action": "等待数据恢复",
        }

    # 场景 E：触及止损
    if price <= stop_loss:
        return {
            "scenario": "E",
            "label": f"🔴 接近止损！预计卖出价 = {stop_loss:.2f}（买入价 -3%）",
            "sell_price": stop_loss,
            "color": "#E84040",
            "bg_color": "#3a0000",
            "action": "立即止损",
        }

    # 场景 D：涨停封板
    if is_limit_up:
        return {
            "scenario": "D",
            "label": "🔒 涨停封板中，不卖出 — 若开板则立即清仓",
            "sell_price": price,
            "color": "#E84040",
            "bg_color": "#1a0010",
            "action": "持仓过夜",
        }

    # 场景 C：高开低走
    if high_low_walk:
        vwap = price_data.get("amount", 0) / (price_data.get("volume", 1) or 1)
        vwap = vwap if vwap > 0 else price
        return {
            "scenario": "C",
            "label": f"⚠️ 高开低走！预计卖出价 ≈ {vwap:.2f}（当前 VWAP 附近）",
            "sell_price": round(vwap, 3),
            "color": "#FFA500",
            "bg_color": "#3a2000",
            "action": "立即卖出50%",
        }

    # 场景 B：MS 40~59，准备减仓
    if 40 <= ms < 60:
        return {
            "scenario": "B",
            "label": f"⚠️ 准备减仓50%，预计卖出价 ≈ {price:.2f}（市价）",
            "sell_price": price,
            "color": "#FFA500",
            "bg_color": "#2a1a00",
            "action": "减仓50%",
        }

    # 场景 A：MS ≥ 60，继续持有
    if ms >= 60:
        return {
            "scenario": "A",
            "label": f"✅ 继续持有 — 移动止盈线: {trailing_stop:.2f}（跌至此价清仓）",
            "sell_price": trailing_stop,
            "color": "#33CC66",
            "bg_color": "#001a00",
            "action": "持有",
        }

    # MS < 40（弱势，准备清仓）
    return {
        "scenario": "B-",
        "label": f"🔴 MS 弱势（{ms}），准备清仓，预计卖出价 ≈ {price:.2f}",
        "sell_price": price,
        "color": "#E84040",
        "bg_color": "#2a0000",
        "action": "清仓",
    }


# ─────────────────────────────────────────────────────────────────────────────
# 数据状态指示灯
# ─────────────────────────────────────────────────────────────────────────────

def _data_status_icon(price_data: dict) -> str:
    """根据数据新鲜度返回 🟢/🟡/🔴"""
    if price_data.get("stale", False):
        stale_sec = price_data.get("stale_seconds", 9999)
        if stale_sec < 120:   return "🟡"
        return "🔴"
    return "🟢"


# ─────────────────────────────────────────────────────────────────────────────
# 加载持仓数据（from sim_trades）
# ─────────────────────────────────────────────────────────────────────────────

@st.cache_data(ttl=60, show_spinner=False)
def _load_open_positions() -> pd.DataFrame:
    """加载当前持仓中的模拟交易"""
    try:
        engine = storage._get_engine()
        from sqlalchemy import text
        sql = text("""
            SELECT trade_date, code, name, buy_price, buy_source,
                   list_price, created_at, signal_score, t2_win_rate, reason_json
            FROM sim_trades
            WHERE status = 'open'
            ORDER BY created_at DESC
        """)
        df = pd.read_sql(sql, engine)
        return df
    except Exception as e:
        logger.warning(f"加载持仓失败: {e}")
        return pd.DataFrame()


@st.cache_data(ttl=300, show_spinner=False)
def _load_today_candidates() -> pd.DataFrame:
    """加载今日龙虎榜候选标的（用于非交易时段展示）"""
    try:
        top10 = storage.read("lhb_top10")
        if top10.empty:
            return pd.DataFrame()
        latest_date = top10["trade_date"].max()
        today_data = top10[top10["trade_date"] == latest_date].copy()
        today_data["代码"] = today_data["代码"].astype(str).str.zfill(6)
        today_data["龙虎榜净买额"] = pd.to_numeric(today_data["龙虎榜净买额"], errors="coerce")
        today_data["流通市值"]    = pd.to_numeric(today_data["流通市值"],    errors="coerce")
        today_data["换手率"]      = pd.to_numeric(today_data["换手率"],      errors="coerce")
        today_data["净买入亿"]    = (today_data["龙虎榜净买额"] / 1e8).round(2)
        today_data["市值亿"]      = (today_data["流通市值"] / 1e8).round(0)
        return today_data.sort_values("龙虎榜净买额", ascending=False).head(20)
    except Exception as e:
        logger.warning(f"加载今日候选失败: {e}")
        return pd.DataFrame()


# ─────────────────────────────────────────────────────────────────────────────
# 单只持仓卡片渲染
# ─────────────────────────────────────────────────────────────────────────────

def _render_position_card(pos: dict, price_data: dict, idx: int):
    """渲染单只持仓的实时监控卡片"""
    code       = str(pos.get("code", "")).zfill(6)
    name       = pos.get("name", code)
    buy_price  = float(pos.get("buy_price", 0) or 0)
    trade_date = str(pos.get("trade_date", ""))

    ms_data   = calc_ms_score(code, price_data, buy_price, trade_date)
    advice    = get_sell_advice(
        price_data.get("price", 0), buy_price, ms_data, price_data
    )

    price     = float(price_data.get("price", 0) or 0)
    chg_pct   = float(price_data.get("change_pct", 0) or 0)
    stale     = price_data.get("stale", False)
    source    = price_data.get("source", "—")
    status_icon = _data_status_icon(price_data)
    board     = _board_name(code)

    ms        = ms_data.get("ms", 50)
    ts        = ms_data.get("trailing_stop", 0)
    sl        = ms_data.get("stop_loss", 0)
    ms_bar_str = _ms_bar(ms)
    ms_lbl    = _ms_label(ms)
    ms_clr    = _ms_color(ms)

    pnl_pct   = (price / buy_price - 1) * 100 if (buy_price > 0 and price > 0) else 0
    pnl_clr   = "#E84040" if pnl_pct >= 0 else "#33CC66"
    chg_clr   = "#E84040" if chg_pct >= 0 else "#33CC66"
    chg_str   = f"+{chg_pct:.2f}%" if chg_pct >= 0 else f"{chg_pct:.2f}%"
    pnl_str   = f"+{pnl_pct:.2f}%" if pnl_pct >= 0 else f"{pnl_pct:.2f}%"

    adv_clr  = advice["color"]
    adv_bg   = advice["bg_color"]
    adv_lbl  = advice["label"]

    with st.container():
        st.markdown(
            f"""
            <div style="
                background:#1a1a2e;
                border:1px solid #2a2a4a;
                border-radius:10px;
                padding:18px 22px 14px 22px;
                margin-bottom:16px;
            ">
              <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
                <span style="font-size:1.15em;font-weight:bold;color:#e0e0e0;">
                  持仓 {idx}　{name}（{code}）　<span style="font-size:0.85em;color:#888;">{board}</span>
                </span>
                <span style="font-size:0.85em;color:#888;">{status_icon} {source}</span>
              </div>

              <div style="display:flex;gap:30px;margin-bottom:12px;flex-wrap:wrap;">
                <div>
                  <span style="color:#888;font-size:0.85em;">买入价</span><br/>
                  <span style="font-size:1.1em;">¥{buy_price:.3f}</span>
                </div>
                <div>
                  <span style="color:#888;font-size:0.85em;">现价</span><br/>
                  <span style="font-size:1.3em;font-weight:bold;color:{chg_clr};">
                    ¥{price:.3f} ({chg_str})
                  </span>
                </div>
                <div>
                  <span style="color:#888;font-size:0.85em;">持仓盈亏</span><br/>
                  <span style="font-size:1.1em;color:{pnl_clr};">{pnl_str}</span>
                </div>
                <div>
                  <span style="color:#888;font-size:0.85em;">T日</span><br/>
                  <span style="font-size:0.95em;">{trade_date}</span>
                </div>
              </div>

              <div style="
                background:{adv_bg};
                border:1px solid {adv_clr};
                border-radius:8px;
                padding:12px 16px;
                margin-bottom:10px;
              ">
                <div style="margin-bottom:6px;">
                  <span style="color:#888;font-size:0.82em;">当前动能</span>　
                  <span style="color:{ms_clr};font-size:1.05em;font-weight:bold;">
                    MS = {ms} ({ms_lbl})
                  </span>　
                  <span style="font-size:0.9em;color:{ms_clr};">{ms_bar_str}</span>
                </div>
                <div style="margin-bottom:6px;">
                  <span style="font-size:0.85em;color:#aaa;">
                    V_score {ms_data.get('v_score',50):.0f}
                    ・P_score {ms_data.get('p_score',50):.0f}
                    ・B_score {ms_data.get('b_score',50):.0f}
                  </span>
                </div>
                <div style="color:{adv_clr};font-size:1.0em;font-weight:bold;">{adv_lbl}</div>
                <div style="margin-top:6px;font-size:0.85em;color:#888;">
                  移动止盈线: ¥{ts:.3f} &nbsp;|&nbsp;
                  止损线: ¥{sl:.3f}（买入价 -3%）
                </div>
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )


# ─────────────────────────────────────────────────────────────────────────────
# 主渲染函数
# ─────────────────────────────────────────────────────────────────────────────

def render():
    st.title("📡 实时监控仪表盘")
    st.caption(
        "龙虎榜策略盘中实时监控 | MS 动能评分 + 移动止盈线 + 预计卖出价 | "
        "数据源: AKShare → 新浪 → 缓存"
    )

    # ── 顶部状态栏 ───────────────────────────────────────────────────────────
    now_str   = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    trading   = _is_trading_now()
    pre_mkt   = _is_pre_market()
    interval  = _refresh_interval()

    col_h1, col_h2, col_h3, col_h4 = st.columns([3, 2, 2, 2])
    col_h1.markdown(f"**🕐 {now_str}**")
    if trading:
        col_h2.success("🟢 交易时段")
    elif pre_mkt:
        col_h2.warning("🟡 集合竞价")
    else:
        col_h2.info("⚫ 非交易时段")
    col_h3.metric("刷新间隔", f"{interval}秒")
    col_h4.markdown(f"<span style='color:#888'>数据: AKShare L2 → 新浪 L3 → 缓存</span>", unsafe_allow_html=True)

    st.divider()

    # ── 加载持仓 ─────────────────────────────────────────────────────────────
    positions_df = _load_open_positions()

    if positions_df.empty:
        # 非交易时段或无持仓：展示今日候选
        st.subheader("📋 当前无持仓")
        st.caption("模拟炒股机器人未持有任何标的。以下是最新龙虎榜候选标的供参考。")

        candidates = _load_today_candidates()
        if not candidates.empty:
            show_cols = ["代码", "名称", "净买入亿", "市值亿", "换手率", "涨跌幅", "trade_date"]
            avcols = [c for c in show_cols if c in candidates.columns]
            st.dataframe(
                candidates[avcols].reset_index(drop=True),
                use_container_width=True,
                hide_index=True,
                column_config={
                    "净买入亿": st.column_config.NumberColumn("净买入(亿)", format="%.2f"),
                    "市值亿":   st.column_config.NumberColumn("市值(亿)", format="%.0f"),
                    "换手率":   st.column_config.NumberColumn("换手率%", format="%.2f%%"),
                    "涨跌幅":   st.column_config.NumberColumn("涨跌幅%", format="%.2f%%"),
                    "trade_date": st.column_config.TextColumn("上榜日"),
                },
            )
        else:
            st.info("暂无龙虎榜数据，请先在「⚙️ 数据管理」拉取。")

        # 刷新按钮（非交易时段不自动刷新）
        if st.button("🔄 手动刷新", key="manual_refresh"):
            st.cache_data.clear()
            st.rerun()
        return

    # ── 有持仓：获取实时价格 ─────────────────────────────────────────────────
    codes = positions_df["code"].astype(str).str.zfill(6).tolist()

    with st.spinner(f"拉取 {len(codes)} 只持仓的实时行情..."):
        prices = fetch_realtime_prices(codes)

    # ── 顶部持仓摘要 KPI ──────────────────────────────────────────────────────
    total_pnl_pct = []
    for _, pos in positions_df.iterrows():
        code = str(pos["code"]).zfill(6)
        pd_  = prices.get(code, {})
        p    = float(pd_.get("price", 0) or 0)
        bp   = float(pos.get("buy_price", 0) or 0)
        if p > 0 and bp > 0:
            total_pnl_pct.append((p / bp - 1) * 100)

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("持仓数量", f"{len(positions_df)} 笔")
    avg_pnl = sum(total_pnl_pct) / len(total_pnl_pct) if total_pnl_pct else 0
    k2.metric("平均盈亏%", f"{avg_pnl:+.2f}%",
              delta_color="normal" if avg_pnl > 0 else "inverse")
    all_green = all(p >= 0 for p in total_pnl_pct)
    k3.metric("盈利笔数", f"{sum(1 for p in total_pnl_pct if p > 0)}/{len(total_pnl_pct)}")
    stale_cnt = sum(1 for c in codes if prices.get(c, {}).get("stale", False))
    k4.metric("数据正常", f"{len(codes) - stale_cnt}/{len(codes)}",
              delta="🟢 全部正常" if stale_cnt == 0 else f"🟡 {stale_cnt}只延迟",
              delta_color="off")

    st.divider()

    # ── 各持仓卡片 ────────────────────────────────────────────────────────────
    st.subheader("💼 持仓实时状态")

    for idx, (_, pos) in enumerate(positions_df.iterrows(), 1):
        code = str(pos["code"]).zfill(6)
        pd_  = prices.get(code, {})
        _render_position_card(dict(pos), pd_, idx)

    st.divider()

    # ── 市场快照（上证/深证指数）─────────────────────────────────────────────
    st.subheader("📊 市场快照")
    with st.spinner("拉取指数行情..."):
        index_data = {}
        try:
            if _HAS_AKSHARE:
                idx_df = ak.stock_zh_index_spot_em(symbol="沪深重要指数")
                if idx_df is not None and not idx_df.empty:
                    for _, row in idx_df.iterrows():
                        nm = str(row.get("名称", ""))
                        if "上证" in nm or "沪" in nm:
                            index_data["上证"] = {
                                "price":  row.get("最新价", 0),
                                "change": row.get("涨跌幅", 0),
                            }
                        elif "深证" in nm or "深成" in nm:
                            index_data["深证"] = {
                                "price":  row.get("最新价", 0),
                                "change": row.get("涨跌幅", 0),
                            }
                        elif "创业板" in nm:
                            index_data["创业板"] = {
                                "price":  row.get("最新价", 0),
                                "change": row.get("涨跌幅", 0),
                            }
        except Exception as e:
            logger.debug(f"指数行情失败: {e}")

    if index_data:
        icols = st.columns(len(index_data))
        for i, (nm, d) in enumerate(index_data.items()):
            chg = float(d.get("change", 0) or 0)
            icols[i].metric(
                nm,
                f"{float(d.get('price', 0) or 0):,.2f}",
                delta=f"{chg:+.2f}%",
                delta_color="normal" if chg > 0 else "inverse",
            )
    else:
        st.caption("指数行情暂无数据")

    st.divider()

    # ── 刷新控制 ─────────────────────────────────────────────────────────────
    c_ref1, c_ref2, c_ref3 = st.columns([2, 2, 3])
    with c_ref1:
        auto_refresh = st.toggle("🔁 自动刷新", value=trading or pre_mkt, key="auto_refresh_toggle")
    with c_ref2:
        if st.button("🔄 立即刷新", key="manual_refresh_btn"):
            st.cache_data.clear()
            st.rerun()
    with c_ref3:
        st.caption(
            f"当前刷新间隔：**{interval}秒**（{'交易时段' if trading else '集合竞价' if pre_mkt else '非交易时段'}）"
            f" | 下次刷新：约 {interval}s 后"
        )

    if auto_refresh:
        time.sleep(interval)
        st.rerun()
