"""
亏损复盘总结模块
================
每次平仓亏损后自动生成结构化总结，写入：
  1. DB sim_trade_lessons 表（结构化存储，供 LLM 历史参考）
  2. docs/loss_lessons.md 文档（Markdown 格式，LLM 每次选股前可读取）

使用场景：
  - close_trade / mark_stop_loss 平仓亏损后自动调用
  - scripts/sim_robot_run.py loss_summary 手动触发，生成全量报告
  - LLM 选股时通过 get_recent_lessons() 获取最近教训，加入 prompt

教训自动分析规则：
  - 止损类型（sell_type='止损'）
      → 买入后持续下跌，价格跌破止损线
      → 检查买入时技术面：RSI/均线/MACD 信号是否真的健康
  - 持有到期（sell_type='持有到期'）
      → T+2 未能冲高，超时平仓
      → 市场情绪弱、股票流动性差、挂牌价设置过高
  - RSI > 70 at 买入时
      → 超买状态追入，成功率偏低
  - 近20日涨幅 > 25%
      → 高位追涨，回调风险大
  - 无历史胜率数据
      → 信号置信度不足，应给更低权重
"""
import sys
import os
from pathlib import Path
from datetime import date, datetime

import pandas as pd
from loguru import logger

_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(_ROOT))
os.chdir(_ROOT)

from utils.storage import storage


# ── DB 表定义 ─────────────────────────────────────────────────────────

