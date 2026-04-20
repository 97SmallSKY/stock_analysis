"""STO-43: /api/lhb 响应缓存单测。

覆盖:
1. 同参数两次请求,第二次不打 DB(HIT)—— 通过统计 pg_conn 调用次数确认
2. 参数不同不命中(MISS)—— 两次都打 DB
3. skip_total 作为 key 的一部分,改变后不复用缓存
4. TTL 过期后重新打 DB
5. 工具函数 _ttl_until_daily_flush / _stable_key 独立用例
"""
from __future__ import annotations

import datetime as dt
import time
from contextlib import contextmanager

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


# -------------------------------------------------------------------
# Fake pg_conn: 和 test_api_lhb_dashboard.py 同一个模式,但加上计数
# -------------------------------------------------------------------


class _FakeCursor:
    def __init__(self, queue):
        self._queue = list(queue)
        self._current_rows: list = []
        self.description = []

    def execute(self, *a, **k):
        if not self._queue:
            self._current_rows = []
            self.description = []
            return
        rows, cols = self._queue.pop(0)
        self._current_rows = list(rows)
        self.description = [type("C", (), {"name": n}) for n in cols]

    def fetchone(self):
        return self._current_rows[0] if self._current_rows else None

    def fetchall(self):
        return self._current_rows

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeConn:
    def __init__(self, queue):
        self._cursor = _FakeCursor(queue)

    def cursor(self):
        return self._cursor

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def commit(self):
        pass


def _make_counting_pg_conn(canned_list):
    """返回 (fake_fn, counter_dict) —— counter['n'] 记录 pg_conn 真正被调用次数。"""
    counter = {"n": 0}

    @contextmanager
    def fake():
        idx = counter["n"]
        counter["n"] += 1
        if idx >= len(canned_list):
            raise AssertionError(
                f"pg_conn 调用超出预设: idx={idx}, 预设={len(canned_list)}"
            )
        conn = _FakeConn(canned_list[idx])
        yield conn

    return fake, counter


# -------------------------------------------------------------------
# fixture: 每个测试独立 app + 清空缓存
# -------------------------------------------------------------------


@pytest.fixture
def lhb_app(monkeypatch):
    """返回 (client, counter, cache),每次测试都是干净缓存。"""
    from backend.api import lhb as lhb_mod
    # 清空单例缓存 + 统计
    lhb_mod._lhb_cache.clear()
    lhb_mod._lhb_cache.hits = 0
    lhb_mod._lhb_cache.misses = 0
    lhb_mod._lhb_cache.bypasses = 0

    app = FastAPI()
    app.include_router(lhb_mod.router, prefix="/api/lhb")
    client = TestClient(app)
    yield client, lhb_mod
    lhb_mod._lhb_cache.clear()


# ====================================================================
# 场景 1: 同参数两次,第二次 HIT(不打 DB)
# ====================================================================


def test_response_cache_hit_same_params(lhb_app, monkeypatch):
    client, lhb_mod = lhb_app
    rows = [
        ("000001.SZ", "平安银行", dt.date(2026, 4, 18), "日振幅值达到7%",
         1000.0, 500.0, 500.0, 3000.0, 8.5, {"x": 1}),
    ]
    cols = ["ts_code", "name", "trade_date", "reason",
            "buy_amount", "sell_amount", "net_amount", "turnover",
            "score", "score_detail"]
    # 只预设一次 DB 调用 —— 如果第二次也打 DB 会 AssertionError
    canned = [[([(1,)], ["count"]), (rows, cols)]]
    fake_pg, counter = _make_counting_pg_conn(canned)
    monkeypatch.setattr("backend.api.lhb.pg_conn", fake_pg)

    # 第 1 次:MISS,打 DB
    r1 = client.get("/api/lhb?date_from=20260418&limit=10")
    assert r1.status_code == 200, r1.text
    assert r1.json()["total"] == 1
    assert counter["n"] == 1
    assert lhb_mod._lhb_cache.misses == 1
    assert lhb_mod._lhb_cache.hits == 0

    # 第 2 次:同参数,HIT,不打 DB
    r2 = client.get("/api/lhb?date_from=20260418&limit=10")
    assert r2.status_code == 200
    assert r2.json() == r1.json()  # 结果完全一致
    assert counter["n"] == 1  # 关键断言:没新增 pg_conn 调用
    assert lhb_mod._lhb_cache.hits == 1


