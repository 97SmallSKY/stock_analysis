"""
post_close_tracker.py — 平仓后跟踪模块

平仓后在 T+1/T+3/T+5/T+10/T+15 采集价格数据，写入 sim_post_tracking 表。
在 daily_update step9 中批量调用。
"""

import logging
from datetime import date as _date

import pandas as pd

from modules.market_data import _get_nth_trading_day, get_kline_raw
from utils.retry import safe_akshare_call

logger = logging.getLogger(__name__)

# 需要跟踪的交易日偏移量
_TRACK_DAYS = [1, 3, 5, 10, 15]

# 上证指数代码
_INDEX_CODE = "000001"


def _get_index_pct(track_date: str) -> float | None:
    """获取上证指数在 track_date 的涨跌幅"""
    try:
        import akshare as ak
        raw = safe_akshare_call(
            ak.stock_zh_index_daily,
            symbol="sh000001",
            max_retries=2,
            base_delay=1.0,
            fallback=None,
        )
        if raw is None or raw.empty:
            return None
        raw["date"] = raw["date"].astype(str).str.replace("-", "")
        row = raw[raw["date"] == track_date]
        if row.empty:
            return None
        close = float(row.iloc[0]["close"])
        prev = raw[raw["date"] < track_date]
        if prev.empty:
            return None
        prev_close = float(prev.iloc[-1]["close"])
        return round((close / prev_close - 1) * 100, 2) if prev_close else None
    except Exception as e:
        logger.debug(f"_get_index_pct [{track_date}] 失败: {e}")
        return None


def track_post_close(
    trade_id: int,
    code: str,
    sell_price: float,
    buy_price: float,
    sell_date: str,
) -> int:
    """
    对单笔已平仓交易采集平仓后 T+N 价格数据，写入 sim_post_tracking。

    Args:
        trade_id:   sim_trades.id
        code:       股票代码
        sell_price: 实际卖出价
        buy_price:  买入价
        sell_date:  卖出日期（YYYYMMDD）

    Returns:
        成功写入的跟踪点数量
    """
    from config.settings import STORAGE_MODE
    if STORAGE_MODE != "postgres":
        logger.info("[post_close_tracker] 非 PostgreSQL 模式，跳过")
        return 0
    from skills.stock_sim_robot.sim_trade_db import save_post_tracking, get_post_tracking

    # 查询已有跟踪记录，跳过已完成的 track_day
    existing = get_post_tracking(trade_id)
    done_days = set(existing["track_day"].tolist()) if not existing.empty else set()

    needed_days = [d for d in _TRACK_DAYS if d not in done_days]
    if not needed_days:
        return 0

    # 拉取从 sell_date 起 15 个交易日以上的 K 线（覆盖所有跟踪点）
    kline = get_kline_raw(code, sell_date, days=20)
    if kline is None or kline.empty:
        logger.warning(f"[post_close_tracker] [{code}] sell_date={sell_date} 无法获取 K 线")
        return 0

    kline["日期"] = kline["日期"].astype(str).str.replace("-", "")
    # 排除 sell_date 当天（T+0 是平仓日本身，不跟踪）
    kline = kline[kline["日期"] > sell_date].reset_index(drop=True)

    if kline.empty:
        return 0

    # 计算 5 日均量（从 sell_date 前取，days=10 确保能覆盖5个交易日）
    kline_before = get_kline_raw(code, sell_date, days=10)
    avg_vol_5d = None
    if kline_before is not None and not kline_before.empty and "成交量" in kline_before.columns:
        kline_before["日期"] = kline_before["日期"].astype(str).str.replace("-", "")
        past = kline_before[kline_before["日期"] <= sell_date].tail(5)
        if len(past) > 0:
            avg_vol_5d = past["成交量"].mean()

    entries = []
    today_str = _date.today().strftime("%Y%m%d")

    for n in needed_days:
        target_date = _get_nth_trading_day(sell_date, n)
        if target_date > today_str:
            # 还没到该跟踪日，跳过（下次 daily_update 再填）
            continue

        row = kline[kline["日期"] == target_date]
        if row.empty:
            # 目标日期 K 线不存在（可能停牌），尝试取最近可用日
            available = kline[kline["日期"] <= target_date]
            if available.empty:
                continue
            row = available.tail(1)

        r = row.iloc[0]
        close = float(r.get("收盘", 0) or 0)
        high  = float(r.get("最高", 0) or 0)
        low   = float(r.get("最低", 0) or 0)
        vol   = float(r.get("成交量", 0) or 0)

        volume_ratio = round(vol / avg_vol_5d, 2) if avg_vol_5d and avg_vol_5d > 0 else None
        pct_vs_sell  = round((close / sell_price - 1) * 100, 2) if sell_price else None
        pct_vs_buy   = round((close / buy_price - 1) * 100, 2) if buy_price else None
        index_pct    = _get_index_pct(str(r.get("日期", target_date)).replace("-", ""))

        entries.append({
            "trade_id":     trade_id,
            "track_day":    n,
            "track_date":   str(r.get("日期", target_date)).replace("-", ""),
            "close":        close,
            "high":         high,
            "low":          low,
            "volume_ratio": volume_ratio,
            "pct_vs_sell":  pct_vs_sell,
            "pct_vs_buy":   pct_vs_buy,
            "index_pct":    index_pct,
        })

    if entries:
        from skills.stock_sim_robot.sim_trade_db import save_post_tracking
        save_post_tracking(entries)
        logger.info(f"[post_close_tracker] trade_id={trade_id} [{code}] 写入 {len(entries)} 个跟踪点")

    return len(entries)


