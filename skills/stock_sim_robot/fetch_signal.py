"""
信号抓取模块
=============
从 PostgreSQL（stock_analysis 库）读取龙虎榜数据，对每只主板候选股进行
技术面 + 资金面 + 历史胜率三维评分，返回排序后的信号列表供 LLM 选股。

本模块逻辑来自 pages/lhb_signal.py 的评分引擎，剥离 Streamlit 依赖，
以便 cron job / robot 直接调用。

主要函数：
  get_mainboard_signals(trade_date) → list[dict]
  get_today_kline(code, trade_date) → pd.DataFrame
"""
import sys
import os
import time
from pathlib import Path
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
from loguru import logger

_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(_ROOT))
os.chdir(_ROOT)

from utils.storage import storage
from utils.retry import safe_akshare_call
from modules.data_fetcher import fetch_lhb_netbuy_by_date
from config.settings import FAMOUS_TRADERS, STRATEGY_CONFIG


# ── 主板判断 ──────────────────────────────────────────────────────────

def _is_mainboard(code: str) -> bool:
    """判断是否为主板股票"""
    c = str(code).zfill(6)
    if c.startswith("688"):
        return False
    if c.startswith("6"):
        return True
    if c[:3] in ("000", "001", "002", "003"):
        return True
    return False


# ── 技术指标辅助 ──────────────────────────────────────────────────────

def _sma(arr: np.ndarray, n: int) -> np.ndarray:
    result = np.full_like(arr, np.nan)
    for i in range(n - 1, len(arr)):
        result[i] = arr[i - n + 1: i + 1].mean()
    return result


def _ema(arr: np.ndarray, n: int) -> np.ndarray:
    result = np.full_like(arr, np.nan, dtype=float)
    k = 2.0 / (n + 1)
    for i in range(len(arr)):
        if np.isnan(arr[i]):
            continue
        if i == 0 or np.isnan(result[i - 1]):
            result[i] = arr[i]
        else:
            result[i] = arr[i] * k + result[i - 1] * (1 - k)
    return result


# ── K 线获取 ──────────────────────────────────────────────────────────

def get_today_kline(code: str, trade_date: str, days: int = 80) -> pd.DataFrame:
    """
    拉取 trade_date 前后 days 个交易日的日 K（前复权）

    Returns:
        DataFrame，含 日期/开盘/最高/最低/收盘/成交量
    """
    import akshare as ak

    end_dt   = (pd.Timestamp(trade_date) + pd.Timedelta(days=10)).strftime("%Y%m%d")
    start_dt = (pd.Timestamp(trade_date) - pd.Timedelta(days=int(days * 2.2))).strftime("%Y%m%d")
    prefix   = "sh" if code.startswith(("6", "9")) else "sz"

    try:
        raw = safe_akshare_call(
            ak.stock_zh_a_daily,
            symbol=f"{prefix}{code}", adjust="qfq",
            start_date=start_dt, end_date=end_dt,
            max_retries=2, base_delay=1.5, fallback=None,
        )
        if raw is not None and len(raw) >= 10:
            raw = raw.rename(columns={
                "date": "日期", "open": "开盘",
                "high": "最高", "low": "最低",
                "close": "收盘", "volume": "成交量",
            })
            return raw.sort_values("日期").tail(days).reset_index(drop=True)
    except Exception as e:
        logger.warning(f"get_today_kline [{code}]: {e}")
    return pd.DataFrame()


# ── 评分引擎（无 Streamlit 依赖版本）────────────────────────────────