# ====================================================================
# 场景 2: 参数不同 → MISS,都打 DB
# ====================================================================


def test_response_cache_params_differ_miss(lhb_app, monkeypatch):
    client, lhb_mod = lhb_app
    rows = [
        ("000001.SZ", "平安银行", dt.date(2026, 4, 18), "R",
         1.0, 1.0, 1.0, 1.0, 1.0, None),
    ]
    cols = ["ts_code", "name", "trade_date", "reason",
            "buy_amount", "sell_amount", "net_amount", "turnover",
            "score", "score_detail"]
    # 预设两次独立 DB 调用
    canned = [
        [([(1,)], ["count"]), (rows, cols)],
        [([(1,)], ["count"]), (rows, cols)],
    ]
    fake_pg, counter = _make_counting_pg_conn(canned)
    monkeypatch.setattr("backend.api.lhb.pg_conn", fake_pg)

    client.get("/api/lhb?date_from=20260418&limit=10")
    client.get("/api/lhb?date_from=20260418&limit=20")  # limit 不同

    assert counter["n"] == 2  # 两次都打了 DB
    assert lhb_mod._lhb_cache.misses == 2
    assert lhb_mod._lhb_cache.hits == 0


# ====================================================================
# 场景 3: skip_total 改变 → 不复用缓存
# ====================================================================


def test_response_cache_skip_total_is_part_of_key(lhb_app, monkeypatch):
    client, lhb_mod = lhb_app
    rows_data = [
        ("000001.SZ", "平安", dt.date(2026, 4, 18), "R",
         1.0, 1.0, 1.0, 1.0, 1.0, None),
    ]
    cols = ["ts_code", "name", "trade_date", "reason",
            "buy_amount", "sell_amount", "net_amount", "turnover",
            "score", "score_detail"]
    canned = [
        # skip_total=true: 不跑 COUNT,只一次 execute
        [(rows_data, cols)],
        # skip_total=false: COUNT + SELECT
        [([(1,)], ["count"]), (rows_data, cols)],
    ]
    fake_pg, counter = _make_counting_pg_conn(canned)
    monkeypatch.setattr("backend.api.lhb.pg_conn", fake_pg)

    r1 = client.get("/api/lhb?date_from=20260418&limit=10&skip_total=true")
    assert r1.status_code == 200
    assert r1.json()["total"] == -1  # skip_total=true 时返回 -1

    r2 = client.get("/api/lhb?date_from=20260418&limit=10&skip_total=false")
    assert r2.status_code == 200
    assert r2.json()["total"] == 1  # 正常返回

    assert counter["n"] == 2  # 两次都打 DB


# ====================================================================
# 场景 4: TTL 过期后重新 MISS
# ====================================================================


def test_response_cache_expiry_reissues_query(lhb_app, monkeypatch):
    client, lhb_mod = lhb_app
    # 把 TTL 强制设为 0.5 秒,然后等待超时
    # 测试不用真 sleep,直接篡改 entry 的 expires_at
    rows = [("000001.SZ", "平安", dt.date(2026, 4, 18), "R",
             1.0, 1.0, 1.0, 1.0, 1.0, None)]
    cols = ["ts_code", "name", "trade_date", "reason",
            "buy_amount", "sell_amount", "net_amount", "turnover",
            "score", "score_detail"]
    canned = [
        [([(1,)], ["count"]), (rows, cols)],
        [([(1,)], ["count"]), (rows, cols)],
    ]
    fake_pg, counter = _make_counting_pg_conn(canned)
    monkeypatch.setattr("backend.api.lhb.pg_conn", fake_pg)

    client.get("/api/lhb?date_from=20260418&limit=10")
    assert counter["n"] == 1

    # 把缓存中所有 entry 的 expires_at 改成 0(立即过期)
    for entry in lhb_mod._lhb_cache._store.values():
        entry.expires_at = 0

    client.get("/api/lhb?date_from=20260418&limit=10")
    assert counter["n"] == 2  # 过期后重新打 DB
    assert lhb_mod._lhb_cache.misses == 2


