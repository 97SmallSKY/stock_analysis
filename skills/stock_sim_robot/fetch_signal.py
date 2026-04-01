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
from modules.scorer import score_tech as _score_tech_shared, score_fund as _score_fund_shared, score_history as _score_history_shared


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


# ── 评分引擎（委托给 modules/scorer.py 统一实现）────────────────────
# 原始内置实现已迁移至 modules/scorer.py，此处保留同名包装以确保向后兼容

def _score_tech(kline: pd.DataFrame, trade_date: str, lianban_count: int = 0) -> dict:
    """技术面评分 — 委托给 modules/scorer.score_tech()
    
    Args:
        kline:          日K DataFrame
        trade_date:     信号日
        lianban_count:  连板数（默认0）
    """
    return _score_tech_shared(kline, trade_date, lianban_count=lianban_count)


def _score_fund(lhb_row: pd.Series) -> dict:
    """资金面评分 — 委托给 modules/scorer.score_fund()（无席位明细路径）"""
    res = _score_fund_shared(lhb_row, seat_df=None, fund_flow=None)
    # 向后兼容：保留旧返回字段 net_buy / duodao_reason
    net_buy = lhb_row.get("龙虎榜净买额", 0)
    net_buy = float(net_buy) if pd.notna(net_buy) else 0.0
    res.setdefault("net_buy", net_buy)
    res.setdefault("duodao_reason", "")
    return res


def _score_history(code: str) -> dict:
    """历史胜率评分 — 从 lhb_top10_prices 读取后调用 scorer.score_history()"""
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
    except Exception as e:
        logger.warning(f"_score_history [{code}]: {e}")

    res = _score_history_shared({"count": count, "t2_win": t2_win, "t5_win": t5_win})
    res["t2_win"] = t2_win
    res["t5_win"] = t5_win
    res["count"]  = count
    return res


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

    # Step 2b: 获取当日连板数据（stock_zt_pool_strong_em），构建 code→连板数 映射
    lianban_map: dict[str, int] = {}
    try:
        import akshare as ak
        df_lb = safe_akshare_call(
            ak.stock_zt_pool_strong_em, date=trade_date,
            max_retries=2, base_delay=1.0, fallback=None,
        )
        if df_lb is not None and not df_lb.empty:
            # 字段名：「代码」「连板数」（东财接口标准字段）
            code_col  = next((c for c in df_lb.columns if "代码" in c), None)
            lian_col  = next((c for c in df_lb.columns if "连板" in c), None)
            if code_col and lian_col:
                for _, lb_row in df_lb.iterrows():
                    c_ = str(lb_row[code_col]).zfill(6)
                    try:
                        lianban_map[c_] = int(lb_row[lian_col])
                    except (ValueError, TypeError):
                        pass
                logger.info(f"连板池数据: {len(lianban_map)} 只连板股")
    except Exception as e:
        logger.warning(f"连板池数据获取失败（不影响评分）: {e}")

    # Step 3: 逐只评分
    signals = []
    for i, (_, row) in enumerate(df.iterrows()):
        code = str(row["code_norm"])
        name = str(row.get("名称", code))
        logger.info(f"  [{i+1}/{len(df)}] 评分: {name}（{code}）...")

        # K 线
        kline = get_today_kline(code, trade_date)
        time.sleep(0.3)  # 避免接口限流

        # 三维评分（注入连板高度因子）
        lianban_count = lianban_map.get(code, 0)
        t_res = _score_tech(kline, trade_date, lianban_count=lianban_count)
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
            "lianban_count":          lianban_count,
            "lianban_score":          t_res.get("lianban_score", 0),
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