def _score_tech(kline: pd.DataFrame, trade_date: str) -> dict:
    """技术面评分（满分 60 → 归一化到 40）
    
    ① 均线多头（15分）
    ② RSI（10分）
    ③ MACD（10分）
    ④ 量比/量能（10分）：优先使用当日量比（量比=当日成交量/近5日均量）
    ⑤ 近20日涨幅（10分）
    ⑥ K线形态（5分）
    """
    if kline.empty or len(kline) < 20:
        return {"score": 0, "detail": {"错误": "K线不足"}, "rsi": None, "gain20": None,
                "macd_score": 0, "volume_ratio": None}

    close  = kline["收盘"].astype(float).values
    volume = kline["成交量"].astype(float).values if "成交量" in kline.columns else None
    last = -1
    raw_score = 0

    # ① 均线多头（15分）
    ma5  = _sma(close, 5)
    ma10 = _sma(close, 10)
    ma20 = _sma(close, 20)
    ma_score = 0
    if not np.isnan(ma5[last]) and not np.isnan(ma10[last]) and not np.isnan(ma20[last]):
        c = close[last]
        if c > ma5[last]:           ma_score += 4
        if ma5[last] > ma10[last]:  ma_score += 5
        if ma10[last] > ma20[last]: ma_score += 6
    raw_score += ma_score

    # ② RSI（10分）
    rsi_val = None
    rsi_score = 0
    period = 14
    if len(close) >= period + 1:
        deltas   = np.diff(close)
        avg_gain = np.where(deltas > 0, deltas, 0.0)[-period:].mean()
        avg_loss = np.where(deltas < 0, -deltas, 0.0)[-period:].mean()
        rsi_val  = round(100 - 100 / (1 + avg_gain / avg_loss), 1) if avg_loss > 0 else 100.0
        if 45 <= rsi_val <= 70:   rsi_score = 10
        elif 35 <= rsi_val < 45:  rsi_score = 7
        elif 70 < rsi_val <= 80:  rsi_score = 5
        elif rsi_val > 80:        rsi_score = 1
        else:                     rsi_score = 3
    raw_score += rsi_score

    # ③ MACD（10分）
    macd_score = 0
    if len(close) >= 35:
        ema12    = _ema(close, 12)
        ema26    = _ema(close, 26)
        dif      = ema12 - ema26
        dea      = _ema(dif, 9)
        hist_arr = (dif - dea) * 2
        valid_h  = hist_arr[~np.isnan(hist_arr)]
        if len(valid_h) >= 2:
            if valid_h[-2] < 0 <= valid_h[-1]:    macd_score = 10
            elif valid_h[-1] > 0 and valid_h[-1] > valid_h[-2]: macd_score = 8
            elif valid_h[-1] > 0:                  macd_score = 5
            elif valid_h[-2] > 0 >= valid_h[-1]:   macd_score = 0
            else:                                   macd_score = 2
    raw_score += macd_score

    # ④ 量比/量能（10分）：当日成交量 / 近5日均量（标准量比定义）
    vol_score    = 0
    volume_ratio = None  # 量比
    if volume is not None and len(volume) >= 6:
        vol5_avg = volume[-6:-1].mean()  # 前5日均量（排除今日）
        if vol5_avg > 0:
            volume_ratio = round(float(volume[-1]) / float(vol5_avg), 2)
            if volume_ratio >= 3.0:   vol_score = 10   # 极度放量
            elif volume_ratio >= 2.0: vol_score = 8    # 明显放量
            elif volume_ratio >= 1.5: vol_score = 6    # 温和放量
            elif volume_ratio >= 1.0: vol_score = 4    # 平量
            else:                     vol_score = 1    # 缩量
        elif len(volume) >= 20:
            # 前5日全为0（停牌复牌等情况），降级用20日均量
            vol20 = volume[-20:].mean()
            if vol20 > 0:
                volume_ratio = round(float(volume[-1]) / float(vol20), 2)
                if volume_ratio >= 2.0: vol_score = 8
                elif volume_ratio >= 1.5: vol_score = 6
                else: vol_score = 3
    raw_score += vol_score

    # ⑤ 近20日涨幅（10分）
    gain20_val   = None
    gain20_score = 0
    if len(close) >= 20:
        base = close[-20]
        if base > 0:
            gain20_val = round((close[last] / base - 1) * 100, 2)
            if gain20_val < 0:         gain20_score = 6
            elif gain20_val <= 10:     gain20_score = 10
            elif gain20_val <= 20:     gain20_score = 7
            elif gain20_val <= 30:     gain20_score = 4
            elif gain20_val <= 50:     gain20_score = 2
    raw_score += gain20_score

    # ⑥ K线形态（5分）
    form_score = 0
    if len(kline) >= 1:
        row = kline.iloc[last]
        h = float(row.get("最高", row["收盘"]))
        l = float(row.get("最低",  row["收盘"]))
        o = float(row["开盘"])
        c_val = float(row["收盘"])
        body  = abs(c_val - o)
        upper_shadow = h - max(c_val, o)
        total_range  = h - l if h > l else 1
        if body / total_range >= 0.6 and c_val > o:     form_score = 5
        elif body / total_range >= 0.4 and c_val > o:   form_score = 3
        elif upper_shadow > body * 1.5:                  form_score = 1
        else:                                            form_score = 2
    raw_score += form_score

    total_possible = 15 + 10 + 10 + 10 + 10 + 5  # = 60
    normalized = round(raw_score / total_possible * 40)

    return {
        "score":        min(normalized, 40),
        "rsi":          rsi_val,
        "gain20":       gain20_val,
        "macd_score":   macd_score,
        "volume_ratio": volume_ratio,
        "ma5":          round(float(ma5[last]),  3) if not np.isnan(ma5[last])  else None,
        "ma10":         round(float(ma10[last]), 3) if not np.isnan(ma10[last]) else None,
        "ma20":         round(float(ma20[last]), 3) if not np.isnan(ma20[last]) else None,
    }


