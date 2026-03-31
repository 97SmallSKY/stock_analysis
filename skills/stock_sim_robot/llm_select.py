"""
LLM 智能选股 + 挂牌价设定模块
================================
输入：今日主板候选信号列表（来自 fetch_signal.py）
输出：
  - 选定的 1 只股票
  - 建议挂牌卖出价（T+2 目标）
  - 详细的 4 维度理由

LLM 调用优先级：
  1. OpenClaw 已配置的百炼 provider（qwen3.5-plus，自动从 ~/.openclaw/openclaw.json 读取）
  2. 环境变量 OPENAI_API_KEY + OPENAI_BASE_URL（或 LLM_API_KEY + LLM_BASE_URL）
  3. 本地规则引擎 fallback（无需 API key，基于评分规则自动选股）
"""
import sys
import os
import json
import re
from pathlib import Path
from typing import Optional

from loguru import logger

_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(_ROOT))

from dotenv import load_dotenv
load_dotenv(dotenv_path=_ROOT / ".env")

from skills.stock_sim_robot.fetch_signal import format_signal_for_llm


# ── OpenClaw 配置读取 ────────────────────────────────────────────────────

def _load_openclaw_llm_config() -> Optional[dict]:
    """
    从 ~/.openclaw/openclaw.json 自动读取第一个可用的 LLM provider 配置

    Returns:
        dict(api_key, base_url, model) 或 None
    """
    config_path = Path.home() / ".openclaw" / "openclaw.json"
    if not config_path.exists():
        return None
    try:
        with open(config_path) as f:
            cfg = json.load(f)
        providers = cfg.get("models", {}).get("providers", {})
        # 按优先顺序选择 provider（qwen 最优先，其次其他）
        priority = ["bailian", "openai", "anthropic", "google", "groq"]
        # 快速模型优先列表（避免使用超慢的推理模型）
        fast_models = [
            "qwen3-max-2026-01-23", "qwen3-max", "qwen-max",
            "glm-5", "glm-4.7",
            "kimi-k2.5",
            "MiniMax-M2.5",
            "gpt-4o-mini", "gpt-4o",
        ]
        for name in priority + [k for k in providers if k not in priority]:
            p = providers.get(name)
            if not isinstance(p, dict):
                continue
            api_key  = p.get("apiKey") or p.get("api_key")
            base_url = p.get("baseUrl") or p.get("base_url")
            models   = p.get("models", [])
            if not api_key or not base_url:
                continue
            # 优先选快速模型
            model_id = None
            for fast in fast_models:
                m = next((m for m in models if isinstance(m, dict) and m.get("id") == fast), None)
                if m and "text" in m.get("input", []):
                    model_id = m["id"]
                    break
            # 没有匹配的快速模型，选第一个文本输入模型
            if not model_id:
                for m in models:
                    if isinstance(m, dict) and "text" in m.get("input", []):
                        model_id = m["id"]
                        break
            if not model_id and models:
                model_id = models[0]["id"] if isinstance(models[0], dict) else str(models[0])
            if model_id:
                logger.info(f"  使用 OpenClaw 配置的 LLM: {name}/{model_id}")
                return {"api_key": api_key, "base_url": base_url, "model": model_id, "provider_name": name}
    except Exception as e:
        logger.warning(f"  读取 OpenClaw LLM 配置失败: {e}")
    return None


# ── 挂牌价计算规则 ────────────────────────────────────────────────────

# 根据近20日涨幅位置设定目标涨幅
_LIST_PRICE_RULES = [
    # (最低20日涨幅, 最高20日涨幅, 目标涨幅%)
    (None, 0,    8.0),   # 超跌 → 目标+8%
    (0,    5,    6.0),   # 低位蓄力 → +6%
    (5,    15,   5.0),   # 正常位置 → +5%
    (15,   25,   4.0),   # 中高位 → +4%
    (25,   None, 3.5),   # 高位 → +3.5%（更保守）
]

STOP_LOSS_PCT = -5.0  # 止损线（-5%）


