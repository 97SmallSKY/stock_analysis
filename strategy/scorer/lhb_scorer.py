"""
modules/scorer.py — 统一评分引擎
===================================

将原本散落在 pages/lhb_signal.py 与 skills/stock_sim_robot/fetch_signal.py
中的重复评分函数统一到此模块，作为唯一实现出口。

公开接口：
    score_tech(kline, trade_date, lianban_count=0)  → dict
    score_fund(lhb_row, seat_df=None, fund_flow=None) → dict
    score_history(hist_dict)   → dict

评分维度总览（满分 100）：
    技术面  40 分（均线/RSI/MACD/量比/近20日涨幅/K线形态 + 连板因子最高+10→归一化）
    资金面  35 分（净买入强度/席位质量/主力净流向/对倒惩罚）
    历史胜率 25 分（T+2/T+5 胜率）

变更历史：
    2026-04-01  新建，合并 lhb_signal._score_tech/fund 与 fetch_signal._score_tech/fund
                新增连板高度因子（lianban_count 参数）
"""

import numpy as np
import pandas as pd
from loguru import logger

# FAMOUS_TRADERS 由 config.settings 导入，仅在此处使用
try:
    from config.settings import FAMOUS_TRADERS
except ImportError:
    FAMOUS_TRADERS = {}


# ── 技术指标辅助（模块内私有，避免重复引入） ──────────────────────────────

def _sma(arr: np.ndarray, n: int) -> np.ndarray:
    result = np.full_like(arr, np.nan, dtype=float)
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


# ── 技术面评分 ─────────────────────────────────────────────────────────────