_CREATE_SIM_TRADE_LESSONS = """
CREATE TABLE IF NOT EXISTS sim_trade_lessons (
    id          SERIAL PRIMARY KEY,
    trade_id    INT,                              -- 关联 sim_trades.id
    trade_date  VARCHAR(8),                       -- 开仓日期 (YYYYMMDD)
    code        VARCHAR(6),                       -- 股票代码
    name        VARCHAR(20),                      -- 股票名称
    sell_type   VARCHAR(20),                      -- 止损 / 持有到期 / 分批卖出完成
    profit_pct  FLOAT,                            -- 盈亏百分比（负数=亏损）
    buy_price   FLOAT,                            -- 买入价
    sell_price  FLOAT,                            -- 卖出价
    signal_score INT,                             -- 买入时综合评分
    t2_win_rate FLOAT,                            -- 买入时 T+2 历史胜率
    from_llm    BOOLEAN DEFAULT FALSE,            -- 是否由 LLM 选股
    loss_reason TEXT,                             -- 亏损原因分析（自动生成）
    lesson      TEXT,                             -- 经验教训（改进建议）
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


def ensure_table():
    """确保 sim_trade_lessons 表已创建（幂等）"""
    try:
        from sqlalchemy import text
        engine = storage._get_engine()
        with engine.begin() as conn:
            conn.execute(text(_CREATE_SIM_TRADE_LESSONS))
        return True
    except Exception as e:
        logger.error(f"ensure_table(sim_trade_lessons) 失败: {e}")
        return False


# ── 亏损原因自动分析 ─────────────────────────────────────────────────

def _analyze_loss_reason(trade: dict) -> tuple[str, str]:
    """
    根据交易记录自动分析亏损原因和经验教训

    Args:
        trade: sim_trades 的一行记录（dict）

    Returns:
        (loss_reason, lesson) — 亏损原因 + 改进建议
    """
    reasons = []
    lessons = []

    sell_type   = str(trade.get("sell_type") or "")
    profit_pct  = float(trade.get("profit_pct") or 0)
    signal_score = int(trade.get("signal_score") or 0)
    t2_win_rate = trade.get("t2_win_rate")
    buy_price   = float(trade.get("buy_price") or 0)
    sell_price  = float(trade.get("sell_price") or 0)
    llm_reason  = str(trade.get("llm_reason") or "")
    from_llm    = bool(trade.get("from_llm") or False)
    reason_tech = str(trade.get("reason_tech") or "")
    code        = str(trade.get("code") or "")
    name        = str(trade.get("name") or "")
    trade_date  = str(trade.get("trade_date") or "")

    # ── 1. 卖出类型原因 ────────────────────────────────────────────
    if "止损" in sell_type:
        reasons.append(
            f"触发止损平仓：买入价 {buy_price:.3f}，止损卖出价 {sell_price:.3f}，"
            f"亏损 {profit_pct:.2f}%。买入后股价持续下跌，未能实现预期冲高。"
        )
        lessons.append("止损类亏损通常发生在买入时机偏晚（大盘/板块转弱时），下次应检查当日大盘情绪再决策。")

    elif "持有到期" in sell_type:
        reasons.append(
            f"超时平仓（持有到期）：T+2 日未能触及挂牌价 {trade.get('list_price', '—')}，"
            f"最终以 {sell_price:.3f} 平仓，亏损 {profit_pct:.2f}%。"
        )
        lessons.append("持有到期亏损说明挂牌价可能设置偏高，或者该股在 T+2 日流动性不足。下次对此类个股可适当降低挂牌目标。")

    elif "分批" in sell_type:
        if profit_pct < 0:
            reasons.append(
                f"分批卖出完成但整体亏损：加权平均卖出价 {sell_price:.3f} < 买入价 {buy_price:.3f}，"
                f"亏损 {profit_pct:.2f}%。部分档位未能完成卖出导致综合亏损。"
            )
            lessons.append("分批亏损说明前几档微盈利无法弥补后续下跌，应注意第1档目标价设置的合理性。")

    # ── 2. 信号质量原因 ────────────────────────────────────────────
    if signal_score < 60:
        reasons.append(f"买入时综合评分偏低（{signal_score}/100，低于 60 分），信号质量不足。")
        lessons.append(f"建议将最低评分门槛提高至 65 分，当前触发的低质量信号（{signal_score}分）应过滤。")

    if t2_win_rate is not None and float(t2_win_rate) < 50:
        reasons.append(f"历史 T+2 胜率偏低（{t2_win_rate:.0f}%），该股上榜后冲高成功率不足。")
        lessons.append(f"此股历史胜率仅 {t2_win_rate:.0f}%，下次选股应优先选胜率 ≥ 50% 的标的。")
    elif t2_win_rate is None:
        reasons.append("无 T+2 历史胜率数据，信号置信度不足（首次上榜标的）。")
        lessons.append("无历史数据的首次上榜股票风险较高，建议降低权重或要求更高的技术面评分弥补。")

    # ── 3. 技术面原因（从 reason_tech 文本中提取）──────────────────
    if reason_tech:
        if "超买" in reason_tech or "RSI=7" in reason_tech or "RSI=8" in reason_tech:
            reasons.append("买入时 RSI 已进入超买区间（>70），短期获利盘压力大。")
            lessons.append("RSI > 70 时追入成功率偏低，建议设置更保守的目标涨幅（如 +3% 而非 +5%）。")

    # ── 4. LLM 选股与亏损 ─────────────────────────────────────────
    if from_llm:
        reasons.append(f"由 LLM 选股（{'选股理由：' + llm_reason[:50] if llm_reason else '理由未记录'}）。")
        lessons.append(
            "LLM 选股导致亏损时，需回顾 LLM 给出的理由是否与实际走势一致，"
            "用于改进下次 LLM 提示词中的选股权重设定。"
        )
    else:
        reasons.append("由规则引擎选股（非 LLM）。")
        lessons.append("规则引擎选股亏损，可能需要调整评分权重或增加新的过滤条件。")

    loss_reason = "；".join(reasons) if reasons else "亏损原因未知"
    lesson      = "；".join(lessons) if lessons else "建议人工复盘"

    return loss_reason, lesson


# ── 生成单笔亏损总结 ─────────────────────────────────────────────────

def generate_loss_lesson(trade: dict) -> dict | None:
    """
    为单笔亏损交易生成总结

    Args:
        trade: sim_trades 的一行记录（dict 或 Series）

    Returns:
        lesson dict，失败返回 None
    """
    try:
        if isinstance(trade, pd.Series):
            trade = trade.to_dict()

        profit_pct = float(trade.get("profit_pct") or 0)
        if profit_pct >= 0:
            return None  # 盈利交易不做亏损总结

        loss_reason, lesson = _analyze_loss_reason(trade)

        lesson_dict = {
            "trade_id":    int(trade.get("id") or 0),
            "trade_date":  str(trade.get("trade_date") or ""),
            "code":        str(trade.get("code") or ""),
            "name":        str(trade.get("name") or ""),
            "sell_type":   str(trade.get("sell_type") or ""),
            "profit_pct":  profit_pct,
            "buy_price":   float(trade.get("buy_price") or 0),
            "sell_price":  float(trade.get("sell_price") or 0),
            "signal_score": int(trade.get("signal_score") or 0),
            "t2_win_rate": float(trade.get("t2_win_rate")) if trade.get("t2_win_rate") is not None else None,
            "from_llm":    bool(trade.get("from_llm") or False),
            "loss_reason": loss_reason,
            "lesson":      lesson,
        }
        return lesson_dict

    except Exception as e:
        logger.error(f"generate_loss_lesson 失败: {e}")
        return None


# ── 写入 DB ───────────────────────────────────────────────────────────

def save_lesson_to_db(lesson: dict) -> bool:
    """将亏损总结写入 sim_trade_lessons 表（幂等：同 trade_id 不重复写）"""
    if not lesson:
        return False
    try:
        ensure_table()
        from sqlalchemy import text
        engine = storage._get_engine()

        # 检查是否已存在（避免重复写入）
        if lesson.get("trade_id"):
            exists = pd.read_sql(
                text("SELECT id FROM sim_trade_lessons WHERE trade_id = :tid"),
                engine, params={"tid": lesson["trade_id"]}
            )
            if not exists.empty:
                logger.info(f"trade_id={lesson['trade_id']} 亏损总结已存在，跳过")
                return True

        sql = text("""
            INSERT INTO sim_trade_lessons
                (trade_id, trade_date, code, name, sell_type, profit_pct,
                 buy_price, sell_price, signal_score, t2_win_rate, from_llm,
                 loss_reason, lesson, created_at)
            VALUES
                (:trade_id, :trade_date, :code, :name, :sell_type, :profit_pct,
                 :buy_price, :sell_price, :signal_score, :t2_win_rate, :from_llm,
                 :loss_reason, :lesson, CURRENT_TIMESTAMP)
        """)
        with engine.begin() as conn:
            conn.execute(sql, {k: v for k, v in lesson.items() if k != "id"})
        logger.info(f"亏损总结已写入 DB: {lesson['name']}（{lesson['code']}）{lesson['profit_pct']:+.2f}%")
        return True
    except Exception as e:
        logger.error(f"save_lesson_to_db 失败: {e}")
        return False


# ── 写入 Markdown 文档 ────────────────────────────────────────────────

def append_lesson_to_md(lesson: dict, md_path: Path | None = None):
    """
    将单笔亏损总结追加到 docs/loss_lessons.md

    文档供 LLM 每次选股前读取，了解历史失败经验。
    格式：Markdown，每笔总结为一个二级标题条目。
    """
    if not lesson:
        return

    if md_path is None:
        md_path = _ROOT / "docs" / "loss_lessons.md"

    md_path.parent.mkdir(parents=True, exist_ok=True)

    # 如果文档不存在，创建带头部说明的文档
    if not md_path.exists():
        header = """# 模拟炒股亏损复盘总结

