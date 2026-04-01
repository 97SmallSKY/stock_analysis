"""
盘中实时监控模块（后台静默模式）v2
================
由系统 crontab 每 3 分钟静默触发（09:35 ~ 15:05），不经过 OpenClaw agent：
  1. 实时获取持仓股票价格（新浪行情，自动处理涨停/跌停场景）
  2. 检查是否触及分批卖出档位（+3% / +5% / +8%）
  3. 检查是否触及止损线（-5%）
  4. 每次检查都写 sim_price_log（价格快照，用于页面画轨迹图）
  5. 重要操作（止损/卖出/行情失败）写 sim_audit_log（可页面查询）
  6. 无触发：完全静默，仅写日志 + price_log
  7. 有触发：记录 DB + 调用 `openclaw message send` 直接推送飞书

行情获取改进（v2 新增）：
  - 使用 fetch_realtime_quote() 代替 fetch_sina_quote()
  - 自动计算 effective_price（处理涨停/跌停封板时 last 字段失真问题）
  - 行情获取失败时写 audit_log 记录，降级跳过该持仓

注意事项：
  - crontab 配置为 `*/3 9-15 * * 1-5`，即 9:00 开始每 3 分钟触发
  - 但实际监控窗口是 9:30~15:05（交易时间），9:00~9:29 会提前退出
  - 为减少无效日志和 DB 连接，脚本启动时先做时间窗口判断
"""
import sys
import os
import subprocess
from pathlib import Path
from datetime import date, datetime

# ── 时间窗口预检（最先执行，避免非交易时间进行不必要的模块导入和日志）──
# crontab 从 9:00 开始，但 A 股开盘是 9:30，Phase B 回填在 9:35
# 9:00~9:29 的执行无实质意义，提前判断直接退出（节省 DB 连接和日志噪音）
def _is_trading_hours_now() -> bool:
    """判断当前是否在监控时间窗口（9:30~15:05，且为工作日）"""
    _now = datetime.now()
    if _now.weekday() >= 5:
        return False
    _hm = _now.hour * 100 + _now.minute
    return 930 <= _hm <= 1505


# 仅在作为脚本直接运行时（crontab 场景）提前退出
if __name__ == "__main__" or os.environ.get("MONITOR_FORCE_RUN") != "1":
    # 在被 crontab 直接执行时，非交易时间静默退出
    if not _is_trading_hours_now():
        # 只在不是被导入的情况下退出
        if __name__ == "__main__":
            import argparse
            _p = argparse.ArgumentParser(add_help=False)
            _p.add_argument("--dry-run", action="store_true")
            _args, _ = _p.parse_known_args()
            # dry-run 模式不受时间限制
            if not _args.dry_run:
                sys.exit(0)

from loguru import logger

_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(_ROOT))
os.chdir(_ROOT)

from dotenv import load_dotenv
load_dotenv(dotenv_path=_ROOT / ".env")

try:
    from skills.stock_sim_robot.sim_trade_db import (
        get_active_batch_positions,
        mark_batch_sold,
        mark_stop_loss,
        refresh_summary,
        BATCH_SELL_TIERS,
        get_live_tiers,
        load_strategy,
        log_price_tick,
        write_audit_log,
        AUDIT_MONITOR_CHECK,
        AUDIT_MONITOR_SELL,
        AUDIT_MONITOR_STOP,
        AUDIT_QUOTE_FAIL,
    )
except Exception as _e:
    logger.error(f"DB 模块导入失败: {_e}")
    sys.exit(1)

try:
    from utils.retry import fetch_realtime_quote
except Exception as _e:
    logger.error(f"行情模块导入失败: {_e}")
    sys.exit(1)

# 飞书推送配置（从 OpenClaw cron jobs.json 取用户 ID）
_FEISHU_TARGET = os.getenv("FEISHU_TARGET", "ou_36a79ea49476c9b64d2361a9ab3bf4ca")
_OPENCLAW_BIN  = os.getenv("OPENCLAW_BIN", "openclaw")