def _calc_list_price(close_price: float, gain20: float | None) -> tuple[float, float]:
    """
    计算挂牌卖出价

    Args:
        close_price: T 日收盘价（作为 T+1 买入价参考）
        gain20: 近20日涨幅（%）

    Returns:
        (list_price, target_pct) — 挂牌价和目标涨幅
    """
    if close_price <= 0:
        return 0.0, 5.0

    target_pct = 5.0  # 默认
    for low, high, pct in _LIST_PRICE_RULES:
        low_ok  = (low is None) or (gain20 is not None and gain20 >= low)
        high_ok = (high is None) or (gain20 is None) or (gain20 < high)
        if low_ok and high_ok:
            target_pct = pct
            break

    # T+1 开盘价估算 = T 日收盘价（开盘价在真实交易时才能确认）
    # 挂牌价 = 买入价 × (1 + target_pct/100)，这里先以 close_price 估算
    list_price = round(close_price * (1 + target_pct / 100), 2)
    return list_price, target_pct


# ── 规则引擎 fallback ─────────────────────────────────────────────────

def _rule_based_select(signals: list[dict]) -> dict:
    """
    基于规则的选股（LLM 不可用时的 fallback）

    规则：
      1. 综合评分 >= 60（如没有则取最高分）
      2. T+2历史胜率 >= 50%（如无数据则不作为硬性条件）
      3. 近20日涨幅 < 25%（避免高位追涨）
      4. 排名靠前（净买入额大）
    """
    if not signals:
        return {}

    # 筛选合格候选
    candidates = [s for s in signals if s["signal_score"] >= 60]
    if not candidates:
        candidates = signals  # 如果无高分股，取所有

    # 过滤高位（近20日涨幅 < 25%）
    low_pos = [s for s in candidates if (s["gain20"] is None or s["gain20"] < 25)]
    if low_pos:
        candidates = low_pos

    # 优先 T+2胜率 >= 50% 的
    good_hist = [s for s in candidates if s["t2_win_rate"] is not None and s["t2_win_rate"] >= 50]
    if good_hist:
        candidates = good_hist

    # 按综合评分 + 净买入额综合排序
    candidates.sort(key=lambda x: (x["signal_score"], x["net_buy_amount"]), reverse=True)
    chosen = candidates[0]

    list_price, target_pct = _calc_list_price(chosen["close_price"], chosen["gain20"])
    g20_str = f"{chosen['gain20']:+.1f}%" if chosen["gain20"] is not None else "N/A"
    t2_str  = f"{chosen['t2_win_rate']:.0f}%" if chosen["t2_win_rate"] is not None else "暂无"
    t5_str  = f"{chosen.get('t5_win_rate'):.0f}%" if chosen.get("t5_win_rate") is not None else "暂无"
    rsi_str = f"{chosen.get('rsi'):.1f}" if chosen.get("rsi") is not None else "N/A"
    seats   = "、".join(chosen.get("famous_buy", [])) if chosen.get("famous_buy") else \
              ("有机构买方" if chosen.get("jigou_buy") else "无知名席位")

    # 构建4维度结构化理由
    reason_tech = (
        f"技术面（{chosen['tech_score']}分）：RSI={rsi_str}，"
        f"近20日涨幅={g20_str}，"
        f"MACD得分={chosen.get('macd_score', 0)}，"
        f"均线MA5={chosen.get('ma5', 'N/A')}/MA10={chosen.get('ma10', 'N/A')}。"
    )
    reason_fund = (
        f"资金面（{chosen['fund_score']}分）：龙虎榜排名第{chosen['rank']}，"
        f"净买入={chosen['net_buy_yi']:.2f}亿，"
        f"知名席位={seats}。"
    )
    reason_history = (
        f"历史胜率（{chosen['hist_score']}分）：T+2历史胜率={t2_str}，"
        f"T+5历史胜率={t5_str}，"
        f"历史样本={chosen.get('hist_count', 0)}次。"
    )
    reason_seat = (
        f"席位分析：知名席位={seats}，"
        f"机构买方={'有' if chosen.get('jigou_buy') else '无'}，"
        f"席位标签={chosen.get('seat_tags', [])}。"
    )
    reason_summary = (
        f"规则引擎选股：{chosen['name']}（{chosen['code']}）综合评分={chosen['signal_score']}分（全场最高），"
        f"净买入={chosen['net_buy_yi']:.2f}亿，T+2历史胜率={t2_str}，近20日涨幅={g20_str}。"
        f"挂牌目标涨幅 +{target_pct}%（基于涨幅位置规则）。"
    )

    return {
        "code":            chosen["code"],
        "name":            chosen["name"],
        "signal_score":    chosen["signal_score"],
        "close_price":     chosen["close_price"],
        "list_price":      list_price,
        "list_price_pct":  target_pct,
        "stop_loss_pct":   STOP_LOSS_PCT,
        "reason":          reason_summary,       # 综合摘要（飞书用）
        "reason_tech":     reason_tech,
        "reason_fund":     reason_fund,
        "reason_history":  reason_history,
        "reason_seat":     reason_seat,
        "t2_win_rate":     chosen["t2_win_rate"],
        "seat_tags":       chosen["seat_tags"],
        "from_llm":        False,
    }