> **说明**：本文档由系统自动生成，每次平仓亏损后自动追加。  
> **用途**：LLM 选股时读取此文档，避免重复犯同类错误。  
> **格式**：每笔亏损一个条目，包含原因分析和改进建议。  
> **维护**：`scripts/sim_robot_run.py loss_summary` 可手动刷新全量内容。

---

"""
        md_path.write_text(header, encoding="utf-8")

    td = lesson.get("trade_date", "")
    d_fmt = f"{td[:4]}-{td[4:6]}-{td[6:]}" if len(td) == 8 else td
    profit_str = f"{lesson['profit_pct']:+.2f}%"

    entry = f"""
## {d_fmt} {lesson['name']}（{lesson['code']}）— {profit_str}

| 字段 | 值 |
|------|-----|
| 开仓日期 | {d_fmt} |
| 股票 | {lesson['name']}（{lesson['code']}） |
| 卖出类型 | {lesson['sell_type']} |
| 买入价 / 卖出价 | {lesson['buy_price']:.3f} / {lesson['sell_price']:.3f} |
| 盈亏 | **{profit_str}** |
| 综合评分 | {lesson['signal_score']}/100 |
| T+2历史胜率 | {f"{lesson['t2_win_rate']:.0f}%" if lesson['t2_win_rate'] is not None else "无数据"} |
| 选股方式 | {"🤖 LLM 选股" if lesson['from_llm'] else "📐 规则引擎"} |

**亏损原因：**  
{lesson['loss_reason']}

**经验教训：**  
{lesson['lesson']}

