#!/usr/bin/env python3
"""
每日定时数据更新脚本
===================
推荐 cron 配置（周一到周五交易日）：

  # 【盘后主更新】18:40 — 拉取当日龙虎榜、情绪、机构/个股统计
  40 18 * * 1-5  cd ~/stock_analysis && /root/miniconda3/bin/python scripts/daily_update.py >> logs/daily_update.log 2>&1

  # 【次日补全】次日 9:05（开盘前）— 补全 T+1 价格
  5 9 * * 1-5   cd ~/stock_analysis && /root/miniconda3/bin/python scripts/daily_update.py >> logs/daily_update.log 2>&1

  # 【盘中情绪】14:55 — 收盘前拉一次最新涨跌停数（可选）
  55 14 * * 1-5  cd ~/stock_analysis && /root/miniconda3/bin/python scripts/daily_update.py --sentiment-only >> logs/daily_update.log 2>&1

  # 【每小时刷新上榜后N日】当日龙虎榜股票的 T+1/T+2/T+5/T+10 收益会随时间陆续填充
  0 * * * 1-5  cd ~/stock_analysis && /root/miniconda3/bin/python scripts/daily_update.py --steps 11,9 >> logs/hourly_fill.log 2>&1

步骤说明：
  Step 1   拉取今日龙虎榜 Top10（lhb_top10）
  Step 2   拉取今日市场情绪（涨跌停家数/大盘指数）
  Step 3   补全 lhb_top10_prices 中已到期但仍为 NULL 的 T+N 价格列
  Step 4   拉取今日龙虎榜明细（lhb_detail，席位详情，供选股/回测）
  Step 5   拉取机构席位追踪（lhb_jigou，近5日/10日汇总）
  Step 6   拉取个股上榜统计（lhb_ggtj，近5日/10日汇总）
  Step 7   拉取今日龙虎榜席位明细（当日买卖方营业部）
  Step 8   补全量比字段（lhb_top10_prices.volume_ratio）
  Step 9   平仓后价格跟踪（sim_post_tracking T+1/T+3/T+5...）
  Step 10  候选股 T+N 收益填充（sim_candidate_results）
  Step 11  刷新 lhb_top10 中上榜后N日为空的记录（每小时补全）  ← 新增
"""
import sys
import time
import os
import argparse
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

import pandas as pd
import akshare as ak
from loguru import logger

from utils.storage import storage
from utils.retry import safe_akshare_call
from modules.data_fetcher import (
    fetch_lhb_netbuy_by_date,
    fetch_market_sentiment,
    fetch_lhb_detail,
    fetch_lhb_jigou,
    fetch_lhb_ggtj,
)

logger.add(ROOT / "logs/daily_update.log", rotation="7 days", retention="30 days",
           encoding="utf-8", level="INFO")

_DAYS = [1, 2, 5, 10]
TODAY = date.today()
TODAY_STR = TODAY.strftime("%Y%m%d")


# ============================================================
# Step 1: 拉取今日龙虎榜 Top10
# ============================================================
def step1_fetch_top10():
    logger.info("=== Step 1: 拉取今日龙虎榜 Top10 ===")
    try:
        df = fetch_lhb_netbuy_by_date(TODAY_STR, top_n=10)
        if df.empty:
            logger.warning("今日无 Top10 数据（非交易日或接口暂无）")
        else:
            logger.info(f"Top10 获取成功，{len(df)} 条")
    except Exception as e:
        logger.error(f"Step 1 失败: {e}")


# ============================================================
# Step 2: 拉取今日市场情绪
# ============================================================
def step2_fetch_sentiment():
    logger.info("=== Step 2: 拉取今日市场情绪 ===")
    try:
        result = fetch_market_sentiment(TODAY_STR, save=True)
        logger.info(
            f"情绪: 涨停={result.get('limit_up','?')}, "
            f"跌停={result.get('limit_down','?')}, "
            f"信号={result.get('trade_signal','?')}"
        )
    except Exception as e:
        logger.error(f"Step 2 失败: {e}")


