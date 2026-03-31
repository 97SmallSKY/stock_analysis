#!/usr/bin/env python3
"""
修复现有 sim_trades 记录的 4 维度选股理由
运行方式：
  cd ~/stock_analysis
  python scripts/fix_reasons.py

逻辑：
  1. 找出 reason_tech IS NULL 的记录
  2. 用 trade_date 重新拉 fetch_signal（读缓存，不重新爬网）
  3. 匹配 code，重新构建 reason_tech/fund/history/seat
  4. UPDATE 回 DB
"""

import sys
import os
from pathlib import Path

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))
os.chdir(_ROOT)

from loguru import logger
from sqlalchemy import text
from utils.storage import storage
from skills.stock_sim_robot.fetch_signal import get_mainboard_signals


def _build_reasons(chosen: dict) -> dict:
    """从 signals 条目构建 4 维度理由"""
    t2_str  = f"{chosen['t2_win_rate']:.0f}%" if chosen.get("t2_win_rate") is not None else "暂无"
    t5_str  = f"{chosen.get('t5_win_rate'):.0f}%" if chosen.get("t5_win_rate") is not None else "暂无"
    g20_str = f"{chosen['gain20']:+.1f}%" if chosen.get("gain20") is not None else "N/A"
    rsi_str = f"{chosen['rsi']:.1f}" if chosen.get("rsi") is not None else "N/A"
    famous  = chosen.get("famous_buy", [])
    seats   = "、".join(famous) if famous else ("有机构买方" if chosen.get("jigou_buy") else "无知名席位")

    reason_tech = (
        f"技术面（{chosen['tech_score']}分）：RSI={rsi_str}，"
        f"近20日涨幅={g20_str}，MACD得分={chosen.get('macd_score', 0)}，"
        f"均线MA5={chosen.get('ma5', 'N/A')}/MA10={chosen.get('ma10', 'N/A')}。"
    )
    reason_fund = (
        f"资金面（{chosen['fund_score']}分）：龙虎榜排名第{chosen['rank']}，"
        f"净买入={chosen['net_buy_yi']:.2f}亿，知名席位={seats}。"
    )
    reason_history = (
        f"历史胜率（{chosen['hist_score']}分）：T+2历史胜率={t2_str}，"
        f"T+5历史胜率={t5_str}，历史样本={chosen.get('hist_count', 0)}次。"
    )
    reason_seat = (
        f"席位分析：知名席位={seats}，"
        f"机构买方={'有' if chosen.get('jigou_buy') else '无'}，"
        f"席位标签={chosen.get('seat_tags', [])}。"
    )
    return {
        "reason_tech":     reason_tech,
        "reason_fund":     reason_fund,
        "reason_history":  reason_history,
        "reason_seat":     reason_seat,
    }


def main():
    engine = storage._get_engine()

    # 找出 reason_tech 为空的记录
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT id, trade_date, code, name FROM sim_trades "
            "WHERE reason_tech IS NULL OR reason_tech = '' "
            "ORDER BY created_at"
        )).fetchall()

    if not rows:
        logger.info("✅ 所有记录已有 reason_tech，无需修复")
        return

    logger.info(f"找到 {len(rows)} 条需要修复的记录")

    # 按 trade_date 分组，避免重复拉信号
    date_signals: dict = {}

    for row in rows:
        trade_id   = row[0]
        trade_date = str(row[1])
        code       = str(row[2])
        name       = str(row[3])

        logger.info(f"  处理: {name}（{code}）trade_date={trade_date}")

        # 拉取当日信号（复用缓存）
        if trade_date not in date_signals:
            logger.info(f"    拉取 {trade_date} 信号（从缓存读取）...")
            sigs = get_mainboard_signals(trade_date)
            date_signals[trade_date] = {s["code"]: s for s in sigs}
            logger.info(f"    获取到 {len(sigs)} 只候选")

        sig_map = date_signals[trade_date]
        chosen  = sig_map.get(code)

        if chosen is None:
            logger.warning(f"    {code} 在 {trade_date} 信号中未找到，跳过")
            continue

        reasons = _build_reasons(chosen)
        logger.info(f"    reason_tech: {reasons['reason_tech'][:80]}...")

        # 更新 DB
        with engine.begin() as conn:
            conn.execute(text("""
                UPDATE sim_trades
                SET reason_tech     = :reason_tech,
                    reason_fund     = :reason_fund,
                    reason_history  = :reason_history,
                    reason_seat     = :reason_seat,
                    updated_at      = CURRENT_TIMESTAMP
                WHERE id = :id
            """), {**reasons, "id": trade_id})
        logger.info(f"  ✅ id={trade_id} 理由已更新")

    logger.info("🎉 全部修复完成！")


if __name__ == "__main__":
    main()
