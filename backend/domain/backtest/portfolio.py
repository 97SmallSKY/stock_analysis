"""组合净值轨迹.

Portfolio 是一个薄封装:
  - 记录每天的 (日期, nav, cash, 持仓市值, 盈亏)
  - 给定 RobotSnapshot + 当日各股票收盘价 -> 计算总资产 -> 写 NavPoint

nav = total_assets / initial_capital   (基准 1.0)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from backend.domain.robot import RobotSnapshot, PositionState


@dataclass
class NavPoint:
    trade_date: date
    total_assets: float
    cash: float
    position_value: float
    position_count: int
    nav: float                        # 相对初始资金的单位净值
    pnl_daily: float = 0.0            # 当日盈亏金额
    pnl_daily_pct: float = 0.0
    trade_count: int = 0              # 当日交易笔数
    buy_amount: float = 0.0
    sell_amount: float = 0.0

    def to_dict(self) -> dict:
        return {
            'date': self.trade_date.isoformat(),
            'nav': round(self.nav, 6),
            'total_assets': round(self.total_assets, 2),
            'cash': round(self.cash, 2),
            'position_value': round(self.position_value, 2),
            'position_count': self.position_count,
            'pnl_daily': round(self.pnl_daily, 2),
            'pnl_daily_pct': round(self.pnl_daily_pct, 6),
            'trade_count': self.trade_count,
            'buy_amount': round(self.buy_amount, 2),
            'sell_amount': round(self.sell_amount, 2),
        }


@dataclass
class Portfolio:
    initial_capital: float
    nav_series: list[NavPoint] = field(default_factory=list)

    def latest_nav(self) -> float:
        return self.nav_series[-1].nav if self.nav_series else 1.0

    def latest_total_assets(self) -> float:
        return (
            self.nav_series[-1].total_assets if self.nav_series
            else self.initial_capital
        )

    # ------------------------------------------------------------------
    # 核心: 用当日收盘价把 snapshot 刷成 NavPoint
    # ------------------------------------------------------------------

    def mark_to_market(
        self,
        trade_date: date,
        snapshot: RobotSnapshot,
        close_prices: dict[str, float],
        *,
        trade_count: int = 0,
        buy_amount: float = 0.0,
        sell_amount: float = 0.0,
    ) -> NavPoint:
        # 用 close_prices 更新每只持仓的 current_price, 重算 total_assets
        new_positions = []
        position_value = 0.0
        for p in snapshot.positions:
            cp = close_prices.get(p.ts_code, p.current_price or p.cost_price)
            p2 = PositionState(
                ts_code=p.ts_code, shares=p.shares,
                cost_price=p.cost_price, open_date=p.open_date,
                current_price=cp,
            )
            new_positions.append(p2)
            position_value += p2.market_value

        total = round(snapshot.cash + position_value, 2)
        nav = total / self.initial_capital if self.initial_capital else 1.0

        prev_total = (
            self.nav_series[-1].total_assets if self.nav_series
            else self.initial_capital
        )
        pnl_daily = round(total - prev_total, 2)
        pnl_daily_pct = pnl_daily / prev_total if prev_total else 0.0

        point = NavPoint(
            trade_date=trade_date,
            total_assets=total,
            cash=snapshot.cash,
            position_value=position_value,
            position_count=len(new_positions),
            nav=round(nav, 6),
            pnl_daily=pnl_daily,
            pnl_daily_pct=round(pnl_daily_pct, 6),
            trade_count=trade_count,
            buy_amount=buy_amount,
            sell_amount=sell_amount,
        )
        self.nav_series.append(point)
        return point

    def equity_curve(self) -> list[dict]:
        return [p.to_dict() for p in self.nav_series]