def score_tech(
    kline: pd.DataFrame,
    trade_date: str,
    lianban_count: int = 0,
) -> dict:
    """
    技术面评分（基础满分 60 → 归一化到 40，含连板加分后可超40但被截断）

    Args:
        kline:          日K DataFrame，含 收盘/开盘/最高/最低/成交量 列
        trade_date:     信号触发日（用于截止日期校验，当前暂为元数据）
        lianban_count:  该股当日连板数（来自 stock_zt_pool_strong_em），0=未连板

    Returns:
        dict 含以下字段：
            score        (int)   归一化后最终得分（0~40，连板加分后最高40）
            raw_score    (int)   原始基础分（0~60）
            detail       (dict)  各子项说明
            rsi          (float|None)
            gain20       (float|None) 近20日涨幅%
            macd_score   (int)
            volume_ratio (float|None) 量比
            lianban_score(int)   连板因子得分（0/4/7/10）
            ma5/ma10/ma20(float|None)
    """
    if kline is None or kline.empty or len(kline) < 20:
        return {
            "score": 0, "raw_score": 0,
            "detail": {"错误": "K线数据不足（<20根）"},
            "rsi": None, "gain20": None, "macd_score": 0,
            "volume_ratio": None, "lianban_score": 0,
            "ma5": None, "ma10": None, "ma20": None,
        }

    close  = kline["收盘"].astype(float).values
    volume = kline["成交量"].astype(float).values if "成交量" in kline.columns else None
    last   = -1
    raw_score = 0
    detail = {}

    # ── ① 均线多头排列（15分）───────────────────────────────────────────
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
    detail["①均线多头排列"] = f"{ma_score}/15分"

    # ── ② RSI 健康区间（10分）───────────────────────────────────────────
    rsi_val   = None
    rsi_score = 0
    period = 14
    if len(close) >= period + 1:
        deltas   = np.diff(close)
        avg_gain = np.where(deltas > 0, deltas, 0.0)[-period:].mean()
        avg_loss = np.where(deltas < 0, -deltas, 0.0)[-period:].mean()
        if avg_loss > 0:
            rsi_val = round(100 - 100 / (1 + avg_gain / avg_loss), 1)
        else:
            rsi_val = 100.0
        if 45 <= rsi_val <= 70:   rsi_score = 10
        elif 35 <= rsi_val < 45:  rsi_score = 7
        elif 70 < rsi_val <= 80:  rsi_score = 5
        elif rsi_val > 80:        rsi_score = 1
        else:                     rsi_score = 3
    raw_score += rsi_score
    detail["②RSI健康区间"] = f"{rsi_score}/10分（RSI={rsi_val}）"

    # ── ③ MACD 金叉/多头（10分）────────────────────────────────────────
    macd_score = 0
    macd_val = hist_val = None
    if len(close) >= 35:
        ema12    = _ema(close, 12)
        ema26    = _ema(close, 26)
        dif      = ema12 - ema26
        dea      = _ema(dif, 9)
        hist_arr = (dif - dea) * 2
        valid_hist = hist_arr[~np.isnan(hist_arr)]
        valid_dif  = dif[~np.isnan(dif)]
        if len(valid_hist) >= 2:
            macd_val  = round(float(valid_dif[-1]), 4) if len(valid_dif) > 0 else None
            hist_val  = round(float(valid_hist[-1]), 4)
            prev_h    = float(valid_hist[-2])
            if prev_h < 0 <= valid_hist[-1]:              macd_score = 10   # 金叉
            elif valid_hist[-1] > 0 and valid_hist[-1] > prev_h: macd_score = 8  # 红柱扩张
            elif valid_hist[-1] > 0:                      macd_score = 5    # 红柱缩小
            elif prev_h > 0 >= valid_hist[-1]:            macd_score = 0    # 死叉
            else:                                         macd_score = 2    # 空头区域
    raw_score += macd_score
    macd_label = f"DIF={macd_val}, HIST={hist_val}" if macd_val is not None else "数据不足"
    detail["③MACD金叉/多头"] = f"{macd_score}/10分（{macd_label}）"

    # ── ④ 量比（10分）：当日成交量 / 近5日均量 ──────────────────────────
    vol_score    = 0
    volume_ratio = None
    if volume is not None and len(volume) >= 6:
        vol5_avg = volume[-6:-1].mean()  # 前5日均量（排除今日）
        if vol5_avg > 0:
            volume_ratio = round(float(volume[-1]) / float(vol5_avg), 2)
            if volume_ratio >= 3.0:   vol_score = 10
            elif volume_ratio >= 2.0: vol_score = 8
            elif volume_ratio >= 1.5: vol_score = 6
            elif volume_ratio >= 1.0: vol_score = 4
            else:                     vol_score = 1
        elif len(volume) >= 20:
            # 前5日均量为0（停牌复牌等），降级用20日均量
            vol20 = volume[-20:].mean()
            if vol20 > 0:
                volume_ratio = round(float(volume[-1]) / float(vol20), 2)
                if volume_ratio >= 2.0:   vol_score = 8
                elif volume_ratio >= 1.5: vol_score = 6
                else:                     vol_score = 3
    raw_score += vol_score
    vr_str = f"{volume_ratio:.2f}" if volume_ratio is not None else "N/A"
    detail["④量比"] = f"{vol_score}/10分（量比={vr_str}，今日量/近5日均量）"

    # ── ⑤ 近20日涨幅位置（10分）──────────────────────────────────────
    gain20_score = 0
    gain20_val   = None
    if len(close) >= 20:
        base = close[-20]
        if base > 0:
            gain20_val = round((close[last] / base - 1) * 100, 2)
            if gain20_val < 0:          gain20_score = 6   # 超跌反弹机会
            elif gain20_val <= 10:      gain20_score = 10  # 低位蓄力，最佳
            elif gain20_val <= 20:      gain20_score = 7
            elif gain20_val <= 30:      gain20_score = 4   # 中位，谨慎
            elif gain20_val <= 50:      gain20_score = 2   # 高位追涨
            else:                       gain20_score = 0   # 过高
    raw_score += gain20_score
    g20_str = f"{gain20_val:+.1f}%" if gain20_val is not None else "数据不足"
    detail["⑤近20日涨幅"] = f"{gain20_score}/10分（{g20_str}）"

    # ── ⑥ K线形态（5分）──────────────────────────────────────────────
    form_score = 0
    if len(kline) >= 1:
        row  = kline.iloc[last]
        h    = float(row["最高"]) if "最高" in kline.columns else float(row["收盘"])
        l    = float(row["最低"]) if "最低" in kline.columns else float(row["收盘"])
        o    = float(row["开盘"])
        c_v  = float(row["收盘"])
        body = abs(c_v - o)
        upper_shadow = h - max(c_v, o)
        total_range  = h - l if h > l else 1
        if body / total_range >= 0.6 and c_v > o:       form_score = 5  # 大阳线
        elif body / total_range >= 0.4 and c_v > o:     form_score = 3
        elif upper_shadow > body * 1.5:                  form_score = 1  # 长上影，主力派发
        else:                                            form_score = 2
    raw_score += form_score
    detail["⑥K线形态"] = f"{form_score}/5分"

    # ── 归一化：满分60 → 40分 ──────────────────────────────────────────
    total_possible = 15 + 10 + 10 + 10 + 10 + 5  # = 60
    normalized = round(raw_score / total_possible * 40)

    # ── ⑦ 连板高度加分（短线核心因子，最高 +10 分）────────────────────
    # 数据来源：stock_zt_pool_strong_em 的「连板数」字段
    lianban_score = 0
    if lianban_count >= 3:   lianban_score = 10   # 高位连板，市场强势共振
    elif lianban_count == 2: lianban_score = 7    # 双连板，持续强势
    elif lianban_count == 1: lianban_score = 4    # 首板，短线信号
    # lianban_count == 0: 无连板，0分（普通龙虎榜上榜标的）
    if lianban_count > 0:
        detail[f"⑦连板加分({lianban_count}板)"] = f"+{lianban_score}分（连板高度因子）"

    final_score = min(normalized + lianban_score, 40)

    return {
        "score":         final_score,
        "raw_score":     raw_score,
        "detail":        detail,
        "rsi":           rsi_val,
        "gain20":        gain20_val,
        "macd_score":    macd_score,
        "volume_ratio":  volume_ratio,
        "lianban_score": lianban_score,
        "ma5":  round(float(ma5[last]),  3) if not np.isnan(ma5[last])  else None,
        "ma10": round(float(ma10[last]), 3) if not np.isnan(ma10[last]) else None,
        "ma20": round(float(ma20[last]), 3) if not np.isnan(ma20[last]) else None,
    }