# ============================================================
# Step 3: 补全 T+N NULL 价格列
# ============================================================
def step3_fill_null_prices():
    logger.info("=== Step 3: 补全 T+N NULL 价格列 ===")
    try:
        from sqlalchemy import text
        tp = storage.read("lhb_top10_prices")
        t10 = storage.read("lhb_top10")
        if tp.empty or t10.empty:
            logger.info("暂无价格数据，跳过")
            return

        engine = storage._get_engine()

        def can_fill(trade_date_str, n):
            td = pd.Timestamp(trade_date_str).date()
            # 用工作日粗略估算经过的交易日
            days_passed = (TODAY - td).days
            # 对于 T+1 只要过了 1 天就可以尝试；T+2 过了 2 天就可以；T+5 过了 7 天；T+10 过了 14 天
            # 但其实 fetch_sina 会返回能拿到的 K 线长度，我们可以用实际返回长度判断，
            # 这里的 can_fill 只是一个粗筛，避免太早请求 API
            min_days = int(n * 1.4) if n > 2 else n
            return days_passed >= min_days

        def fetch_kline(code, trade_date_str):
            """获取K线数据，优先新浪，失败则用东方财富备用源
            支持主板/创业板/科创板/北交所/可转债等各类代码"""
            c = str(code).zfill(6)
            # 新浪前缀规则：
            #   sh - 沪市 (6开头) + 上交所国债/基金 (11/51开头)
            #   sz - 深市 (0/1/2/3开头) + 深交所可转债 (127/128开头)
            #   bj - 北交所 (4/8开头，82/83/87/88/43)
            if c.startswith("6") or c[:2] in ("11", "51"):
                prefix = "sh"
            elif c[0] in ("4", "8") or c[:2] in ("82", "83", "87", "88", "43"):
                prefix = "bj"  # 北交所
            else:
                prefix = "sz"
            end_dt = (pd.Timestamp(trade_date_str) + pd.Timedelta(days=40)).strftime("%Y%m%d")

            # 主源：新浪 stock_zh_a_daily（北交所也可用此接口）
            for attempt in range(2):
                try:
                    raw = ak.stock_zh_a_daily(
                        symbol=f"{prefix}{c}", adjust="qfq",
                        start_date=trade_date_str, end_date=end_dt,
                    )
                    if raw is not None and len(raw) >= 2:
                        raw = raw.rename(columns={
                            "date": "日期", "open": "开盘",
                            "high": "最高", "low": "最低", "close": "收盘",
                        })
                        if all(col in raw.columns for col in ["日期", "开盘", "最高", "最低", "收盘"]):
                            return raw[["日期", "开盘", "最高", "最低", "收盘"]].sort_values("日期").reset_index(drop=True)
                except Exception as e:
                    status_code = getattr(getattr(e, 'response', None), 'status_code', 'N/A')
                    logger.error(
                        f"  [新浪日K/{attempt+1}/2] {prefix}{c} 拉取失败 "
                        f"| 接口=stock_zh_a_daily "
                        f"| 错误类型={type(e).__name__} "
                        f"| 状态码={status_code} "
                        f"| 详情={str(e)[:300]}"
                    )
                    time.sleep(2)

            # 备用源：东方财富 stock_zh_a_hist（支持北交所代码直接传6位）
            logger.info(f"  {c} 切换东方财富备用源...")
            for attempt in range(2):
                try:
                    raw2 = ak.stock_zh_a_hist(
                        symbol=c, period="daily", adjust="qfq",
                        start_date=trade_date_str, end_date=end_dt,
                    )
                    if raw2 is not None and len(raw2) >= 2:
                        if all(col in raw2.columns for col in ["日期", "开盘", "最高", "最低", "收盘"]):
                            return raw2[["日期", "开盘", "最高", "最低", "收盘"]].sort_values("日期").reset_index(drop=True)
                except Exception as e:
                    status_code = getattr(getattr(e, 'response', None), 'status_code', 'N/A')
                    logger.error(
                        f"  [东方财富日K/{attempt+1}/2] {c} 拉取失败 "
                        f"| 接口=stock_zh_a_hist "
                        f"| 错误类型={type(e).__name__} "
                        f"| 状态码={status_code} "
                        f"| 详情={str(e)[:300]}"
                    )
                    time.sleep(2)

            # 第三路：北交所可转债直接跳过（没有K线）
            if prefix == "bj" or c[:3] in ("127", "128", "110", "113"):
                logger.info(f"  {c} 北交所/可转债代码，无K线数据，安全跳过")
            else:
                logger.error(f"  {c} @ {trade_date_str}: 两路数据源（新浪+东方财富）均失败，跳过本次价格补全")
            return None

        # 向后兼容：保留 fetch_sina 别名
        fetch_sina = fetch_kline

        filled_total = 0
        for trade_date_str in sorted(t10["trade_date"].astype(str).unique()):
            day_tp = tp[tp["trade_date"].astype(str) == trade_date_str] if not tp.empty else pd.DataFrame()
            stocks_of_day = t10[t10["trade_date"].astype(str) == trade_date_str]

            for _, row in stocks_of_day.iterrows():
                code = str(row["代码"]).zfill(6)
                
                # Check what needs to be filled for THIS specific stock
                bp_row = day_tp[day_tp["代码"].astype(str).str.zfill(6) == code] if not day_tp.empty else pd.DataFrame()
                is_new = bp_row.empty

                needs = []
                for n in _DAYS:
                    col = f"T+{n}收盘价"
                    if is_new or col not in bp_row.columns or pd.isna(bp_row.iloc[0].get(col)):
                        if can_fill(trade_date_str, n):
                            needs.append(n)

                if not needs and not is_new:
                    # Not new and nothing to fill
                    continue

                hist = fetch_sina(code, trade_date_str)
                if hist is None:
                    logger.warning(f"    {code} 拉取失败，跳过")
                    time.sleep(1)
                    continue

                if is_new:
                    # 新股票：计算 T+1 买入价（开盘价）
                    if len(hist) >= 2:
                        buy_price = round(float(hist.iloc[1]["开盘"]), 3)
                    else:
                        continue
                    bp = buy_price
                else:
                    if pd.isna(bp_row.iloc[0].get("T+1买入价")):
                        # Needs T+1 buy price
                        if len(hist) >= 2:
                            bp = round(float(hist.iloc[1]["开盘"]), 3)
                            needs.append("buy_price") # Special marker
                        else:
                            continue
                    else:
                        bp = float(bp_row.iloc[0]["T+1买入价"])

                updates = {}
                if is_new or "buy_price" in needs:
                    updates["T+1买入价"] = bp
                    # Remove the special marker if present
                    if "buy_price" in needs:
                        needs.remove("buy_price")

                for n in needs:
                    if n < len(hist):
                        r = hist.iloc[n]
                        hp = float(r["最高"])
                        lp = float(r["最低"])
                        cp = float(r["收盘"])
                        updates[f"T+{n}最高价"] = round(hp, 3)
                        updates[f"T+{n}最低价"] = round(lp, 3)
                        updates[f"T+{n}收盘价"] = round(cp, 3)
                        if bp > 0:
                            updates[f"T+{n}收盘收益%"] = round((cp / bp - 1) * 100, 2)
                            updates[f"T+{n}最高收益%"] = round((hp / bp - 1) * 100, 2)

                if not updates:
                    continue

                if is_new:
                    # INSERT 新行（直接 SQL 避免 storage.write 读全表的性能问题）
                    try:
                        new_rec = {"代码": code, "trade_date": trade_date_str, **updates}
                        cols_sql = ", ".join([f'"{c}"' for c in new_rec])
                        def _pn(c): return c.replace("+", "p").replace("%", "pct").replace(" ", "_")
                        vals_sql = ", ".join([f":{_pn(c)}" for c in new_rec])
                        params_insert = {_pn(c): v for c, v in new_rec.items()}
                        with engine.begin() as conn:
                            conn.execute(
                                text(f"INSERT INTO lhb_top10_prices ({cols_sql}) VALUES ({vals_sql}) ON CONFLICT DO NOTHING"),
                                params_insert,
                            )
                        filled_total += 1
                        logger.info(f"    {trade_date_str} {code} INSERT 新行: {list(updates.keys())}")
                    except Exception as e:
                        logger.error(f"    {code} INSERT 失败: {e}")
                else:
                    # UPDATE 已有行
                    param_map = {k: k.replace("+", "p").replace("%", "pct") for k in updates}
                    set_clause = ", ".join([f'"{k}" = :{param_map[k]}' for k in updates])
                    params = {param_map[k]: v for k, v in updates.items()}
                    params["code"] = code
                    params["td"] = trade_date_str

                    try:
                        with engine.begin() as conn:
                            r2 = conn.execute(
                                text(f'UPDATE lhb_top10_prices SET {set_clause} WHERE "代码" = :code AND trade_date = :td'),
                                params,
                            )
                        filled_total += r2.rowcount
                        logger.info(f"    {trade_date_str} {code} UPDATE {r2.rowcount} 行: {list(updates.keys())}")
                    except Exception as e:
                        logger.error(f"    {code} UPDATE 失败: {e}")

                time.sleep(0.8)

        logger.info(f"Step 3 完成，共更新 {filled_total} 行")
    except Exception as e:
        logger.error(f"Step 3 失败: {e}")