def batch_track_pending() -> int:
    """
    批量处理所有 status='closed' 但 sim_post_tracking 记录数 < 5 的交易。
    在 daily_update step9 中调用。

    Returns:
        处理的交易笔数
    """
    from config.settings import STORAGE_MODE
    if STORAGE_MODE != "postgres":
        logger.info("[post_close_tracker] 非 PostgreSQL 模式，跳过")
        return 0
    from skills.stock_sim_robot.sim_trade_db import get_pending_post_tracking_trades

    pending = get_pending_post_tracking_trades()
    if pending.empty:
        logger.info("[post_close_tracker] batch_track_pending: 无待处理记录")
        return 0

    count = 0
    for _, row in pending.iterrows():
        try:
            n = track_post_close(
                trade_id=int(row["trade_id"]),
                code=str(row["code"]),
                sell_price=float(row["sell_price"] or 0),
                buy_price=float(row["buy_price"] or 0),
                sell_date=str(row["sell_date"]),
            )
            if n > 0:
                count += 1
        except Exception as e:
            logger.error(f"[post_close_tracker] trade_id={row.get('trade_id')} 失败: {e}")

    logger.info(f"[post_close_tracker] batch_track_pending 完成，处理 {count} 笔交易")
    return count


def fill_candidate_returns(trade_date: str) -> int:
    """
    补全指定日期所有候选股（sim_selection_log）的 T+N 实际收益。
    数据来源：AKShare K 线（不复权，用收盘价计算相对涨跌幅）。

    Args:
        trade_date: T 日龙虎榜日期（YYYYMMDD）

    Returns:
        成功处理的候选股数量
    """
    from config.settings import STORAGE_MODE
    if STORAGE_MODE != "postgres":
        logger.info("[post_close_tracker] 非 PostgreSQL 模式，跳过")
        return 0
    from skills.stock_sim_robot.sim_trade_db import (
        get_candidate_results,
        save_candidate_results,
    )

    existing = get_candidate_results(trade_date)
    if existing.empty:
        logger.info(f"[post_close_tracker] {trade_date} 无 selection_log 记录，跳过")
        return 0

    today_str = _date.today().strftime("%Y%m%d")
    entries = []

    for _, row in existing.iterrows():
        if pd.notna(row.get("t5_ret")):
            continue  # 已有数据，跳过

        selection_id = int(row["selection_id"])
        code         = str(row["code"])
        close_price  = float(row.get("close_price") or 0)  # T 日收盘价（基准）

        if close_price <= 0:
            continue

        kline = get_kline_raw(code, trade_date, days=15)
        if kline is None or kline.empty:
            continue

        kline["日期"] = kline["日期"].astype(str).str.replace("-", "")
        kline = kline[kline["日期"] > trade_date].reset_index(drop=True)

        def _ret(n: int):
            target = _get_nth_trading_day(trade_date, n)
            if target > today_str:
                return None, None
            r = kline[kline["日期"] == target]
            if r.empty:
                available = kline[kline["日期"] <= target]
                if available.empty:
                    return None, None
                r = available.tail(1)
            c = float(r.iloc[0].get("收盘", 0) or 0)
            ret = round((c / close_price - 1) * 100, 2) if close_price and c else None
            return ret, c

        t1_ret, t1_close = _ret(1)
        t3_ret, _        = _ret(3)
        t5_ret, t5_close = _ret(5)
        t10_ret, _       = _ret(10)

        entries.append({
            "selection_id": selection_id,
            "trade_date":   trade_date,
            "code":         code,
            "t1_ret":       t1_ret,
            "t3_ret":       t3_ret,
            "t5_ret":       t5_ret,
            "t10_ret":      t10_ret,
            "t1_close":     t1_close,
            "t5_close":     t5_close,
        })

    if entries:
        save_candidate_results(entries)
        logger.info(f"[post_close_tracker] {trade_date} 填充 {len(entries)} 只候选股收益")

    return len(entries)


def batch_fill_candidate_returns() -> int:
    """
    批量补全所有待填充日期的候选股 T+N 收益（daily_update step10 调用）

    Returns:
        处理的日期数量
    """
    from config.settings import STORAGE_MODE
    if STORAGE_MODE != "postgres":
        logger.info("[post_close_tracker] 非 PostgreSQL 模式，跳过")
        return 0
    from skills.stock_sim_robot.sim_trade_db import get_pending_candidate_dates

    dates = get_pending_candidate_dates()
    if not dates:
        logger.info("[post_close_tracker] batch_fill_candidate_returns: 无待处理日期")
        return 0

    count = 0
    for d in dates:
        try:
            n = fill_candidate_returns(d)
            if n > 0:
                count += 1
        except Exception as e:
            logger.error(f"[post_close_tracker] fill_candidate_returns({d}) 失败: {e}")

    logger.info(f"[post_close_tracker] batch_fill_candidate_returns 完成，处理 {count} 个日期")
    return count
