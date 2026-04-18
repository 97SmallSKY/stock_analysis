"""资金台账.

对应 DB `cash_ledger` 表. 本模块是纯逻辑: 给一个 balance(可用现金)输入,
返回 (new_balance, LedgerEntry) 元组. 落库由上层负责.

设计要点:
- LedgerType 与 DB enum `ledger_type` 保持一致
- direction 自动推断 (BUY_FREEZE/WITHDRAW/COMMISSION/SLIPPAGE = OUT, 其他 = IN)
- amount 非负; 若传负数会抛 ValueError (保护约束)
- balance_before/after 保留完整流水, 可用于事后对账
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Optional, Union

Number = Union[int, float, Decimal]


class LedgerType(str, Enum):
    DEPOSIT = "DEPOSIT"              # 入金
    WITHDRAW = "WITHDRAW"            # 出金
    BUY_FREEZE = "BUY_FREEZE"        # 买入冻结(现金 -> 持仓)
    SELL_RETURN = "SELL_RETURN"      # 卖出回款(持仓 -> 现金)
    COMMISSION = "COMMISSION"        # 佣金
    SLIPPAGE = "SLIPPAGE"            # 滑点成本
    DIVIDEND = "DIVIDEND"            # 分红


_OUTGOING = {
    LedgerType.WITHDRAW,
    LedgerType.BUY_FREEZE,
    LedgerType.COMMISSION,
    LedgerType.SLIPPAGE,
}


def _infer_direction(tp: LedgerType) -> str:
    return "OUT" if tp in _OUTGOING else "IN"


@dataclass
class LedgerEntry:
    """对应 cash_ledger 一行 (未落库)."""
    robot_id: int
    ledger_type: LedgerType
    direction: str              # IN / OUT
    amount: float               # 正数
    balance_before: float
    balance_after: float
    biz_date: date
    ts_code: Optional[str] = None
    trade_id: Optional[int] = None
    description: Optional[str] = None

    def to_db_row(self) -> dict:
        return {
            "robot_id": self.robot_id,
            "ledger_type": self.ledger_type.value,
            "direction": self.direction,
            "amount": round(float(self.amount), 2),
            "balance_before": round(float(self.balance_before), 2),
            "balance_after": round(float(self.balance_after), 2),
            "biz_date": self.biz_date,
            "ts_code": self.ts_code,
            "trade_id": self.trade_id,
            "description": self.description,
        }


# --------------------------------------------------------------------
# 应用函数 (纯函数: 输入余额 + 参数 -> 输出新余额 + LedgerEntry)
# --------------------------------------------------------------------


def _validate_amount(amount: Number) -> float:
    a = float(amount)
    if a < 0:
        raise ValueError(f"amount 必须 >= 0, got {a}")
    return a


def _make(robot_id: int, tp: LedgerType, amount: float,
          balance_before: float, balance_after: float,
          biz_date: date, *, ts_code=None, trade_id=None,
          description=None) -> LedgerEntry:
    return LedgerEntry(
        robot_id=robot_id,
        ledger_type=tp,
        direction=_infer_direction(tp),
        amount=amount,
        balance_before=balance_before,
        balance_after=balance_after,
        biz_date=biz_date,
        ts_code=ts_code,
        trade_id=trade_id,
        description=description,
    )


def apply_deposit(robot_id: int, balance: float, amount: Number,
                  biz_date: date, *, description=None) -> tuple[float, LedgerEntry]:
    a = _validate_amount(amount)
    new_bal = balance + a
    entry = _make(robot_id, LedgerType.DEPOSIT, a, balance, new_bal,
                  biz_date, description=description)
    return new_bal, entry


def apply_buy(robot_id: int, balance: float, amount: Number,
              biz_date: date, *, ts_code: str, trade_id=None,
              description=None) -> tuple[float, LedgerEntry]:
    """买入: 冻结现金 (cash -= amount). amount 必须 <= balance."""
    a = _validate_amount(amount)
    if a > balance + 1e-6:
        raise ValueError(
            f"现金不足: 需要 {a:.2f}, 可用 {balance:.2f}"
        )
    new_bal = balance - a
    entry = _make(robot_id, LedgerType.BUY_FREEZE, a, balance, new_bal,
                  biz_date, ts_code=ts_code, trade_id=trade_id,
                  description=description)
    return new_bal, entry


def apply_sell(robot_id: int, balance: float, amount: Number,
               biz_date: date, *, ts_code: str, trade_id=None,
               description=None) -> tuple[float, LedgerEntry]:
    """卖出回款: cash += amount."""
    a = _validate_amount(amount)
    new_bal = balance + a
    entry = _make(robot_id, LedgerType.SELL_RETURN, a, balance, new_bal,
                  biz_date, ts_code=ts_code, trade_id=trade_id,
                  description=description)
    return new_bal, entry


def apply_commission(robot_id: int, balance: float, amount: Number,
                     biz_date: date, *, ts_code=None, trade_id=None,
                     description=None) -> tuple[float, LedgerEntry]:
    """佣金/印花税/滑点: cash -= amount."""
    a = _validate_amount(amount)
    new_bal = balance - a
    entry = _make(robot_id, LedgerType.COMMISSION, a, balance, new_bal,
                  biz_date, ts_code=ts_code, trade_id=trade_id,
                  description=description)
    return new_bal, entry