# ============================================================
# Step 4: 拉取今日龙虎榜明细（席位详情）
# ============================================================
def step4_fetch_lhb_detail():
    logger.info("=== Step 4: 拉取今日龙虎榜明细 ===")
    try:
        df = fetch_lhb_detail(start_date=TODAY_STR, end_date=TODAY_STR, save=True)
        if df.empty:
            logger.info("今日无龙虎榜明细（非交易日）")
        else:
            logger.info(f"明细获取成功，{len(df)} 条")
    except Exception as e:
        logger.error(f"Step 4 失败: {e}")


# ============================================================
# Step 5: 拉取机构席位追踪（lhb_jigou）
# ============================================================
def step5_fetch_jigou():
    logger.info("=== Step 5: 拉取机构席位追踪（lhb_jigou）===")
    try:
        # 近5日（当日精度最高）
        df5 = fetch_lhb_jigou(days=5, save=True)
        if not df5.empty:
            logger.info(f"机构席位(近5日) 获取成功，{len(df5)} 条")
        else:
            logger.warning("机构席位(近5日) 获取失败或为空")
        time.sleep(1)

        # 近10日（更长视角，不覆盖5日数据，追加写入）
        df10 = safe_akshare_call(
            ak.stock_lhb_jgzz_sina, symbol="10",
            max_retries=2, base_delay=1.5, fallback=None,
        )
        if df10 is not None and not df10.empty:
            df10["fetch_date"] = TODAY_STR
            df10["stat_days"]  = 10
            # 与5日数据合并后一起写入（replace 模式）
            combined = pd.concat([df5, df10], ignore_index=True) if not df5.empty else df10
            storage.write(combined, "lhb_jigou", mode="replace")
            logger.info(f"机构席位(近5日+10日) 合并保存，共 {len(combined)} 条")
        else:
            logger.warning("机构席位(近10日) 获取失败")
    except Exception as e:
        logger.error(f"Step 5 失败: {e}")