# ── LLM 调用 ──────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """你是一位专业的量化交易助手，专注于 A 股主板短线交易（龙虎榜策略）。

你的任务：从今日龙虎榜主板候选信号中，选出 1 只最值得操作的股票，并设定合理的 T+2 挂牌卖出价。

策略背景：
- T 日（今日）：龙虎榜上榜，分析信号
- T+1 日（明日）：开盘集合竞价买入
- T+2 日：设挂单卖出（冲高走人策略）
- 固定买入金额：10万元/笔，股数 = floor(100000/买入价/100)*100
- 止损线：-5%（T+2收盘价 < 买入价×95% 时自动止损）

选股核心规则（按权重排序）：
1. 综合评分越高越好（≥65 优先）
2. T+2历史胜率 ≥ 50% 更可靠（有数据时）
3. 近20日涨幅 < 20%（避免高位追涨，涨幅>25%则谨慎）
4. 无机构卖出席位（有机构只卖不买则风险较高）
5. 净买入额越大越好（主力资金认可度高）

挂牌价设定规则：
- 近20日涨幅 < 0%（超跌）：目标 +8%
- 近20日涨幅 0~5%（低位蓄力）：目标 +6%
- 近20日涨幅 5~15%（正常位置）：目标 +5%
- 近20日涨幅 15~25%（中高位）：目标 +4%
- 近20日涨幅 > 25%（高位）：目标 +3.5%

⚠️ 如果今日所有候选股综合评分均 < 50，或近20日涨幅均 > 30%，可以选择"不操作"（code="SKIP"）。

📌 用户消息中如果包含"近期亏损教训"部分，请务必参考这些历史错误模式：
   - 如果历史教训提示某类股票（高位追涨/RSI>80/特定板块）容易亏损，请在选股时降低该类股票的优先级
   - 如果历史教训提示止损模式与某候选股高度相似，请在理由中明确说明规避原因

