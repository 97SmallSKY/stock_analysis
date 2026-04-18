"""事件驱动日 K 回测引擎.

流程 (每个交易日 T):
  1. open(T): 以 T 日开盘价执行上一日 select_stocks 的 buy 候选
  2. 盘中: 用 T 日收盘价 check_sell, 触发则以 close 成交
  3. close(T): mark_to_market (用 T 日 close)
  4. 盘后: strategy.select_stocks(ctx) 生成 T+1 交易日的买入候选

简化:
- 不撮合, 买入直接按 open 成交, 卖出按 close
- 交易成本走 RobotSnapshot.plan_buy / plan_sell (佣金 + 印花税)
- 风控复用 domain.robot 的 RiskEvaluator
- 日历由 provider 提供

provider 接口:
  list_trade_days(start, end) -> list[date]
  get_daily(ts_code, trade_date) -> dict|None  {open, high, low, close}
  list_lhb_records(as_of) -> list[dict]   # 给策略 select_stocks 用
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from datetime import date
from typing import Any, Iterable, Optional, Protocol

from backend.domain.backtest.portfolio import Portfolio
from backend.domain.robot import (
    PositionState,
    RobotSnapshot,
    RobotState,
)
from backend.domain.strategy import BaseStrategy, MarketContext, Position

logger = logging.getLogger(__name__)


class DailyProvider(Protocol):
    def list_trade_days(self, start: date, end: date) -> list[date]: ...
    def get_daily(self, ts_code: str, trade_date: date) -> Optional[dict]: ...
    def list_lhb_records(self, as_of: date) -> list[dict]: ...


@dataclass
class TradeRecord:
    trade_date: date
    ts_code: str
    direction: str           # BUY / SELL
    price: float
    shares: int
    amount: float
    reason: str = ''
    pnl_amount: Optional[float] = None
    pnl_pct: Optional[float] = None
    hold_days: Optional[int] = None

    def to_dict(self) -> dict:
        return {
            'date': self.trade_date.isoformat(),
            'ts_code': self.ts_code,
            'direction': self.direction,
            'price': round(self.price, 4),
            'shares': self.shares,
            'amount': round(self.amount, 2),
            'reason': self.reason,
            'pnl_amount': (
                round(self.pnl_amount, 2) if self.pnl_amount is not None
                else None
            ),
            'pnl_pct': (
                round(self.pnl_pct, 6) if self.pnl_pct is not None
                else None
            ),
            'hold_days': self.hold_days,
        }


@dataclass
class BacktestResult:
    start_date: date
    end_date: date
    initial_capital: float
    final_assets: float
    nav_series: list[dict]
    trades: list[dict]
    # 辅助原数据, 便于 report 计算
    _nav_raw: list = field(default_factory=list, repr=False)
    _trades_raw: list[TradeRecord] = field(default_factory=list, repr=False)

    @property
    def total_return(self) -> float:
        if self.initial_capital == 0:
            return 0.0
        return (self.final_assets - self.initial_capital) / self.initial_capital


@dataclass
class BacktestConfig:
    initial_capital: float = 1_000_000.0
    commission_rate: float = 0.0003
    stamp_rate: float = 0.001
    slippage_rate: float = 0.001
    max_positions: int = 5
    per_position_pct: float = 0.20
    risk_config: dict = field(default_factory=lambda: {
        'daily_loss_limit': -0.05,
        'score_threshold': 60,
    })


class BacktestEngine:
    def __init__(self, strategy: BaseStrategy, provider: DailyProvider,
                 config: Optional[BacktestConfig] = None):
        self.strategy = strategy
        self.provider = provider
        self.config = config or BacktestConfig()

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------

    def run(self, start: date, end: date) -> BacktestResult:
        cfg = self.config
        days = self.provider.list_trade_days(start, end)
        if not days:
            logger.warning(f'[backtest] 无交易日 {start} -> {end}')
            return BacktestResult(
                start_date=start, end_date=end,
                initial_capital=cfg.initial_capital,
                final_assets=cfg.initial_capital,
                nav_series=[], trades=[],
            )

        snap = RobotSnapshot(
            robot_id=0, state=RobotState.RUNNING,
            cash=cfg.initial_capital, total_assets=cfg.initial_capital,
            max_positions=cfg.max_positions,
            per_position_pct=cfg.per_position_pct,
            risk_config=dict(cfg.risk_config),
        )
        pf = Portfolio(initial_capital=cfg.initial_capital)
        trades: list[TradeRecord] = []
        pending_buys: list = []   # list[Candidate]

        for d in days:
            # 1. 开盘: 执行昨日产生的 pending_buys (按当日 open)
            buy_amount_day = 0.0
            exec_count = 0
            for cand in pending_buys:
                bar = self.provider.get_daily(cand.ts_code, d)
                if not bar:
                    continue
                price = bar.get('open') or bar.get('close')
                if not price:
                    continue
                # slippage: 买入溢价
                buy_price = price * (1 + cfg.slippage_rate)
                planned = self.strategy.calc_buy_amount(
                    cand, snap.cash, snap.total_assets,
                )
                if planned <= 0:
                    continue
                res = snap.plan_buy(
                    cand.ts_code, planned, price=buy_price,
                    score=cand.score, biz_date=d,
                    commission_rate=cfg.commission_rate,
                )
                if not res.ok or res.filled_shares == 0:
                    continue
                snap = res.snapshot
                trades.append(TradeRecord(
                    trade_date=d, ts_code=cand.ts_code,
                    direction='BUY', price=buy_price,
                    shares=res.filled_shares, amount=res.filled_amount,
                    reason=cand.reason,
                ))
                buy_amount_day += res.filled_amount
                exec_count += 1
            pending_buys = []

            # 2. 盘中/收盘: check_sell 每个持仓
            sell_amount_day = 0.0
            positions_today = list(snap.positions)
            for pos in positions_today:
                bar = self.provider.get_daily(pos.ts_code, d)
                if not bar:
                    continue
                close_price = bar.get('close')
                if not close_price:
                    continue
                # 把当日 close 喂给策略的 Position
                hold_days = (d - pos.open_date).days
                strat_pos = Position(
                    ts_code=pos.ts_code, shares=pos.shares,
                    cost_price=pos.cost_price, current_price=close_price,
                    open_date=pos.open_date, hold_days=hold_days,
                )
                ctx = MarketContext(as_of=d, data_provider=self.provider)
                sig = self.strategy.check_sell(strat_pos, ctx)
                if not sig:
                    continue
                sell_price = close_price * (1 - cfg.slippage_rate)
                res = snap.plan_sell(
                    pos.ts_code, price=sell_price, biz_date=d,
                    commission_rate=cfg.commission_rate,
                    stamp_rate=cfg.stamp_rate,
                )
                if not res.ok or res.filled_shares == 0:
                    continue
                snap = res.snapshot
                pnl_amt = (sell_price - pos.cost_price) * res.filled_shares
                pnl_pct = (sell_price / pos.cost_price - 1) if pos.cost_price else 0.0
                trades.append(TradeRecord(
                    trade_date=d, ts_code=pos.ts_code,
                    direction='SELL', price=sell_price,
                    shares=res.filled_shares, amount=res.filled_amount,
                    reason=sig.reason,
                    pnl_amount=pnl_amt, pnl_pct=pnl_pct,
                    hold_days=hold_days,
                ))
                sell_amount_day += res.filled_amount
                exec_count += 1

            # 3. 盘后: mark_to_market (用 close)
            close_map = {}
            for p in snap.positions:
                bar = self.provider.get_daily(p.ts_code, d)
                if bar and bar.get('close'):
                    close_map[p.ts_code] = float(bar['close'])
            pf.mark_to_market(
                d, snap, close_map,
                trade_count=exec_count,
                buy_amount=buy_amount_day,
                sell_amount=sell_amount_day,
            )
            # 把 snapshot 的 total_assets 同步到当日收盘
            snap = pf_snap_sync(snap, pf.latest_total_assets())

            # 4. 收盘后选股, 产出 T+1 买入候选
            try:
                ctx = MarketContext(as_of=d, data_provider=self.provider)
                pending_buys = self.strategy.select_stocks(ctx) or []
            except Exception as e:  # noqa: BLE001
                logger.warning(f'[backtest] select_stocks 失败 {d}: {e}')
                pending_buys = []

        return BacktestResult(
            start_date=start, end_date=end,
            initial_capital=cfg.initial_capital,
            final_assets=pf.latest_total_assets(),
            nav_series=[p.to_dict() for p in pf.nav_series],
            trades=[t.to_dict() for t in trades],
            _nav_raw=list(pf.nav_series),
            _trades_raw=list(trades),
        )


def pf_snap_sync(snap: RobotSnapshot, total_assets: float) -> RobotSnapshot:
    """把 snap.total_assets 同步为最新组合净值 (cash 不变)."""
    return replace(snap, total_assets=round(total_assets, 2))