# ============================================================
# Step 6: 拉取个股上榜统计（lhb_ggtj）
# ============================================================
def step6_fetch_ggtj():
    logger.info("=== Step 6: 拉取个股上榜统计（lhb_ggtj）===")
    try:
        df5 = fetch_lhb_ggtj(days=5, save=False)
        time.sleep(1)
        df10 = fetch_lhb_ggtj(days=10, save=False)
        time.sleep(1)
        df30 = fetch_lhb_ggtj(days=30, save=False)

        frames = [f for f in [df5, df10, df30] if f is not None and not f.empty]
        if frames:
            combined = pd.concat(frames, ignore_index=True)
            storage.write(combined, "lhb_ggtj", mode="replace")
            logger.info(f"个股上榜统计 保存成功，共 {len(combined)} 条（5/10/30日合并）")
        else:
            logger.warning("个股上榜统计全部获取失败")
    except Exception as e:
        logger.error(f"Step 6 失败: {e}")


# ============================================================
# Step 7: 拉取今日龙虎榜买卖方席位明细（lhb_seat_detail）
# ============================================================
def step7_fetch_seat_detail():
    """
    拉取今日所有上榜股票的买卖方营业部席位，存入 lhb_seat_detail 表。
    这是席位质量评分的正确数据来源（按 trade_date 精确过滤当日）。
    """
    logger.info("=== Step 7: 拉取今日席位明细（lhb_seat_detail）===")
    try:
        raw = safe_akshare_call(
            ak.stock_lhb_detail_em,
            start_date=TODAY_STR, end_date=TODAY_STR,
            max_retries=3, base_delay=2.0, fallback=None,
        )
        if raw is None or raw.empty:
            logger.info("今日无席位明细数据（非交易日或接口暂无）")
            return

        raw["trade_date"] = TODAY_STR
        # append 模式内置按 trade_date 去重，安全写入
        storage.write(raw, "lhb_seat_detail", mode="append")
        logger.info(f"席位明细保存成功，{len(raw)} 条，列: {list(raw.columns)}")
    except Exception as e:
        logger.error(f"Step 7 失败: {e}")