请输出严格的 JSON 格式（不要包含其他文字），包含4个维度的选股理由：
{
  "code": "股票代码（6位）或 SKIP",
  "name": "股票名称",
  "list_price_pct": 目标涨幅（数字，如5.0表示+5%）,
  "reason": "综合选股摘要（60字以内，用于飞书通知）",
  "reason_tech": "技术面分析（包含RSI/均线/MACD/涨幅位置，50字以内）",
  "reason_fund": "资金面分析（包含净买入额/排名/席位质量，50字以内）",
  "reason_history": "历史胜率分析（T+2/T+5胜率及样本数，50字以内）",
  "reason_seat": "席位深度分析（具体席位名称/机构情况/买卖方向，50字以内）"
}"""


def _call_llm(signals: list[dict]) -> Optional[dict]:
    """
    调用 LLM 进行选股

    优先级：
      1. OpenClaw ~/.openclaw/openclaw.json 配置的 provider（百炼/qwen 等）
      2. 环境变量 OPENAI_API_KEY / LLM_API_KEY
      3. 返回 None → 规则引擎 fallback

    Returns:
        解析后的 dict，或 None（失败时）
    """
    # ── 解析 LLM 凭据（三层优先级）─────────────────────────────────
    api_key  = None
    base_url = None
    model    = None

    # 1. 从 OpenClaw 配置自动读取
    openclaw_cfg = _load_openclaw_llm_config()
    if openclaw_cfg:
        api_key  = openclaw_cfg["api_key"]
        base_url = openclaw_cfg["base_url"]
        model    = openclaw_cfg["model"]

    # 2. 环境变量覆盖（允许手动指定）
    if os.getenv("OPENAI_API_KEY") or os.getenv("LLM_API_KEY"):
        api_key  = os.getenv("OPENAI_API_KEY") or os.getenv("LLM_API_KEY")
        base_url = os.getenv("OPENAI_BASE_URL") or os.getenv("LLM_BASE_URL") or base_url
        model    = os.getenv("LLM_MODEL") or model or "gpt-4o-mini"

    if not api_key:
        logger.info("未找到可用的 LLM 配置（OpenClaw 或环境变量），跳过 LLM 调用")
        return None

    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key, base_url=base_url if base_url else None)

        signal_text = format_signal_for_llm(signals)

        # ── 读取近期亏损教训（注入 prompt，避免重复犯错）──────────────
        loss_context = ""
        try:
            from skills.stock_sim_robot.trade_log import (
                get_recent_lessons,
                format_lessons_for_llm,
            )
            lessons = get_recent_lessons(n=5)
            if lessons:
                loss_context = "\n\n" + format_lessons_for_llm(lessons)
                logger.info(f"已加载 {len(lessons)} 条亏损教训注入 LLM prompt")
            else:
                # 尝试从 Markdown 文档读取（DB 中可能还没有记录）
                md_path = _ROOT / "docs" / "loss_lessons.md"
                if md_path.exists():
                    content = md_path.read_text(encoding="utf-8")
                    # 只取 <!-- 注释后的内容（实际条目）
                    if "<!-- 以下内容由系统自动追加" in content:
                        tail = content.split("<!-- 以下内容由系统自动追加")[1]
                        entries = [l for l in tail.split("\n") if l.startswith("###")]
                        if entries:
                            loss_context = "\n\n【近期亏损教训（Markdown）】\n" + "\n".join(entries[:5])
        except Exception as _e:
            logger.debug(f"读取亏损教训非致命: {_e}")

        user_msg = (
            f"{signal_text}{loss_context}\n\n"
            f"请根据以上信号数据，选出 1 只最佳主板股票并设定挂牌价。\n"
            f"直接输出 JSON，不要包含其他说明文字。"
        )

        logger.info(f"调用 LLM 选股（model={model}）...")
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user",   "content": user_msg},
            ],
            temperature=0.2,
            max_tokens=800,
        )
        content = response.choices[0].message.content.strip()
        logger.info(f"LLM 响应: {content[:200]}")

        # 提取 JSON（防止 markdown 包裹）
        json_match = re.search(r"\{[\s\S]+\}", content)
        if not json_match:
            logger.error("LLM 响应中未找到 JSON")
            return None

        result = json.loads(json_match.group())
        return result

    except Exception as e:
        logger.error(f"LLM 调用失败: {e}")
        return None


# ── 主入口 ────────────────────────────────────────────────────────────

def select_stock(signals: list[dict]) -> dict:
    """
    主选股函数：尝试 LLM → 失败则 fallback 到规则引擎

    Args:
        signals: get_mainboard_signals() 的返回值

    Returns:
        dict，包含：
          code, name, signal_score, close_price,
          list_price, list_price_pct, stop_loss_pct,
          reason, reason_tech, reason_fund, reason_history, reason_seat,
          t2_win_rate, seat_tags, from_llm
    """
    if not signals:
        return {
            "code": "SKIP", "name": "无候选", "reason": "今日无主板候选信号",
            "reason_tech": "", "reason_fund": "", "reason_history": "", "reason_seat": "",
            "list_price": 0, "list_price_pct": 0, "stop_loss_pct": STOP_LOSS_PCT,
            "close_price": 0, "signal_score": 0, "from_llm": False,
            "t2_win_rate": None, "seat_tags": [],
        }

    # 尝试 LLM
    llm_result = _call_llm(signals)
    if llm_result and llm_result.get("code") and llm_result["code"] != "SKIP":
        code = str(llm_result["code"]).zfill(6)
        # 在 signals 中找到对应数据
        chosen = next((s for s in signals if s["code"] == code), None)
        if chosen is None:
            logger.warning(f"LLM 选了 {code} 但在信号列表中找不到，使用规则引擎")
            return _rule_based_select(signals)

        list_pct   = float(llm_result.get("list_price_pct", 5.0))
        list_price = round(chosen["close_price"] * (1 + list_pct / 100), 2)

        return {
            "code":            chosen["code"],
            "name":            chosen["name"],
            "signal_score":    chosen["signal_score"],
            "close_price":     chosen["close_price"],
            "list_price":      list_price,
            "list_price_pct":  list_pct,
            "stop_loss_pct":   STOP_LOSS_PCT,
            "reason":          llm_result.get("reason", "LLM 选股"),
            "reason_tech":     llm_result.get("reason_tech", ""),
            "reason_fund":     llm_result.get("reason_fund", ""),
            "reason_history":  llm_result.get("reason_history", ""),
            "reason_seat":     llm_result.get("reason_seat", ""),
            "t2_win_rate":     chosen["t2_win_rate"],
            "seat_tags":       chosen["seat_tags"],
            "from_llm":        True,
        }

    elif llm_result and llm_result.get("code") == "SKIP":
        logger.info("LLM 建议今日不操作（SKIP）")
        return {
            "code": "SKIP", "name": "不操作", "reason": llm_result.get("reason", "LLM 建议今日不操作"),
            "reason_tech": "", "reason_fund": "", "reason_history": "", "reason_seat": "",
            "list_price": 0, "list_price_pct": 0, "stop_loss_pct": STOP_LOSS_PCT,
            "close_price": 0, "signal_score": 0, "from_llm": True,
            "t2_win_rate": None, "seat_tags": [],
        }

    # Fallback: 规则引擎
    logger.info("使用规则引擎 fallback 选股")
    return _rule_based_select(signals)


# ══════════════════════════════════════════════════════════════════════════════
# LLM 自然语言策略：草拟 + 解析
# ══════════════════════════════════════════════════════════════════════════════

_STRATEGY_SYSTEM_PROMPT = """你是一位专业的量化交易风险管理助手，专注于 A 股主板短线交易。