def _score_fund(lhb_row: pd.Series) -> dict:
    """
    资金面评分（满分 35 分）

    评分组成：
      净买入强度（15分）：用「净买入额 / 流通市值」归一化，不同市值股票可比
                         流通市值字段缺失时降级为绝对额评分
      席位质量  （12分）：机构买方 +5，知名游资席位每席 +3（上限 6）
      对倒风险惩罚（-10分）：解读字段含"主买"+"卖"且净买额为负，疑似对倒
    """
    score  = 0

    net_buy = lhb_row.get("龙虎榜净买额", 0)
    if pd.notna(net_buy):
        net_buy = float(net_buy)
    else:
        net_buy = 0

    # ── 净买入强度（归一化 vs 流通市值）────────────────────────────────
    free_float_mv = lhb_row.get("流通市值", None)
    net_buy_strength_pct: float | None = None

    if free_float_mv is not None and pd.notna(free_float_mv):
        mv = float(free_float_mv)
        if mv > 0 and net_buy > 0:
            net_buy_strength_pct = round(net_buy / mv * 100, 3)
            # 归一化强度阈值（相对市值，更公平地比较大小市值股票）
            if net_buy_strength_pct >= 2.0:   score += 15
            elif net_buy_strength_pct >= 1.0: score += 12
            elif net_buy_strength_pct >= 0.5: score += 8
            elif net_buy_strength_pct >= 0.1: score += 4
            else:                              score += 1
        elif net_buy > 0:
            # mv = 0（异常数据），降级为绝对额
            if net_buy >= 3e8:   score += 15
            elif net_buy >= 1e8: score += 12
            elif net_buy >= 5e7: score += 8
            elif net_buy >= 1e7: score += 4
            elif net_buy > 0:    score += 1
    else:
        # 无流通市值字段，降级为原绝对额评分（兼容旧缓存数据）
        logger.debug(f"_score_fund: 无流通市值字段，降级为绝对额评分（代码={lhb_row.get('代码','?')}）")
        if net_buy >= 3e8:   score += 15
        elif net_buy >= 1e8: score += 12
        elif net_buy >= 5e7: score += 8
        elif net_buy >= 1e7: score += 4
        elif net_buy > 0:    score += 1

    # ── 席位评分：从 lhb_row 中快速判断 ────────────────────────────────
    seat_tags   = []
    jigou_buy   = 0
    famous_buy  = []

    row_str = " ".join(str(v) for v in lhb_row.values)
    for seat_name, alias in FAMOUS_TRADERS.items():
        if seat_name in row_str and alias not in famous_buy:
            famous_buy.append(alias)
            seat_tags.append(alias)

    # 机构买方（解读字段含"机构买"/"机构"且净买额为正）
    if ("机构" in row_str and net_buy > 0):
        jigou_buy = 1
    # 机构只卖不买：净买额为负且含"机构卖"
    jigou_sell_only = ("机构" in row_str and "卖" in row_str and net_buy < 0)

    seat_score = 0
    if jigou_buy >= 1 and not jigou_sell_only:
        seat_score += 5
    seat_score += min(len(famous_buy) * 3, 6)
    if not seat_tags and not jigou_buy:
        seat_score = 4  # 无席位信息时给保守中间分

    score += min(seat_score, 12)

    # ── 对倒风险检测（基于解读字段文本分析）────────────────────────────
    # 规则：净买额为负（主力出货）+ 解读含"主买"暗示对倒
    # 或：机构只卖不买，疑似机构出逃拉高砸盘
    duodao_risk = False
    duodao_reason = ""

    # 规则1：净买额为负（主力净卖出），但解读含"买"（对倒嫌疑）
    jieda = str(lhb_row.get("解读", "") or "")
    if net_buy < 0 and ("买" in jieda):
        duodao_risk  = True
        duodao_reason = f"净买额为负({net_buy/1e8:.2f}亿)但解读含买方，疑似对倒"
        score -= 10
        logger.debug(f"  ⚠️ 对倒风险({lhb_row.get('代码','?')})：{duodao_reason}")

    # 规则2：机构只卖不买（出逃信号，不算对倒但同样危险）
    if jigou_sell_only and not duodao_risk:
        score -= 5  # 轻度惩罚
        logger.debug(f"  ⚠️ 机构卖出信号({lhb_row.get('代码','?')})：{jieda[:40]}")

    return {
        "score":                 min(max(score, 0), 35),
        "net_buy":               net_buy,
        "jigou_buy":             jigou_buy,
        "famous_buy":            famous_buy,
        "seat_tags":             seat_tags,
        "net_buy_strength_pct":  net_buy_strength_pct,  # 净买入强度（%市值），None 表示无数据
        "duodao_risk":           duodao_risk,
        "duodao_reason":         duodao_reason,
    }


