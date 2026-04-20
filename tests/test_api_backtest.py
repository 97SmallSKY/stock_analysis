"""/api/backtest 路由测试 (TestClient + 假 runner, 不起真实 DB)."""
from __future__ import annotations

from datetime import date, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api import backtest as bt_mod


@pytest.fixture
def app_with_bt():
    """最小 FastAPI, 只挂 backtest 路由, 避免 bootstrap 依赖."""
    # 重置进程内 task store (前一个 test 残留)
    bt_mod._store = bt_mod.TaskStore()

    app = FastAPI()
    app.include_router(bt_mod.router, prefix='/api/backtest')

    # 注入假 runner: 同步完成, 记录调用参数
    called = {}

    def fake_runner(task, store):
        called['task_id'] = task.task_id
        called['strategy'] = task.strategy_name
        store.update(
            task.task_id,
            status='SUCCESS', progress=100,
            started_at=datetime.now(), finished_at=datetime.now(),
            elapsed_seconds=0.01,
            result={'final_assets': 1_100_000,
                    'total_return': 0.1,
                    'trade_count': 3,
                    'nav_series': [{'date': '2024-01-02', 'nav': 1.1}]},
            metrics={'sharpe_ratio': 1.2, 'max_drawdown': -0.05},
        )

    bt_mod.set_runner(fake_runner)
    yield TestClient(app), called
    # 复原
    bt_mod.set_runner(bt_mod._default_runner)


def test_submit_then_get(app_with_bt):
    client, called = app_with_bt
    payload = {
        'strategy_name': 'lhb_hot_money',
        'start_date': '2024-01-02',
        'end_date': '2024-01-10',
        'initial_capital': 1_000_000,
        'params': {'score_threshold': 70},
    }
    r = client.post('/api/backtest', json=payload)
    assert r.status_code == 200
    body = r.json()
    assert body['status'] in ('SUCCESS', 'PENDING')   # BG 同步执行后可能已成功
    tid = body['task_id']

    # runner 应被调用
    assert called.get('task_id') == tid
    assert called.get('strategy') == 'lhb_hot_money'

    r2 = client.get(f'/api/backtest/{tid}')
    assert r2.status_code == 200
    body2 = r2.json()
    assert body2['task_id'] == tid
    assert body2['status'] == 'SUCCESS'
    assert body2['result']['final_assets'] == 1_100_000
    assert body2['metrics']['sharpe_ratio'] == 1.2


def test_reject_bad_dates(app_with_bt):
    client, _ = app_with_bt
    r = client.post('/api/backtest', json={
        'strategy_name': 'x', 'start_date': '2024-05-01',
        'end_date': '2024-04-01',
    })
    assert r.status_code == 400


def test_get_404(app_with_bt):
    client, _ = app_with_bt
    r = client.get('/api/backtest/not-exist')
    assert r.status_code == 404


def test_list_tasks(app_with_bt):
    client, _ = app_with_bt
    # 先提交两个
    for i in range(2):
        client.post('/api/backtest', json={
            'strategy_name': 'lhb_hot_money',
            'start_date': '2024-01-02',
            'end_date': '2024-01-10',
        })
    r = client.get('/api/backtest?limit=10')
    assert r.status_code == 200
    arr = r.json()
    assert len(arr) == 2
    # 返回字段齐全
    assert {'task_id', 'strategy_name', 'status'} <= set(arr[0].keys())


def test_list_strategies(app_with_bt):
    """STO-47: 端点升级为 [{name, category, description, default_params}]。"""
    client, _ = app_with_bt
    r = client.get('/api/backtest/strategies')
    assert r.status_code == 200
    arr = r.json()['strategies']
    # 结构校验:富对象数组
    assert all(isinstance(s, dict) for s in arr)
    assert all(
        {'name', 'category', 'description', 'default_params'} <= set(s.keys())
        for s in arr
    )
    # 内置两个至少在(按 name)
    names = [s['name'] for s in arr]
    assert 'lhb_hot_money' in names
    assert 'lhb_institution' in names
    # lhb_hot_money 应有非空 description + category
    hot = next(s for s in arr if s['name'] == 'lhb_hot_money')
    assert hot['description'], 'lhb_hot_money 的 description 不应为空'
    assert hot['category']


def test_task_params_merged(app_with_bt):
    client, called = app_with_bt
    client.post('/api/backtest', json={
        'strategy_name': 'lhb_hot_money',
        'start_date': '2024-01-02',
        'end_date': '2024-01-10',
        'params': {'score_threshold': 85},
        'max_positions': 8,
        'per_position_pct': 0.1,
    })
    # 取到 store 里的 task 看 params 融合
    t = bt_mod.get_store().list()[0]
    assert t.params['score_threshold'] == 85
    assert t.params['max_positions'] == 8
    assert t.params['per_position_pct'] == 0.1
