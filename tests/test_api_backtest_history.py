"""P7.1 /api/backtest/history 接口单测.

TaskStore 是进程内单例, 直接往里塞任务再请求, 不需要 DB.
"""
from __future__ import annotations

import datetime as dt

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.backtest import (
    BacktestTask, get_store, router as bt_router,
)


def _mount() -> TestClient:
    app = FastAPI()
    app.include_router(bt_router, prefix="/api/backtest")
    return TestClient(app)


def _reset_store():
    s = get_store()
    with s._lock:
        s._tasks.clear()


def _seed(
    task_id: str, strategy: str = 'lhb_hot_money', status: str = 'SUCCESS',
    metrics: dict | None = None,
):
    t = BacktestTask(
        task_id=task_id,
        strategy_name=strategy,
        start_date=dt.date(2026, 1, 1),
        end_date=dt.date(2026, 4, 1),
        initial_capital=1_000_000,
    )
    t.status = status
    t.started_at = dt.datetime(2026, 4, 18, 10, 0, 0)
    t.finished_at = dt.datetime(2026, 4, 18, 10, 5, 0)
    t.elapsed_seconds = 300.0
    t.metrics = metrics
    get_store()._tasks[task_id] = t
    return t


def test_history_basic():
    _reset_store()
    _seed('t1', metrics={
        'total_return': 0.12, 'sharpe': 1.1, 'max_drawdown': -0.08,
        'n_trades': 40, 'win_rate': 0.55,
    })
    _seed('t2', strategy='lhb_institution', metrics={
        'total_return': -0.02, 'sharpe': -0.3, 'max_drawdown': -0.15,
        'n_trades': 10, 'win_rate': 0.3,
    })

    c = _mount()
    r = c.get('/api/backtest/history')
    assert r.status_code == 200
    body = r.json()
    assert body['total'] == 2
    ids = {row['task_id'] for row in body['rows']}
    assert ids == {'t1', 't2'}
    row = next(r for r in body['rows'] if r['task_id'] == 't1')
    assert row['total_return'] == 0.12
    assert row['sharpe'] == 1.1
    assert row['n_trades'] == 40
    assert row['status'] == 'SUCCESS'
    assert row['strategy_name'] == 'lhb_hot_money'


def test_history_filter_by_status():
    _reset_store()
    _seed('ok1', status='SUCCESS')
    _seed('bad1', status='FAILED')
    c = _mount()
    r = c.get('/api/backtest/history?status=FAILED')
    assert r.status_code == 200
    rows = r.json()['rows']
    assert len(rows) == 1 and rows[0]['task_id'] == 'bad1'


def test_history_filter_by_strategy():
    _reset_store()
    _seed('a', strategy='lhb_hot_money')
    _seed('b', strategy='lhb_institution')
    c = _mount()
    r = c.get('/api/backtest/history?strategy_name=lhb_institution')
    rows = r.json()['rows']
    assert len(rows) == 1 and rows[0]['task_id'] == 'b'


def test_history_metrics_none_when_running():
    _reset_store()
    _seed('run1', status='RUNNING', metrics=None)
    c = _mount()
    rows = c.get('/api/backtest/history').json()['rows']
    row = next(r for r in rows if r['task_id'] == 'run1')
    assert row['total_return'] is None
    assert row['sharpe'] is None
    assert row['n_trades'] is None


def test_history_limit():
    _reset_store()
    for i in range(5):
        _seed(f't{i}')
    c = _mount()
    body = c.get('/api/backtest/history?limit=2').json()
    assert body['total'] == 2
    assert len(body['rows']) == 2
