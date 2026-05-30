# 前后端接口契约规约(原 CLAUDE.md §11,2026-04-22 独立)

> **事故回放**(2026-04-20 P19.13 · STO-40):前端 `fetchDailyKline({ limit: 2000 })` 超过后端 `Query(..., le=1000)` 边界 → 所有日 K 请求 422 Unprocessable Entity。用户几小时看不到日 K,最后才被发现。根因:前后端各改各的,互不看对方边界。
>
> 本文从 CLAUDE.md 抽出独立维护,供前后端改动前速查。改 API 参数前**必读**。

## 1. 红线(改 API 参数时必做)

1. **改前端 `fetchXxx` 的 query 参数前**,必须 `grep` 后端 FastAPI 的 `Query(..., le=, ge=, pattern=)` 拿到真实边界:
   ```bash
   # 例:改 /api/kline/daily 的 limit 前
   grep -n 'Query' backend/api/kline.py
   # 看到 limit: int = Query(500, ge=1, le=1000) → 前端 limit 不能超过 1000
   ```
2. **改后端边界前**,必须 `grep` 所有前端 `fetchXxx` 调用,确认没有调用方在用超出新边界的值:
   ```bash
   grep -rn 'fetchDailyKline\|/api/kline/daily' frontend/src/
   ```
3. **边界绝对值不走小本子**:边界写在后端 `Query(...)` 作为单一事实源,前端 `api/*.ts` 里的 TypeScript 类型仅提供「可选性」,数值边界不在前端定义(防小本子脱节)。

## 2. API 单测 checklist(新端点必写)

写新 FastAPI 端点时,**参数越界 case 必须有**。最小模板:

```python
# tests/test_api_xxx.py
def test_limit_above_max_returns_422(client):
    """契约测试:limit > 后端上限 (le=1000) 应 422,不是被 clip 到 1000。"""
    r = client.get("/api/kline/daily?ts_code=000001.SZ&limit=1001")
    assert r.status_code == 422
    # 422 的 detail 里应包含哪个参数越界,前端看得懂
    assert "limit" in r.text


def test_limit_below_min_returns_422(client):
    r = client.get("/api/kline/daily?ts_code=000001.SZ&limit=0")
    assert r.status_code == 422
```

## 3. 至少一次真实 `curl` 验证

PR 提交前,必须对**每个改动的端点**做一次真实 `curl`(不只看 pytest 里的 TestClient mock):

```bash
# 健康路径 (200)
curl -i 'http://localhost:8000/api/kline/daily?ts_code=000001.SZ&limit=500'
# 边界正上方 (422)
curl -i 'http://localhost:8000/api/kline/daily?ts_code=000001.SZ&limit=1001'
# 边界正下方 (200)
curl -i 'http://localhost:8000/api/kline/daily?ts_code=000001.SZ&limit=1000'
```

原因:pytest 的 `TestClient` 共享 ASGI app 内存,可能掩盖 uvicorn/nginx 层的真实行为(超时、body size、gzip)。一次 curl 相当于最后 80 分到 99 分的保险。

## 4. 现有端点边界速查(改之前先看)

| 端点 | 关键参数边界 | 定义位置 |
|---|---|---|
| `GET /api/kline/daily` | `limit: 1 ≤ ≤ 5000` / `ts_code: required` | `backend/api/kline.py` |
| `GET /api/kline/weekly` | `limit: 1 ≤ ≤ 2000` | `backend/api/kline.py` |
| `GET /api/lhb` | `limit: 1 ≤ ≤ 1000` / `order_by` 白名单 3 值 | `backend/api/lhb.py` |
| `GET /api/lhb/seats` | `limit: 1 ≤ ≤ 2000` / `direction ∈ {BUY, SELL}` | `backend/api/lhb.py` |

**发现新的请在此补,不要散在代码里**。