def _score_history(code: str) -> dict:
    """历史胜率评分（满分 25 分）"""
    score  = 0
    t2_win = None
    t5_win = None
    count  = 0

    try:
        df = storage.read("lhb_top10_prices")
        if not df.empty:
            code_norm = str(code).lstrip("0")
            sub = df[df["代码"].astype(str).str.lstrip("0") == code_norm]
            sub = sub[sub["T+1买入价"].notna()]
            count = len(sub)

            for n, k_name in [(2, "t2"), (5, "t5")]:
                col = f"T+{n}收盘收益%"
                if col in sub.columns:
                    vals = sub[col].dropna()
                    if len(vals) > 0:
                        win_rate = round((vals > 0).mean() * 100, 1)
                        if k_name == "t2":
                            t2_win = win_rate
                        else:
                            t5_win = win_rate

        if count == 0:
            score = 12  # 无历史数据给中间分
        else:
            if t2_win is not None:
                if t2_win >= 70:   score += 12
                elif t2_win >= 60: score += 9
                elif t2_win >= 50: score += 6
                elif t2_win >= 40: score += 3
            if t5_win is not None:
                if t5_win >= 70:   score += 13
                elif t5_win >= 60: score += 10
                elif t5_win >= 50: score += 7
                elif t5_win >= 40: score += 3
    except Exception as e:
        logger.warning(f"_score_history [{code}]: {e}")
        score = 12

    return {"score": min(score, 25), "t2_win": t2_win, "t5_win": t5_win, "count": count}


# ── 获取集合竞价信号 ─────────────────────────────────────────────────

def get_current_price(code: str) -> float | None:
    """获取最新收盘价（作为 T+1 开盘价的估算参考）"""
    import akshare as ak
    try:
        prefix = "sh" if code.startswith(("6", "9")) else "sz"
        raw = safe_akshare_call(
            ak.stock_zh_a_daily,
            symbol=f"{prefix}{code}", adjust="qfq",
            max_retries=2, base_delay=1.5, fallback=None,
        )
        if raw is not None and not raw.empty:
            return float(raw["close"].iloc[-1])
    except Exception:
        pass
    return None


# ── 主入口：获取今日主板信号列表 ─────────────────────────────────────

