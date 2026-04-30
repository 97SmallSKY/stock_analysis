"""STO-121 Phase 1b: lhb API 响应 Pydantic schema.

宽模式 (extra='ignore') 起步，7 天观察后视情况切严。
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict


class LhbRowSchema(BaseModel):
    model_config = ConfigDict(extra='ignore')

    ts_code: str
    name: Optional[str] = None
    trade_date: Optional[str] = None
    reason: Optional[str] = None
    close: Optional[float] = None
    pct_change: Optional[float] = None
    buy_amount: Optional[float] = None
    sell_amount: Optional[float] = None
    net_amount: Optional[float] = None
    turnover: Optional[float] = None
    score: Optional[float] = None
    score_detail: Optional[dict] = None
    # T+N 字段（with_tn=True 时存在）
    t1_pnl_pct: Optional[float] = None
    t1_limit_up: Optional[bool] = None
    t3_pnl_pct: Optional[float] = None
    t5_pnl_pct: Optional[float] = None
    t5_high_pct: Optional[float] = None
    t7_pnl_pct: Optional[float] = None
    t10_pnl_pct: Optional[float] = None


class LhbListResp(BaseModel):
    model_config = ConfigDict(extra='ignore')

    total: int
    limit: int
    offset: int
    rows: list[LhbRowSchema]


class LhbSeatRowSchema(BaseModel):
    model_config = ConfigDict(extra='ignore')

    ts_code: Optional[str] = None
    name: Optional[str] = None
    broker_name: Optional[str] = None
    trade_date: Optional[str] = None
    direction: Optional[str] = None
    buy_amount: Optional[float] = None
    sell_amount: Optional[float] = None
    net_amount: Optional[float] = None
    rank_no: Optional[int] = None


class LhbSeatsResp(BaseModel):
    model_config = ConfigDict(extra='ignore')

    rows: list[LhbSeatRowSchema]
    n: int


class LhbStatResp(BaseModel):
    model_config = ConfigDict(extra='ignore')

    stat_type: Optional[str] = None
    stat_key: Optional[str] = None
    stat_period: Optional[str] = None
    payload: Optional[dict] = None
    updated_at: Optional[str] = None
