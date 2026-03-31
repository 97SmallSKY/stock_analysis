#!/usr/bin/env python3
"""
模拟炒股 Robot 入口脚本
========================
由 OpenClaw cron job 或飞书命令触发。
脚本直接将结果输出到 stdout，由 OpenClaw agent 读取并发送到飞书。

用法：
  # Phase A（盘后，每个交易日 18:45）
  python scripts/sim_robot_run.py phase_a

  # Phase B（次日开盘后，每个交易日 09:35）
  python scripts/sim_robot_run.py phase_b

  # 飞书查询命令
  python scripts/sim_robot_run.py query weekly       # 本周战报
  python scripts/sim_robot_run.py query positions    # 当前持仓
  python scripts/sim_robot_run.py query history      # 最近10笔
  python scripts/sim_robot_run.py query history 5    # 最近5笔
  python scripts/sim_robot_run.py query weekly 2026-W12  # 指定周战报

  # 初始化（首次运行建表）
  python scripts/sim_robot_run.py init

  # 指定日期测试
  python scripts/sim_robot_run.py phase_a 20260326
"""
import sys
import os
import argparse
import traceback
from pathlib import Path
from datetime import date

# 将 stock_analysis 根目录加入 path
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from loguru import logger
from dotenv import load_dotenv

load_dotenv(dotenv_path=ROOT / ".env")

# 初始化日志
logger.add(
    ROOT / "logs/sim_robot.log",
    rotation="7 days",
    retention="30 days",
    encoding="utf-8",
    level="INFO",
    format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {message}",
)


def cmd_init():
    """初始化：建表"""
    from skills.stock_sim_robot.sim_trade_db import create_tables
    ok = create_tables()
    if ok:
        print("✅ sim_trades / sim_summary 表初始化成功")
    else:
        print("❌ 建表失败，请检查数据库连接配置")
        sys.exit(1)


def cmd_phase_a(trade_date: str | None = None):
    """Phase A：盘后结算 + 今日选股"""
    from skills.stock_sim_robot.trade_runner import run_phase_a
    from skills.stock_sim_robot.summary_report import format_phase_a_report

    logger.info(f"=== Phase A 开始 (trade_date={trade_date or '今日'}) ===")
    result = run_phase_a(trade_date)
    msg    = format_phase_a_report(result)
    print(msg)
    logger.info("=== Phase A 完成 ===")
    return msg


def cmd_phase_b(trade_date: str | None = None):
    """Phase B：回填 T+1 买入价"""
    from skills.stock_sim_robot.trade_runner import run_phase_b
    from skills.stock_sim_robot.summary_report import format_phase_b_report

    logger.info(f"=== Phase B 开始 (trade_date={trade_date or '昨日'}) ===")
    result = run_phase_b(trade_date)
    msg    = format_phase_b_report(result)
    print(msg)
    logger.info("=== Phase B 完成 ===")
    return msg


def cmd_query(query_type: str, extra: str | None = None):
    """查询命令：供飞书菜单触发"""
    from skills.stock_sim_robot.summary_report import build_message

    if query_type == "weekly":
        week_tag = extra if extra and extra.startswith("20") else None
        msg = build_message("weekly", week_tag=week_tag)
    elif query_type == "positions":
        msg = build_message("positions")
    elif query_type == "history":
        n = int(extra) if extra and extra.isdigit() else 10
        msg = build_message("history", n=n)
    else:
        msg = f"未知查询类型: {query_type}\n可用: weekly / positions / history"

    print(msg)
    return msg


def cmd_signals(trade_date: str | None = None):
    """查看今日信号列表（不开仓，仅展示）"""
    from skills.stock_sim_robot.fetch_signal import get_mainboard_signals, format_signal_for_llm

    if trade_date is None:
        trade_date = date.today().strftime("%Y%m%d")

    logger.info(f"获取信号 ({trade_date})...")
    signals = get_mainboard_signals(trade_date)
    msg     = format_signal_for_llm(signals)
    print(msg)
    return msg


def cmd_monitor():
    """盘中监控：检查分批卖出/止损条件"""
    from skills.stock_sim_robot.monitor_intraday import run_monitor, format_monitor_report

    logger.info("=== 盘中监控 开始 ===")
    events = run_monitor()
    msg = format_monitor_report(events)
    if msg:
        print(msg)
    else:
        print("📈 **盘中监控** — 当前无触发事件")
    logger.info("=== 盘中监控 完成 ===")
    return msg


def cmd_loss_summary(n: int = 50):
    """
    生成/补全亏损总结（LLM 用）
    扫描未生成总结的亏损记录，写入 DB 和 docs/loss_lessons.md
    """
    from skills.stock_sim_robot.trade_log import generate_all_lessons, format_lessons_report

    logger.info(f"=== 亏损总结生成 开始（最近 {n} 笔）===")
    new_lessons = generate_all_lessons(n)
    msg = format_lessons_report(new_lessons)
    print(msg)
    logger.info(f"=== 亏损总结生成 完成（新增 {len(new_lessons)} 条）===")
    return msg


# ── 主入口 ────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="模拟炒股 Robot 入口脚本",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument(
        "command",
        choices=["phase_a", "phase_b", "query", "init", "signals", "monitor", "loss_summary"],
        help=(
            "phase_a      — 盘后：结算持仓 + 今日选股\n"
            "phase_b      — 次日开盘后：回填买入价 + 初始化分批卖出计划\n"
            "monitor      — 盘中监控：检查分批卖出/止损条件（每3分钟触发）\n"
            "query        — 飞书查询（需要第二个参数）\n"
            "init         — 初始化建表\n"
            "signals      — 仅查看今日信号（不开仓）\n"
            "loss_summary — 生成/补全亏损总结（写入 DB 和 docs/loss_lessons.md）"
        ),
    )
    parser.add_argument(
        "arg1",
        nargs="?",
        default=None,
        help=(
            "phase_a / phase_b: 交易日期（YYYYMMDD，可选）\n"
            "query: 子命令（weekly / positions / history）\n"
            "signals: 交易日期（YYYYMMDD，可选）"
        ),
    )
    parser.add_argument(
        "arg2",
        nargs="?",
        default=None,
        help="额外参数（如 query history 10 中的 10）",
    )

    args = parser.parse_args()

    try:
        if args.command == "init":
            cmd_init()

        elif args.command == "phase_a":
            cmd_phase_a(args.arg1)

        elif args.command == "phase_b":
            cmd_phase_b(args.arg1)

        elif args.command == "query":
            if not args.arg1:
                print("❌ query 命令需要子命令: weekly / positions / history")
                sys.exit(1)
            cmd_query(args.arg1, args.arg2)

        elif args.command == "signals":
            cmd_signals(args.arg1)

        elif args.command == "monitor":
            cmd_monitor()

        elif args.command == "loss_summary":
            n = int(args.arg1) if args.arg1 and args.arg1.isdigit() else 50
            cmd_loss_summary(n)

    except KeyboardInterrupt:
        logger.info("用户中断")
        sys.exit(0)
    except Exception as e:
        logger.error(f"执行失败: {e}\n{traceback.format_exc()}")
        print(f"❌ 执行出错: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