你的任务：根据入选股的技术特征，草拟一条自然语言卖出策略，并同时输出对应的结构化 JSON 规则。

策略设计原则：
- 策略必须覆盖「止盈条件」和「止损条件」，两者缺一不可
- 止损条件应考虑买入价的百分比（通常 -3% 到 -5%）
- 止盈条件应基于技术面特征（涨幅、RSI、时间等）
- 策略应简洁明了，避免过于复杂的嵌套逻辑

JSON 规则 schema（必须严格遵守）：
{
  "version": 1,
  "rules": [
    {
      "id": "r1",
      "condition": {"type": "CONDITION_TYPE", ...},
      "action": {"type": "ACTION_TYPE", ...},
      "priority": 1,
      "description": "规则描述"
    }
  ],
  "hard_stop_pct": -5.0,
  "parsed_at": "ISO时间戳"
}

支持的 condition.type：
- change_pct: {"type":"change_pct","op":">=","value":5.0}  — 相对买入价涨跌幅
- time_range: {"type":"time_range","after":"14:00"}  — 时间条件
- rsi: {"type":"rsi","op":">","value":80}  — RSI指标
- is_limit_up: {"type":"is_limit_up","value":true}  — 涨停状态
- days_held: {"type":"days_held","op":">=","value":2}  — 持仓天数
- AND: {"type":"AND","conditions":[...]}  — 逻辑与

支持的 action.type：
- sell_all: {}  — 全仓卖出
- sell_pct: {"pct": 50}  — 卖出百分比(0-100)

注意：hard_stop_pct 是系统级兜底止损，始终生效，无需在 rules 中添加止损规则（已由系统保障）。
但如果你认为止盈条件中有特殊止损逻辑（如涨停后次日跌破某价位），可以添加到 rules 中。

