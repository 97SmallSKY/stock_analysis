"""内置参考策略.

这里给出两个最小可用实现, 用来验证 BaseStrategy 契约 + registry 可用:
  1. LhbHotMoneyStrategy   - 龙虎榜游资为主 (score >= 阈值 + 净买为主)
  2. LhbInstitutionStrategy - 龙虎榜机构为主 (机构席位数 >= 2)

数据访问通过 `ctx.data_provider`, 由上层注入; 本文件不直接碰 DB.
provider 需要实现:
    list_lhb_records(as_of) -> list[dict]         # {ts_code, score, reason, seats}
    # 其中 seats = [{broker_type: 'INSTITUTION'/'HOT_MONEY', direction, net_amount}, ...]
"""
from __future__ import annotations

from typing import Optional

from backend.domain.strategy.base import (
    BaseStrategy,
    Candidate,
    MarketContext,
    Position,
    SellSignal,
)
from backend.domain.strategy.registry import register_strategy


@register_strategy("lhb_hot_money")
class LhbHotMoneyStrategy(BaseStrategy):
    """龙虎榜游资跟随策略.

    入场:
      - score >= score_threshold (默认 70)
      - HOT_MONEY 席位净买金额 >= hot_money_net_min (默认 1000 万元)
    出场 (check_sell):
      - 亏损 <= stop_loss_pct 触发止损
      - 盈利 >= take_profit_pct 触发止盈
      - 持仓超过 max_hold_days 触发平仓
    """

    name = "lhb_hot_money"
    category = "LHB"
    description = "龙虎榜游资跟随"

    DEFAULT_PARAMS = {
        "score_threshold": 70,
        "hot_money_net_min": 1000.0,      # 万元
        "per_position_pct": 0.20,          # 单票占总资产 20%
        "stop_loss_pct": -0.07,
        "take_profit_pct": 0.15,
        "max_hold_days": 10,
        "max_candidates": 10,
    }

    def select_stocks(self, ctx: MarketContext) -> list[Candidate]:
        provider = ctx.data_provider
        if provider is None:
            return []
        records = provider.list_lhb_records(ctx.as_of) or []
        out: list[Candidate] = []
        th = float(self.get_param("score_threshold"))
        min_net = float(self.get_param("hot_money_net_min"))
        for r in records:
            score = float(r.get("score") or 0)
            if score < th:
                continue
            hot_net = sum(
                float(s.get("net_amount") or 0)
                for s in (r.get("seats") or [])
                if s.get("broker_type") == "HOT_MONEY"
                and s.get("direction") == "BUY"
            )
            if hot_net < min_net:
                continue
            out.append(Candidate(
                ts_code=r["ts_code"],
                score=score,
                reason=f"hot_money_net={hot_net:.0f}万 score={score:.1f}",
                signal_detail={"hot_money_net": hot_net, "score": score},
                lhb_daily_id=r.get("lhb_daily_id"),
                reference_price=r.get("close_price"),
            ))
        # 按 score 降序, 取前 N
        out.sort(key=lambda c: c.score, reverse=True)
        return out[: int(self.get_param("max_candidates"))]

    def calc_buy_amount(self, candidate: Candidate,
                        robot_cash: float, robot_total_assets: float) -> float:
        pct = float(self.get_param("per_position_pct"))
        # 基于总资产的百分比, 但不能超过可用现金
        planned = robot_total_assets * pct
        return max(0.0, min(planned, robot_cash))

    def check_sell(self, position: Position,
                   ctx: MarketContext) -> Optional[SellSignal]:
        stop_loss = float(self.get_param("stop_loss_pct"))
        take_profit = float(self.get_param("take_profit_pct"))
        max_hold = int(self.get_param("max_hold_days"))

        pct = position.pnl_pct
        if pct <= stop_loss:
            return SellSignal(
                reason="STOP_LOSS",
                price_hint=position.current_price,
                detail={"pnl_pct": pct, "threshold": stop_loss},
            )
        if pct >= take_profit:
            return SellSignal(
                reason="TAKE_PROFIT",
                price_hint=position.current_price,
                detail={"pnl_pct": pct, "threshold": take_profit},
            )
        if position.hold_days >= max_hold:
            return SellSignal(
                reason="MAX_HOLD",
                price_hint=position.current_price,
                detail={"hold_days": position.hold_days, "max": max_hold},
            )
        return None


@register_strategy("lhb_institution")
class LhbInstitutionStrategy(LhbHotMoneyStrategy):
    """龙虎榜机构跟随策略, 继承 HotMoney 的风控."""

    name = "lhb_institution"
    category = "LHB"
    description = "龙虎榜机构跟随"

    DEFAULT_PARAMS = {
        **LhbHotMoneyStrategy.DEFAULT_PARAMS,
        "min_institution_seats": 2,    # 至少 N 个机构席位
    }

    def select_stocks(self, ctx: MarketContext) -> list[Candidate]:
        provider = ctx.data_provider
        if provider is None:
            return []
        records = provider.list_lhb_records(ctx.as_of) or []
        out: list[Candidate] = []
        th = float(self.get_param("score_threshold"))
        min_inst = int(self.get_param("min_institution_seats"))
        for r in records:
            score = float(r.get("score") or 0)
            if score < th:
                continue
            inst_buy = [
                s for s in (r.get("seats") or [])
                if s.get("broker_type") == "INSTITUTION"
                and s.get("direction") == "BUY"
            ]
            if len(inst_buy) < min_inst:
                continue
            total_net = sum(float(s.get("net_amount") or 0) for s in inst_buy)
            out.append(Candidate(
                ts_code=r["ts_code"],
                score=score,
                reason=f"inst_buy_seats={len(inst_buy)} net={total_net:.0f}万",
                signal_detail={
                    "inst_buy_seats": len(inst_buy),
                    "inst_net": total_net,
                    "score": score,
                },
                lhb_daily_id=r.get("lhb_daily_id"),
                reference_price=r.get("close_price"),
            ))
        out.sort(key=lambda c: c.score, reverse=True)
        return out[: int(self.get_param("max_candidates"))]