# ====================================================================
# 工具函数单测: _stable_key 稳定性 + _ttl_until_daily_flush 边界
# ====================================================================


def test_stable_key_same_params_same_hash():
    from backend.utils.response_cache import _stable_key
    a = _stable_key({"ts_code": "000001.SZ", "limit": 100, "offset": 0})
    # 参数顺序换一下
    b = _stable_key({"offset": 0, "limit": 100, "ts_code": "000001.SZ"})
    assert a == b


def test_stable_key_none_vs_missing_differ():
    """None 值和缺失键产生不同 key(避免「不传 = 传 None」的歧义)。"""
    from backend.utils.response_cache import _stable_key
    a = _stable_key({"ts_code": None, "limit": 100})
    b = _stable_key({"limit": 100})
    assert a != b


def test_ttl_until_daily_flush_before_19():
    from backend.utils.response_cache import _ttl_until_daily_flush
    # 上午 10:00,距离 19:00 = 9h = 32400s,但 cap=3600
    now = dt.datetime(2026, 4, 20, 10, 0, 0)
    ttl = _ttl_until_daily_flush(now=now, flush_hour=19, cap_seconds=3600)
    assert ttl == 3600.0


def test_ttl_until_daily_flush_after_19_goes_tomorrow():
    from backend.utils.response_cache import _ttl_until_daily_flush
    # 晚上 20:00, 下次 flush 是次日 19:00,距离 23h,cap 仍 3600
    now = dt.datetime(2026, 4, 20, 20, 0, 0)
    ttl = _ttl_until_daily_flush(now=now, flush_hour=19, cap_seconds=3600)
    assert ttl == 3600.0


def test_ttl_until_daily_flush_near_flush_under_cap():
    from backend.utils.response_cache import _ttl_until_daily_flush
    # 18:30, 距离 19:00 = 1800s, 小于 cap 3600
    now = dt.datetime(2026, 4, 20, 18, 30, 0)
    ttl = _ttl_until_daily_flush(now=now, flush_hour=19, cap_seconds=3600)
    assert 1780 <= ttl <= 1800


# ====================================================================
# ResponseCache LRU 淘汰
# ====================================================================


def test_response_cache_lru_evicts_oldest():
    from backend.utils.response_cache import ResponseCache
    c = ResponseCache(name="test", max_size=3, ttl_cap=3600)
    c.set(c.make_key(k=1), "v1")
    c.set(c.make_key(k=2), "v2")
    c.set(c.make_key(k=3), "v3")
    assert c.get(c.make_key(k=1)) == "v1"  # k=1 变最新
    c.set(c.make_key(k=4), "v4")           # 触发淘汰 k=2 (现在最旧)
    assert c.get(c.make_key(k=2)) is None
    assert c.get(c.make_key(k=1)) == "v1"
    assert c.get(c.make_key(k=3)) == "v3"
    assert c.get(c.make_key(k=4)) == "v4"


def test_response_cache_disabled_bypasses():
    from backend.utils.response_cache import ResponseCache
    c = ResponseCache(name="test", max_size=3, enabled=False)
    k = c.make_key(x=1)
    c.set(k, "v")
    assert c.get(k) is None
    assert c.bypasses >= 1


def test_response_cache_clear_returns_count():
    from backend.utils.response_cache import ResponseCache
    c = ResponseCache(name="test", max_size=10)
    c.set(c.make_key(k=1), "v1")
    c.set(c.make_key(k=2), "v2")
    assert c.clear() == 2
    assert c.get(c.make_key(k=1)) is None