---
"""
    with open(md_path, "a", encoding="utf-8") as f:
        f.write(entry)

    logger.info(f"亏损总结已追加到 {md_path}")


# ── 批量生成 / 补全历史 ───────────────────────────────────────────────

def generate_all_lessons(n: int = 50) -> list[dict]:
    """
    扫描最近 n 笔亏损交易，为没有总结的记录生成总结并写入 DB 和 Markdown

    Args:
        n: 最多处理最近 n 笔亏损记录

    Returns:
        本次新生成的总结列表
    """
    ensure_table()

    try:
        from sqlalchemy import text
        engine = storage._get_engine()

        # 查询最近亏损记录（profit_pct < 0，且在 sim_trade_lessons 中没有记录）
        sql = text("""
            SELECT st.*
            FROM sim_trades st
            LEFT JOIN sim_trade_lessons stl ON st.id = stl.trade_id
            WHERE st.status = 'closed'
              AND st.profit_pct < 0
              AND stl.id IS NULL
            ORDER BY st.created_at DESC
            LIMIT :n
        """)
        df = pd.read_sql(sql, engine, params={"n": n})
    except Exception as e:
        logger.error(f"查询亏损记录失败: {e}")
        return []

    if df.empty:
        logger.info("没有需要生成总结的亏损记录")
        return []

    new_lessons = []
    for _, row in df.iterrows():
        lesson = generate_loss_lesson(row.to_dict())
        if lesson:
            saved = save_lesson_to_db(lesson)
            if saved:
                append_lesson_to_md(lesson)
                new_lessons.append(lesson)

    logger.info(f"共生成 {len(new_lessons)} 条新亏损总结")
    return new_lessons


# ── 读取历史教训（供 LLM prompt 使用）────────────────────────────────

def get_recent_lessons(n: int = 5) -> list[dict]:
    """
    获取最近 n 条亏损总结（供 LLM 选股时参考）

    Returns:
        list of dict，按时间降序
    """
    try:
        ensure_table()
        from sqlalchemy import text
        engine = storage._get_engine()
        df = pd.read_sql(
            text("SELECT * FROM sim_trade_lessons ORDER BY created_at DESC LIMIT :n"),
            engine, params={"n": n}
        )
        return df.to_dict("records") if not df.empty else []
    except Exception as e:
        logger.warning(f"get_recent_lessons 失败: {e}")
        return []


def format_lessons_for_llm(lessons: list[dict]) -> str:
    """
    将历史教训格式化为 LLM Prompt 可用的文本

    Returns:
        多行文本，每条教训包含日期、股票、原因、改进点
    """
    if not lessons:
        return "（近期无亏损记录，继续保持）"

    lines = ["【近期亏损教训（请参考，避免重复犯同类错误）】\n"]
    for i, l in enumerate(lessons, 1):
        td = str(l.get("trade_date", ""))
        d_fmt = f"{td[:4]}/{td[4:6]}/{td[6:]}" if len(td) == 8 else td
        t2 = f"{l['t2_win_rate']:.0f}%" if l.get("t2_win_rate") is not None else "无数据"
        lines.append(
            f"  [{i}] {d_fmt} {l['name']}（{l['code']}）"
            f" [{l['sell_type']}] {l['profit_pct']:+.2f}%\n"
            f"    原因: {l['loss_reason'][:100]}...\n"
            f"    教训: {l['lesson'][:100]}..."
        )
    return "\n".join(lines)


# ── 格式化报告（供 CLI 打印）─────────────────────────────────────────

def format_lessons_report(lessons: list[dict]) -> str:
    """格式化亏损总结报告（CLI 使用）"""
    if not lessons:
        return "✅ 近期暂无亏损总结记录（或历史亏损已全部生成总结）"

    lines = [f"📋 **亏损复盘总结**（共 {len(lessons)} 条）\n"]
    for l in lessons:
        td = str(l.get("trade_date", ""))
        d_fmt = f"{td[:4]}/{td[4:6]}/{td[6:]}" if len(td) == 8 else td
        lines.append(
            f"  💥 [{d_fmt}] {l['name']}（{l['code']}）"
            f" [{l['sell_type']}] {l['profit_pct']:+.2f}%\n"
            f"     原因: {l['loss_reason']}\n"
            f"     教训: {l['lesson']}\n"
        )
    return "\n".join(lines)


# ── 自动触发（被 sim_trade_db 平仓函数调用）──────────────────────────

def auto_log_if_loss(trade_id: int):
    """
    在平仓后自动检查是否亏损，亏损则自动生成总结。
    由 sim_trade_db.close_trade / mark_stop_loss 调用。

    Args:
        trade_id: sim_trades 记录的 id
    """
    try:
        from sqlalchemy import text
        engine = storage._get_engine()
        row = pd.read_sql(
            text("SELECT * FROM sim_trades WHERE id = :id"),
            engine, params={"id": trade_id}
        )
        if row.empty:
            return
        trade = row.iloc[0].to_dict()
        profit_pct = float(trade.get("profit_pct") or 0)
        if profit_pct < 0:
            lesson = generate_loss_lesson(trade)
            if lesson:
                save_lesson_to_db(lesson)
                append_lesson_to_md(lesson)
    except Exception as e:
        logger.warning(f"auto_log_if_loss 失败（非致命）: {e}")


if __name__ == "__main__":
    # 测试：扫描所有亏损记录并生成总结
    new_lessons = generate_all_lessons(100)
    print(format_lessons_report(new_lessons))

    # 显示最近教训
    recent = get_recent_lessons(5)
    print("\n" + format_lessons_for_llm(recent))
