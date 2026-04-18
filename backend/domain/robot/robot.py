"""机器人快照 (不可变) + 纯函数化的 buy / sell 行为.

这一层不做任何 DB / IO, 只负责:
  1. 持有机器人的关键状态 (cash / total_assets / positions / state ...)
  2. 提供 `plan_buy(req)` 和 `plan_sell(position, price)`,
     组合 RiskEvaluator + ledger, 返回 (new_snapshot, entries)

上层 (domain/robot/service.py 或 celery task) 负责把 entries 落 cash_ledger /
  robot_trades 表, 并 UPDATE robots 表的 cash/positions 字段.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date
from typing import Optional

from backend.domain.robot.ledger import (
    LedgerEntry,
    apply_buy,
    apply_commission,
    apply_sell,
)
from backend.domain.robot.risk import (
    BuyRequest,
    RiskDecision,
    RiskEvaluator,
)
from backend.domain.robot.state import RobotState, RobotStateMachine


@dataclass(frozen=True)
class PositionState:
    """某机器人当前某只股票的持仓 (memory-only)."""
    ts_code: str
    shares: int
    cost_price: float
    open_date: date
    current_price: Optional[float] = None

    @property
    def market_value(self) -> float:
        price = self.current_price if self.current_price is not None else self.cost_price
        return round(float(price) * self.shares, 2)


@dataclass
class PlanResult:
    """plan_buy / plan_sell 的返回."""
    ok: bool
    reason: str = ''
    entries: list[LedgerEntry] = field(default_factory=list)
    # 更新后的快照 (若 ok=False 通常等于原快照, caller 可忽略)
    snapshot: Optional['RobotSnapshot'] = None
    decision: Optional[RiskDecision] = None
    filled_amount: float = 0.0         # 实际成交金额
    filled_shares: int = 0


@dataclass(frozen=True)
class RobotSnapshot:
    """机器人一致性快照. 不可变, 操作返回新快照."""
    robot_id: int
    state: RobotState = RobotState.IDLE
    cash: float = 0.0
    total_assets: float = 0.0
    positions: tuple[PositionState, ...] = ()
    daily_pnl_pct: float = 0.0
    # 风控规则 (构建时注入; 不参与 equal 比较/frozen 兼容)
    risk_config: dict = field(default_factory=dict)
    max_positions: int = 5
    per_position_pct: float = 0.20

    # ------------------------------------------------------------------
    # 查询辅助
    # ------------------------------------------------------------------

    def position_count(self) -> int:
        return len(self.positions)

    def holding(self, ts_code: str) -> Optional[PositionState]:
        for p in self.positions:
            if p.ts_code == ts_code:
                return p
        return None

    def is_holding(self, ts_code: str) -> bool:
        return self.holding(ts_code) is not None

    def state_machine(self) -> RobotStateMachine:
        return RobotStateMachine(self.state)

    def evaluator(self) -> RiskEvaluator:
        return RiskEvaluator.from_robot_config(
            self.risk_config,
            max_positions=self.max_positions,
            per_position_pct=self.per_position_pct,
        )

    def recompute_total_assets(self) -> 'RobotSnapshot':
        pv = sum(p.market_value for p in self.positions)
        return replace(self, total_assets=round(self.cash + pv, 2))

    # ------------------------------------------------------------------
    # 行为: 买入 (计划)
    # ------------------------------------------------------------------

    def plan_buy(
        self,
        ts_code: str,
        planned_amount: float,
        price: float,
        score: float,
        biz_date: date,
        *,
        commission_rate: float = 0.0003,  # 万三
        min_commission: float = 5.0,
    ) -> PlanResult:
        """返回买入计划 + 修改后的快照."""
        req = BuyRequest(
            ts_code=ts_code,
            planned_amount=planned_amount,
            score=score,
            robot_state=self.state,
            cash=self.cash,
            total_assets=self.total_assets,
            current_positions=self.position_count(),
            daily_pnl_pct=self.daily_pnl_pct,
            already_holding=self.is_holding(ts_code),
        )
        dec = self.evaluator().evaluate_buy(req)
        if dec.blocked:
            return PlanResult(
                ok=False,
                reason='; '.join(v.message for v in dec.violations) or 'BLOCKED',
                decision=dec,
                snapshot=self,
            )
        amount = dec.adjusted_amount if dec.adjusted_amount is not None else planned_amount
        amount = min(amount, self.cash)
        if amount <= 0 or price <= 0:
            return PlanResult(ok=False, reason='金额或价格为 0', decision=dec, snapshot=self)

        # 股票按 100 股为单位 (A 股一手 = 100)
        shares = int(amount // (price * 100)) * 100
        if shares <= 0:
            return PlanResult(ok=False, reason='资金不足买一手',
                              decision=dec, snapshot=self)
        actual_amount = round(shares * price, 2)
        commission = max(min_commission, round(actual_amount * commission_rate, 2))

        # ledger: 先扣买入冻结, 再扣佣金
        cash1, buy_entry = apply_buy(
            self.robot_id, self.cash, actual_amount, biz_date,
            ts_code=ts_code, description=f'BUY {shares}x{price}',
        )
        cash2, fee_entry = apply_commission(
            self.robot_id, cash1, commission, biz_date,
            ts_code=ts_code, description='买入佣金',
        )

        # 持仓更新
        existing = self.holding(ts_code)
        if existing is None:
            new_pos = PositionState(
                ts_code=ts_code, shares=shares, cost_price=price,
                open_date=biz_date, current_price=price,
            )
            positions = (*self.positions, new_pos)
        else:
            # 加仓按加权平均
            total_shares = existing.shares + shares
            new_cost = round(
                (existing.shares * existing.cost_price + shares * price)
                / total_shares, 4,
            )
            merged = PositionState(
                ts_code=ts_code, shares=total_shares, cost_price=new_cost,
                open_date=existing.open_date, current_price=price,
            )
            positions = tuple(
                merged if p.ts_code == ts_code else p for p in self.positions
            )
        new_snap = replace(self, cash=cash2, positions=positions).recompute_total_assets()
        return PlanResult(
            ok=True, reason='OK',
            entries=[buy_entry, fee_entry],
            snapshot=new_snap,
            decision=dec,
            filled_amount=actual_amount,
            filled_shares=shares,
        )

    # ------------------------------------------------------------------
    # 行为: 卖出 (计划)
    # ------------------------------------------------------------------

    def plan_sell(
        self,
        ts_code: str,
        price: float,
        biz_date: date,
        *,
        shares: Optional[int] = None,
        commission_rate: float = 0.0003,
        min_commission: float = 5.0,
        stamp_rate: float = 0.001,       # 印花税千一 (仅卖出)
    ) -> PlanResult:
        pos = self.holding(ts_code)
        if pos is None:
            return PlanResult(ok=False, reason='未持有该股票', snapshot=self)
        sell_shares = pos.shares if shares is None else min(shares, pos.shares)
        if sell_shares <= 0 or price <= 0:
            return PlanResult(ok=False, reason='卖出股数或价格为 0', snapshot=self)
        gross = round(sell_shares * price, 2)
        commission = max(min_commission, round(gross * commission_rate, 2))
        stamp = round(gross * stamp_rate, 2)

        cash1, sell_entry = apply_sell(
            self.robot_id, self.cash, gross, biz_date,
            ts_code=ts_code, description=f'SELL {sell_shares}x{price}',
        )
        cash2, fee_entry = apply_commission(
            self.robot_id, cash1, commission, biz_date,
            ts_code=ts_code, description='卖出佣金',
        )
        cash3, tax_entry = apply_commission(
            self.robot_id, cash2, stamp, biz_date,
            ts_code=ts_code, description='印花税',
        )

        # 持仓更新
        remaining = pos.shares - sell_shares
        if remaining == 0:
            positions = tuple(p for p in self.positions if p.ts_code != ts_code)
        else:
            new_pos = PositionState(
                ts_code=pos.ts_code, shares=remaining,
                cost_price=pos.cost_price, open_date=pos.open_date,
                current_price=price,
            )
            positions = tuple(
                new_pos if p.ts_code == ts_code else p for p in self.positions
            )
        new_snap = replace(self, cash=cash3, positions=positions).recompute_total_assets()
        return PlanResult(
            ok=True, reason='OK',
            entries=[sell_entry, fee_entry, tax_entry],
            snapshot=new_snap,
            filled_amount=gross,
            filled_shares=sell_shares,
        )

    # ------------------------------------------------------------------
    # 状态切换 (返回新快照)
    # ------------------------------------------------------------------

    def with_state(self, new_state: RobotState) -> 'RobotSnapshot':
        sm = self.state_machine().transition(new_state)
        return replace(self, state=sm.state)
