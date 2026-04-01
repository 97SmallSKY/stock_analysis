"""
每日交易执行引擎
================
负责：
  Phase A（盘后 18:45）：
    1. 结算已持仓记录（T+2 以上的订单，检查是否到达挂牌价 / 止损）
    2. 选今日目标股 + 设挂牌价 → 写入 DB（status=open，buy_price=NULL 等T+1确认）
    3. 更新 sim_summary

  Phase B（次日 09:35 开盘后）：
    4. 回填 T+1 开盘价（买入价）、买入股数

  由 sim_robot_run.py 的 CLI 分别触发两个 phase。

关键业务逻辑：
  - T+2 最高价 >= 挂牌价 → 止盈成交，卖出价 = 挂牌价
  - T+2 收盘价 < 买入价 × (1 + STOP_LOSS_PCT/100) → 止损
  - 超过 MAX_HOLD_DAYS 天未成交 → 以当日收盘价平仓（持有到期）
"""
import sys
import os
import json
import time
from pathlib import Path
from datetime import date, timedelta

import akshare as ak
import pandas as pd
from loguru import logger

_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(_ROOT))
os.chdir(_ROOT)

from utils.retry import safe_akshare_call, fetch_sina_quote
from skills.stock_sim_robot.sim_trade_db import (
    create_tables,
    open_trade,
    get_open_positions,
    update_buy_price,
    close_trade,
    refresh_summary,
    is_trade_exists_for_date,
    _current_week_tag,
    save_selection_log,
    save_nl_strategy,
    write_audit_log,
    AUDIT_PHASE_A_SELECT,
    AUDIT_PHASE_A_SKIP,
    get_available_capital,
    TOTAL_CAPITAL,
)
from skills.stock_sim_robot.fetch_signal import get_mainboard_signals
from skills.stock_sim_robot.llm_select import select_stock, STOP_LOSS_PCT

MAX_HOLD_DAYS       = 3  # 超过 3 个交易日强制平仓
MAX_OPEN_POSITIONS  = 2  # 同时最多持仓数量（含 buy_price=None 的待回填仓）
BUY_AMOUNT          = TOTAL_CAPITAL  # 兼容旧代码（现在总资金即10万）


# ── 飞书报警（Phase B 失败时使用）────────────────────────────────────────

_OPENCLAW_BIN  = os.getenv("OPENCLAW_BIN", "openclaw")
_FEISHU_TARGET = os.getenv("FEISHU_TARGET", "ou_36a79ea49476c9b64d2361a9ab3bf4ca")


