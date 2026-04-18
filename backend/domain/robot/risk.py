"""风控模块.

RiskEvaluator 在每次买入前被调用, 返回 RiskDecision:
    - allow:    是否放行
    - violations: 触发的风控规则列表
    - adjusted_amount: 如果是 cap 规则, 给出修正后可买入金额

规则类型 (RiskRule.kind):
    max_positions       单机器人最大持仓只数
    per_position_pct    单票仓位占总资产比例上限
    daily_loss_limit    当日亏损达到该阈值 -> 触发 COOL_DOWN (BLOCK)
    max_single_loss     持仓单票回撤 <= 阈值 -> 强制卖出 (外部用; 本模块只检查)
    cool_down           处于 COOL_DOWN / PAUSED 状态 -> 禁止买入
    score_threshold     候选 score < 阈值 -> 禁入

RiskEvaluator 组合多条规则, 同类规则叠加取最严.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from backend.domain.robot.state import RobotState


class RiskRuleKind(str, Enum):
    MAX_POSITIONS = 'max_positions'
    PER_POSITION_PCT = 'per_position_pct'
    DAILY_LOSS_LIMIT = 'daily_loss_limit'
    COOL_DOWN = 'cool_down'
    SCORE_THRESHOLD = 'score_threshold'
    MAX_SINGLE_LOSS = 'max_single_loss'


@dataclass
class RiskRule:
    kind: RiskRuleKind
    value: float = 0.0
    note: str = ''


@dataclass
class RiskViolation:
    rule: RiskRuleKind
    message: str
    severity: str = 'BLOCK'   # BLOCK / CAP / WARN


@dataclass
class RiskDecision:
    allow: bool
    violations: list[RiskViolation] = field(default_factory=list)
    adjusted_amount: Optional[float] = None  # None 表示无修正

    @property
    def blocked(self) -> bool:
        return not self.allow


@dataclass
class BuyRequest:
    """买入前的候选状态快照 (外部组装, 传给 evaluator)."""
    ts_code: str
    planned_amount: float
    score: float
    robot_state: RobotState
    cash: float
    total_assets: float
    current_positions: int
    daily_pnl_pct: float = 0.0
    already_holding: bool = False


class RiskEvaluator:
    def __init__(self, rules: list[RiskRule]):
        self.rules = list(rules)

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------

    def evaluate_buy(self, req: BuyRequest) -> RiskDecision:
        violations: list[RiskViolation] = []
        adjusted: Optional[float] = None

        for rule in self.rules:
            v = self._check_rule(rule, req)
            if v is None:
                continue
            violations.append(v)
            if v.severity == 'CAP':
                cap_value = rule.value * req.total_assets
                adjusted = cap_value if adjusted is None else min(adjusted, cap_value)

        blocked = any(v.severity == 'BLOCK' for v in violations)
        if adjusted is not None:
            adjusted = max(0.0, min(adjusted, req.cash))

        return RiskDecision(
            allow=not blocked,
            violations=violations,
            adjusted_amount=adjusted,
        )

    # ------------------------------------------------------------------
    # 每条规则的 check
    # ------------------------------------------------------------------

    def _check_rule(self, rule: RiskRule,
                    req: BuyRequest) -> Optional[RiskViolation]:
        K = RiskRuleKind
        if rule.kind == K.COOL_DOWN:
            if req.robot_state in (RobotState.COOL_DOWN, RobotState.PAUSED,
                                    RobotState.STOPPED, RobotState.ERROR):
                return RiskViolation(
                    rule=K.COOL_DOWN,
                    message=f'机器人状态 {req.robot_state.value} 不允许下单',
                    severity='BLOCK',
                )
            return None
        if rule.kind == K.MAX_POSITIONS:
            if req.already_holding:
                return None
            if req.current_positions >= int(rule.value):
                return RiskViolation(
                    rule=K.MAX_POSITIONS,
                    message=f'已达最大持仓数 {int(rule.value)}',
                    severity='BLOCK',
                )
            return None
        if rule.kind == K.PER_POSITION_PCT:
            cap = rule.value * req.total_assets
            if req.planned_amount > cap + 1e-6:
                return RiskViolation(
                    rule=K.PER_POSITION_PCT,
                    message=(f'单票仓位超限 {rule.value*100:.1f}%, '
                            f'计划 {req.planned_amount:.0f} 上限 {cap:.0f}'),
                    severity='CAP',
                )
            return None
        if rule.kind == K.DAILY_LOSS_LIMIT:
            if req.daily_pnl_pct <= rule.value:
                return RiskViolation(
                    rule=K.DAILY_LOSS_LIMIT,
                    message=(f'触发日亏损限额: '
                            f'{req.daily_pnl_pct*100:.2f}% <= {rule.value*100:.2f}%'),
                    severity='BLOCK',
                )
            return None
        if rule.kind == K.SCORE_THRESHOLD:
            if req.score < rule.value:
                return RiskViolation(
                    rule=K.SCORE_THRESHOLD,
                    message=f'评分不达标: {req.score} < {rule.value}',
                    severity='BLOCK',
                )
            return None
        return None

    # ------------------------------------------------------------------
    # 工厂
    # ------------------------------------------------------------------

    @classmethod
    def from_robot_config(
        cls,
        risk_config: dict,
        *,
        max_positions: int = 5,
        per_position_pct: float = 0.20,
    ) -> 'RiskEvaluator':
        rules = [
            RiskRule(RiskRuleKind.COOL_DOWN),
            RiskRule(RiskRuleKind.MAX_POSITIONS, float(max_positions)),
            RiskRule(RiskRuleKind.PER_POSITION_PCT, float(per_position_pct)),
        ]
        if 'daily_loss_limit' in risk_config:
            rules.append(RiskRule(
                RiskRuleKind.DAILY_LOSS_LIMIT,
                float(risk_config['daily_loss_limit']),
            ))
        if 'score_threshold' in risk_config:
            rules.append(RiskRule(
                RiskRuleKind.SCORE_THRESHOLD,
                float(risk_config['score_threshold']),
            ))
        return cls(rules)
