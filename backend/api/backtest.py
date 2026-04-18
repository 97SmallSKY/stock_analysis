"""回测管理 API.

端点:
    POST   /api/backtest           提交回测任务 (BackgroundTasks 异步执行)
    GET    /api/backtest/{task_id} 查询任务 + 结果
    GET    /api/backtest           任务列表 (最近 N)
    GET    /api/backtest/strategies 已注册策略列表

当前实现用进程内 dict 作为 task store, 不依赖 Celery (W6-3 首期).
真实 DB 落库走 backtest_tasks / backtest_results 表的逻辑由上层完成,
这里保留 `_save_to_db` 钩子供后续接入.
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Any, Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter()


# ====================================================================
# 内存任务存储
# ====================================================================


@dataclass
class BacktestTask:
    task_id: str
    strategy_name: str
    start_date: date
    end_date: date
    initial_capital: float
    params: dict = field(default_factory=dict)
    status: str = 'PENDING'          # PENDING / RUNNING / SUCCESS / FAILED
    progress: int = 0
    error_message: Optional[str] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    elapsed_seconds: Optional[float] = None
    result: Optional[dict] = None
    metrics: Optional[dict] = None

    def to_dict(self) -> dict:
        d = {
            'task_id': self.task_id,
            'strategy_name': self.strategy_name,
            'start_date': self.start_date.isoformat(),
            'end_date': self.end_date.isoformat(),
            'initial_capital': self.initial_capital,
            'params': self.params,
            'status': self.status,
            'progress': self.progress,
            'error_message': self.error_message,
            'started_at': (
                self.started_at.isoformat() if self.started_at else None
            ),
            'finished_at': (
                self.finished_at.isoformat() if self.finished_at else None
            ),
            'elapsed_seconds': self.elapsed_seconds,
        }
        return d


class TaskStore:
    """进程内任务表."""
    def __init__(self):
        self._tasks: dict[str, BacktestTask] = {}
        self._lock = threading.Lock()

    def create(self, **kw) -> BacktestTask:
        tid = kw.pop('task_id', None) or uuid.uuid4().hex[:16]
        task = BacktestTask(task_id=tid, **kw)
        with self._lock:
            self._tasks[tid] = task
        return task

    def get(self, task_id: str) -> Optional[BacktestTask]:
        with self._lock:
            return self._tasks.get(task_id)

    def list(self, limit: int = 50) -> list[BacktestTask]:
        with self._lock:
            arr = list(self._tasks.values())
        arr.sort(key=lambda t: t.started_at or datetime.min, reverse=True)
        return arr[:limit]

    def update(self, task_id: str, **kw) -> None:
        with self._lock:
            t = self._tasks.get(task_id)
            if not t:
                return
            for k, v in kw.items():
                setattr(t, k, v)


_store = TaskStore()


def get_store() -> TaskStore:
    """依赖注入点, 测试里可替换."""
    return _store


# ====================================================================
# 请求 / 响应 schema
# ====================================================================


class SubmitReq(BaseModel):
    strategy_name: str = Field(..., description='已注册的策略名')
    start_date: date
    end_date: date
    initial_capital: float = 1_000_000.0
    params: dict[str, Any] = Field(default_factory=dict)
    max_positions: int = 5
    per_position_pct: float = 0.20
    commission_rate: float = 0.0003
    slippage_rate: float = 0.001
    stamp_rate: float = 0.001


class SubmitResp(BaseModel):
    task_id: str
    status: str


class TaskResp(BaseModel):
    task_id: str
    strategy_name: str
    start_date: str
    end_date: str
    initial_capital: float
    params: dict
    status: str
    progress: int
    error_message: Optional[str]
    started_at: Optional[str]
    finished_at: Optional[str]
    elapsed_seconds: Optional[float]


# ====================================================================
# 回测执行 (可注入, 便于测试)
# ====================================================================


def _default_runner(task: BacktestTask, store: TaskStore) -> None:
    """实际跑回测的函数. 走 BacktestEngine + 默认 DB provider."""
    from backend.domain.backtest import BacktestEngine, BacktestConfig
    from backend.domain.backtest.report import compute_metrics
    from backend.domain.strategy import get_strategy
    # builtins import 触发注册
    import backend.domain.strategy.builtins  # noqa: F401

    try:
        store.update(task.task_id, status='RUNNING',
                     started_at=datetime.now())
        cls = get_strategy(task.strategy_name)
        strat = cls(params=task.params or {})
        cfg = BacktestConfig(
            initial_capital=task.initial_capital,
        )
        provider = _make_db_provider()
        engine = BacktestEngine(strat, provider, cfg)
        result = engine.run(task.start_date, task.end_date)
        metrics = compute_metrics(result)
        store.update(
            task.task_id,
            status='SUCCESS',
            progress=100,
            finished_at=datetime.now(),
            elapsed_seconds=(
                (datetime.now() - (task.started_at or datetime.now())).total_seconds()
            ),
            result={
                'final_assets': result.final_assets,
                'total_return': result.total_return,
                'trade_count': len(result.trades),
                'nav_series': result.nav_series[-100:],  # 仅保留尾部
            },
            metrics=asdict(metrics),
        )
    except Exception as e:  # noqa: BLE001
        logger.exception(f'[backtest] task={task.task_id} failed')
        store.update(
            task.task_id,
            status='FAILED', error_message=str(e),
            finished_at=datetime.now(),
        )


def _make_db_provider():
    """构造一个从 DB 读 K 线 / 日历 / 龙虎榜的 DailyProvider.

    这个函数在真实服务里才会被调用; 单元测试会注入 fake runner 绕过.
    """
    from backend.db.session import pg_conn

    class _Provider:
        def list_trade_days(self, start, end):
            with pg_conn() as conn, conn.cursor() as cur:
                cur.execute(
                    'SELECT cal_date FROM trade_calendar '
                    "WHERE exchange='SSE' AND is_open=TRUE "
                    'AND cal_date BETWEEN %s AND %s ORDER BY cal_date',
                    (start, end),
                )
                return [r[0] for r in cur.fetchall()]

        def get_daily(self, ts_code, trade_date):
            with pg_conn() as conn, conn.cursor() as cur:
                cur.execute(
                    'SELECT open, high, low, close FROM kline_daily '
                    'WHERE ts_code=%s AND trade_date=%s',
                    (ts_code, trade_date),
                )
                r = cur.fetchone()
                if not r:
                    return None
                return {'open': float(r[0]) if r[0] else None,
                        'high': float(r[1]) if r[1] else None,
                        'low': float(r[2]) if r[2] else None,
                        'close': float(r[3]) if r[3] else None}

        def list_lhb_records(self, as_of):
            with pg_conn() as conn, conn.cursor() as cur:
                cur.execute(
                    'SELECT ts_code, score, close_price, reason, id '
                    'FROM lhb_stock_daily WHERE trade_date=%s',
                    (as_of,),
                )
                return [
                    {'ts_code': r[0], 'score': float(r[1] or 0),
                     'close_price': float(r[2]) if r[2] else None,
                     'reason': r[3], 'lhb_daily_id': r[4], 'seats': []}
                    for r in cur.fetchall()
                ]

    return _Provider()


_runner = _default_runner


def set_runner(fn) -> None:
    """测试点: 替换执行器."""
    global _runner
    _runner = fn


# ====================================================================
# 路由
# ====================================================================


@router.post('', response_model=SubmitResp)
def submit_backtest(req: SubmitReq, bg: BackgroundTasks) -> SubmitResp:
    if req.end_date < req.start_date:
        raise HTTPException(status_code=400, detail='end_date < start_date')
    task = get_store().create(
        strategy_name=req.strategy_name,
        start_date=req.start_date,
        end_date=req.end_date,
        initial_capital=req.initial_capital,
        params={
            **req.params,
            'max_positions': req.max_positions,
            'per_position_pct': req.per_position_pct,
        },
    )
    bg.add_task(_runner, task, get_store())
    return SubmitResp(task_id=task.task_id, status=task.status)


@router.get('/strategies')
def list_strategies_ep():
    from backend.domain.strategy import list_strategies
    import backend.domain.strategy.builtins  # noqa: F401
    return {'strategies': list_strategies()}


@router.get('', response_model=list[TaskResp])
def list_tasks(limit: int = 50):
    return [
        TaskResp(**t.to_dict()) for t in get_store().list(limit=limit)
    ]


@router.get('/{task_id}')
def get_task(task_id: str):
    t = get_store().get(task_id)
    if t is None:
        raise HTTPException(status_code=404, detail='task not found')
    d = t.to_dict()
    d['result'] = t.result
    d['metrics'] = t.metrics
    return d


# ====================================================================
# W10-2 compare: 并行对比多个策略
# ====================================================================


class CompareStrategy(BaseModel):
    name: str
    strategy_name: str                 # 已注册策略名
    params: dict = Field(default_factory=dict)


class CompareReq(BaseModel):
    start_date: date
    end_date: date
    initial_capital: float = 1_000_000
    strategies: list[CompareStrategy] = Field(..., min_length=1)
    max_positions: int = 5
    per_position_pct: float = 0.20
    max_workers: int = 4


_compare_provider_factory = _make_db_provider  # 测试点: 替换为 fake provider 工厂


def set_compare_provider_factory(fn) -> None:
    global _compare_provider_factory
    _compare_provider_factory = fn


@router.post('/compare')
def compare_endpoint(req: CompareReq):
    if req.end_date < req.start_date:
        raise HTTPException(400, 'end_date < start_date')
    # 懒导入, 避免启动时 strategy 注册表未就绪
    from backend.domain.strategy import get_strategy
    import backend.domain.strategy.builtins  # noqa: F401
    from backend.domain.backtest.compare import run_compare
    from backend.domain.backtest.engine import BacktestConfig

    strategies = []
    for s in req.strategies:
        try:
            cls = get_strategy(s.strategy_name)
        except Exception as e:
            raise HTTPException(400, f'未知策略 {s.strategy_name}: {e}')
        params = {
            **s.params,
            'max_positions': req.max_positions,
            'per_position_pct': req.per_position_pct,
        }
        strategies.append((s.name, cls(params=params)))

    provider = _compare_provider_factory()
    cfg = BacktestConfig(
        initial_capital=req.initial_capital,
        max_positions=req.max_positions,
        per_position_pct=req.per_position_pct,
    )
    rep = run_compare(
        strategies, provider, req.start_date, req.end_date,
        config=cfg, max_workers=req.max_workers,
    )
    return rep.to_dict()