# ── 资金面评分 ─────────────────────────────────────────────────────────────

def score_fund(
    lhb_row: pd.Series,
    seat_df: pd.DataFrame | None = None,
    fund_flow: dict | None = None,
) -> dict:
    """
    资金面评分（满分 35 分）

    Args:
        lhb_row:    龙虎榜单行数据（含 龙虎榜净买额/流通市值/解读 等字段）
        seat_df:    席位明细 DataFrame（页面端传入，机器人端为 None 则简化评分）
        fund_flow:  主力资金流向字典（页面端传入，含 main_net 字段）
                    若为 None，则跳过③主力资金流向维度（机器人调用场景）

    Returns:
        dict 含以下字段：
            score               (int)    最终得分（0~35）
            detail              (dict)   各子项说明
            seat_tags           (list)   识别到的知名席位标签
            jigou_buy           (int)    机构买方席位数
            famous_buy          (list)   知名游资别名列表
            has_jigou_sell      (bool)   是否有机构卖出
            net_buy_strength_pct(float|None) 净买入强度（%流通市值）
            duodao_risk         (bool)   对倒风险
    """
    if seat_df is None:
        seat_df = pd.DataFrame()
    if fund_flow is None:
        fund_flow = {}

    score  = 0
    detail = {}
    seat_tags      = []
    jigou_buy      = 0
    famous_buy     = []
    has_jigou_sell = False

    # ── ① 净买入强度（15分）：优先归一化，降级绝对额 ─────────────────────
    net_buy = lhb_row.get("龙虎榜净买额", 0)
    net_buy = float(net_buy) if pd.notna(net_buy) else 0.0

    _circ_val = lhb_row.get("流通市值") or lhb_row.get("流通股本")
    _strength_pct: float | None = None
    try:
        if _circ_val is not None and pd.notna(_circ_val) and float(_circ_val) > 0:
            _strength_pct = round(net_buy / float(_circ_val) * 100, 3)
    except Exception:
        pass

    if _strength_pct is not None:
        if _strength_pct >= 2.0:   nb_score = 15
        elif _strength_pct >= 1.0: nb_score = 12
        elif _strength_pct >= 0.5: nb_score = 8
        elif _strength_pct >= 0.1: nb_score = 4
        elif _strength_pct > 0:    nb_score = 1
        else:                      nb_score = 0
        detail["①净买入强度"] = (
            f"{nb_score}/15分（{_strength_pct:.2f}% 流通市值，净买额{net_buy/1e8:+.2f}亿）"
        )
    else:
        # 无流通市值字段时降级为绝对额评分（兼容旧数据）
        logger.debug(
            f"score_fund: 无流通市值字段，降级绝对额评分（代码={lhb_row.get('代码','?')}）"
        )
        if net_buy >= 3e8:   nb_score = 15
        elif net_buy >= 1e8: nb_score = 12
        elif net_buy >= 5e7: nb_score = 8
        elif net_buy >= 1e7: nb_score = 4
        elif net_buy > 0:    nb_score = 1
        else:                nb_score = 0
        detail["①龙虎榜净买额"] = (
            f"{nb_score}/15分（{net_buy/1e8:+.2f}亿，无流通市值降级）"
        )
    score += nb_score

    # ── ② 席位质量：机构/知名游资（12分）──────────────────────────────
    seat_score = 0

    if not seat_df.empty:
        # 识别席位类型列
        type_col = next(
            (c for c in seat_df.columns if "类型" in c or "方向" in c), None
        )
        for _, row_s in seat_df.iterrows():
            row_str = " ".join(str(v) for v in row_s.values)
            if "机构" in row_str:
                if type_col and "卖" in str(row_s.get(type_col, "")):
                    has_jigou_sell = True
                else:
                    jigou_buy += 1
            for seat_name, alias in FAMOUS_TRADERS.items():
                if seat_name in row_str and alias not in famous_buy:
                    famous_buy.append(alias)
                    seat_tags.append(f"{alias}（买入）")
        # 计分
        if jigou_buy >= 2:   seat_score += 8
        elif jigou_buy == 1: seat_score += 5
        if has_jigou_sell:   seat_score = max(0, seat_score - 3)
        seat_score += min(len(famous_buy) * 3, 6)
    else:
        # 无席位明细时退化到行数据粗判（机器人调用路径）
        row_str = " ".join(str(v) for v in lhb_row.values)
        if "机构" in row_str and net_buy > 0:
            jigou_buy = 1
            seat_score = 5
        elif "机构" in row_str and net_buy < 0:
            has_jigou_sell = True
        for seat_name, alias in FAMOUS_TRADERS.items():
            if seat_name in row_str and alias not in famous_buy:
                famous_buy.append(alias)
                seat_tags.append(alias)
        seat_score += min(len(famous_buy) * 3, 6)
        if not seat_tags and not jigou_buy:
            seat_score = 4  # 无席位信息时给保守中间分

    score += min(seat_score, 12)
    jg_label = f"机构买方{jigou_buy}席" + ("（有机构卖出⚠️）" if has_jigou_sell else "")
    fy_label = "，".join(famous_buy) if famous_buy else "无知名游资"
    detail["②席位质量"] = f"{min(seat_score,12)}/12分（{jg_label}；{fy_label}）"

    # ── ③ 主力资金流向（8分，可选）─────────────────────────────────────
    main_net = fund_flow.get("main_net")
    mf_score = 0
    if main_net is not None:
        if main_net >= 1e8:    mf_score = 8
        elif main_net >= 5e7:  mf_score = 6
        elif main_net >= 1e7:  mf_score = 4
        elif main_net >= 0:    mf_score = 2
        else:                  mf_score = 0
        score += mf_score
        mf_label = f"{main_net/1e8:+.2f}亿"
        detail["③主力净流入(5日)"] = f"{mf_score}/8分（{mf_label}）"
    # fund_flow 未传入时不加分，但不影响其他维度

    # ── 对倒风险检测（基于解读字段文本分析）──────────────────────────
    _duodao_risk = False
    _jieda = str(lhb_row.get("解读", "") or "")
    if net_buy < 0 and "买" in _jieda:
        _duodao_risk = True
        score = max(score - 10, 0)
        detail["⚠️对倒风险"] = (
            f"-10分（净买额为负但解读含买方，疑似对倒：{_jieda[:30]}）"
        )
    elif has_jigou_sell and not _duodao_risk:
        score = max(score - 5, 0)
        detail["⚠️机构卖出"] = "-5分（机构只卖不买）"

    return {
        "score":                min(max(score, 0), 35),
        "detail":               detail,
        "seat_tags":            seat_tags,
        "jigou_buy":            jigou_buy,
        "famous_buy":           famous_buy,
        "has_jigou_sell":       has_jigou_sell,
        "net_buy_strength_pct": _strength_pct,
        "duodao_risk":          _duodao_risk,
    }


