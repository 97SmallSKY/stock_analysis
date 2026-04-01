"""
飞书消息格式化模块
==================
将模拟交易结果格式化为飞书可读的文本消息（Markdown 样式）

支持以下消息类型：
  1. phase_a_report  — 每日盘后执行报告（自动推送）
  2. phase_b_report  — 次日开盘价回填报告（T+1 买入确认）
  3. weekly_report   — 本周/指定周汇总战报（飞书命令查询）
  4. positions_report — 当前持仓明细
  5. history_report  — 最近 N 笔交易记录
"""
import sys
from pathlib import Path
from datetime import date

import pandas as pd
from loguru import logger

_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(_ROOT))

from skills.stock_sim_robot.sim_trade_db import (
    get_weekly_report,
    get_open_positions,
    get_recent_trades,
    _current_week_tag,
)

# ── 颜色辅助（飞书 Markdown 中无法用颜色，用符号代替）────────────────

def _pnl_emoji(pct: float | None) -> str:
    if pct is None:
        return "—"
    if pct >= 5:
        return "🚀"
    elif pct >= 0:
        return "📈"
    elif pct >= -2:
        return "📉"
    else:
        return "💥"


def _pct_str(pct: float | None, show_emoji: bool = True) -> str:
    if pct is None:
        return "—"
    emoji = f" {_pnl_emoji(pct)}" if show_emoji else ""
    return f"{pct:+.2f}%{emoji}"


def _fen_str(amount: float | None) -> str:
    """格式化金额，None 返回 —"""
    if amount is None:
        return "—"
    sign = "+" if amount >= 0 else ""
    return f"{sign}{amount:.0f}元"


# ── 0. 账户健康摘要（供 Phase A 报告调用）───────────────────────────────

def _format_health_summary() -> str:
    """
    生成账户健康度摘要文本（嵌入到 Phase A 报告中）。
    获取失败时静默返回空字符串，不影响主报告。
    """
    try:
        from skills.stock_sim_robot.sim_trade_db import (
            get_performance_stats, calc_account_health_score, check_circuit_breaker
        )
        stats = get_performance_stats()
        ahs   = calc_account_health_score(stats)
        cb    = check_circuit_breaker(stats=stats)

        if stats.get("total_closed", 0) < 3:
            return "⚪ **【账户健康度】** 数据积累中（不足3笔），AHS 参考意义有限"

        # AHS 图标
        if ahs >= 70:   ahs_icon = "🟢"
        elif ahs >= 50: ahs_icon = "🟡"
        elif ahs >= 30: ahs_icon = "🟠"
        else:           ahs_icon = "🔴"

        # 熔断图标
        if cb["level"] == "ok":      cb_icon = "✅"
        elif cb["level"] == "warning": cb_icon = "⚠️"
        else:                          cb_icon = "🔴"

        wr  = stats.get("recent_win_rate", 0)
        cl  = stats.get("consecutive_losses", 0)
        wpnl = stats.get("week_pnl", 0)
        mpnl = stats.get("month_pnl", 0)

        lines = [
            f"\n{ahs_icon} **【账户健康度】** AHS = {ahs}/100",
            f"  近10笔胜率: {wr:.0f}% | 连续亏损: {cl} 笔 | 本周: {wpnl:+.1f}% | 本月: {mpnl:+.1f}%",
            f"  熔断状态: {cb_icon} {cb['reason'] if cb['reason'] else '正常，今日可开仓'}",
        ]
        return "\n".join(lines)
    except Exception as e:
        logger.debug(f"_format_health_summary 失败（非致命）: {e}")
        return ""


# ── 1. Phase A 报告（每日盘后自动推送）──────────────────────────────

