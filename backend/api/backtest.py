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

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from backend.core.auth import require_api_key
from backend.core.rate_limit import default_limit, write_limit

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


def _persist_run(
    task_id: str,
    strategy_name: str,
    status: str,
    config: dict,
    result_json: Optional[dict] = None,
    error_message: Optional[str] = None,
    finished: bool = False,
) -> None:
    """STO-88: 把回测任务写入 backtest_runs(append-only audit log)。

    DB 不可用不影响回测主流程,失败仅记录 warning。
    """
    import json
    try:
        from backend.db.session import pg_conn
        with pg_conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO backtest_runs
                  (task_id, strategy_name, status, config,
                   result_json, error_message, finished_at)
                VALUES (%s, %s, %s, %s::jsonb, %s::jsonb, %s,
                  CASE WHEN %s THEN NOW() ELSE NULL END)
                ON CONFLICT (task_id) DO UPDATE SET
                  status = EXCLUDED.status,
                  result_json = EXCLUDED.result_json,
                  error_message = EXCLUDED.error_message,
                  finished_at = COALESCE(
                    EXCLUDED.finished_at, backtest_runs.finished_at)
                """,
                (
                    task_id, strategy_name, status,
                    json.dumps(config),
                    json.dumps(result_json) if result_json else None,
                    error_message,
                    finished,
                ),
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            '[backtest] persist run failed task=%s status=%s err=%s',
            task_id, status, exc)


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
                'trades_sample': result.trades[:50],
            },
            metrics=asdict(metrics),
        )
        _persist_run(
            task_id=task.task_id,
            strategy_name=task.strategy_name,
            status='SUCCESS',
            config={
                'start_date': task.start_date.isoformat(),
                'end_date': task.end_date.isoformat(),
                'initial_capital': task.initial_capital,
                'params': task.params,
            },
            result_json={
                'final_assets': result.final_assets,
                'total_return': result.total_return,
                'trade_count': len(result.trades),
                'metrics': asdict(metrics),
            },
            finished=True,
        )
    except Exception as e:  # noqa: BLE001
        logger.exception(f'[backtest] task={task.task_id} failed')
        store.update(
            task.task_id,
            status='FAILED', error_message=str(e),
            finished_at=datetime.now(),
        )
        _persist_run(
            task_id=task.task_id,
            strategy_name=task.strategy_name,
            status='FAILED',
            config={
                'start_date': task.start_date.isoformat(),
                'end_date': task.end_date.isoformat(),
                'initial_capital': task.initial_capital,
                'params': task.params,
            },
            error_message=str(e),
            finished=True,
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
                main = [
                    {'ts_code': r[0], 'score': float(r[1] or 0),
                     'close_price': float(r[2]) if r[2] else None,
                     'reason': r[3], 'lhb_daily_id': r[4], 'seats': []}
                    for r in cur.fetchall()
                ]
                if not main:
                    return []
                # 批量拉当日 seats,按 ts_code 分桶
                cur.execute(
                    'SELECT ts_code, broker_name, broker_type, direction, '
                    'buy_amount, sell_amount, net_amount, is_institution '
                    'FROM lhb_seats WHERE trade_date=%s', (as_of,),
                )
                seats_by: dict[str, list] = {}
                for s in cur.fetchall():
                    seats_by.setdefault(s[0], []).append({
                        'broker_name': s[1],
                        'broker_type': s[2],
                        'direction': s[3],
                        'buy_amount': float(s[4]) if s[4] is not None else 0.0,
                        'sell_amount': float(s[5]) if s[5] is not None else 0.0,
                        'net_amount': float(s[6]) if s[6] is not None else 0.0,
                        'is_institution': bool(s[7]) if s[7] is not None else False,
                    })
                for rec in main:
                    rec['seats'] = seats_by.get(rec['ts_code'], [])
                return main

    return _Provider()


_runner = _default_runner


def set_runner(fn) -> None:
    """测试点: 替换执行器."""
    global _runner
    _runner = fn


# ====================================================================
# 路由
# ====================================================================


@router.post('', response_model=SubmitResp,
             dependencies=[Depends(require_api_key)])
@write_limit
def submit_backtest(
    req: SubmitReq, bg: BackgroundTasks, request: Request,
) -> SubmitResp:
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
    _persist_run(
        task_id=task.task_id,
        strategy_name=task.strategy_name,
        status='PENDING',
        config={
            'start_date': task.start_date.isoformat(),
            'end_date': task.end_date.isoformat(),
            'initial_capital': task.initial_capital,
            'params': task.params,
        },
    )
    bg.add_task(_runner, task, get_store())
    return SubmitResp(task_id=task.task_id, status=task.status)


@router.get('/strategies')
@default_limit
def list_strategies_ep(request: Request):
    """返回已注册策略的富信息 (STO-47)。

    从 {'strategies': ['lhb_hot_money', ...]} 升级为
    {'strategies': [{name, category, description, default_params}, ...]}

    前端策略对比页用这些字段做中文展示 + hover tooltip。
    与 /api/robots/strategies 返回结构对齐。
    """
    # 本地 import 触发装饰器注册(registry 是模块级 dict)
    import backend.domain.strategy.builtins  # noqa: F401
    from backend.domain.strategy.registry import _REGISTRY, list_strategies

    out = []
    for name in list_strategies():
        cls = _REGISTRY[name]
        out.append({
            'name': name,
            'category': getattr(cls, 'category', ''),
            'description': getattr(cls, 'description', ''),
            'default_params': dict(getattr(cls, 'DEFAULT_PARAMS', {}) or {}),
        })
    return {'strategies': out}


@router.get('', response_model=list[TaskResp])
@default_limit
def list_tasks(request: Request, limit: int = 50):
    return [
        TaskResp(**t.to_dict()) for t in get_store().list(limit=limit)
    ]


@router.get('/history')
@default_limit
def list_history(
    request: Request,
    limit: int = 200,
    status: Optional[str] = None,
    strategy_name: Optional[str] = None,
):
    """P7.1 历史任务摘要: 在 /api/backtest 基础上补 metrics 摘要 + 过滤.

    STO-118: 合并 in-memory TaskStore + DB backtest_runs 两个来源。

    每行返回字段 = TaskResp + {
        total_return, sharpe, max_drawdown, n_trades, win_rate
    } (若任务未完成, 这些字段为 None).
    """
    # 1. 从 in-memory TaskStore 读取（运行中 + 最近完成的）
    items = get_store().list(limit=limit * 4)
    out: list[dict] = []
    seen_task_ids = set()
    for t in items:
        if status and t.status != status:
            continue
        if strategy_name and t.strategy_name != strategy_name:
            continue
        d = t.to_dict()
        # STO-124: compare- 任务 t.result 是 ComparisonReport，t.metrics 为空
        # 从 result['runs'] 提取 winner metrics
        if t.task_id.startswith('compare-') and t.result and 'runs' in t.result:
            # STO-124 fix: compare- 任务直接从 runs[] 最优 ok run 取字段名正确的指标
            ok_runs = [r for r in t.result.get('runs', []) if r.get('ok')]
            best = max(ok_runs, key=lambda r: r.get('total_return', -999), default=None)
            if best:
                d['total_return'] = best.get('total_return')
                d['sharpe'] = best.get('sharpe_ratio')  # 字段名映射
                d['max_drawdown'] = best.get('max_drawdown')
                d['n_trades'] = best.get('trade_count')  # 字段名映射
                d['win_rate'] = best.get('win_rate')
            else:
                d['total_return'] = d['sharpe'] = d['max_drawdown'] = d['n_trades'] = d['win_rate'] = None
        else:
            m = t.metrics or {}
            d['total_return'] = m.get('total_return')
            d['sharpe'] = m.get('sharpe') or m.get('sharpe_ratio')
            d['max_drawdown'] = m.get('max_drawdown')
            d['n_trades'] = m.get('n_trades') or m.get('trade_count')
            d['win_rate'] = m.get('win_rate')
        out.append(d)
        seen_task_ids.add(t.task_id)
        if len(out) >= limit:
            break

    # 2. 从 DB backtest_runs 补充历史记录（去重）
    if len(out) < limit:
        try:
            from backend.db.session import pg_conn
            with pg_conn() as conn:
                with conn.cursor() as cur:
                    where = ['1=1']
                    params = []
                    if status:
                        where.append('status = %s')
                        params.append(status)
                    if strategy_name:
                        where.append('strategy_name = %s')
                        params.append(strategy_name)
                    sql = f"""
                        SELECT task_id, strategy_name, status, config, result_json,
                               error_message, created_at, finished_at
                        FROM backtest_runs
                        WHERE {' AND '.join(where)}
                        ORDER BY created_at DESC
                        LIMIT %s
                    """
                    params.append(limit * 2)
                    cur.execute(sql, params)
                    for row in cur.fetchall():
                        task_id = row[0]
                        if task_id in seen_task_ids:
                            continue
                        seen_task_ids.add(task_id)
                        result = row[4] or {}
                        config = row[3] or {}
                        # STO-124: compare- 任务 result_json 是 ComparisonReport.to_dict()
                        # 含 runs[] 而非 metrics{}，从 winner 或第一个 run 提取 metrics
                        base_row = {
                            'task_id': task_id,
                            'strategy_name': row[1],
                            'status': row[2],
                            'config': config,
                            'start_date': config.get('start_date'),
                            'end_date': config.get('end_date'),
                            'result': result,
                            'error_message': row[5],
                            'created_at': row[6].isoformat() if row[6] else None,
                            'finished_at': row[7].isoformat() if row[7] else None,
                        }
                        if task_id.startswith('compare-') and 'runs' in result:
                            # STO-124 fix: compare- 任务从 runs[] 中直接取最优 ok run
                            ok_runs = [r for r in result.get('runs', []) if r.get('ok')]
                            best = max(ok_runs, key=lambda r: r.get('total_return', -999), default=None)
                            if best:
                                base_row['total_return'] = best.get('total_return')
                                base_row['sharpe'] = best.get('sharpe_ratio')  # 字段名映射
                                base_row['max_drawdown'] = best.get('max_drawdown')
                                base_row['n_trades'] = best.get('trade_count')  # 字段名映射
                                base_row['win_rate'] = best.get('win_rate')
                            else:
                                base_row.update({'total_return': None, 'sharpe': None,
                                                 'max_drawdown': None, 'n_trades': None, 'win_rate': None})
                        else:
                            metrics = result.get('metrics', {})
                            base_row['total_return'] = metrics.get('total_return')
                            base_row['sharpe'] = metrics.get('sharpe') or metrics.get('sharpe_ratio')
                            base_row['max_drawdown'] = metrics.get('max_drawdown')
                            base_row['n_trades'] = metrics.get('n_trades') or metrics.get('trade_count')
                            base_row['win_rate'] = metrics.get('win_rate')
                        out.append(base_row)
                        if len(out) >= limit:
                            break
        except Exception as exc:  # noqa: BLE001
            logger.warning('[backtest] list_history DB query failed: %s', exc)

    return {'total': len(out), 'rows': out}


@router.get('/{task_id}')
@default_limit
def get_task(task_id: str, request: Request):
    t = get_store().get(task_id)
    if t is not None:
        d = t.to_dict()
        d['result'] = t.result
        d['metrics'] = t.metrics
        return d

    # STO-125: in-memory 找不到时回退查 DB（compare-* 等持久化但未重载到内存的任务）
    try:
        from backend.db.session import pg_conn
        with pg_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT task_id, strategy_name, status, config, result_json,"
                    " error_message, created_at, finished_at"
                    " FROM backtest_runs WHERE task_id = %s",
                    (task_id,),
                )
                row = cur.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail='task not found')
        config = row[3] or {}
        result = row[4] or {}
        metrics = result.get('metrics', {})
        return {
            'task_id': row[0],
            'strategy_name': row[1],
            'status': row[2],
            'config': config,
            'start_date': config.get('start_date'),
            'end_date': config.get('end_date'),
            'result': result,
            'error_message': row[5],
            'created_at': row[6].isoformat() if row[6] else None,
            'finished_at': row[7].isoformat() if row[7] else None,
            'metrics': metrics,
            'total_return': metrics.get('total_return'),
            'sharpe': metrics.get('sharpe') or metrics.get('sharpe_ratio'),
            'max_drawdown': metrics.get('max_drawdown'),
            'n_trades': metrics.get('n_trades') or metrics.get('trade_count'),
            'win_rate': metrics.get('win_rate'),
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning('[backtest] get_task DB fallback failed task=%s err=%s', task_id, exc)
        raise HTTPException(status_code=404, detail='task not found')


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


@router.post('/compare', dependencies=[Depends(require_api_key)])
@write_limit
def compare_endpoint(req: CompareReq, request: Request):
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

    # STO-118: 持久化对比任务到 backtest_runs
    import uuid
    task_id = f"compare-{uuid.uuid4().hex[:8]}"
    strategy_names = ",".join(s.name for s in req.strategies)
    _persist_run(
        task_id=task_id,
        strategy_name=strategy_names,
        status='SUCCESS',
        config={
            'start_date': req.start_date.isoformat(),
            'end_date': req.end_date.isoformat(),
            'initial_capital': req.initial_capital,
            'strategies': [{'name': s.name, 'strategy_name': s.strategy_name, 'params': s.params} for s in req.strategies],
            'max_positions': req.max_positions,
            'per_position_pct': req.per_position_pct,
        },
        result_json=rep.to_dict(),
        finished=True,
    )

    return rep.to_dict()