请输出严格的 JSON 格式（不要包含其他文字）：
{
  "nl_text": "一段自然语言策略描述（80-150字）",
  "hard_stop_pct": -5.0,
  "parsed_rules": { ...上述 JSON 结构... }
}"""


def draft_nl_strategy(
    trade_id: int,
    selection: dict,
    signals: list[dict],
) -> dict | None:
    """
    基于入选股的技术特征，让 LLM 草拟一条自然语言卖出策略。

    选股完成后自动调用，结果保存为 status='draft'，用户确认后激活。

    Args:
        trade_id:   新建交易 ID（用于关联）
        selection:  select_stock() 返回的入选股结果
        signals:    全部候选信号列表（用于上下文）

    Returns:
        dict(nl_text, hard_stop_pct, parsed_rules, parse_error) 或 None（LLM 不可用时）
    """
    # ── 构建 LLM 上下文 ──────────────────────────────────────────
    code        = selection.get("code", "")
    name        = selection.get("name", "")
    close_price = selection.get("close_price", 0)
    signal_score = selection.get("signal_score", 0)
    t2_win_rate = selection.get("t2_win_rate")
    gain20      = None

    # 从 signals 中找到对应的详细数据
    sig_detail = next((s for s in signals if str(s.get("code", "")) == str(code)), None)
    if sig_detail:
        gain20 = sig_detail.get("gain20")
        rsi    = sig_detail.get("rsi")
    else:
        rsi = None

    g20_str = f"{gain20:+.1f}%" if gain20 is not None else "未知"
    t2_str  = f"{t2_win_rate:.0f}%" if t2_win_rate is not None else "暂无数据"
    rsi_str = f"{rsi:.1f}" if rsi is not None else "未知"

    user_msg = f"""入选股信息：
- 股票：{name}（{code}），收盘价：{close_price}元
- 综合评分：{signal_score}分
- T+2历史胜率：{t2_str}
- 近20日涨幅：{g20_str}
- RSI(14)：{rsi_str}
- 技术面分析：{selection.get('reason_tech', '暂无')}
- 资金面分析：{selection.get('reason_fund', '暂无')}

请根据以上股票特征，草拟一条适合的卖出策略，重点考虑：
1. 该股近期涨幅位置（是低位还是高位）
2. T+2历史胜率（历史表现如何）
3. 资金面强弱（是否有知名主力）

直接输出 JSON，不要包含其他说明文字。"""

    # ── 调用 LLM ─────────────────────────────────────────────────
    api_key  = None
    base_url = None
    model    = None

    openclaw_cfg = _load_openclaw_llm_config()
    if openclaw_cfg:
        api_key  = openclaw_cfg["api_key"]
        base_url = openclaw_cfg["base_url"]
        model    = openclaw_cfg["model"]

    if os.getenv("OPENAI_API_KEY") or os.getenv("LLM_API_KEY"):
        api_key  = os.getenv("OPENAI_API_KEY") or os.getenv("LLM_API_KEY")
        base_url = os.getenv("OPENAI_BASE_URL") or os.getenv("LLM_BASE_URL") or base_url
        model    = os.getenv("LLM_MODEL") or model or "gpt-4o-mini"

    if not api_key:
        logger.info(f"draft_nl_strategy: 无可用 LLM 配置，返回 None（将使用默认三档策略）")
        return None

    try:
        from openai import OpenAI
        from datetime import datetime as _dt
        client = OpenAI(api_key=api_key, base_url=base_url if base_url else None)

        logger.info(f"LLM 草拟策略: {name}({code})（model={model}）...")
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _STRATEGY_SYSTEM_PROMPT},
                {"role": "user",   "content": user_msg},
            ],
            temperature=0.3,
            max_tokens=1200,
        )
        content = response.choices[0].message.content.strip()
        logger.info(f"LLM 策略草拟响应: {content[:200]}")

        # 提取 JSON
        json_match = re.search(r"\{[\s\S]+\}", content)
        if not json_match:
            logger.error("LLM 策略响应中未找到 JSON")
            return {"nl_text": "", "hard_stop_pct": -5.0, "parsed_rules": None, "parse_error": "no_json_in_response"}

        result = json.loads(json_match.group())
        nl_text      = result.get("nl_text", "")
        hard_stop    = float(result.get("hard_stop_pct", -5.0))
        parsed_rules = result.get("parsed_rules")

        # 补充 parsed_at 字段
        if parsed_rules and isinstance(parsed_rules, dict):
            parsed_rules["parsed_at"] = _dt.now().isoformat()
            parsed_rules["parse_model"] = model

        logger.info(f"LLM 草拟策略成功: {name}({code}) 规则数={len(parsed_rules.get('rules', [])) if parsed_rules else 0}")
        return {
            "nl_text":      nl_text,
            "hard_stop_pct": hard_stop,
            "parsed_rules": parsed_rules,
            "parse_error":  None,
        }

    except Exception as e:
        logger.error(f"draft_nl_strategy LLM 调用失败: {e}")
        return {"nl_text": "", "hard_stop_pct": -5.0, "parsed_rules": None, "parse_error": str(e)[:200]}


def parse_nl_strategy(
    nl_text: str,
    buy_price: float,
    code: str = "",
    name: str = "",
) -> dict:
    """
    解析用户手动输入的自然语言策略为结构化 JSON 规则。

    与 draft_nl_strategy 的区别：
    - draft_nl_strategy 是 LLM 自动生成（基于技术特征）
    - parse_nl_strategy 是解析用户输入的自然语言文本

    Args:
        nl_text:    用户输入的策略文本（如"涨5%全仓卖，跌3%止损"）
        buy_price:  实际买入价（用于计算绝对价格条件）
        code:       股票代码（用于日志）
        name:       股票名称（用于日志）

    Returns:
        dict(parsed_rules, hard_stop_pct, parse_error)
    """
    PARSE_PROMPT = f"""你是一位量化策略解析助手。请将以下自然语言策略解析为结构化 JSON 规则。

