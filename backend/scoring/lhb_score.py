"""龙虎榜评分模型。

评分公式: score = tech*0.40 + fund*0.35 + hist*0.25, 乘大盘情绪系数.

设计文档见 docs/W3_SCORING_DESIGN.md.

三个维度都是纯函数(pure function), 方便单元测试与调参。
批量入库由 backend.scoring.runner 处理。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

# --------------------------------------------------------------------
# 常量权重(可调)
# --------------------------------------------------------------------

W_TECH = 0.40
W_FUND = 0.35
W_HIST = 0.25

SENTIMENT_COEF = {
    "HOT": 1.05,
    "WARM": 1.02,
    "NEUTRAL": 1.00,
    "COOL": 0.97,
    "COLD": 0.92,
}


# --------------------------------------------------------------------
# 输入 dataclass
# --------------------------------------------------------------------


@dataclass
class ScoreInput:
    """评分输入。单个 lhb_stock_daily 行 + 所需上下文"""

    # lhb 行本身(字典形式, 来自 psycopg2 RealDictCursor 或 pandas to_dict)
    lhb_row: dict

    # 上榜日前 60 个交易日的 kline, 按 trade_date 升序
    # 每个元素: {trade_date, open, high, low, close, pct_change, vol, amount,
    #            turnover_rate, ...}
    kline_window: list[dict] = field(default_factory=list)

    # 上榜当日的大盘情绪 level: HOT / WARM / NEUTRAL / COOL / COLD
    sentiment_level: str = "NEUTRAL"

    # 过去 1 年该 ts_code 的上榜统计
    # {
    #   "total_count": int,           # 1 年内上榜次数
    #   "avg_chg_3d":  float or None, # 上榜后 3 日平均涨幅(小数)
    #   "avg_chg_5d":  float or None,
    #   "win_rate_5d": float or None, # 上榜后 5 日胜率(0-1)
    # }
    hist_stats: dict = field(default_factory=dict)


# --------------------------------------------------------------------
# 工具: 把任意分数 clamp 到 [0, 100]
# --------------------------------------------------------------------


def _clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    try:
        if x is None or math.isnan(x):
            return 0.0
    except (TypeError, ValueError):
        return 0.0
    return max(lo, min(hi, float(x)))


def _safe_float(v: Any, default: float = 0.0) -> float:
    """把 Decimal / str / None 安全转 float"""
    if v is None:
        return default
    try:
        f = float(v)
        if math.isnan(f):
            return default
        return f
    except (TypeError, ValueError):
        return default


# --------------------------------------------------------------------
# 技术面评分
# --------------------------------------------------------------------


def compute_tech_score(inp: ScoreInput) -> tuple[float, dict]:
    """返回 (分数 0-100, 明细 dict)。"""
    detail: dict[str, float] = {}
    row = inp.lhb_row
    win = inp.kline_window

    # 1. 当日涨跌幅 (权重 0.30)
    # pct_change 是小数 0.0523 = 5.23%
    pct = abs(_safe_float(row.get("pct_change")))
    if pct >= 0.095:
        s1 = 100.0
    elif pct >= 0.07:
        s1 = 85.0
    elif pct >= 0.05:
        s1 = 70.0
    elif pct >= 0.03:
        s1 = 50.0
    else:
        s1 = 30.0
    detail["day_pct"] = s1

    # 2. 近 5 日累计涨幅 (权重 0.25)
    if len(win) >= 5:
        last5 = win[-5:]
        # 累计: ∏(1 + pct_change) - 1
        cum = 1.0
        for r in last5:
            cum *= (1.0 + _safe_float(r.get("pct_change")))
        cum -= 1.0
        if cum >= 0.15:
            s2 = 100.0
        elif cum >= 0.10:
            s2 = 85.0
        elif cum >= 0.05:
            s2 = 65.0
        elif cum >= 0.0:
            s2 = 50.0
        elif cum >= -0.05:
            s2 = 30.0
        else:
            s2 = 10.0
    else:
        s2 = 50.0  # 样本不足给中性
    detail["last5_cum"] = s2

    # 3. 量比 (权重 0.20)
    # 需要最近一日 vol / 前 20 日平均
    if len(win) >= 21:
        today_vol = _safe_float(win[-1].get("vol"))
        prev20_vols = [_safe_float(r.get("vol")) for r in win[-21:-1]]
        avg20 = sum(prev20_vols) / max(len(prev20_vols), 1)
        ratio = today_vol / max(avg20, 1e-9)
        if ratio >= 3.0:
            s3 = 100.0
        elif ratio >= 2.0:
            s3 = 80.0
        elif ratio >= 1.5:
            s3 = 65.0
        elif ratio >= 1.0:
            s3 = 50.0
        else:
            s3 = 25.0
    else:
        s3 = 50.0
    detail["volume_ratio"] = s3

    # 4. 换手率 (权重 0.15) —— 5%-30% 满分窗口
    tr = abs(_safe_float(row.get("turnover_rate")))  # 小数形式
    # 小数 0.05 = 5%
    if 0.05 <= tr <= 0.30:
        s4 = 100.0
    elif 0.03 <= tr < 0.05 or 0.30 < tr <= 0.50:
        s4 = 75.0
    elif tr < 0.03:
        s4 = 40.0  # 不够活跃
    else:
        s4 = 30.0  # 过度换手(>50%)
    detail["turnover_rate"] = s4

    # 5. 距 60 日高点距离 (权重 0.10)
    if len(win) >= 20:
        highs = [_safe_float(r.get("high")) for r in win if r.get("high")]
        if highs:
            max60 = max(highs)
            today_close = _safe_float(win[-1].get("close"))
            if max60 > 0:
                dist = (max60 - today_close) / max60  # 0 = 创新高, 0.3 = 离高点 30%
                if dist <= 0.02:
                    s5 = 100.0
                elif dist <= 0.05:
                    s5 = 85.0
                elif dist <= 0.10:
                    s5 = 70.0
                elif dist <= 0.20:
                    s5 = 50.0
                else:
                    s5 = 30.0
            else:
                s5 = 50.0
        else:
            s5 = 50.0
    else:
        s5 = 50.0
    detail["pos_to_high"] = s5

    score = (
        s1 * 0.30 + s2 * 0.25 + s3 * 0.20 + s4 * 0.15 + s5 * 0.10
    )

    # 新股惩罚: kline 窗口不足 60 天的股整体打 8 折
    if len(win) < 60:
        score *= 0.92
        detail["new_stock_penalty"] = 0.92

    return _clamp(score), detail


# --------------------------------------------------------------------
# 资金面评分
# --------------------------------------------------------------------


def compute_fund_score(inp: ScoreInput) -> tuple[float, dict]:
    detail: dict[str, float] = {}
    row = inp.lhb_row

    l_net = _safe_float(row.get("l_net"))
    l_buy = _safe_float(row.get("l_buy"))
    l_sell = _safe_float(row.get("l_sell"))
    amount = _safe_float(row.get("amount"))  # 成交额单位 万元
    float_mv = _safe_float(row.get("float_market_cap"))  # 流通市值 万元
    reason_type = str(row.get("reason_type") or "")

    # 1. 净买入金额的对数归一化 (权重 0.35)
    # 净买入 1 亿 ~ 80 分, 净买入 10 亿 ~ 100 分; 卖出转负给 0 分基础加反向刻度
    if l_net > 0:
        # 1e4 万 = 1 亿; log10(1e4)=4 → 映射 4→80, 5→100
        s1 = min(100.0, 40.0 + 15.0 * math.log10(max(l_net, 1.0)))
    elif l_net == 0:
        s1 = 50.0
    else:
        # 净卖出: 惩罚
        mag = math.log10(max(-l_net, 1.0))
        s1 = max(0.0, 40.0 - 10.0 * mag)
    detail["l_net_abs"] = s1

    # 2. 净买入占成交比 (权重 0.25) —— 5%-20% 满分
    if amount > 0 and l_net != 0:
        ratio = abs(l_net) / amount
        if 0.05 <= ratio <= 0.20:
            s2 = 100.0
        elif 0.03 <= ratio < 0.05 or 0.20 < ratio <= 0.40:
            s2 = 80.0
        elif 0.01 <= ratio < 0.03:
            s2 = 60.0
        elif ratio > 0.40:
            s2 = 50.0  # 成交主要来自龙虎榜=异常集中,也给中等
        else:
            s2 = 40.0
        if l_net < 0:
            s2 *= 0.4  # 净卖出占比高时惩罚
    else:
        s2 = 50.0
    detail["net_pct"] = s2

    # 3. 买卖力度比 (权重 0.20)
    if l_sell > 0:
        bs_ratio = l_buy / (l_sell + 1e-9)
    elif l_buy > 0:
        bs_ratio = 5.0  # 只买不卖, 视为强买
    else:
        bs_ratio = 1.0
    if bs_ratio >= 2.5:
        s3 = 100.0
    elif bs_ratio >= 1.5:
        s3 = 80.0
    elif bs_ratio >= 1.0:
        s3 = 60.0
    elif bs_ratio >= 0.5:
        s3 = 40.0
    else:
        s3 = 20.0
    detail["bs_ratio"] = s3

    # 4. 流通市值适中性 (权重 0.10): 20-300 亿(200000-3000000 万)给满分
    if 200000 <= float_mv <= 3000000:
        s4 = 100.0
    elif 100000 <= float_mv < 200000 or 3000000 < float_mv <= 5000000:
        s4 = 75.0
    elif 50000 <= float_mv < 100000 or 5000000 < float_mv <= 10000000:
        s4 = 55.0
    elif float_mv > 0:
        s4 = 40.0
    else:
        s4 = 50.0  # 市值未知, 中性
    detail["float_mv"] = s4

    # 5. 上榜原因分级 (权重 0.10)
    # 优先级: 连续三日涨幅累计 > 单日涨跌 > 换手 > 振幅
    reason = reason_type or str(row.get("reason") or "")
    if "连续三" in reason or "CONSECUTIVE" in reason:
        s5 = 100.0
    elif "涨幅" in reason or "涨跌" in reason or "PRICE_DEV" in reason:
        s5 = 80.0
    elif "换手" in reason or "TURNOVER" in reason:
        s5 = 60.0
    elif "振幅" in reason or "AMPLITUDE" in reason:
        s5 = 50.0
    else:
        s5 = 50.0
    detail["reason_rank"] = s5

    score = (
        s1 * 0.35 + s2 * 0.25 + s3 * 0.20 + s4 * 0.10 + s5 * 0.10
    )
    return _clamp(score), detail


# --------------------------------------------------------------------
# 历史胜率评分
# --------------------------------------------------------------------


def compute_hist_score(inp: ScoreInput) -> tuple[float, dict]:
    detail: dict[str, float] = {}
    st = inp.hist_stats
    total = int(st.get("total_count", 0) or 0)

    if total == 0:
        # 首次上榜, 样本不足, 保守中性
        detail["first_time"] = 50.0
        return 50.0, detail

    # 1. 上榜次数 (0.20)
    if 3 <= total <= 8:
        s1 = 100.0
    elif 2 <= total <= 10:
        s1 = 80.0
    elif 1 <= total <= 15:
        s1 = 60.0
    else:
        s1 = 40.0  # 过多(>15): 惯性炒作; 过少 handled above
    detail["total_count"] = s1

    # 2. 上榜后 3 日平均涨幅 (0.35)
    a3 = st.get("avg_chg_3d")
    if a3 is None:
        s2 = 50.0
    else:
        a3 = float(a3)
        if a3 >= 0.05:
            s2 = 100.0
        elif a3 >= 0.03:
            s2 = 80.0
        elif a3 >= 0.01:
            s2 = 65.0
        elif a3 >= 0.0:
            s2 = 50.0
        elif a3 >= -0.03:
            s2 = 30.0
        else:
            s2 = 10.0
    detail["avg_chg_3d"] = s2

    # 3. 上榜后 5 日平均涨幅 (0.25)
    a5 = st.get("avg_chg_5d")
    if a5 is None:
        s3 = 50.0
    else:
        a5 = float(a5)
        if a5 >= 0.07:
            s3 = 100.0
        elif a5 >= 0.04:
            s3 = 80.0
        elif a5 >= 0.0:
            s3 = 60.0
        elif a5 >= -0.05:
            s3 = 30.0
        else:
            s3 = 10.0
    detail["avg_chg_5d"] = s3

    # 4. 5 日胜率 (0.20)
    wr = st.get("win_rate_5d")
    if wr is None:
        s4 = 50.0
    else:
        wr = float(wr)
        if wr >= 0.60:
            s4 = 100.0
        elif wr >= 0.50:
            s4 = 75.0
        elif wr >= 0.40:
            s4 = 55.0
        else:
            s4 = 30.0
    detail["win_rate_5d"] = s4

    score = s1 * 0.20 + s2 * 0.35 + s3 * 0.25 + s4 * 0.20
    return _clamp(score), detail


# --------------------------------------------------------------------
# 主函数
# --------------------------------------------------------------------


def compute_score(inp: ScoreInput) -> dict:
    """返回 {score, score_tech, score_fund, score_hist, score_detail}.

    score 已经应用大盘情绪系数并 clamp 到 [0, 100].
    """
    s_tech, d_tech = compute_tech_score(inp)
    s_fund, d_fund = compute_fund_score(inp)
    s_hist, d_hist = compute_hist_score(inp)

    base = s_tech * W_TECH + s_fund * W_FUND + s_hist * W_HIST
    coef = SENTIMENT_COEF.get(inp.sentiment_level or "NEUTRAL", 1.0)
    final = _clamp(base * coef)

    return {
        "score": round(final, 2),
        "score_tech": round(s_tech, 2),
        "score_fund": round(s_fund, 2),
        "score_hist": round(s_hist, 2),
        "score_detail": {
            "tech": d_tech,
            "fund": d_fund,
            "hist": d_hist,
            "sentiment_coef": coef,
            "weights": {"tech": W_TECH, "fund": W_FUND, "hist": W_HIST},
        },
    }