def format_phase_a_report(result: dict) -> str:
    """
    格式化 Phase A（盘后选股+结算）的飞书消息

    Args:
        result: trade_runner.run_phase_a() 的返回值
    """
    today_str = result.get("trade_date", date.today().strftime("%Y%m%d"))
    d_fmt     = f"{today_str[:4]}/{today_str[4:6]}/{today_str[6:]}"
    lines     = [f"📊 **模拟炒股日报** — {d_fmt}\n"]

    # ── 账户健康度摘要 ────────────────────────────────────────────────
    health_summary = _format_health_summary()
    if health_summary:
        lines.append(health_summary)
        lines.append("")

    # ── 结算结果 ──────────────────────────────────────────────────
    settled = result.get("settled_trades", [])
    if settled:
        lines.append("**【今日结算】**")
        for t in settled:
            emoji = _pnl_emoji(t.get("profit_pct"))
            lines.append(
                f"  {emoji} {t['name']}（{t['code']}）"
                f" [{t['sell_type']}] "
                f"卖出 {t.get('sell_price', '—')} "
                f"盈亏 {_pct_str(t.get('profit_pct'))}"
            )
        lines.append("")
    else:
        lines.append("**【今日结算】** 无持仓到期\n")

    # ── 今日新开仓 ────────────────────────────────────────────────
    new_trade = result.get("new_trade")
    skipped   = result.get("skipped", False)

    if new_trade:
        t2_win = new_trade.get("t2_win_rate")
        t2_str = f"{t2_win:.0f}%" if t2_win is not None else "暂无"
        from_llm = new_trade.get("from_llm", False)
        method_tag = "🤖 LLM" if from_llm else "📐 规则"

        lines.append("**【今日选股】**")
        lines.append(f"  **{new_trade['name']}（{new_trade['code']}）** {method_tag}")
        lines.append(f"  综合评分: {new_trade['signal_score']}/100 | T+2历史胜率: {t2_str}")
        lines.append(f"  T 日参考价: {new_trade.get('close_price', '—')}元")
        lines.append(f"  T+2 挂牌卖出价: **{new_trade.get('list_price', '—')}** "
                     f"(+{new_trade.get('list_price_pct', '—')}%)")
        lines.append(f"  止损线: -5%")
        lines.append(f"  理由: {new_trade.get('reason', '—')}")
        lines.append("")
        lines.append("⏰ 明日（T+1）开盘集合竞价**买入**，后日（T+2）挂单卖出")
    elif skipped:
        reason = result.get("skip_reason", "")
        lines.append(f"**【今日选股】** ⏭️ 跳过 — {reason}")
    else:
        lines.append("**【今日选股】** 未知状态")

    lines.append("")
    lines.append("💡 发送「模拟炒股 持仓」查看当前未平仓记录")
    lines.append("💡 发送「模拟炒股 本周战报」查看本周汇总")
    return "\n".join(lines)


# ── 2. Phase B 报告（T+1 开盘买入确认）──────────────────────────────

def format_phase_b_report(result: dict) -> str:
    """格式化 Phase B（T+1 买入价回填）的飞书消息"""
    trade_date = result.get("trade_date", "")
    d_fmt = f"{trade_date[:4]}/{trade_date[4:6]}/{trade_date[6:]}" if len(trade_date) == 8 else trade_date

    lines = [f"🛒 **模拟买入确认** — T 日 {d_fmt}\n"]

    filled = result.get("filled", [])
    errors = result.get("errors", [])

    if filled:
        lines.append("**【买入成交】**")
        for f in filled:
            lines.append(
                f"  ✅ **{f['name']}（{f['code']}）**\n"
                f"     买入价: {f['buy_price']} | 股数: {f['buy_shares']} 股\n"
                f"     挂牌卖出价已更新: **{f['new_list_price']}**"
            )
    else:
        lines.append("**【买入确认】** 暂无待回填记录")

    if errors:
        lines.append("\n⚠️ 以下股票 T+1 开盘价获取失败，请手动确认：")
        for e in errors:
            lines.append(f"  ❌ {e['name']}（{e['code']}）: {e.get('error', '未知错误')}")

    return "\n".join(lines)


# ── 3. 周战报 ─────────────────────────────────────────────────────────

def format_weekly_report(week_tag: str | None = None) -> str:
    """
    格式化周战报消息

    Args:
        week_tag: 如 '2026-W13'，None 则取本周
    """
    if week_tag is None:
        week_tag = _current_week_tag()

    data    = get_weekly_report(week_tag)
    summary = data.get("summary", {})
    trades  = data.get("trades",  [])

    lines   = [f"🏆 **模拟炒股周战报** — {week_tag}\n"]

    if not summary:
        lines.append("本周暂无已平仓记录（可能还在持仓中）")
        # 显示未平仓
        open_pos = get_open_positions()
        week_pos = [p for p in open_pos.to_dict("records")
                    if str(p.get("week_tag", "")) == week_tag]
        if week_pos:
            lines.append(f"\n📌 **本周持仓中** ({len(week_pos)} 笔):")
            for p in week_pos:
                lines.append(f"  · {p['name']}（{p['code']}）"
                              f" 挂牌价={p.get('list_price', '—')}")
        return "\n".join(lines)

    total  = int(summary.get("total_trades", 0))
    win    = int(summary.get("win_trades", 0))
    lose   = int(summary.get("lose_trades", 0))
    wr     = float(summary.get("win_rate", 0) or 0)
    profit = float(summary.get("total_profit", 0) or 0)
    avg    = float(summary.get("avg_profit_pct", 0) or 0)
    mx_p   = float(summary.get("max_profit", 0) or 0)
    mx_l   = float(summary.get("max_loss", 0) or 0)

    wr_emoji = "🟢" if wr >= 60 else ("🟡" if wr >= 50 else "🔴")
    pnl_emoji = "📈" if profit >= 0 else "📉"

    lines += [
        "**【本周汇总】**",
        f"  总交易: {total} 笔 | 盈利: {win} 笔 | 亏损: {lose} 笔",
        f"  胜率: {wr_emoji} **{wr:.1f}%**",
        f"  总盈亏: {pnl_emoji} **{_fen_str(profit)}**",
        f"  平均盈亏: {_pct_str(avg)}",
        f"  最大单笔盈利: {_fen_str(mx_p)}",
        f"  最大单笔亏损: {_fen_str(mx_l)}",
        "",
    ]

    if trades:
        lines.append("**【交易明细】**")
        for t in trades:
            status = t.get("status", "open")
            if status == "closed":
                sell_type = t.get("sell_type", "—")
                pct       = t.get("profit_pct")
                profit_   = t.get("profit")
                emoji     = _pnl_emoji(pct)
                lines.append(
                    f"  {emoji} {t['name']}（{t['code']}）"
                    f" [{sell_type}] "
                    f"{_pct_str(pct)} / {_fen_str(profit_)}"
                )
            else:
                lines.append(
                    f"  ⏳ {t['name']}（{t['code']}）"
                    f" 挂牌={t.get('list_price', '—')} [持仓中]"
                )

    return "\n".join(lines)


