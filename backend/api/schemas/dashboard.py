"""STO-123 Phase 1c: dashboard API 响应 Pydantic schema.

宽模式 (extra='ignore') 起步，7 天观察后视情况切严。
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict


class SentimentSchema(BaseModel):
    model_config = ConfigDict(extra='ignore')

    trade_date: Optional[str] = None
    limit_up_count: Optional[int] = None
    limit_down_count: Optional[int] = None
    first_limit_up: Optional[int] = None
    cont_limit_up: Optional[int] = None
    max_cont_board: Optional[int] = None
    up_down_ratio: Optional[float] = None
    avg_pct_change: Optional[float] = None
    median_pct_change: Optional[float] = None
    strong_stock_count: Optional[int] = None
    weak_stock_count: Optional[int] = None
    lhb_stock_count: Optional[int] = None
    lhb_total_net: Optional[float] = None
    lhb_avg_score: Optional[float] = None
    sentiment_score: Optional[float] = None
    sentiment_level: Optional[str] = None
    sentiment_change: Optional[float] = None
    total_stocks: Optional[int] = None
    up_count: Optional[int] = None
    down_count: Optional[int] = None


class TopLhbItemSchema(BaseModel):
    model_config = ConfigDict(extra='ignore')

    ts_code: Optional[str] = None
    name: Optional[str] = None
    trade_date: Optional[str] = None
    reason: Optional[str] = None
    close: Optional[float] = None
    pct_change: Optional[float] = None
    net_amount: Optional[float] = None
    buy_amount: Optional[float] = None
    turnover: Optional[float] = None
    score: Optional[float] = None


class AdapterStatusSchema(BaseModel):
    model_config = ConfigDict(extra='ignore')

    name: Optional[str] = None
    healthy: Optional[bool] = None
    last_ok_at: Optional[str] = None
    last_err_at: Optional[str] = None
    cb_state: Optional[str] = None


class DashboardResp(BaseModel):
    model_config = ConfigDict(extra='ignore')

    sentiment: Optional[SentimentSchema] = None
    top_lhb: list[TopLhbItemSchema] = []
    adapters: list[AdapterStatusSchema] = []
