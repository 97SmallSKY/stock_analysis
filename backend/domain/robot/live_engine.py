"""W9 盘中机器人引擎.

职责:
  * tick(snapshot_by_code): 一次 10s 循环 - 读 snapshot -> plan_sell 检查卖出 ->
                            选股 plan_buy -> 返回 TickReport (新 RobotSnapshot + entries)
  * run_phase_a(candidates, prices): 开盘前 (9:25 前) 从 candidates 选股, 用集合竞价价
                                     挂模拟买单; 填满仓位或耗完资金后停.
  * run_phase_b(snapshot_by_code): 盘中监控 - 对每个持仓判断风控卖出条件.
  * pause()/resume(): 切状态, pause 期间 tick no-op.

不做 IO: snapshot / 价格 / 选股分数都由外部注入;
决策结果 (LedgerEntry + 新 RobotSnapshot) 也只返回, 不写库,
由上层 service (W10 或 api) 持久化.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Callable, Iterable, Optional

from backend.domain.robot.ledger import LedgerEntry
from backend.domain.robot.robot import RobotSnapshot
from backend.domain.robot.state import RobotState

logger = logging.getLogger(__name__)


@dataclass
class Candidate:
    """实时选股候选."""
    ts_code: str
    score: float
    reason: str = ""


@dataclass
class TickDecision:
    """单次 tick 内的一个决策 (买/卖)."""
    action: str          # BUY | SELL
    ts_code: str
    price: float
    shares: int = 0
    amount: float = 0.0
    reason: str = ""
    entries: list[LedgerEntry] = field(default_factory=list)


@dataclass
class TickReport:
    snapshot: RobotSnapshot
    decisions: list[TickDecision] = field(default_factory=list)
    skipped_reason: Optional[str] = None
    ts: datetime = field(default_factory=datetime.now)

    @property
    def applied(self) -> bool:
        return self.skipped_reason is None and bool(self.decisions)


# --------------------------------------------------------------------
# Sell 规则: 可替换的策略回调
# --------------------------------------------------------------------


def default_sell_policy(
    snap: RobotSnapshot, price: float, position, *, now: Optional[date] = None,
) -> Optional[str]:
    """默认盘中卖出策略. 返回触发卖出的原因字符串, 或 None 表示保持持仓.

    规则 (轻量, 可被 LiveRobotEngine(sell_policy=...) 替换):
      - 止盈: 当前价 >= 成本价 * 1.10  (reason='TP_10%')
      - 止损: 当前价 <= 成本价 * 0.93  (reason='SL_-7%')
      - 兜底: T+N 最大持仓 5 天 (if open_date + 5d < now)  (reason='MAX_HOLD_5D')
    """
    if price <= 0 or position.cost_price <= 0:
        return None
    up = price / position.cost_price - 1
    if up >= 0.10:
        return "TP_10%"
    if up <= -0.07:
        return "SL_-7%"
    if now is not None and (now - position.open_date).days >= 5:
        return "MAX_HOLD_5D"
    return None


# --------------------------------------------------------------------
# LiveRobotEngine
# --------------------------------------------------------------------


class LiveRobotEngine:
    """盘中机器人编排.

    snapshot: 当前 RobotSnapshot (不可变), 每次 tick 后可能被替换.
    sell_policy: (snap, price, position, now=None) -> reason|None
    planned_amount_per_buy: 默认 per_position_pct * total_assets,
                            可显式传入以节流.
    max_buys_per_tick / max_sells_per_tick: 限速, 避免一个 tick 多单.
    """

    def __init__(
        self,
        snapshot: RobotSnapshot,
        *,
        sell_policy: Callable = default_sell_policy,
        max_buys_per_tick: int = 1,
        max_sells_per_tick: int = 2,
        enabled: bool = False,          # W12-1 灰度: 默认 OFF
    ):
        self.snapshot = snapshot
        self.sell_policy = sell_policy
        self.max_buys_per_tick = max_buys_per_tick
        self.max_sells_per_tick = max_sells_per_tick
        self.enabled = bool(enabled)

    # ------------------------------------------------------------------
    # 灰度开关 (W12-1)
    # ------------------------------------------------------------------

    def enable(self) -> bool:
        """打开灰度开关. 返回当前 enabled 状态."""
        self.enabled = True
        return self.enabled

    def disable(self) -> bool:
        """关闭灰度开关 (不改变 state machine). 返回当前 enabled 状态."""
        self.enabled = False
        return self.enabled

    # ------------------------------------------------------------------
    # 状态控制
    # ------------------------------------------------------------------

    def pause(self) -> RobotSnapshot:
        """RUNNING -> PAUSED, 幂等 (已 PAUSED 返回自身)."""
        snap = self.snapshot
        if snap.state == RobotState.PAUSED:
            return snap
        sm = snap.state_machine().pause()
        from dataclasses import replace
        self.snapshot = replace(snap, state=sm.state)
        return self.snapshot

    def resume(self) -> RobotSnapshot:
        """PAUSED -> RUNNING (幂等). 如果是 IDLE 也走 start."""
        snap = self.snapshot
        if snap.state == RobotState.RUNNING:
            return snap
        sm = snap.state_machine()
        if snap.state == RobotState.IDLE:
            sm = sm.start()
        elif snap.state == RobotState.PAUSED:
            sm = sm.resume()
        else:
            return snap
        from dataclasses import replace
        self.snapshot = replace(snap, state=sm.state)
        return self.snapshot

    # ------------------------------------------------------------------
    # 主循环: tick
    # ------------------------------------------------------------------

    def tick(
        self,
        snapshot_by_code: dict[str, dict],
        *,
        candidates: Iterable[Candidate] = (),
        biz_date: Optional[date] = None,
    ) -> TickReport:
        """一次 10s tick.

        snapshot_by_code: {'600000.SH': {'price': 10.25, ...}}
        candidates: 今日实时选股候选 (score 降序)
        biz_date: 决策时钟, 默认 today()
        """
        biz_date = biz_date or date.today()
        snap = self.snapshot

        # W12-1 灰度: disabled 直接跳过, 上层由运营逐个 enable
        if not self.enabled:
            return TickReport(snapshot=snap, skipped_reason="disabled")
        # 非 RUNNING 直接跳过 (PAUSED / STOPPED / IDLE / COOL_DOWN / ERROR)
        if snap.state != RobotState.RUNNING:
            return TickReport(snapshot=snap, skipped_reason=f"state={snap.state.value}")

        decisions: list[TickDecision] = []

        # 1. 先结算卖出 (Phase B)
        sold_codes: set[str] = set()
        sell_budget = self.max_sells_per_tick
        for pos in list(snap.positions):
            if sell_budget <= 0:
                break
            tick = snapshot_by_code.get(pos.ts_code)
            if tick is None:
                continue
            price = _as_float(tick.get("price"))
            if price is None or price <= 0:
                continue
            reason = self.sell_policy(snap, price, pos, now=biz_date)
            if reason is None:
                continue
            plan = snap.plan_sell(pos.ts_code, price, biz_date)
            if not plan.ok:
                logger.warning(f"[LiveRobotEngine] plan_sell 拒绝 {pos.ts_code}: {plan.reason}")
                continue
            snap = plan.snapshot
            decisions.append(TickDecision(
                action="SELL", ts_code=pos.ts_code, price=price,
                shares=plan.filled_shares, amount=plan.filled_amount,
                reason=reason, entries=plan.entries,
            ))
            sold_codes.add(pos.ts_code)
            sell_budget -= 1

        # 2. 再尝试买入 (Phase A 盘中补仓 / 实时建仓)
        buy_budget = self.max_buys_per_tick
        cand_sorted = sorted(
            (c for c in candidates if c and c.ts_code and c.ts_code not in sold_codes),
            key=lambda c: c.score, reverse=True,
        )
        for c in cand_sorted:
            if buy_budget <= 0:
                break
            if snap.is_holding(c.ts_code):
                continue
            tick = snapshot_by_code.get(c.ts_code)
            if tick is None:
                continue
            price = _as_float(tick.get("price"))
            if price is None or price <= 0:
                continue
            planned_amount = round(snap.total_assets * snap.per_position_pct, 2)
            if planned_amount <= 0:
                break
            plan = snap.plan_buy(c.ts_code, planned_amount, price, c.score, biz_date)
            if not plan.ok:
                logger.debug(f"[LiveRobotEngine] plan_buy 拒绝 {c.ts_code}: {plan.reason}")
                continue
            snap = plan.snapshot
            decisions.append(TickDecision(
                action="BUY", ts_code=c.ts_code, price=price,
                shares=plan.filled_shares, amount=plan.filled_amount,
                reason=c.reason or f"score={c.score:.2f}",
                entries=plan.entries,
            ))
            buy_budget -= 1

        self.snapshot = snap
        return TickReport(snapshot=snap, decisions=decisions)

    # ------------------------------------------------------------------
    # Phase A: 开盘前 / 集合竞价挂单
    # ------------------------------------------------------------------

    def run_phase_a(
        self,
        candidates: Iterable[Candidate],
        prices: dict[str, float],
        *,
        biz_date: Optional[date] = None,
        max_new_positions: Optional[int] = None,
    ) -> TickReport:
        """Phase A (开盘前选股 -> 9:25 集合竞价挂模拟单).

        prices: ts_code -> 集合竞价参考价 (通常 9:25 开盘价).
        max_new_positions: 限制新增持仓数, 默认 = max_positions - 当前持仓数.
        """
        biz_date = biz_date or date.today()
        snap = self.snapshot
        decisions: list[TickDecision] = []

        # W12-1 灰度
        if not self.enabled:
            return TickReport(snapshot=snap, skipped_reason="disabled")
        if snap.state not in (RobotState.RUNNING, RobotState.IDLE):
            return TickReport(snapshot=snap, skipped_reason=f"state={snap.state.value}")

        # Phase A 自动启动 (IDLE -> RUNNING)
        if snap.state == RobotState.IDLE:
            snap = self.resume()

        remaining_slots = max_new_positions
        if remaining_slots is None:
            remaining_slots = max(0, snap.max_positions - snap.position_count())

        for c in sorted(candidates, key=lambda c: c.score, reverse=True):
            if remaining_slots <= 0:
                break
            if snap.is_holding(c.ts_code):
                continue
            price = prices.get(c.ts_code)
            if price is None or price <= 0:
                continue
            planned_amount = round(snap.total_assets * snap.per_position_pct, 2)
            if planned_amount <= 0:
                break
            plan = snap.plan_buy(c.ts_code, planned_amount, float(price), c.score, biz_date)
            if not plan.ok:
                logger.info(f"[PhaseA] plan_buy 拒绝 {c.ts_code}: {plan.reason}")
                continue
            snap = plan.snapshot
            decisions.append(TickDecision(
                action="BUY", ts_code=c.ts_code, price=float(price),
                shares=plan.filled_shares, amount=plan.filled_amount,
                reason=f"PhaseA score={c.score:.2f}",
                entries=plan.entries,
            ))
            remaining_slots -= 1

        self.snapshot = snap
        return TickReport(snapshot=snap, decisions=decisions)

    # ------------------------------------------------------------------
    # Phase B: 盘中纯卖出检查 (等价 tick 不走买入)
    # ------------------------------------------------------------------

    def run_phase_b(
        self,
        snapshot_by_code: dict[str, dict],
        *,
        biz_date: Optional[date] = None,
    ) -> TickReport:
        prev_buys = self.max_buys_per_tick
        self.max_buys_per_tick = 0
        try:
            return self.tick(snapshot_by_code, candidates=(), biz_date=biz_date)
        finally:
            self.max_buys_per_tick = prev_buys


# --------------------------------------------------------------------
# 实时选股 (W9 需求: 涨停池 + 简单评分)
# --------------------------------------------------------------------


def score_from_zt_pool(
    zt_codes: Iterable[str],
    zt_meta: Optional[dict[str, dict]] = None,
) -> list[Candidate]:
    """把 AKShare stock_zt_pool_em 的结果转成 Candidate 列表.

    默认 score = 60 (保底); 若 meta 里有 '连板数' (int) 则 +10/板, 'open_times' -2/次.
    """
    out: list[Candidate] = []
    meta = zt_meta or {}
    for code in zt_codes:
        if not code:
            continue
        m = meta.get(code, {}) or {}
        score = 60.0
        streak = m.get("streak")
        if isinstance(streak, (int, float)):
            score += 10.0 * float(streak)
        opens = m.get("open_times")
        if isinstance(opens, (int, float)):
            score -= 2.0 * float(opens)
        score = max(0.0, min(100.0, score))
        out.append(Candidate(
            ts_code=str(code).upper(), score=score,
            reason=f"ZT streak={streak or 0}",
        ))
    return out


def _as_float(v) -> Optional[float]:
    if v is None:
        return None
    try:
        import math
        f = float(v)
        if math.isnan(f):
            return None
        return f
    except (TypeError, ValueError):
        return None