# ── 历史胜率评分 ───────────────────────────────────────────────────────────

def score_history(hist: dict) -> dict:
    """
    历史胜率评分（满分 25 分）

    Args:
        hist: 含以下字段的字典
            count   (int)           历史上榜次数
            t2_win  (float|None)    T+2 胜率（%）
            t5_win  (float|None)    T+5 胜率（%）

    Returns:
        dict: score(0~25) / detail(dict)
    """
    score  = 0
    detail = {}
    count  = hist.get("count", 0)

    if count == 0:
        detail["历史上榜次数"] = "无历史记录，给予中间分"
        return {"score": 12, "detail": detail}

    # T+2 胜率（12分）
    t2_win   = hist.get("t2_win")
    t2_score = 0
    if t2_win is not None:
        if t2_win >= 70:   t2_score = 12
        elif t2_win >= 60: t2_score = 9
        elif t2_win >= 50: t2_score = 6
        elif t2_win >= 40: t2_score = 3
    score += t2_score
    detail[f"历史T+2胜率（{count}次）"] = (
        f"{t2_score}/12分（{t2_win}%）" if t2_win else "0/12分（数据不足）"
    )

    # T+5 胜率（13分）
    t5_win   = hist.get("t5_win")
    t5_score = 0
    if t5_win is not None:
        if t5_win >= 70:   t5_score = 13
        elif t5_win >= 60: t5_score = 10
        elif t5_win >= 50: t5_score = 7
        elif t5_win >= 40: t5_score = 3
    score += t5_score
    detail[f"历史T+5胜率（{count}次）"] = (
        f"{t5_score}/13分（{t5_win}%）" if t5_win else "0/13分（数据不足）"
    )

    return {"score": min(score, 25), "detail": detail}