# ============================================================
# Step 8: 补全 lhb_top10_prices.volume_ratio（量比）
# ============================================================
def step8_fill_volume_ratio():
    """
    为 lhb_top10_prices 表补充量比字段（volume_ratio）。
    量比 = 当日成交量 / 近5个交易日均量。
    先检查列是否存在，不存在则自动 ALTER TABLE 添加。
    """
    logger.info("=== Step 8: 补全量比字段（volume_ratio）===")
    try:
        from sqlalchemy import text
        engine = storage._get_engine()

        # 1. 确保列存在
        with engine.begin() as conn:
            conn.execute(text(
                "ALTER TABLE lhb_top10_prices "
                "ADD COLUMN IF NOT EXISTS volume_ratio FLOAT"
            ))

        # 2. 找出还没有量比的记录
        tp = storage.read("lhb_top10_prices")
        if tp.empty:
            logger.info("lhb_top10_prices 为空，跳过")
            return

        need_fill = tp[tp.get("volume_ratio", pd.Series(dtype=float)).isna()] if "volume_ratio" in tp.columns \
            else tp.copy()
        if need_fill.empty:
            logger.info("所有记录已有量比，无需补全")
            return

        logger.info(f"需要补全量比的记录: {len(need_fill)} 条")
        filled = 0

        for _, row in need_fill.iterrows():
            code      = str(row["代码"]).zfill(6)
            trade_date_str = str(row["trade_date"])

            try:
                # 拉取上榜日前后 30 日 K 线（最少需要 6 根）
                prefix  = "sh" if code.startswith(("6", "9")) else "sz"
                start_d = (pd.Timestamp(trade_date_str) - pd.Timedelta(days=20)).strftime("%Y%m%d")
                end_d   = (pd.Timestamp(trade_date_str) + pd.Timedelta(days=3)).strftime("%Y%m%d")
                raw_k   = ak.stock_zh_a_daily(
                    symbol=f"{prefix}{code}", adjust="qfq",
                    start_date=start_d, end_date=end_d,
                )
                if raw_k is None or len(raw_k) < 6:
                    continue

                raw_k = raw_k.sort_values("date")
                # 找上榜日对应行（<=trade_date 的最后一行）
                mask = raw_k["date"].astype(str).str.replace("-", "") <= trade_date_str
                if not mask.any():
                    continue
                idx_t = raw_k[mask].index[-1]
                pos   = raw_k.index.get_loc(idx_t)
                if pos < 5:
                    continue

                vols    = raw_k["volume"].values
                vol_day = float(vols[pos])
                vol5avg = float(vols[max(0, pos - 5):pos].mean())
                if vol5avg > 0:
                    vr = round(vol_day / vol5avg, 2)
                    with engine.begin() as conn:
                        conn.execute(
                            text('UPDATE lhb_top10_prices SET volume_ratio = :vr '
                                 'WHERE "代码" = :code AND trade_date = :td'),
                            {"vr": vr, "code": code, "td": trade_date_str},
                        )
                    filled += 1
                    logger.debug(f"  {trade_date_str} {code} 量比={vr}")
            except Exception as e:
                logger.debug(f"  {code}@{trade_date_str} 量比补全失败: {e}")
                continue
            time.sleep(0.4)

        logger.info(f"Step 8 完成，共补全量比 {filled} 条")
    except Exception as e:
        logger.error(f"Step 8 失败: {e}")


