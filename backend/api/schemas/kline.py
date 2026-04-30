"""STO-37 Phase 1a: K 线 API 响应 Pydantic schema.

宽模式 (extra='ignore') 起步，7 天观察后视情况切严。
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict


class KlineBarSchema(BaseModel):
    model_config = ConfigDict(extra='ignore')

    date: Optional[str] = None
    open: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    close: Optional[float] = None
    vol: Optional[float] = None


class KlineDailyResp(BaseModel):
    model_config = ConfigDict(extra='ignore')

    ts_code: str
    freq: str
    rows: list[KlineBarSchema]
    empty_reason: Optional[str] = None
    indicators: Optional[dict] = None


class KlineWeeklyResp(BaseModel):
    model_config = ConfigDict(extra='ignore')

    ts_code: str
    freq: str
    rows: list[KlineBarSchema]
    empty_reason: Optional[str] = None


class KlineDaterangeResp(BaseModel):
    model_config = ConfigDict(extra='ignore')

    ts_code: str
    freq: str
    min: Optional[str] = None
    max: Optional[str] = None
    count: int
    empty_reason: Optional[str] = None