# ── 4. 持仓报告 ────────────────────────────────────────────────────────

def format_positions_report() -> str:
    """格式化当前持仓报告"""
    open_pos = get_open_positions()
    lines    = ["📌 **当前模拟持仓**\n"]

    if open_pos.empty:
        lines.append("当前无持仓")
        return "\n".join(lines)

    for _, pos in open_pos.iterrows():
        buy_price  = pos.get("buy_price")
        list_price = pos.get("list_price")
        td         = str(pos.get("trade_date", ""))
        d_fmt      = f"{td[:4]}/{td[4:6]}/{td[6:]}" if len(td) == 8 else td

        if pd.notna(buy_price) and float(buy_price or 0) > 0:
            bp_str = f"{float(buy_price):.2f}"
            stop   = round(float(buy_price) * 0.95, 2)
            stop_str = f"止损: {stop}"
        else:
            bp_str   = "等待T+1开盘"
            stop_str = "—"

        lp_str = f"{float(list_price):.2f}" if pd.notna(list_price) and float(list_price or 0) > 0 else "—"

        lines.append(
            f"  · **{pos['name']}（{pos['code']}）**\n"
            f"    T 日: {d_fmt} | 周: {pos.get('week_tag', '—')}\n"
            f"    买入价: {bp_str} | 挂牌卖出价: {lp_str} | {stop_str}\n"
            f"    评分: {pos.get('signal_score', '—')}/100"
        )

    return "\n".join(lines)


# ── 5. 历史交易记录 ────────────────────────────────────────────────────

def format_history_report(n: int = 10) -> str:
    """格式化最近 N 笔交易历史"""
    df    = get_recent_trades(n)
    lines = [f"📜 **最近 {n} 笔交易记录**\n"]

    if df.empty:
        lines.append("暂无交易记录")
        return "\n".join(lines)

    for _, row in df.iterrows():
        status = row.get("status", "open")
        td     = str(row.get("trade_date", ""))
        d_fmt  = f"{td[:4]}/{td[4:6]}/{td[6:]}" if len(td) == 8 else td

        if status == "closed":
            pct = row.get("profit_pct")
            emoji = _pnl_emoji(float(pct) if pd.notna(pct) else None)
            sell_type = row.get("sell_type", "—")
            lines.append(
                f"  {emoji} [{d_fmt}] {row['name']}（{row['code']}）"
                f" [{sell_type}]"
                f" {_pct_str(float(pct) if pd.notna(pct) else None)}"
                f" / {_fen_str(float(row['profit']) if pd.notna(row.get('profit')) else None)}"
            )
        else:
            lp = row.get("list_price")
            lp_str = f"挂牌={float(lp):.2f}" if pd.notna(lp) and float(lp or 0) > 0 else "挂牌=—"
            lines.append(
                f"  ⏳ [{d_fmt}] {row['name']}（{row['code']}）"
                f" [持仓中] {lp_str}"
            )

    return "\n".join(lines)


# ── 6. 快捷工厂函数（供 sim_robot_run.py 调用）─────────────────────

def build_message(msg_type: str, **kwargs) -> str:
    """
    消息路由

    Args:
        msg_type: 'phase_a' / 'phase_b' / 'weekly' / 'positions' / 'history'
        **kwargs: 额外参数（如 result=..., week_tag=..., n=...）
    """
    if msg_type == "phase_a":
        return format_phase_a_report(kwargs.get("result", {}))
    elif msg_type == "phase_b":
        return format_phase_b_report(kwargs.get("result", {}))
    elif msg_type == "weekly":
        return format_weekly_report(kwargs.get("week_tag"))
    elif msg_type == "positions":
        return format_positions_report()
    elif msg_type == "history":
        return format_history_report(kwargs.get("n", 10))
    else:
        return f"未知消息类型: {msg_type}"


if __name__ == "__main__":
    # 测试各种消息格式
    print("=== 持仓报告 ===")
    print(format_positions_report())
    print("\n=== 本周战报 ===")
    print(format_weekly_report())
    print("\n=== 最近10笔 ===")
    print(format_history_report(5))
