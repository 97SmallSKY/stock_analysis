"""STO-88: backtest_runs 持久化 + DB 失败降级测试。"""
from __future__ import annotations

import json
from datetime import date, datetime
from unittest.mock import MagicMock, patch

import pytest

from backend.api import backtest as bt_mod


@pytest.fixture
def fake_pg_conn():
    """Mock pg_conn() ContextManager,捕获 SQL execute 调用。"""
    cur = MagicMock()
    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cur
    conn.cursor.return_value.__exit__.return_value = None

    cm = MagicMock()
    cm.__enter__.return_value = conn
    cm.__exit__.return_value = None

    with patch('backend.db.session.pg_conn', return_value=cm):
        yield cur


def test_persist_run_pending_writes_correct_sql(fake_pg_conn):
    """PENDING 状态写入,finished_at 应为 NULL(finished=False)。"""
    bt_mod._persist_run(
        task_id='abc123',
        strategy_name='lhb_hot_money',
        status='PENDING',
        config={'k': 'v'},
        finished=False,
    )
    assert fake_pg_conn.execute.call_count == 1
    sql, params = fake_pg_conn.execute.call_args[0]
    assert 'INSERT INTO backtest_runs' in sql
    assert 'ON CONFLICT' in sql
    assert params[0] == 'abc123'
    assert params[1] == 'lhb_hot_money'
    assert params[2] == 'PENDING'
    assert json.loads(params[3]) == {'k': 'v'}
    assert params[4] is None  # result_json
    assert params[5] is None  # error_message
    assert params[6] is False  # finished flag


def test_persist_run_success_includes_result_json(fake_pg_conn):
    bt_mod._persist_run(
        task_id='succ001',
        strategy_name='lhb_hot_money',
        status='SUCCESS',
        config={'a': 1},
        result_json={'final_assets': 1_100_000, 'total_return': 0.1},
        finished=True,
    )
    sql, params = fake_pg_conn.execute.call_args[0]
    assert params[2] == 'SUCCESS'
    assert json.loads(params[4])['total_return'] == 0.1
    assert params[6] is True


def test_persist_run_failed_includes_error_message(fake_pg_conn):
    bt_mod._persist_run(
        task_id='fail001',
        strategy_name='bad',
        status='FAILED',
        config={},
        error_message='boom',
        finished=True,
    )
    _, params = fake_pg_conn.execute.call_args[0]
    assert params[2] == 'FAILED'
    assert params[5] == 'boom'


def test_persist_run_db_failure_degrades_silently(caplog):
    """DB 不可用时不应抛异常,只 warning。"""
    with patch(
        'backend.db.session.pg_conn',
        side_effect=RuntimeError('db down'),
    ):
        bt_mod._persist_run(
            task_id='x',
            strategy_name='s',
            status='PENDING',
            config={},
        )
    assert any(
        'persist run failed' in r.message for r in caplog.records
    )


def test_submit_persists_pending_row(fake_pg_conn, monkeypatch):
    """端到端:POST /api/backtest 应触发一次 PENDING insert。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    bt_mod._store = bt_mod.TaskStore()

    # 跳过 require_api_key + write_limit 这种装饰器对路由的影响
    app = FastAPI()
    app.include_router(bt_mod.router, prefix='/api/backtest')

    monkeypatch.setattr(bt_mod, '_runner', lambda task, store: None)

    client = TestClient(app)
    r = client.post('/api/backtest', json={
        'strategy_name': 'lhb_hot_money',
        'start_date': '2024-01-02',
        'end_date': '2024-01-05',
        'initial_capital': 1_000_000,
    })
    assert r.status_code == 200, r.text

    # submit 至少触发 1 次 INSERT(PENDING)
    assert fake_pg_conn.execute.call_count >= 1
    insert_calls = [
        c for c in fake_pg_conn.execute.call_args_list
        if 'INSERT INTO backtest_runs' in c[0][0]
    ]
    assert len(insert_calls) >= 1
    # 第一个 INSERT 是 PENDING
    assert insert_calls[0][0][1][2] == 'PENDING'