def get_mainboard_signals(trade_date: str, top_n: int = 10) -> list[dict]:
    """
    获取指定交易日的龙虎榜主板候选信号

    流程：
      1. 从 DB/接口 拉取当日龙虎榜净买入 Top N
      2. 过滤仅保留主板
      3. 对每只股票进行三维评分（技术+资金+历史胜率）
      4. 按综合评分降序返回

    Args:
        trade_date: 格式 YYYYMMDD
        top_n: 龙虎榜取前 N 名

    Returns:
        list of dict，每条包含：
          code, name, rank, net_buy_amount, signal_score,
          tech_score, fund_score, hist_score,
          t2_win_rate, t5_win_rate, hist_count,
          rsi, gain20, macd_score, ma5, ma10, ma20,
          seat_tags, jigou_buy, famous_buy,
          close_price, board_label
    """
    logger.info(f"=== 获取 {trade_date} 龙虎榜主板信号 ===")

    # Step 1: 拉取龙虎榜
    df = fetch_lhb_netbuy_by_date(trade_date, top_n=top_n)
    if df.empty:
        logger.warning(f"{trade_date} 龙虎榜数据为空（非交易日或数据未更新）")
        return []

    # Step 2: 主板过滤
    df["code_norm"] = df["代码"].astype(str).str.zfill(6)
    df = df[df["code_norm"].apply(_is_mainboard)].copy()
    logger.info(f"主板候选: {len(df)} 只（原始 Top{top_n} 共 {len(df)} 行）")

    if df.empty:
        logger.warning("当日龙虎榜中无主板股票")
        return []

    # Step 3: 逐只评分
    signals = []
    for i, (_, row) in enumerate(df.iterrows()):
        code = str(row["code_norm"])
        name = str(row.get("名称", code))
        logger.info(f"  [{i+1}/{len(df)}] 评分: {name}（{code}）...")

        # K 线
        kline = get_today_kline(code, trade_date)
        time.sleep(0.3)  # 避免接口限流

        # 三维评分
        t_res = _score_tech(kline, trade_date)
        f_res = _score_fund(row)
        h_res = _score_history(code)

        total_score = t_res["score"] + f_res["score"] + h_res["score"]

        # 当前参考价（上榜日收盘价）
        close_price = float(row.get("收盘价", 0) or 0)
        if close_price <= 0 and not kline.empty:
            close_price = float(kline["收盘"].iloc[-1])

        signals.append({
            "code":                   code,
            "name":                   name,
            "rank":                   int(row.get("排名", i + 1)),
            "net_buy_amount":         float(f_res["net_buy"]),
            "net_buy_yi":             round(f_res["net_buy"] / 1e8, 2),  # 亿元
            "net_buy_strength_pct":   f_res.get("net_buy_strength_pct"),  # 净买入强度（%市值）
            "duodao_risk":            f_res.get("duodao_risk", False),
            "duodao_reason":          f_res.get("duodao_reason", ""),
            "signal_score":           total_score,
            "tech_score":             t_res["score"],
            "fund_score":             f_res["score"],
            "hist_score":             h_res["score"],
            "t2_win_rate":            h_res["t2_win"],
            "t5_win_rate":            h_res["t5_win"],
            "hist_count":             h_res["count"],
            "rsi":                    t_res.get("rsi"),
            "gain20":                 t_res.get("gain20"),
            "macd_score":             t_res.get("macd_score", 0),
            "ma5":                    t_res.get("ma5"),
            "ma10":                   t_res.get("ma10"),
            "ma20":                   t_res.get("ma20"),
            "volume_ratio":           t_res.get("volume_ratio"),
            "seat_tags":              f_res["seat_tags"],
            "jigou_buy":              f_res["jigou_buy"],
            "famous_buy":             f_res["famous_buy"],
            "close_price":            close_price,
        })

    # Step 4: 按综合评分降序
    signals.sort(key=lambda x: x["signal_score"], reverse=True)
    logger.info(
        f"=== 信号获取完成，共 {len(signals)} 只主板候选 ==="
        + (f"\n  Top1: {signals[0]['name']}（{signals[0]['code']}）评分={signals[0]['signal_score']}"
           if signals else "")
    )
    return signals


def format_signal_for_llm(signals: list[dict]) -> str:
    """
    将信号列表格式化为 LLM 可读的文本

    Returns:
        多行文本，每行描述一只候选股
    """
    if not signals:
        return "今日暂无主板候选信号。"

    lines = ["【今日龙虎榜主板候选信号】\n"]
    for s in signals:
        t2 = f"{s['t2_win_rate']:.0f}%" if s["t2_win_rate"] is not None else "暂无"
        t5 = f"{s['t5_win_rate']:.0f}%" if s["t5_win_rate"] is not None else "暂无"
        g20 = f"{s['gain20']:+.1f}%" if s["gain20"] is not None else "N/A"
        rsi = f"{s['rsi']:.0f}" if s["rsi"] is not None else "N/A"
        seats = "、".join(s["famous_buy"]) if s["famous_buy"] else ("机构买方" if s["jigou_buy"] else "无知名席位")
        strength = s.get("net_buy_strength_pct")
        strength_str = f"{strength:.2f}%市值" if strength is not None else "—"
        duodao_tag = " ⚠️[疑似对倒]" if s.get("duodao_risk") else ""
        lines.append(
            f"  [{s['rank']:2d}] {s['name']}（{s['code']}）{duodao_tag}"
            f" 综合={s['signal_score']:3d}分"
            f"（技术{s['tech_score']}+资金{s['fund_score']}+历史{s['hist_score']}）"
            f" | 净买入{s['net_buy_yi']:.2f}亿（强度{strength_str}）"
            f" | T+2历史胜率={t2} T+5={t5}（{s['hist_count']}次）"
            f" | RSI={rsi} 20日涨幅={g20}"
            f" | 参考价={s['close_price']:.2f}"
            f" | 席位: {seats}"
        )
    return "\n".join(lines)


if __name__ == "__main__":
    # 测试
    from datetime import date
    today = date.today().strftime("%Y%m%d")
    sigs  = get_mainboard_signals(today)
    print(format_signal_for_llm(sigs))