已知信息：
- 股票：{name}（{code}）
- 实际买入价：{buy_price}元

自然语言策略：
「{nl_text}」

请解析为以下 JSON schema（严格遵守）：
{{
  "version": 1,
  "rules": [
    {{
      "id": "r1",
      "condition": {{"type": "change_pct", "op": ">=", "value": 5.0}},
      "action": {{"type": "sell_all"}},
      "priority": 1,
      "description": "规则说明"
    }}
  ],
  "hard_stop_pct": -5.0
}}

规则要点：
- 将文字描述中的每个触发条件各自生成一条规则
- change_pct 的 value 是涨跌幅百分比数字（如 +5% 写 5.0，-3% 写 -3.0）
- 时间条件用 time_range
- 涨停用 is_limit_up
- hard_stop_pct 是系统兜底止损（从用户策略中提取，如未提及则使用 -5.0）
- 直接输出 JSON，不要包含其他说明"""

    api_key  = None
    base_url = None
    model    = None

    openclaw_cfg = _load_openclaw_llm_config()
    if openclaw_cfg:
        api_key  = openclaw_cfg["api_key"]
        base_url = openclaw_cfg["base_url"]
        model    = openclaw_cfg["model"]

    if os.getenv("OPENAI_API_KEY") or os.getenv("LLM_API_KEY"):
        api_key  = os.getenv("OPENAI_API_KEY") or os.getenv("LLM_API_KEY")
        base_url = os.getenv("OPENAI_BASE_URL") or os.getenv("LLM_BASE_URL") or base_url
        model    = os.getenv("LLM_MODEL") or model or "gpt-4o-mini"

    if not api_key:
        return {"parsed_rules": None, "hard_stop_pct": -5.0, "parse_error": "no_llm_config"}

    try:
        from openai import OpenAI
        from datetime import datetime as _dt
        client = OpenAI(api_key=api_key, base_url=base_url if base_url else None)

        logger.info(f"parse_nl_strategy: {name}({code}) model={model}")
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": PARSE_PROMPT}],
            temperature=0.1,
            max_tokens=1000,
        )
        content = response.choices[0].message.content.strip()

        json_match = re.search(r"\{[\s\S]+\}", content)
        if not json_match:
            return {"parsed_rules": None, "hard_stop_pct": -5.0, "parse_error": "no_json"}

        parsed = json.loads(json_match.group())
        parsed["parsed_at"]    = _dt.now().isoformat()
        parsed["parse_model"]  = model

        hard_stop = float(parsed.pop("hard_stop_pct", -5.0))
        logger.info(f"parse_nl_strategy 成功: 规则数={len(parsed.get('rules', []))}")
        return {"parsed_rules": parsed, "hard_stop_pct": hard_stop, "parse_error": None}

    except Exception as e:
        logger.error(f"parse_nl_strategy 失败: {e}")
        return {"parsed_rules": None, "hard_stop_pct": -5.0, "parse_error": str(e)[:200]}


if __name__ == "__main__":
    # 测试
    from skills.stock_sim_robot.fetch_signal import get_mainboard_signals
    from datetime import date

    today   = date.today().strftime("%Y%m%d")
    signals = get_mainboard_signals(today)
    result  = select_stock(signals)
    print("\n=== 选股结果 ===")
    for k, v in result.items():
        print(f"  {k}: {v}")