# ============================================================
# 主入口
# ============================================================
def step9_post_close_tracking():
    """
    Step 9: 批量补全平仓后 T+N 价格跟踪（sim_post_tracking）
    针对所有 status='closed' 但跟踪记录不足5个节点的交易。
    """
    logger.info("=== Step 9: 平仓后跟踪数据补全（sim_post_tracking）===")
    try:
        from modules.post_close_tracker import batch_track_pending
        n = batch_track_pending()
        logger.info(f"Step 9 完成：处理 {n} 笔交易的平仓后跟踪")
    except Exception as e:
        logger.error(f"Step 9 失败: {e}")


def step10_fill_candidate_returns():
    """
    Step 10: 批量补全候选股 T+N 实际收益（sim_candidate_results）
    针对 sim_selection_log 中还未填充 T+5 收益的日期。
    """
    logger.info("=== Step 10: 候选股 T+N 收益填充（sim_candidate_results）===")
    try:
        from modules.post_close_tracker import batch_fill_candidate_returns
        n = batch_fill_candidate_returns()
        logger.info(f"Step 10 完成：处理 {n} 个日期的候选股收益")
    except Exception as e:
        logger.error(f"Step 10 失败: {e}")


# ============================================================
# Step 11: 刷新 lhb_top10 中上榜后N日为空的记录
# ============================================================
def step11_refresh_lhb_returns():
    """
    Step 11: 刷新 lhb_top10 表中 `上榜后N日` 为空的历史记录。
    东方财富接口会随时间补充 T+1/T+2/T+5/T+10 收益数据，每小时触发一次此步骤
    可确保页面数据及时呈现，无需等待次日主更新。

    逻辑：
    1. 找出 lhb_top10 中 `上榜后1日` 为空且 trade_date 距今 >= 1 个交易日的日期
    2. 对每个日期，强制重拉接口（绕过缓存），更新 `上榜后1日/2日/5日/10日`
    3. 最多处理最近 30 天内的数据
    """
    logger.info("=== Step 11: 刷新 lhb_top10 上榜后N日收益 ===")
    try:
        from sqlalchemy import text
        from utils.storage import storage as _storage

        engine = _storage._engine  # type: ignore[attr-defined]
        if engine is None:
            logger.warning("Step 11 仅支持 PostgreSQL 模式，跳过")
            return

        # 找出近30天内上榜后1日为空的日期
        cutoff = (TODAY - timedelta(days=1)).strftime("%Y%m%d")
        cutoff_30 = (TODAY - timedelta(days=30)).strftime("%Y%m%d")
        sql_dates = text("""
            SELECT DISTINCT trade_date
            FROM lhb_top10
            WHERE ("上榜后1日" IS NULL OR "上榜后2日" IS NULL)
              AND trade_date <= :cutoff
              AND trade_date >= :cutoff_30
            ORDER BY trade_date DESC
        """)
        with engine.connect() as conn:
            dates_df = pd.read_sql(sql_dates, conn, params={"cutoff": cutoff, "cutoff_30": cutoff_30})

        if dates_df.empty:
            logger.info("Step 11: 所有近期 lhb_top10 记录已有收益数据，无需刷新")
            return

        dates_to_refresh = dates_df["trade_date"].tolist()
        logger.info(f"Step 11: 需刷新日期 {len(dates_to_refresh)} 个: {dates_to_refresh}")

        updated_total = 0
        for trade_date in dates_to_refresh:
            try:
                # 强制从接口重拉（top_n=50 覆盖所有记录）
                df_fresh = safe_akshare_call(
                    ak.stock_lhb_detail_em,
                    start_date=trade_date,
                    end_date=trade_date,
                    max_retries=3,
                    base_delay=3.0,
                    fallback=None,
                )
                if df_fresh is None or df_fresh.empty:
                    logger.warning(f"Step 11: {trade_date} 接口无数据，跳过")
                    continue

                # 标准化代码列名
                col_map = {}
                for c in df_fresh.columns:
                    if c in ("股票代码", "代码"):
                        col_map[c] = "代码"
                df_fresh = df_fresh.rename(columns=col_map)
                if "代码" not in df_fresh.columns:
                    continue

                updated = 0
                with engine.begin() as conn:
                    for _, row in df_fresh.iterrows():
                        code = str(row.get("代码", ""))
                        if not code:
                            continue
                        set_parts = []
                        params = {"code": code, "td": trade_date}
                        for col in ["上榜后1日", "上榜后2日", "上榜后5日", "上榜后10日"]:
                            val = row.get(col)
                            if val is not None and pd.notna(val):
                                param_key = col.replace("+", "p").replace(" ", "")
                                set_parts.append(f'"{ col }" = :{param_key}')
                                params[param_key] = float(val)
                        if set_parts:
                            upd_sql = text(
                                f'UPDATE lhb_top10 SET {", ".join(set_parts)} '
                                f'WHERE "代码" = :code AND trade_date = :td'
                            )
                            result = conn.execute(upd_sql, params)
                            updated += result.rowcount

                updated_total += updated
                logger.info(f"Step 11: {trade_date} 更新 {updated} 条")
                time.sleep(1)  # 接口限流

            except Exception as e:
                logger.warning(f"Step 11: {trade_date} 刷新失败: {e}")
                continue

        logger.info(f"Step 11 完成：共更新 {updated_total} 条记录")

    except Exception as e:
        logger.error(f"Step 11 失败: {e}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sentiment-only", action="store_true",
                        help="仅更新市场情绪（盘中快速刷新用）")
    parser.add_argument("--steps", type=str, default="",
                        help="指定执行步骤，逗号分隔，如 1,2,5,6（默认全部）")
    args = parser.parse_args()

    logger.info(f"{'='*60}")
    logger.info(f"每日自动更新开始: {TODAY_STR}  参数: {sys.argv[1:]}")
    logger.info(f"{'='*60}")

    if args.sentiment_only:
        step2_fetch_sentiment()
        logger.info("仅情绪更新完成")
        return

    # 解析要执行的步骤
    if args.steps:
        want = set(args.steps.split(","))
    else:
        want = {"1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11"}

    step_map = {
        "1":  step1_fetch_top10,
        "2":  step2_fetch_sentiment,
        "3":  step3_fill_null_prices,
        "4":  step4_fetch_lhb_detail,
        "5":  step5_fetch_jigou,
        "6":  step6_fetch_ggtj,
        "7":  step7_fetch_seat_detail,
        "8":  step8_fill_volume_ratio,
        "9":  step9_post_close_tracking,
        "10": step10_fill_candidate_returns,
        "11": step11_refresh_lhb_returns,
    }
    for k in sorted(want, key=lambda x: int(x)):
        fn = step_map.get(k)
        if fn:
            fn()
            time.sleep(2)

    logger.info(f"{'='*60}")
    logger.info(f"每日自动更新完成: {TODAY_STR}")
    logger.info(f"{'='*60}")


if __name__ == "__main__":
    main()