def _send_feishu(message: str):
    """
    调用 openclaw message send 直接推送飞书消息
    （不通过 agent，静默后台执行）
    """
    try:
        result = subprocess.run(
            [_OPENCLAW_BIN, "message", "send",
             "--channel", "feishu",
             "--target",  _FEISHU_TARGET,
             "--message", message],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            logger.info(f"飞书推送成功: {message[:50]}...")
        else:
            logger.error(f"飞书推送失败: {result.stderr[:200]}")
    except subprocess.TimeoutExpired:
        logger.error("飞书推送超时（>30s）")
    except Exception as e:
        logger.error(f"飞书推送异常: {e}")


def check_and_trigger(pos_row, strategy: dict | None = None, dry_run: bool = False) -> list[dict]:
    """
    检查单个持仓是否触发分批卖出或止损条件（v2：使用 effective_price）

    改进：
      - 使用 fetch_realtime_quote() 获取包含 effective_price 的行情
      - effective_price 正确处理涨停（bid1）/ 跌停（ask1）/ 正常（last）场景
      - 每次检查都写 sim_price_log（无论是否触发）
      - 重要操作写 sim_audit_log

    Args:
        pos_row:  DataFrame 行（sim_trades 一条记录）
        strategy: 当前策略配置 dict（由 run_monitor 传入，避免重复查 DB）
                  None 时从 DB 实时读取（兼容单独调用场景）
        dry_run:  True 时只检测，不执行 DB 写入和飞书推送

    Returns:
        触发事件列表，每个 dict 包含 tier/type/price/shares/profit_pct 等
    """
    if strategy is None:
        strategy = load_strategy()

    # 从策略中获取三档触发涨幅
    tiers = [
        {"tier": 1, "pct": strategy["sell1_pct"], "ratio": strategy["sell1_ratio"]},
        {"tier": 2, "pct": strategy["sell2_pct"], "ratio": strategy["sell2_ratio"]},
        {"tier": 3, "pct": strategy["sell3_pct"], "ratio": 1.0},
    ]

    trade_id   = int(pos_row["id"])
    code       = str(pos_row["code"])
    name       = str(pos_row["name"])
    today_str  = date.today().strftime("%Y%m%d")
    trade_date = str(pos_row.get("trade_date", ""))

    # events 必须在所有 return 路径之前初始化，防止 UnboundLocalError
    events = []

    # ── buy_price / buy_shares 空值防御：Phase B 未回填时跳过监控 ────────
    # Phase B（09:35）回填前 buy_price=None/0，此时无法计算涨跌幅，直接跳过
    _raw_bp = pos_row.get("buy_price")
    _raw_bs = pos_row.get("buy_shares")
    if _raw_bp is None or _raw_bp == 0 or (isinstance(_raw_bp, float) and _raw_bp != _raw_bp):
        logger.info(
            f"  ⏳ {name}({code}) buy_price 尚未回填（等待 Phase B），跳过本次监控"
        )
        return events
    buy_price  = float(_raw_bp)
    buy_shares = int(_raw_bs) if (_raw_bs is not None and _raw_bs != 0) else 0
    if buy_price <= 0:
        logger.info(f"  ⏳ {name}({code}) buy_price={buy_price} 无效，跳过本次监控")
        return events

    # ── A 股 T+1 合规保护：买入当天（T+1 日）不允许卖出 ─────────────────
    # 本系统语义：trade_date 是信号日（T日），T+1 日才是真正买入日
    # 最早可卖出日 = T+2 = trade_date 后的第 2 个交易日
    # 判断：今天必须 >= T+2 日，即 trade_date 必须 < 昨日（至少已过 2 个交易日）
    # 简单判断：_trading_days_since(trade_date) >= 2
    try:
        from skills.stock_sim_robot.trade_runner import _trading_days_since, _get_nth_trading_day
        days_since_t = _trading_days_since(trade_date)
        t2_date      = _get_nth_trading_day(trade_date, 2)
        if today_str < t2_date:
            logger.info(
                f"  ⏳ [T+1合规] {name}({code}) 买入日={_get_nth_trading_day(trade_date, 1)}，"
                f"最早可卖={t2_date}，今天={today_str}，跳过（A股T+1制度）"
            )
            return events  # 买入当天或买入前不允许卖出（events=[]，已初始化）
    except Exception as _compliance_err:
        logger.warning(f"  T+1合规检查异常（非致命，继续执行）: {_compliance_err}")

    # 目标价快照（用于 price_log）
    sell1_price = float(pos_row.get("sell1_price") or 0)
    sell3_price = float(pos_row.get("sell3_price") or 0)
    stop_loss_pct = float(pos_row.get("stop_loss_pct") or strategy["stop_loss_pct"])
    stop_price = round(buy_price * (1 + stop_loss_pct / 100), 3)

    events = []

    # ── 获取实时行情（v2：使用 fetch_realtime_quote，含 effective_price）────
    quote = None
    try:
        quote = fetch_realtime_quote(code, timeout=8)
    except Exception as e:
        logger.warning(f"  {name}({code}) fetch_realtime_quote 异常: {e}")

    if quote is None:
        logger.warning(f"  {name}({code}) 行情获取失败（新浪/直连均失败），跳过本次检查")
        if not dry_run:
            write_audit_log(
                event_type=AUDIT_QUOTE_FAIL,
                summary=f"行情获取失败: {name}({code})，跳过本次检查",
                trade_id=trade_id,
                code=code,
                name=name,
                detail={"buy_price": buy_price, "error": "fetch_realtime_quote returned None"},
                result="failed",
            )
        return events

    current_price = quote["effective_price"]
    raw_last      = quote.get("last", current_price)
    bid1          = quote.get("bid1", 0.0)
    ask1          = quote.get("ask1", 0.0)
    high          = quote.get("high", current_price)
    low           = quote.get("low", current_price)

    if current_price <= 0:
        logger.warning(f"  {name}({code}) effective_price={current_price} 无效，跳过")
        return events

    change_pct = (current_price / buy_price - 1) * 100

    # 判断是否涨停/跌停（用于日志标注）
    is_limit_up   = (ask1 == 0.0 and bid1 > 0.0 and bid1 > raw_last)
    is_limit_down = (bid1 == 0.0 and ask1 > 0.0 and ask1 < raw_last)
    price_note = " [涨停]" if is_limit_up else (" [跌停]" if is_limit_down else "")

    logger.info(
        f"  {name}({code}) effective={current_price:.3f}{price_note} "
        f"raw_last={raw_last:.3f} bid1={bid1} ask1={ask1} "
        f"买入={buy_price:.3f} 涨幅={change_pct:+.2f}%"
    )

    # ── 写 price_log（无论是否触发，都记录价格快照）────────────────
    triggered_event = None  # 先暂存，触发后填入 price_log

    # ── 检查止损（优先级最高）──────────────────────────────────────
    stop_loss_done = bool(pos_row.get("stop_loss_done") or False)

    if not stop_loss_done and change_pct <= stop_loss_pct:
        logger.info(
            f"  ⚠️ {name} 触发止损！"
            f"涨幅={change_pct:+.2f}% <= 止损线={stop_loss_pct}%"
            f"（买入={buy_price:.3f}，当前={current_price:.3f}）"
        )
        triggered_event = "止损"

        if not dry_run:
            mark_stop_loss(trade_id, current_price, today_str)
            refresh_summary()
            write_audit_log(
                event_type=AUDIT_MONITOR_STOP,
                summary=f"⚠️ 止损触发: {name}({code}) 当前价={current_price:.3f} 涨幅={change_pct:+.2f}%",
                trade_id=trade_id,
                code=code,
                name=name,
                detail={
                    "price":          current_price,
                    "price_raw":      raw_last,
                    "buy_price":      buy_price,
                    "change_pct":     round(change_pct, 3),
                    "stop_loss_pct":  stop_loss_pct,
                    "bid1": bid1, "ask1": ask1,
                    "is_limit_down":  is_limit_down,
                    "shares":         buy_shares,
                },
            )
            # 写入交易流水
            try:
                from skills.stock_sim_robot.sim_trade_db import log_trade_event
                profit_val  = round((current_price - buy_price) * buy_shares, 2)
                profit_pct_ = round((current_price / buy_price - 1) * 100, 4)
                log_trade_event(
                    trade_id=trade_id,
                    code=code,
                    name=name,
                    event_type="STOP_LOSS",
                    price=current_price,
                    shares=buy_shares,
                    event_date=today_str,
                    tier=0,
                    profit=profit_val,
                    profit_pct=profit_pct_,
                    note=f"止损触发 涨幅={change_pct:+.2f}% 止损线={stop_loss_pct}%",
                )
            except Exception as _le:
                logger.debug(f"  止损流水记录非致命: {_le}")

        events.append({
            "type":       "止损",
            "tier":       0,
            "code":       code,
            "name":       name,
            "price":      current_price,
            "shares":     buy_shares,
            "change_pct": change_pct,
            "profit_pct": change_pct,
            "all_done":   True,
        })

    elif not stop_loss_done:
        # ── 检查三档分批卖出 ───────────────────────────────────────────
        for tier_cfg in tiers:
            tier       = tier_cfg["tier"]
            done_col   = f"sell{tier}_done"
            price_col  = f"sell{tier}_price"

            already_done = bool(pos_row.get(done_col) or False)
            target_price = float(pos_row.get(price_col) or 0)

            if already_done or target_price <= 0:
                continue

            if current_price >= target_price:
                logger.info(
                    f"  🎯 {name} 第{tier}档触发！"
                    f"当前价={current_price:.3f}{price_note} >= 目标价={target_price:.3f} "
                    f"（涨幅={change_pct:+.2f}%）"
                )
                triggered_event = f"第{tier}档"

                if not dry_run:
                    info = mark_batch_sold(trade_id, tier, today_str)
                    if info:
                        write_audit_log(
                            event_type=AUDIT_MONITOR_SELL,
                            summary=(
                                f"🎯 第{tier}档卖出: {name}({code}) "
                                f"@{current_price:.3f}{price_note} 涨幅={change_pct:+.2f}% "
                                f"盈亏={info['profit_pct']:+.2f}%"
                            ),
                            trade_id=trade_id,
                            code=code,
                            name=name,
                            detail={
                                "tier":         tier,
                                "price":        current_price,
                                "price_raw":    raw_last,
                                "target_price": target_price,
                                "buy_price":    buy_price,
                                "change_pct":   round(change_pct, 3),
                                "profit_pct":   info["profit_pct"],
                                "shares":       info["shares"],
                                "sold_amount":  info["sold_amount"],
                                "all_done":     info["all_done"],
                                "bid1": bid1, "ask1": ask1,
                                "is_limit_up":  is_limit_up,
                            },
                        )
                        # 写入交易流水
                        try:
                            from skills.stock_sim_robot.sim_trade_db import log_trade_event
                            profit_val_  = round((current_price - buy_price) * info["shares"], 2)
                            log_trade_event(
                                trade_id=trade_id,
                                code=code,
                                name=name,
                                event_type=f"SELL_T{tier}",
                                price=current_price,
                                shares=info["shares"],
                                event_date=today_str,
                                tier=tier,
                                profit=profit_val_,
                                profit_pct=round(info["profit_pct"] / 100, 4),
                                note=f"第{tier}档卖出触发 涨幅={change_pct:+.2f}% 目标价={target_price:.3f}",
                            )
                        except Exception as _le:
                            logger.debug(f"  卖出流水记录非致命: {_le}")
                        events.append({
                            "type":         f"分批卖出第{tier}档",
                            "tier":         tier,
                            "tier_pct":     tier_cfg["pct"],
                            "code":         code,
                            "name":         name,
                            "price":        current_price,
                            "target_price": target_price,
                            "shares":       info["shares"],
                            "change_pct":   change_pct,
                            "profit_pct":   info["profit_pct"],
                            "sold_amount":  info["sold_amount"],
                            "all_done":     info["all_done"],
                        })
                        if info["all_done"]:
                            refresh_summary()
                            break
                else:
                    # dry-run 模式：只记录触发但不写 DB
                    events.append({
                        "type":         f"[dry-run] 分批卖出第{tier}档",
                        "tier":         tier,
                        "code":         code,
                        "name":         name,
                        "price":        current_price,
                        "target_price": target_price,
                        "change_pct":   change_pct,
                        "profit_pct":   change_pct,
                        "all_done":     False,
                    })

    # ── 写 price_log（事后补充触发事件字段）────────────────────────
    if not dry_run:
        try:
            log_price_tick(
                trade_id=trade_id,
                code=code,
                price=current_price,
                price_raw=raw_last,
                high=high,
                low=low,
                bid1=bid1,
                ask1=ask1,
                change_pct=round(change_pct, 4),
                sell1_price=sell1_price if sell1_price > 0 else None,
                sell3_price=sell3_price if sell3_price > 0 else None,
                stop_price=stop_price,
                event=triggered_event,
            )
        except Exception as e:
            logger.warning(f"  price_log 写入失败 trade_id={trade_id}: {e}")

    return events


def run_monitor(dry_run: bool = False) -> list[dict]:
    """
    主监控函数：检查所有活跃持仓，返回所有触发事件

    每次运行前从 DB 实时读取策略（支持前端修改后即时生效）
    无触发时：返回空列表，仅写 DEBUG 日志 + price_log
    有触发时：返回事件列表，并自动调用 _send_feishu 推送

    注：crontab 脚本入口已在文件顶部做了时间预检，此处保留兼容性判断
    以支持被其他模块 import 直接调用的场景。
    """
    if not _is_trading_hours_now():
        return []  # 非交易时间静默（周末 / 9:30 前 / 15:05 后）

    now = datetime.now()

    try:
        positions = get_active_batch_positions()
    except Exception as e:
        logger.error(f"获取持仓失败: {e}")
        return []

    if positions.empty:
        return []  # 无持仓静默

    # ── 实时加载策略（前端修改后下一个3分钟周期即生效）──────────────
    strategy = load_strategy()
    logger.info(
        f"盘中监控: {now.strftime('%H:%M')}, {len(positions)} 个持仓 "
        f"| 止损={strategy['stop_loss_pct']}% "
        f"| 档1=+{strategy['sell1_pct']}% 档2=+{strategy['sell2_pct']}% 档3=+{strategy['sell3_pct']}%"
        f"{' [DRY-RUN]' if dry_run else ''}"
    )
    all_events = []

    for _, pos in positions.iterrows():
        try:
            # 打印持仓止盈进度摘要（INFO级别，便于日志中快速看出进度）
            _s1d = bool(pos.get("sell1_done"))
            _s2d = bool(pos.get("sell2_done"))
            _s3d = bool(pos.get("sell3_done"))
            _progress = (
                f"档1={'✅' if _s1d else '⏳'} "
                f"档2={'✅' if _s2d else '⏳'} "
                f"档3={'✅' if _s3d else '⏳'}"
            )
            logger.debug(
                f"  持仓 {pos.get('name','?')}({pos.get('code','?')}) "
                f"买入价={float(pos.get('buy_price') or 0):.3f} "
                f"止盈进度: {_progress}"
            )
            events = check_and_trigger(pos, strategy=strategy, dry_run=dry_run)
            all_events.extend(events)
        except Exception as e:
            code = pos.get("code", "?")
            name = pos.get("name", "?")
            logger.error(f"  {name}({code}) check_and_trigger 异常: {e}")
            # 单只股票异常不影响其他持仓的检查
            continue

    if all_events and not dry_run:
        logger.info(f"盘中监控触发 {len(all_events)} 个卖出事件，推送飞书...")
        msg = format_monitor_report(all_events)
        _send_feishu(msg)

    return all_events


def format_monitor_report(events: list[dict]) -> str:
    """将触发事件格式化为飞书通知文本"""
    if not events:
        return ""

    today = date.today().strftime("%Y/%m/%d")
    lines = [f"📊 **盘中分批卖出提醒** — {today}\n"]

    for ev in events:
        name       = ev["name"]
        code       = ev["code"]
        ev_type    = ev["type"]
        price      = ev["price"]
        shares     = ev.get("shares", 0)
        profit_pct = ev["profit_pct"]
        change_pct = ev.get("change_pct", 0)

        if ev_type == "止损":
            detail = (
                f"🔴 **[止损]** {name}（{code}）\n"
                f"  触发价: {price:.3f}  涨幅: {change_pct:+.2f}%  清仓 {shares} 股\n"
                f"  盈亏: {profit_pct:+.2f}%"
            )
        else:
            tier         = ev.get("tier", 0)
            target_price = ev.get("target_price", price)
            sold_amount  = ev.get("sold_amount", 0)
            all_done     = ev.get("all_done", False)
            icon         = "🟢" if tier == 3 else "🟡"
            suffix       = "（已全部清仓 ✅）" if all_done else ""
            detail = (
                f"{icon} **[第{tier}档 +{ev.get('tier_pct', tier*3)}%]** {name}（{code}）{suffix}\n"
                f"  触发价: {price:.3f}（目标: {target_price:.3f}）\n"
                f"  卖出 {shares} 股 = {sold_amount:.0f}元，盈亏: {profit_pct:+.2f}%"
            )

        lines.append(detail)

    return "\n\n".join(lines)


if __name__ == "__main__":
    # 手动运行 / 调试
    import argparse
    parser = argparse.ArgumentParser(description="盘中分批卖出监控 v2")
    parser.add_argument("--dry-run", action="store_true", help="只检测不推送飞书（也不写 DB）")
    args = parser.parse_args()

    # 手动运行时总是启用 INFO 日志（crontab 里静默的是 DEBUG）
    logger.remove()
    logger.add(sys.stderr, level="INFO", format="{time:HH:mm:ss} | {level} | {message}")

    if args.dry_run:
        print("=" * 60)
        print("DRY-RUN 模式：只检测，不写 DB，不推飞书")
        print("=" * 60)
        positions = get_active_batch_positions()
        if positions.empty:
            print("无活跃持仓")
        else:
            strategy = load_strategy()
            print(f"当前策略: 止损={strategy['stop_loss_pct']}%, "
                  f"档1=+{strategy['sell1_pct']}%, "
                  f"档2=+{strategy['sell2_pct']}%, "
                  f"档3=+{strategy['sell3_pct']}%")
            print()
            for _, pos in positions.iterrows():
                events = check_and_trigger(pos, strategy=strategy, dry_run=True)
                code  = pos["code"]
                name  = pos["name"]
                bp    = float(pos["buy_price"])

                if events:
                    for ev in events:
                        print(f"  ⚡ 触发: {ev['type']} | {name}({code}) "
                              f"@{ev['price']:.3f} 涨幅={ev['change_pct']:+.2f}%")
                else:
                    # 显示当前行情（即使无触发也要打印，方便人工确认）
                    from utils.retry import fetch_realtime_quote as _fq
                    q = _fq(code)
                    if q:
                        ep   = q["effective_price"]
                        chg  = (ep / bp - 1) * 100
                        s1p  = float(pos.get("sell1_price") or 0)
                        s2p  = float(pos.get("sell2_price") or 0)
                        s3p  = float(pos.get("sell3_price") or 0)
                        s1d  = bool(pos.get("sell1_done"))
                        s2d  = bool(pos.get("sell2_done"))
                        s3d  = bool(pos.get("sell3_done"))
                        s1s  = int(pos.get("sell1_shares") or 0)
                        s2s  = int(pos.get("sell2_shares") or 0)
                        s3s  = int(pos.get("sell3_shares") or 0)
                        sl   = round(bp * (1 + strategy["stop_loss_pct"] / 100), 3)
                        bid1 = q.get("bid1", 0)
                        ask1 = q.get("ask1", 0)
                        flag = " [涨停]" if (ask1 == 0 and bid1 > 0 and bid1 > q["last"]) else ""
                        # 止盈进度展示
                        def _tier_str(done, price, shares, pct_val):
                            done_tag = "✅已触发" if done else "⏳待触发"
                            arrow    = "→" if not done and ep >= price * 0.98 else "  "
                            return f"{done_tag} {arrow} {price:.3f}(+{pct_val:.1f}%, {shares}股)"
                        print(
                            f"  📈 {name}({code}): 有效价={ep:.3f}{flag} "
                            f"raw_last={q['last']:.3f} 涨幅={chg:+.2f}%\n"
                            f"     止损线={sl:.3f} | 分批止盈进度:\n"
                            f"       第1档: {_tier_str(s1d, s1p, s1s, float(pos.get('sell1_pct') or 3))}\n"
                            f"       第2档: {_tier_str(s2d, s2p, s2s, float(pos.get('sell2_pct') or 5))}\n"
                            f"       第3档: {_tier_str(s3d, s3p, s3s, float(pos.get('sell3_pct') or 8))}"
                        )
                    else:
                        print(f"  ❌ {name}({code}): 行情获取失败")
    else:
        run_monitor(dry_run=False)
