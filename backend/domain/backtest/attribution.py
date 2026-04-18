"""W10-1 绩效归因: 按席位 / 行业 / 上榜原因分桶.

输入:
    BacktestResult._trades_raw (list[TradeRecord]) — 含 BUY/SELL 配对
    meta_lookup 字典 {ts_code -> {seats: [...], industry: str, reason: str}}
        由上层从 DB join 构造; 测试里用纯字典.

输出:
    AttributionReport {
        by_industry / by_seat / by_reason: [BucketStat{...}]
    }

约定:
    - 只聚合 SELL 且 pnl_amount is not None 的记录 (已平仓).
    - 一个 ts_code 可能映射到多个席位: 每个席位都算一次 (不摊分, 保守估计).
    - 未知行业/原因 统一标为 '未知'.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from backend.domain.backtest.engine import TradeRecord


@dataclass
class BucketStat:
    bucket: str
    trade_count: int = 0
    win_count: int = 0
    total_pnl: float = 0.0
    sum_pnl_pct: float = 0.0

    @property
    def win_rate(self) -> float:
        return self.win_count / self.trade_count if self.trade_count else 0.0

    @property
    def avg_pnl_pct(self) -> float:
        return self.sum_pnl_pct / self.trade_count if self.trade_count else 0.0

    def to_dict(self) -> dict:
        return {
            "bucket": self.bucket,
            "trade_count": self.trade_count,
            "win_count": self.win_count,
            "total_pnl": round(self.total_pnl, 2),
            "avg_pnl_pct": round(self.avg_pnl_pct, 6),
            "win_rate": round(self.win_rate, 4),
        }


@dataclass
class AttributionReport:
    by_industry: list[BucketStat] = field(default_factory=list)
    by_seat: list[BucketStat] = field(default_factory=list)
    by_reason: list[BucketStat] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "by_industry": [b.to_dict() for b in self.by_industry],
            "by_seat": [b.to_dict() for b in self.by_seat],
            "by_reason": [b.to_dict() for b in self.by_reason],
        }


def _closed_sell_trades(trades: Iterable[TradeRecord]) -> list[TradeRecord]:
    return [t for t in trades if t.direction == "SELL" and t.pnl_amount is not None]


def _accumulate(stats: dict[str, BucketStat], bucket: str, tr: TradeRecord):
    s = stats.setdefault(bucket, BucketStat(bucket=bucket))
    s.trade_count += 1
    pnl = tr.pnl_amount or 0.0
    s.total_pnl += pnl
    s.sum_pnl_pct += (tr.pnl_pct or 0.0)
    if pnl > 0:
        s.win_count += 1


def _to_sorted(stats: dict[str, BucketStat]) -> list[BucketStat]:
    # 交易多, 盈利高的桶排前, bucket 名字做稳定尾键
    return sorted(
        stats.values(),
        key=lambda s: (-s.trade_count, -s.total_pnl, s.bucket),
    )


def build_attribution(
    trades: Iterable[TradeRecord],
    meta_lookup: dict[str, dict],
) -> AttributionReport:
    by_ind: dict[str, BucketStat] = {}
    by_seat: dict[str, BucketStat] = {}
    by_reason: dict[str, BucketStat] = {}

    for tr in _closed_sell_trades(trades):
        meta = meta_lookup.get(tr.ts_code, {}) or {}
        industry = (meta.get("industry") or "未知").strip() or "未知"
        _accumulate(by_ind, industry, tr)

        reason = (meta.get("reason") or "未知").strip() or "未知"
        _accumulate(by_reason, reason, tr)

        seats = meta.get("seats") or []
        if not seats:
            _accumulate(by_seat, "未知", tr)
        else:
            for s in seats:
                name = (str(s or "").strip()) or "未知"
                _accumulate(by_seat, name, tr)

    return AttributionReport(
        by_industry=_to_sorted(by_ind),
        by_seat=_to_sorted(by_seat),
        by_reason=_to_sorted(by_reason),
    )
