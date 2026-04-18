"""统一的龙虎榜数据 parser。

职责: 把不同数据源(akshare/sina/crawler 各自风格)返回的 DataFrame
      规整为统一的 `LhbRecord` (主表行) + `LhbSeat` (席位行) dataclass。

设计原则:
- pure function, 不依赖 DB / 网络
- 字段缺失时给合理默认 (None / 0 / "UNKNOWN")
- 上榜原因统一映射到 reason_type (PRICE_DEV/TURNOVER/CONSECUTIVE/AMPLITUDE/OTHER)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Iterable, Optional

import pandas as pd


# --------------------------------------------------------------------
# 数据类
# --------------------------------------------------------------------


@dataclass
class LhbSeat:
    """营业部席位 -> lhb_seats 一行"""

    broker_name: str
    direction: str  # BUY / SELL
    buy_amount: float = 0.0   # 万元
    sell_amount: float = 0.0
    net_amount: float = 0.0
    rank: Optional[int] = None
    is_institution: bool = False
    broker_type: str = "UNKNOWN"  # INSTITUTION / HOT_MONEY / UNKNOWN

    def to_db_row(self, ts_code: str, trade_date, stock_daily_id: int) -> dict:
        return {
            "stock_daily_id": stock_daily_id,
            "ts_code": ts_code,
            "trade_date": trade_date,
            "broker_name": self.broker_name,
            "broker_type": self.broker_type,
            "is_institution": self.is_institution,
            "buy_amount": self.buy_amount,
            "sell_amount": self.sell_amount,
            "net_amount": self.net_amount,
            "direction": self.direction,
            "rank": self.rank,
        }


@dataclass
class LhbRecord:
    """龙虎榜主表 -> lhb_stock_daily 一行 + 关联 seats"""

    ts_code: str
    trade_date: Any  # date 或 'YYYY-MM-DD' / 'YYYYMMDD'
    name: Optional[str] = None
    close_price: Optional[float] = None
    pct_change: Optional[float] = None
    turnover_rate: Optional[float] = None
    amount: Optional[float] = None
    l_buy: Optional[float] = None
    l_sell: Optional[float] = None
    l_net: Optional[float] = None
    reason: Optional[str] = None
    reason_type: Optional[str] = None  # PRICE_DEV/TURNOVER/CONSECUTIVE/AMPLITUDE/OTHER
    industry: Optional[str] = None
    market_cap: Optional[float] = None
    float_market_cap: Optional[float] = None

    seats: list[LhbSeat] = field(default_factory=list)

    # 数据源标记(便于审计)
    source: Optional[str] = None

    # ----------------- 转换辅助 -----------------

    def to_main_row(self) -> dict:
        """转换为 lhb_stock_daily upsert 用 dict"""
        return {
            "ts_code": self.ts_code,
            "trade_date": self.trade_date,
            "name": self.name,
            "close_price": self.close_price,
            "pct_change": self.pct_change,
            "turnover_rate": self.turnover_rate,
            "amount": self.amount,
            "l_buy": self.l_buy,
            "l_sell": self.l_sell,
            "l_net": self.l_net,
            "reason": self.reason,
            "reason_type": self.reason_type,
            "industry": self.industry,
            "market_cap": self.market_cap,
            "float_market_cap": self.float_market_cap,
        }


# --------------------------------------------------------------------
# reason_type 推断
# --------------------------------------------------------------------


REASON_KEYWORDS = (
    # (关键字, type)  按优先级
    (("连续三",), "CONSECUTIVE"),
    (("涨幅偏离", "涨跌幅偏离", "日涨幅"), "PRICE_DEV"),
    (("换手率",), "TURNOVER"),
    (("振幅",), "AMPLITUDE"),
)


def classify_reason(reason: Optional[str]) -> str:
    if not reason:
        return "OTHER"
    s = str(reason)
    for kws, t in REASON_KEYWORDS:
        for kw in kws:
            if kw in s:
                return t
    return "OTHER"


# --------------------------------------------------------------------
# 通用 parse: DataFrame -> list[LhbRecord]
# --------------------------------------------------------------------


def _to_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    if isinstance(v, (Decimal, int, float)):
        try:
            f = float(v)
            return None if pd.isna(f) else f
        except (TypeError, ValueError):
            return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def parse_lhb_dataframe(df: Optional[pd.DataFrame], source: Optional[str] = None) -> list[LhbRecord]:
    """把 DataGatewayManager.get_lhb_detail 返回的 DataFrame 转成 LhbRecord list.

    DataFrame 必须已经经过 adapter 内的列名规整(ts_code, trade_date, ...),
    本函数只做类型转换 + reason_type 推断 + 规整。
    seats 暂留空(席位独立接口拉取后再 attach)。
    """
    if df is None or df.empty:
        return []
    src = source or (
        df["_source"].iloc[0]
        if "_source" in df.columns and len(df) > 0 else None
    )
    out: list[LhbRecord] = []
    for _, row in df.iterrows():
        reason = row.get("reason")
        reason_type = row.get("reason_type") or classify_reason(reason)
        rec = LhbRecord(
            ts_code=str(row.get("ts_code")),
            trade_date=row.get("trade_date"),
            name=row.get("name"),
            close_price=_to_float(row.get("close_price")),
            pct_change=_to_float(row.get("pct_change")),
            turnover_rate=_to_float(row.get("turnover_rate")),
            amount=_to_float(row.get("amount")),
            l_buy=_to_float(row.get("l_buy")),
            l_sell=_to_float(row.get("l_sell")),
            l_net=_to_float(row.get("l_net")),
            reason=str(reason) if reason is not None else None,
            reason_type=reason_type,
            industry=row.get("industry"),
            market_cap=_to_float(row.get("market_cap")),
            float_market_cap=_to_float(row.get("float_market_cap")),
            source=src,
        )
        out.append(rec)
    return out


# --------------------------------------------------------------------
# 同键合并 (与 ingest/lhb._dedup_same_key 等价, 但操作 LhbRecord)
# --------------------------------------------------------------------


def merge_same_key(records: Iterable[LhbRecord]) -> list[LhbRecord]:
    """同 (ts_code, trade_date) 的多次上榜合并:
    - l_buy/l_sell/l_net 求和
    - reason 用 ; 拼接(去重)
    - seats 列表合并
    """
    from collections import OrderedDict

    merged: OrderedDict[tuple, LhbRecord] = OrderedDict()
    for r in records:
        key = (r.ts_code, r.trade_date)
        if key not in merged:
            merged[key] = r
            continue
        m = merged[key]
        for fld in ("l_buy", "l_sell", "l_net"):
            v1 = getattr(m, fld) or 0.0
            v2 = getattr(r, fld) or 0.0
            setattr(m, fld, float(v1) + float(v2))
        # reason 拼接
        r1 = (m.reason or "").strip()
        r2 = (r.reason or "").strip()
        if r2 and r2 not in r1:
            m.reason = f"{r1};{r2}" if r1 else r2
            m.reason_type = m.reason_type or classify_reason(m.reason)
        # seats 合并
        if r.seats:
            m.seats.extend(r.seats)
    return list(merged.values())