def _send_feishu_alert(message: str):
    """
    通过 openclaw message send 发送飞书报警（不阻塞主流程，非致命）
    """
    import subprocess
    try:
        result = subprocess.run(
            [_OPENCLAW_BIN, "message", "send",
             "--channel", "feishu",
             "--target",  _FEISHU_TARGET,
             "--message", message],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            logger.info(f"飞书报警发送成功: {message[:50]}...")
        else:
            logger.error(f"飞书报警发送失败: {result.stderr[:100]}")
    except Exception as e:
        logger.error(f"飞书报警发送异常: {e}")


# ── 交易日历工具 ──────────────────────────────────────────────────────

_TRADE_DATES_CACHE: list[str] = []   # 模块级缓存，避免反复拉取
_TRADE_DATES_CACHE_DATE: str = ""     # 缓存日期，当天有效


def _get_trade_dates() -> list[str]:
    """
    获取交易日历列表（YYYYMMDD 格式，升序）。
    当天内缓存，失败时返回空列表（调用方自行降级）。
    """
    global _TRADE_DATES_CACHE, _TRADE_DATES_CACHE_DATE
    today_str = date.today().strftime("%Y%m%d")
    if _TRADE_DATES_CACHE and _TRADE_DATES_CACHE_DATE == today_str:
        return _TRADE_DATES_CACHE
    try:
        df = safe_akshare_call(
            ak.tool_trade_date_hist_sina,
            max_retries=2, base_delay=1.0, fallback=None,
        )
        if df is not None and not df.empty:
            col = df.columns[0]
            dates = sorted(df[col].astype(str).str.replace("-", "").tolist())
            _TRADE_DATES_CACHE = dates
            _TRADE_DATES_CACHE_DATE = today_str
            return dates
    except Exception as e:
        logger.warning(f"获取交易日历失败: {e}")
    return []


def _is_trading_day(d: str | date | None = None) -> bool:
    """
    检查指定日期是否为交易日（优先使用完整交易日历含节假日，失败降级为跳过周末）
    """
    if d is None:
        d = date.today()
    if isinstance(d, str):
        d = date.fromisoformat(f"{d[:4]}-{d[4:6]}-{d[6:]}")
    if d.weekday() >= 5:  # 周末直接排除
        return False
    trade_dates = _get_trade_dates()
    if trade_dates:
        return d.strftime("%Y%m%d") in trade_dates
    # fallback：仅过滤周末
    return True


def _get_nth_trading_day(start_date: str, n: int) -> str:
    """
    从 start_date 起，取第 n 个交易日。
    严格遵循总控 Prompt：优先使用完整交易日历（含节假日），
    禁止使用自然日 +1 或 kline.iloc[1] 替代。
    """
    trade_dates = _get_trade_dates()
    if trade_dates:
        # 精确：从 start_date 之后取第 n 个交易日
        future = [d for d in trade_dates if d > start_date]
        if len(future) >= n:
            return future[n - 1]
        logger.warning(f"交易日历数据不足（start={start_date}, n={n}），降级处理")

    # fallback：跳过周末（不含节假日）
    d = date.fromisoformat(f"{start_date[:4]}-{start_date[4:6]}-{start_date[6:]}")
    count = 0
    while count < n:
        d += timedelta(days=1)
        if d.weekday() < 5:
            count += 1
    return d.strftime("%Y%m%d")


def _trading_days_since(trade_date_str: str) -> int:
    """
    从 trade_date_str 到今天经过多少交易日（含节假日日历，严格 T+N 语义）。
    失败时降级为自然日 * 5/7（保守估算，不会误触发强制平仓）。
    """
    try:
        today_str = date.today().strftime("%Y%m%d")
        trade_dates = _get_trade_dates()
        if trade_dates:
            # 区间内的交易日数量（不含 trade_date_str，含 today）
            between = [d for d in trade_dates if trade_date_str < d <= today_str]
            return len(between)
        # fallback：自然日粗略转换
        td = date.fromisoformat(f"{trade_date_str[:4]}-{trade_date_str[4:6]}-{trade_date_str[6:]}")
        natural = (date.today() - td).days
        return max(0, int(natural * 5 / 7))
    except Exception:
        return 0


def _days_since(trade_date_str: str) -> int:
    """从 trade_date_str 到今天经过多少自然日（仅用于日志/报告，不用于 T+N 判断）"""
    try:
        td = date.fromisoformat(f"{trade_date_str[:4]}-{trade_date_str[4:6]}-{trade_date_str[6:]}")
        return (date.today() - td).days
    except Exception:
        return 0


# ── 价格获取 ──────────────────────────────────────────────────────────

def _fetch_kline(code: str, start_date: str, days: int = 5) -> pd.DataFrame | None:
    """拉取 start_date 起 days 根 K 线（不复权）"""
    prefix  = "sh" if code.startswith(("6", "9")) else "sz"
    end_dt  = (pd.Timestamp(start_date) + pd.Timedelta(days=days * 2 + 5)).strftime("%Y%m%d")
    symbol  = f"{prefix}{code}"
    source  = f"AKShare stock_zh_a_daily ({symbol})"
    try:
        raw = safe_akshare_call(
            ak.stock_zh_a_daily,
            symbol=symbol, adjust="",  # 不复权，拿真实价
            start_date=start_date, end_date=end_dt,
            max_retries=2, base_delay=2.0, fallback=None,
        )
        if raw is not None and not raw.empty:
            raw = raw.rename(columns={
                "date": "日期", "open": "开盘",
                "high": "最高", "low": "最低", "close": "收盘",
            })
            return raw.sort_values("日期").reset_index(drop=True)
    except Exception as e:
        import requests
        if isinstance(e, requests.exceptions.ConnectionError):
            logger.warning(f"  [{source}] 网络连接失败（DNS解析/TCP连接被拒绝）: {type(e).__name__}")
        elif isinstance(e, requests.exceptions.Timeout):
            logger.warning(f"  [{source}] 请求超时: {type(e).__name__}")
        elif "429" in str(e) or "Too Many Requests" in str(e) or "rate" in str(e).lower():
            logger.warning(f"  [{source}] 接口限流 HTTP 429: {type(e).__name__}")
        elif "ProxyError" in type(e).__name__ or "proxy" in str(e).lower():
            logger.warning(f"  [{source}] 代理错误（VPS代理配置问题）: {type(e).__name__}: {e}")
        else:
            logger.warning(f"  [{source}] 未知错误: {type(e).__name__}: {e}")
    return None


def _get_realtime_open_price(code: str) -> tuple[float | None, str]:
    """
    获取当日实时开盘价（用于 Phase B 集合竞价买入价回填）

    策略（四层备用，优先使用无代理限制的接口）：
      1. 新浪实时行情（直接 HTTP，无代理，最稳定）
      2. AKShare stock_zh_a_spot_em（东财实时行情，依赖代理，集合竞价期间不稳定）
      3. AKShare stock_zh_a_hist_min_em（分钟K线）
      4. AKShare stock_zh_a_daily 当日 K 线（09:35 可能还没有）

    Returns:
        (price, source) — price=None 表示获取失败
    """
    # ── 方法 1（最稳定）：新浪实时行情，代理感知（先走代理，失败自动直连）────
    try:
        quote = fetch_sina_quote(code, timeout=8)
        if quote:
            v = quote["open"]  # 今开
            if v > 0:
                logger.info(f"  新浪实时行情获取 {code} 今日开盘价={v}")
                return round(v, 3), "sina_realtime"
    except Exception as e:
        import requests
        if isinstance(e, requests.exceptions.ConnectionError):
            logger.warning(f"  [方法1/新浪实时行情/{code}] 网络连接失败（DNS/TCP）: {type(e).__name__}")
        elif isinstance(e, requests.exceptions.Timeout):
            logger.warning(f"  [方法1/新浪实时行情/{code}] 请求超时（>8s）: {type(e).__name__}")
        elif "429" in str(e) or "Too Many Requests" in str(e):
            logger.warning(f"  [方法1/新浪实时行情/{code}] 接口限流 HTTP 429")
        else:
            logger.warning(f"  [方法1/新浪实时行情/{code}] 失败: {type(e).__name__}: {e}")

    # ── 方法 2：东财实时行情（含今日开盘价 open）─────────────────────
    try:
        df = safe_akshare_call(
            ak.stock_zh_a_spot_em,
            max_retries=2, base_delay=1.5, fallback=None,
        )
        if df is not None and not df.empty:
            code_col = "代码" if "代码" in df.columns else "股票代码"
            row = df[df[code_col].astype(str).str.zfill(6) == code]
            if not row.empty:
                for col in ["今开", "开盘价", "今日开盘价"]:
                    if col in row.columns:
                        v = float(row.iloc[0][col])
                        if v > 0:
                            logger.info(f"  实时行情[东财]获取 {code} 开盘价={v}")
                            return round(v, 3), "akshare_spot"
    except Exception as e:
        import requests
        if isinstance(e, requests.exceptions.ConnectionError):
            logger.warning(f"  [方法2/东财实时行情/{code}] 网络连接失败（可能需要代理，VPS应设NO_PROXY）: {type(e).__name__}")
        elif isinstance(e, requests.exceptions.Timeout):
            logger.warning(f"  [方法2/东财实时行情/{code}] 请求超时: {type(e).__name__}")
        elif "429" in str(e) or "Too Many Requests" in str(e):
            logger.warning(f"  [方法2/东财实时行情/{code}] 接口限流 HTTP 429")
        else:
            logger.warning(f"  [方法2/东财实时行情/{code}] 失败: {type(e).__name__}: {e}")

    # ── 方法 3：分钟 K 线（取当日第一根）─────────────────────────────
    try:
        df2 = safe_akshare_call(
            ak.stock_zh_a_hist_min_em,
            symbol=code, period="1", adjust="",
            start_date=date.today().strftime("%Y-%m-%d %H:%M:%S"),
            end_date=date.today().strftime("%Y-%m-%d %H:%M:%S"),
            max_retries=1, base_delay=2.0, fallback=None,
        )
        if df2 is not None and not df2.empty:
            v = float(df2.iloc[0]["开盘"] if "开盘" in df2.columns else df2.iloc[0].iloc[1])
            if v > 0:
                logger.info(f"  分钟K线获取 {code} 今日开盘价={v}")
                return round(v, 3), "akshare_min"
    except Exception as e:
        logger.warning(f"  分钟K线获取失败 [{code}]: {e}")

    # ── 方法 4：日 K 线当日开盘价（09:35 有时数据还未入库）─────────────
    try:
        today_str = date.today().strftime("%Y%m%d")
        kline = _fetch_kline(code, today_str, days=1)
        if kline is not None and not kline.empty:
            today_rows = kline[kline["日期"].astype(str).str.replace("-", "") == today_str]
            if not today_rows.empty:
                v = float(today_rows.iloc[0]["开盘"])
                if v > 0:
                    logger.info(f"  日K线获取 {code} 今日开盘价={v}")
                    return round(v, 3), "akshare_daily"
    except Exception as e:
        import requests
        if isinstance(e, requests.exceptions.ConnectionError):
            logger.warning(f"  [方法4/日K线/{code}] 网络连接失败: {type(e).__name__}")
        elif isinstance(e, requests.exceptions.Timeout):
            logger.warning(f"  [方法4/日K线/{code}] 请求超时: {type(e).__name__}")
        else:
            logger.warning(f"  [方法4/日K线/{code}] 失败: {type(e).__name__}: {e}")

    logger.warning(f"  {code} 四种方式均未获取到今日开盘价")
    return None, "failed"


def _get_open_price_t1(code: str, trade_date: str) -> tuple[float | None, str]:
    """
    获取 T+1 日开盘价（用作买入价）

    - 若今天就是 T+1：优先用实时行情
    - 否则：用历史 K 线

    Returns:
        (price, source)
    """
    t1_date = _get_nth_trading_day(trade_date, 1)
    today_str = date.today().strftime("%Y%m%d")

    if today_str == t1_date:
        # 今天就是 T+1，尝试获取实时开盘价
        price, source = _get_realtime_open_price(code)
        if price and price > 0:
            return price, source
        # 实时行情失败，fallback 到日K线
        logger.info(f"  实时行情失败，尝试日K线...")

    # 历史日K线
    kline = _fetch_kline(code, t1_date, days=2)
    if kline is None or kline.empty:
        return None, "failed"
    t1_rows = kline[kline["日期"].astype(str).str.replace("-", "") == t1_date]
    if not t1_rows.empty:
        return round(float(t1_rows.iloc[0]["开盘"]), 3), "akshare_daily_hist"
    if len(kline) >= 1:
        return round(float(kline.iloc[0]["开盘"]), 3), "akshare_daily_hist"
    return None, "failed"


def _get_t2_prices(code: str, trade_date: str) -> dict | None:
    """
    获取 T+2 日价格（用于结算）。
    T+2 = trade_date 之后的第 2 个交易日，通过交易日历精确定位，
    禁止使用 kline.iloc[1]（违反总控 Prompt 规则）。
    Returns: {date, open, high, low, close} 或 None
    """
    t2_date = _get_nth_trading_day(trade_date, 2)  # T 之后第 2 个交易日
    kline   = _fetch_kline(code, t2_date, days=3)
    if kline is None or kline.empty:
        return None
    # 精确日期匹配，不依赖 iloc 偏移
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
    # 日期匹配失败 fallback：取 kline 第一行（极少数情况）
    if not kline.empty:
        row = kline.iloc[0]
        actual_date = str(row["日期"]).replace("-", "")
        logger.warning(f"  {code} T+2={t2_date} 日期精确匹配失败，fallback 取 kline[0]={actual_date}")
        return {
            "date":  actual_date,
            "open":  round(float(row["开盘"]), 3),
            "high":  round(float(row["最高"]), 3),
            "low":   round(float(row["最低"]), 3),
            "close": round(float(row["收盘"]), 3),
        }
    return None


def _get_t2_prices_with_retry(code: str, trade_date: str, max_retries: int = 3) -> dict | None:
    """
    获取 T+2 日价格，带重试机制（解决 AKShare 偶发超时问题）

    Args:
        code:        股票代码
        trade_date:  T 日日期（YYYYMMDD）
        max_retries: 最大重试次数（默认3次，间隔递增）

    Returns:
        {date, open, high, low, close} 或 None（全部失败）
    """
    last_err = None
    for attempt in range(1, max_retries + 1):
        try:
            result = _get_t2_prices(code, trade_date)
            if result is not None:
                if attempt > 1:
                    logger.info(f"  {code} T+2价格第{attempt}次获取成功")
                return result
        except Exception as e:
            last_err = e
        if attempt < max_retries:
            wait = attempt * 2  # 2s, 4s, 6s
            logger.warning(f"  {code} T+2价格第{attempt}次失败，{wait}s后重试...")
            time.sleep(wait)

    logger.warning(f"  {code} T+2价格 {max_retries} 次均失败: {last_err}")
    return None


FORCE_CLOSE_DAYS = 5   # 超过 5 个交易日仍未结算，强制平仓（交易日计数）


def _get_latest_close(code: str) -> float | None:
    """获取最新收盘价"""
    prefix = "sh" if code.startswith(("6", "9")) else "sz"
    try:
        raw = safe_akshare_call(
            ak.stock_zh_a_daily,
            symbol=f"{prefix}{code}", adjust="",
            max_retries=2, base_delay=1.5, fallback=None,
        )
        if raw is not None and not raw.empty:
            return round(float(raw["close"].iloc[-1]), 3)
    except Exception:
        pass
    return None


# ── Phase A：盘后结算 + 新开仓 ────────────────────────────────────────

def run_phase_a(trade_date: str | None = None) -> dict:
    """
    Phase A：盘后执行（每日 18:45）

    1. 结算已持仓（已有 buy_price 的 open 记录）
    2. 新开仓（今日未开仓时）
    3. 刷新 sim_summary

    Args:
        trade_date: 今日日期（YYYYMMDD），None 则取今日

    Returns:
        dict，包含 settled_trades / new_trade / summary_text
    """
    if trade_date is None:
        trade_date = date.today().strftime("%Y%m%d")

    today_str  = trade_date
    week_tag   = _current_week_tag()
    result     = {
        "trade_date":      today_str,
        "settled_trades":  [],
        "new_trade":       None,
        "skipped":         False,
        "skip_reason":     "",
        "error":           None,
    }

    # 确保建表
    create_tables()

    # ── Step 1: 结算已持仓 ──────────────────────────────────────────
    open_pos = get_open_positions()
    logger.info(f"当前未平仓记录: {len(open_pos)} 条")

    for _, pos in open_pos.iterrows():
        trade_id   = int(pos["id"])
        code       = str(pos["code"])
        name       = str(pos["name"])
        td         = str(pos["trade_date"])
        buy_price  = pos.get("buy_price")
        list_price = float(pos.get("list_price") or 0)

        # 无买入价 → 说明 T+1 开盘价还未回填，跳过（等 Phase B）
        if pd.isna(buy_price) or buy_price is None or float(buy_price) <= 0:
            logger.info(f"  {name}（{code}）尚无买入价，等待 Phase B 回填")
            continue

        buy_price = float(buy_price)
        days_held = _trading_days_since(td)  # 交易日计数（含节假日日历）
        days_natural = _days_since(td)          # 自然日（仅用于日志）

        logger.info(f"  结算 {name}（{code}）已持 {days_held} 交易日（{days_natural} 自然日），买入价={buy_price}, 挂牌价={list_price}")

        # ── A 股 T+1 合规保护：T+2 日未到则不允许结算 ──────────────────────
        # 本系统语义：trade_date 是 T 日（信号日），T+1 日买入，T+2 日才能卖出
        # 即最早结算日 = _get_nth_trading_day(td, 2)
        t2_date_earliest = _get_nth_trading_day(td, 2)
        if today_str < t2_date_earliest:
            logger.info(
                f"  ⏳ [T+1合规] {name}（{code}）最早可结算日={t2_date_earliest}，"
                f"今日={today_str}，跳过（A股T+1制度）"
            )
            continue

        # ── 超过 FORCE_CLOSE_DAYS 交易日且仍未结算 → 强制平仓（不等 T+2 价格）──
        if days_held >= FORCE_CLOSE_DAYS:
            logger.warning(
                f"  {name}（{code}）持仓超过 {FORCE_CLOSE_DAYS} 交易日，强制以最新收盘价平仓"
            )
            latest_close = _get_latest_close(code)
            if latest_close and latest_close > 0:
                sell_price = latest_close
                sell_date  = today_str
                sell_type  = "超时强制平仓"
                close_trade(
                    trade_id   = trade_id,
                    sell_price = sell_price,
                    sell_date  = sell_date,
                    sell_type  = sell_type,
                    buy_price  = buy_price,
                    buy_shares = int(pos.get("buy_shares") or 0),
                    buy_amount = float(pos.get("buy_amount") or BUY_AMOUNT),
                )
                profit_pct = round((sell_price / buy_price - 1) * 100, 2) if buy_price > 0 else 0
                result["settled_trades"].append({
                    "code": code, "name": name,
                    "sell_type": sell_type, "sell_price": sell_price,
                    "profit_pct": profit_pct,
                })
                logger.info(f"  ✅ {name} [超时强制平仓] 卖出价={sell_price}, 盈亏={profit_pct:+.2f}%")
            else:
                logger.error(f"  {code} 最新收盘价获取失败，超时强制平仓跳过（仍持有）")
            continue

        # 计算 T+2 价格（带重试）
        t2_prices = _get_t2_prices_with_retry(code, td, max_retries=3)
        time.sleep(0.5)

        if t2_prices is None:
            logger.warning(
                f"  {code} T+2 价格 3 次重试均失败，跳过本次结算"
                f"（已持 {days_held} 交易日，超过 {FORCE_CLOSE_DAYS} 交易日后将强制平仓）"
            )
            continue

        t2_high  = t2_prices["high"]
        t2_close = t2_prices["close"]
        t2_date  = t2_prices["date"]
        stop_loss_price = round(buy_price * (1 + STOP_LOSS_PCT / 100), 3)

        # 判断是否止盈
        if list_price > 0 and t2_high >= list_price:
            # 挂单成交 → 止盈
            sell_price = list_price
            sell_type  = "止盈"
        elif t2_close <= stop_loss_price:
            # 止损
            sell_price = t2_close
            sell_type  = "止损"
        elif days_held >= MAX_HOLD_DAYS:
            # 超过 MAX_HOLD_DAYS 交易日 → 持有到期平仓（交易日计数）
            sell_price = t2_close
            sell_type  = "持有到期"
        else:
            logger.info(f"  {name} T+2未触及挂牌价也未止损（T2高={t2_high}, 挂牌={list_price}），继续持有")
            continue

        close_trade(
            trade_id   = trade_id,
            sell_price = sell_price,
            sell_date  = t2_date,
            sell_type  = sell_type,
            buy_price  = buy_price,
            buy_shares = int(pos.get("buy_shares") or 0),
            buy_amount = float(pos.get("buy_amount") or BUY_AMOUNT),
        )
        profit_pct = round((sell_price / buy_price - 1) * 100, 2) if buy_price > 0 else 0
        result["settled_trades"].append({
            "code": code, "name": name,
            "sell_type": sell_type, "sell_price": sell_price,
            "profit_pct": profit_pct,
        })
        logger.info(f"  ✅ {name} [{sell_type}] 卖出价={sell_price}, 盈亏={profit_pct:+.2f}%")

    # ── Step 2: 新开仓 ────────────────────────────────────────────
    if is_trade_exists_for_date(today_str):
        logger.info(f"今日（{today_str}）已有交易记录，跳过开仓")
        result["skipped"]     = True
        result["skip_reason"] = "今日已有交易记录"
    elif len(get_open_positions()) >= MAX_OPEN_POSITIONS:
        # 当前持仓数已达上限，跳过开仓（避免过度分散资金）
        cur_cnt = len(get_open_positions())
        logger.info(f"当前 open 持仓 {cur_cnt} 只 >= MAX_OPEN_POSITIONS({MAX_OPEN_POSITIONS})，跳过开仓")
        result["skipped"]     = True
        result["skip_reason"] = f"持仓已满（{cur_cnt}/{MAX_OPEN_POSITIONS}），等待现有仓位结算"
        try:
            write_audit_log(
                event_type=AUDIT_PHASE_A_SKIP,
                summary=f"Phase A 跳过（{today_str}）：持仓已满 {cur_cnt}/{MAX_OPEN_POSITIONS}",
                detail={"trade_date": today_str, "reason": "max_positions", "cur_cnt": cur_cnt},
                result="skipped",
            )
        except Exception:
            pass
    else:
        logger.info(f"开始选股（{today_str}）...")
        # ── 查询当前可用资金（用于 LLM 仓位决策）─────────────────────
        try:
            capital_info = get_available_capital()
            logger.info(
                f"账户状况: 总={capital_info['total']:,.0f} "
                f"已投={capital_info['invested']:,.0f} "
                f"可用={capital_info['available']:,.0f} "
                f"持仓={capital_info['open_count']}只"
            )
        except Exception as _ce:
            logger.warning(f"get_available_capital 失败，使用默认满额: {_ce}")
            capital_info = None

        signals = get_mainboard_signals(today_str)

        if not signals:
            logger.warning("今日无主板信号，跳过开仓")
            result["skipped"]     = True
            result["skip_reason"] = "今日暂无主板候选信号（非交易日或数据未更新）"
            # 审计日志
            try:
                write_audit_log(
                    event_type=AUDIT_PHASE_A_SKIP,
                    summary=f"Phase A 跳过（{today_str}）：今日暂无主板候选信号",
                    detail={"trade_date": today_str, "reason": "no_signals"},
                    result="skipped",
                )
            except Exception:
                pass
        else:
            selection = select_stock(signals, capital_info=capital_info)

            if selection.get("code") == "SKIP":
                logger.info(f"选股结果：SKIP — {selection.get('reason')}")
                result["skipped"]     = True
                result["skip_reason"] = selection.get("reason", "不操作")
                # 审计日志
                try:
                    write_audit_log(
                        event_type=AUDIT_PHASE_A_SKIP,
                        summary=f"Phase A LLM建议不操作（{today_str}）：{selection.get('reason', '')[:80]}",
                        detail={
                            "trade_date": today_str,
                            "reason": selection.get("reason", ""),
                            "from_llm": selection.get("from_llm", False),
                            "candidates_count": len(signals),
                        },
                        result="skipped",
                    )
                except Exception:
                    pass
            else:
                # 写入 DB（buy_price 在 Phase B 回填）
                seat_tags_str = json_str(selection.get("seat_tags", []))
                record = {
                    "trade_date":      today_str,
                    "code":            selection["code"],
                    "name":            selection["name"],
                    "signal_score":    selection["signal_score"],
                    "t2_win_rate":     selection["t2_win_rate"],
                    "seat_tags":       seat_tags_str,
                    "list_price":      selection["list_price"],
                    "list_reason":     selection.get("reason", ""),
                    "llm_reason":      selection.get("reason", ""),      # 综合摘要
                    "reason_tech":     selection.get("reason_tech", ""),
                    "reason_fund":     selection.get("reason_fund", ""),
                    "reason_history":  selection.get("reason_history", ""),
                    "reason_seat":     selection.get("reason_seat", ""),
                    "buy_amount":      float(selection.get("invest_amount") or TOTAL_CAPITAL),
                    "invest_ratio":    selection.get("invest_ratio", 1.0),
                    "week_tag":        week_tag,
                    "status":          "open",
                }
                new_id = open_trade(record)
                result["new_trade"] = {
                    "id":             new_id,
                    "code":           selection["code"],
                    "name":           selection["name"],
                    "signal_score":   selection["signal_score"],
                    "close_price":    selection["close_price"],
                    "list_price":     selection["list_price"],
                    "list_price_pct": selection["list_price_pct"],
                    "reason":         selection.get("reason", ""),
                    "from_llm":       selection.get("from_llm", False),
                    "t2_win_rate":    selection["t2_win_rate"],
                }
                logger.info(
                    f"✅ 新开仓: {selection['name']}（{selection['code']}）"
                    f" 估算挂牌价={selection['list_price']} "
                    f"(+{selection['list_price_pct']}%)"
                )

                # ── 记录选股过程（入选股 + Top3候选对比）────────────────
                try:
                    _save_selection_log(today_str, new_id, selection, signals)
                except Exception as _sel_err:
                    logger.warning(f"保存选股过程记录失败（非致命）: {_sel_err}")

                # ── 写入审计日志 ─────────────────────────────────────
                try:
                    write_audit_log(
                        event_type=AUDIT_PHASE_A_SELECT,
                        summary=(
                            f"Phase A 选股完成（{today_str}）: "
                            f"{selection['name']}({selection['code']}) "
                            f"评分={selection['signal_score']} "
                            f"挂牌价={selection['list_price']}(+{selection['list_price_pct']}%) "
                            f"{'[LLM]' if selection.get('from_llm') else '[规则引擎]'}"
                        ),
                        trade_id=new_id,
                        code=selection["code"],
                        name=selection["name"],
                        detail={
                            "trade_date":      today_str,
                            "signal_score":    selection["signal_score"],
                            "t2_win_rate":     selection["t2_win_rate"],
                            "close_price":     selection["close_price"],
                            "list_price":      selection["list_price"],
                            "list_price_pct":  selection["list_price_pct"],
                            "from_llm":        selection.get("from_llm", False),
                            "candidates_count": len(signals),
                            "reason":          selection.get("reason", "")[:200],
                        },
                    )
                except Exception:
                    pass

                # ── LLM 草拟卖出策略（异步，不影响主流程）──────────────
                try:
                    _draft_strategy_for_trade(new_id, selection, signals)
                except Exception as _strat_err:
                    logger.warning(f"LLM 草拟策略失败（非致命）: {_strat_err}")

    # ── Step 3: 刷新汇总 ─────────────────────────────────────────
    refresh_summary(week_tag)

    return result


# ── 上一个交易日获取（节假日兼容）────────────────────────────────────

def _get_last_trading_day() -> str:
    """
    获取上一个交易日（兼容法定节假日）

    优先使用 AKShare 交易日历；失败时降级为仅跳过周末。
    在节假日后第一天执行时，确保找到正确的 T 日（而不是节假日）。

    Returns:
        YYYYMMDD 格式的上一个交易日字符串
    """
    today = date.today()
    today_str = today.strftime("%Y%m%d")

    # 方法 1：AKShare 交易日历（最准确，包含法定节假日）
    try:
        df = safe_akshare_call(
            ak.tool_trade_date_hist_sina,
            max_retries=2, base_delay=1.0, fallback=None,
        )
        if df is not None and not df.empty:
            col = df.columns[0]
            trade_dates = sorted(df[col].astype(str).str.replace("-", "").tolist())
            past = [d for d in trade_dates if d < today_str]
            if past:
                result = past[-1]
                logger.info(f"上一个交易日（日历法）: {result}")
                return result
    except Exception as e:
        logger.warning(f"获取交易日历失败，降级处理: {e}")

    # 方法 2（fallback）：仅跳过周末
    yesterday = today - timedelta(days=1)
    while yesterday.weekday() >= 5:
        yesterday -= timedelta(days=1)
    result = yesterday.strftime("%Y%m%d")
    logger.info(f"上一个交易日（跳周末法）: {result}")
    return result


# ── Phase B：回填 T+1 开盘买入价 ─────────────────────────────────────

def run_phase_b(trade_date: str | None = None) -> dict:
    """
    Phase B：T+1 日开盘后（09:35）执行

    - 找到昨日（或近期）open 且 buy_price=NULL 的记录
    - 拉 T+1 开盘价并回填
    - 同时更新挂牌价（因为挂牌价是相对买入价的，之前是用收盘价估算的）

    Args:
        trade_date: T 日日期（即开仓的那天），None 则取上一个交易日
    """
    if trade_date is None:
        # 今天是 T+1，所以 trade_date = 上一个交易日（兼容节假日）
        trade_date = _get_last_trading_day()

    result = {"trade_date": trade_date, "filled": [], "errors": []}
    open_pos = get_open_positions()

    for _, pos in open_pos.iterrows():
        trade_id = int(pos["id"])
        code     = str(pos["code"])
        name     = str(pos["name"])
        td       = str(pos["trade_date"])

        # 只处理 trade_date 对应的开仓记录（且还没有买入价）
        if td != trade_date:
            continue
        if pd.notna(pos.get("buy_price")) and float(pos.get("buy_price") or 0) > 0:
            logger.info(f"  {name}（{code}）买入价已有 ({pos['buy_price']})，跳过")
            continue

        logger.info(f"  回填 {name}（{code}）T+1 开盘价...")
        buy_price, buy_source = _get_open_price_t1(code, td)
        time.sleep(0.5)

        if buy_price is None or buy_price <= 0:
            logger.warning(f"  {code} T+1 开盘价获取失败，将在下次重试")
            # 累加失败次数并判断是否需要飞书报警
            try:
                from skills.stock_sim_robot.sim_trade_db import (
                    increment_phase_b_fail,
                    mark_phase_b_alerted,
                )
                fail_count = increment_phase_b_fail(trade_id)
                already_alerted = bool(pos.get("phase_b_alerted"))
                if fail_count >= 3 and not already_alerted:
                    _send_feishu_alert(
                        f"⚠️ **Phase B 报警**\n"
                        f"股票：{name}（{code}）\n"
                        f"问题：T+1 开盘价连续 {fail_count} 次获取失败\n"
                        f"trade_date={trade_date}，trade_id={trade_id}\n"
                        f"请检查 AKShare 接口或手动补填买入价"
                    )
                    mark_phase_b_alerted(trade_id)
                    logger.warning(f"  已发送飞书报警（连续失败 {fail_count} 次）")
            except Exception as _e:
                logger.debug(f"phase_b_fail 记录非致命: {_e}")
            result["errors"].append({"code": code, "name": name, "error": "T+1开盘价获取失败"})
            continue

        buy_shares = int(BUY_AMOUNT / buy_price / 100) * 100
        if buy_shares <= 0:
            buy_shares = 100

        # 更新挂牌价（基于真实买入价重算）
        old_list_pct = float(pos.get("list_price") or 0)
        old_close    = float(pos.get("list_price") or buy_price * 1.05) / 1.05  # 反推
        # 用 list_reason 里的目标% 重算（存的时候写到 list_reason 里）
        # 简单处理：按相同百分比重算
        if old_list_pct > 0 and old_close > 0:
            pct = old_list_pct / old_close - 1
        else:
            pct = 0.05  # 默认 5%

        new_list_price = round(buy_price * (1 + pct), 2)

        update_buy_price(trade_id, buy_price, buy_shares)

        # 同步更新挂牌价 + 买入价来源
        try:
            from utils.storage import storage
            engine = storage._get_engine()
            from sqlalchemy import text
            with engine.begin() as conn:
                conn.execute(text("""
                    UPDATE sim_trades
                    SET list_price = :lp,
                        buy_source = :src,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = :id
                """), {"id": trade_id, "lp": new_list_price, "src": buy_source})
        except Exception as e:
            logger.warning(f"更新挂牌价/来源失败: {e}")

        result["filled"].append({
            "code":             code,
            "name":             name,
            "buy_price":        buy_price,
            "buy_source":       buy_source,
            "buy_shares":       buy_shares,
            "new_list_price":   new_list_price,
        })
        logger.info(f"  ✅ {name} 买入价={buy_price}（{buy_source}）, 股数={buy_shares}, 挂牌价={new_list_price}")

        # ── 写入交易流水（BUY 事件）────────────────────────────────────────
        try:
            from skills.stock_sim_robot.sim_trade_db import log_trade_event
            log_trade_event(
                trade_id=trade_id,
                code=code,
                name=name,
                event_type="BUY",
                price=buy_price,
                shares=buy_shares,
                event_date=_get_nth_trading_day(td, 1),  # T+1 日
                source=buy_source,
                note=f"Phase B 回填买入价，挂牌价={new_list_price}",
            )
        except Exception as _e:
            logger.debug(f"  写入BUY流水非致命: {_e}")

        # ── 初始化三档分批卖出计划 ───────────────────────────────────────
        try:
            from skills.stock_sim_robot.sim_trade_db import init_batch_sell
            init_batch_sell(trade_id, buy_price, buy_shares)
        except Exception as e:
            logger.warning(f"  初始化分批卖出计划失败: {e}")

    return result


# ── 辅助：JSON 序列化 list ────────────────────────────────────────────

def json_str(obj) -> str:
    import json
    if isinstance(obj, list):
        return json.dumps(obj, ensure_ascii=False)
    return str(obj)


# ── 辅助：记录选股过程（入选 + Top3候选）────────────────────────────────

def _save_selection_log(
    trade_date: str,
    trade_id: int | None,
    selection: dict,
    signals: list[dict],
):
    """
    将本次选股过程写入 sim_selection_log：
    - rank=1：入选股（trade_id 已写入）
    - rank=2/3：Top2 候选（仅记录分析数据，trade_id=None）

    Args:
        trade_date: T 日 YYYYMMDD
        trade_id:   新建交易的 ID（None 表示今日未开仓）
        selection:  select_stock() 返回的入选股结果
        signals:    全部候选信号列表（从中取 Top3）
    """
    from skills.stock_sim_robot.sim_trade_db import save_selection_log as _db_save

    entries = []

    # rank=1：入选股
    entries.append({
        "trade_date":      trade_date,
        "trade_id":        trade_id,
        "rank":            1,
        "code":            selection["code"],
        "name":            selection.get("name", ""),
        "signal_score":    selection.get("signal_score"),
        "t2_win_rate":     selection.get("t2_win_rate"),
        "close_price":     selection.get("close_price"),
        "reason_tech":     selection.get("reason_tech", ""),
        "reason_fund":     selection.get("reason_fund", ""),
        "reason_history":  selection.get("reason_history", ""),
        "llm_rank_reason": selection.get("reason", ""),  # 综合摘要作为排名理由
    })

    # rank=2/3：从 signals 中排除入选股，取次优前2名
    selected_code = selection["code"]
    # 按综合评分降序排列候选
    other_signals = sorted(
        [s for s in signals if str(s.get("code", "")) != selected_code],
        key=lambda x: (x.get("signal_score", 0), x.get("net_buy_amount", 0)),
        reverse=True,
    )
    for rank_idx, sig in enumerate(other_signals[:2], start=2):
        # 简单生成候选理由（无 LLM 详细分析，用规则引擎的摘要格式）
        g20  = sig.get("gain20")
        t2wr = sig.get("t2_win_rate")
        g20_s  = f"{g20:+.1f}%" if g20 is not None else "N/A"
        t2_s   = f"{t2wr:.0f}%" if t2wr is not None else "暂无"
        reason = (
            f"候选排名第{rank_idx}：评分={sig.get('signal_score', 0):.0f}，"
            f"净买入={sig.get('net_buy_yi', 0):.2f}亿，"
            f"T+2胜率={t2_s}，近20日涨幅={g20_s}。"
            f"未入选原因：综合评分低于入选股或历史胜率较低。"
        )
        entries.append({
            "trade_date":      trade_date,
            "trade_id":        None,  # 候选未开仓
            "rank":            rank_idx,
            "code":            str(sig["code"]),
            "name":            sig.get("name", ""),
            "signal_score":    sig.get("signal_score"),
            "t2_win_rate":     sig.get("t2_win_rate"),
            "close_price":     sig.get("close_price"),
            "reason_tech":     "",
            "reason_fund":     f"净买入={sig.get('net_buy_yi', 0):.2f}亿",
            "reason_history":  f"T+2胜率={t2_s}",
            "llm_rank_reason": reason,
        })

    ok = _db_save(entries)
    if ok:
        logger.info(f"选股过程记录写入 sim_selection_log：{len(entries)} 条（入选1+候选{len(entries)-1}）✅")
    else:
        logger.warning("选股过程记录写入失败")


# ── 辅助：LLM 草拟卖出策略（Phase A 开仓后异步触发）─────────────────────

def _draft_strategy_for_trade(trade_id: int, selection: dict, signals: list[dict]):
    """
    选股完成后，调用 LLM 草拟一条自然语言卖出策略，保存为 draft 状态。
    用户在页面查看后可修改并确认激活。

    此函数是非阻塞的最佳努力：任何失败都只记录日志，不影响主流程。
    """
    try:
        from skills.stock_sim_robot.llm_select import draft_nl_strategy
        from skills.stock_sim_robot.sim_trade_db import save_nl_strategy, write_audit_log, AUDIT_STRATEGY_DRAFT

        result = draft_nl_strategy(trade_id, selection, signals)
        if result:
            strategy_id = save_nl_strategy(
                trade_id=trade_id,
                nl_text=result["nl_text"],
                parsed_rules=result.get("parsed_rules"),
                hard_stop_pct=result.get("hard_stop_pct", -5.0),
                source="llm_auto",
                parse_error=result.get("parse_error"),
            )
            if strategy_id:
                write_audit_log(
                    event_type=AUDIT_STRATEGY_DRAFT,
                    summary=(
                        f"LLM 自动草拟策略: {selection['name']}({selection['code']}) "
                        f"strategy_id={strategy_id}"
                    ),
                    trade_id=trade_id,
                    code=selection["code"],
                    name=selection.get("name", ""),
                    detail={
                        "strategy_id": strategy_id,
                        "nl_text_preview": result["nl_text"][:100],
                        "rules_count": len(result.get("parsed_rules", {}).get("rules", [])) if result.get("parsed_rules") else 0,
                        "hard_stop_pct": result.get("hard_stop_pct", -5.0),
                    },
                    result="success",
                )
                logger.info(f"LLM 草拟策略已保存 strategy_id={strategy_id} ✅")
            else:
                logger.warning("LLM 草拟策略保存失败（DB 写入返回 None）")
        else:
            logger.info("LLM 草拟策略跳过（LLM 调用失败或无内容，将使用默认三档策略）")
    except Exception as e:
        logger.warning(f"_draft_strategy_for_trade 非致命异常: {e}")


if __name__ == "__main__":
    import sys
    phase = sys.argv[1] if len(sys.argv) > 1 else "a"
    tdate = sys.argv[2] if len(sys.argv) > 2 else None

    if phase == "a":
        res = run_phase_a(tdate)
        print("Phase A 结果:", res)
    elif phase == "b":
        res = run_phase_b(tdate)
        print("Phase B 结果:", res)
