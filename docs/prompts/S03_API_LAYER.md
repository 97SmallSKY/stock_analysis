# S03 — API 层（FastAPI Backend）

> **阶段目标**：基于 S01/S02 的服务层，构建完整的 FastAPI RESTful API 与 WebSocket 实时推送，提供给前端消费。所有接口必须有 OpenAPI 文档，类型注解完整，错误响应统一格式。

---

## 0. 前置阅读（必须先读）

- `docs/lhb-quant-v2/remade.md` 第四章（API 设计规范）
- S01 产出的数据库模型（`backend/app/models/`）
- S02 产出的服务层（`backend/app/services/`）

---

## 1. 项目结构

```
backend/app/
├── main.py                    # FastAPI 应用入口，注册路由、中间件、生命周期
├── core/
│   ├── config.py              # Settings（pydantic-settings，从环境变量读取）
│   ├── database.py            # async engine、Session 工厂、get_db 依赖
│   ├── redis.py               # Redis 连接池、get_redis 依赖
│   ├── security.py            # JWT 签发/验证、密码哈希
│   └── logging.py             # 结构化日志配置（JSON 格式）
├── api/
│   ├── deps.py                # 公共依赖（get_current_user、require_robot_owner 等）
│   ├── v1/
│   │   ├── router.py          # 聚合所有子路由，挂载到 /api/v1
│   │   ├── auth.py            # 认证接口
│   │   ├── robots.py          # 机器人 CRUD + 操作接口
│   │   ├── trades.py          # 持仓 + 历史交易接口
│   │   ├── selection.py       # 选股日志 + 评分详情接口
│   │   ├── market.py          # 龙虎榜 + K 线 + 席位接口
│   │   ├── backtest.py        # 回测接口
│   │   └── system.py          # 系统配置 + 健康检查接口
│   └── ws/
│       └── realtime.py        # WebSocket 实时推送
└── schemas/                   # Pydantic v2 请求/响应 Schema
    ├── auth.py
    ├── robot.py
    ├── trade.py
    ├── selection.py
    ├── market.py
    └── common.py              # 通用 Schema（分页、错误响应等）
```

---

## 2. 通用规范

### 2.1 响应格式（所有接口统一）

```python
# schemas/common.py

class APIResponse(BaseModel, Generic[T]):
    """统一成功响应包装"""
    code: int = 200
    data: T
    message: str = "ok"

class PageResponse(BaseModel, Generic[T]):
    """分页响应"""
    code: int = 200
    data: list[T]
    total: int
    page: int
    page_size: int

class ErrorResponse(BaseModel):
    """统一错
<function_calls>
<invoke name="Bash">
<parameter name="command">cat >> ~/Develop/personal/stock_analysis/docs/prompts/S03_API_LAYER.md << 'EOF'
误响应"""
    code: int
    message: str
    detail: str | None = None   # 开发环境才暴露，生产环境置 None
```

**HTTP 状态码约定**：

| 场景 | 状态码 | code 字段 |
|------|--------|----------|
| 成功 | 200 | 200 |
| 创建成功 | 201 | 201 |
| 未认证 | 401 | 401 |
| 无权限 | 403 | 403 |
| 资源不存在 | 404 | 404 |
| 参数校验失败 | 422 | 422 |
| 风控拒绝 | 422 | 4220（业务子码） |
| 服务器错误 | 500 | 500 |

### 2.2 认证方式

```python
# 所有需要登录的接口使用 Bearer JWT
# Header: Authorization: Bearer <access_token>

# Access Token 有效期：30 分钟
# Refresh Token 有效期：7 天（存 Redis，支持主动吊销）

# deps.py 中的依赖函数
async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User: ...

async def require_robot_owner(
    robot_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> RobotConfig:
    """验证当前用户是否拥有该机器人，无权限返回 403"""
```

### 2.3 缓存策略

```python
# 使用 Redis 缓存，装饰器方式统一管理
# TTL 值引用 S01 CACHE_TTL 常量

@cached(key="lhb:daily:{trade_date}", ttl=CACHE_TTL["lhb_daily"])
async def get_lhb_daily(trade_date: str, db: AsyncSession): ...

# 写操作后主动失效对应缓存
# 例如：修改机器人配置后，清除 robot:config:{robot_id} 缓存
```

---

## 3. 接口清单

### 3.1 认证（`/api/v1/auth`）

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/login` | 用户名密码登录，返回 access_token + refresh_token |
| POST | `/refresh` | 使用 refresh_token 换新 access_token |
| POST | `/logout` | 将当前 access_token 加入 Redis 黑名单 |

```python
# 登录请求/响应
class LoginRequest(BaseModel):
    username: str
    password: str

class LoginResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int  # access_token 有效秒数
```

### 3.2 机器人管理（`/api/v1/robots`）

| 方法 | 路径 | 说明 | 缓存 |
|------|------|------|------|
| GET | `/` | 获取当前用户所有机器人列表 | 无 |
| POST | `/` | 创建机器人 | 无 |
| GET | `/{robot_id}` | 获取机器人详情（含最新 AHS、净值） | 30s |
| PUT | `/{robot_id}` | 更新机器人配置（不含 mode） | 清除缓存 |
| DELETE | `/{robot_id}` | 删除机器人（软删除，is_active=False） | 清除缓存 |
| POST | `/{robot_id}/activate` | 切换 active 状态 | 清除缓存 |
| POST | `/{robot_id}/switch-mode` | 切换 paper/live 模式（需二次确认 token） | 清除缓存 |
| GET | `/{robot_id}/nav` | 获取净值曲线（支持 days 参数：7/30/90/all） | 5min |
| GET | `/{robot_id}/ahs` | 获取 AHS 历史序列 | 5min |

```python
# schemas/robot.py 关键 Schema
class RobotCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=100)
    initial_capital: float = Field(..., gt=10000, le=10_000_000)
    max_positions: int = Field(default=5, ge=1, le=20)
    max_single_ratio: float = Field(default=0.20, gt=0, le=0.5)
    stop_loss_ratio: float = Field(default=0.08, gt=0, le=0.3)
    hold_days_limit: int = Field(default=3, ge=1, le=10)
    ahs_stop_threshold: int = Field(default=80, ge=50, le=100)

class RobotDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    mode: RobotMode
    is_active: bool
    initial_capital: float
    current_nav: float | None       # 最新净值
    total_return: float | None      # 累计收益率
    current_ahs: float | None       # 最新 AHS 分
    risk_level: RiskLevel | None
    open_positions: int             # 当前持仓数
    # ... 其余配置字段
```

### 3.3 交易记录（`/api/v1/trades`）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/positions` | 当前所有机器人的持仓列表（status=open） |
| GET | `/{robot_id}/positions` | 指定机器人持仓（含实时浮盈，从 Redis 取最新价） |
| GET | `/{robot_id}/history` | 历史交易记录（分页，支持日期范围筛选） |
| POST | `/{trade_id}/close` | 手动平仓（reason=manual，需确认弹窗） |
| GET | `/{robot_id}/stats` | 交易统计（胜率、盈亏比、平均持仓天数） |

```python
class PositionItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    robot_id: int
    robot_name: str
    stock_code: str
    stock_name: str
    buy_date: date
    buy_price: float
    shares: int
    cost_amount: float              # buy_price × shares
    current_price: float | None     # 来自 Redis 实时行情，可能为 None
    unrealized_pnl: float | None    # 浮盈（元）
    pnl_ratio: float | None         # 浮盈比例
    hold_days: int
    stop_loss_price: float          # 止损价 = buy_price × (1 - stop_loss_ratio)
```

### 3.4 选股中心（`/api/v1/selection`）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/today` | 今日选股结果（Phase A + B 分开返回） |
| GET | `/{robot_id}/logs` | 历史选股日志（分页） |
| GET | `/{robot_id}/score/{stock_code}` | 单只股票的评分因子详情 |
| GET | `/{robot_id}/hit-rate` | 历史命中率统计（选中后实际盈利比例） |

### 3.5 市场数据（`/api/v1/market`）

| 方法 | 路径 | 说明 | 缓存 |
|------|------|------|------|
| GET | `/lhb/daily` | 龙虎榜日数据（必须传 trade_date 参数） | 12h |
| GET | `/lhb/stocks/{stock_code}` | 个股历史上榜记录（近 N 次） | 1h |
| GET | `/lhb/seats` | 游资席位排行（指定日期范围内净买入 TOP N） | 1h |
| GET | `/kline/{stock_code}` | 日 K 线数据（start_date、end_date、adjust） | 1h |
| GET | `/sentiment` | 市场情绪指数历史（近 30 日） | 5min |

```python
# GET /api/v1/market/lhb/daily 查询参数
class LhbDailyQuery(BaseModel):
    trade_date: date                         # 必填
    reason_type: ReasonType | None = None    # 过滤上榜原因
    min_net_buy_amount: float | None = None  # 最小净买入额（元）
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=50, ge=10, le=200)
```

### 3.6 回测（`/api/v1/backtest`）

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/run` | 提交回测任务（异步，返回 task_id） |
| GET | `/{task_id}/status` | 查询回测进度（0~100%） |
| GET | `/{task_id}/result` | 获取回测结果（净值曲线、指标、交易明细） |
| GET | `/history` | 历史回测记录列表 |

```python
class BacktestRequest(BaseModel):
    robot_id: int
    start_date: date
    end_date: date
    # 可选覆盖机器人配置（用于参数调优）
    stop_loss_ratio: float | None = None
    hold_days_limit: int | None = None
    initial_capital: float | None = None

class BacktestResult(BaseModel):
    task_id: str
    total_return: float
    annualized_return: float
    max_drawdown: float
    sharpe_ratio: float
    win_rate: float
    profit_loss_ratio: float
    total_trades: int
    nav_curve: list[NavPoint]       # [{date, nav, benchmark_nav}]
    trade_list: list[TradeRecord]   # 所有交易明细
```

### 3.7 系统（`/api/v1/system`）

| 方法 | 路径 | 认证 | 说明 |
|------|------|------|------|
| GET | `/health` | 无需 | 健康检查（PostgreSQL + Redis 连通性） |
| GET | `/config` | 需要 | 获取系统配置（known_traders、权重等） |
| PUT | `/config/{key}` | 需要管理员 | 更新系统配置 |
| POST | `/tasks/trigger` | 需要管理员 | 手动触发指定 Celery Task |

---

## 4. WebSocket 实时推送（`/ws/realtime`）

```python
# api/ws/realtime.py

# 连接鉴权：连接时在 query string 传 token
# ws://host/ws/realtime?token=<access_token>

# 连接管理器（单例）
class ConnectionManager:
    def __init__(self):
        # robot_id → set of WebSocket 连接（同一机器人可多端订阅）
        self._connections: dict[int, set[WebSocket]] = defaultdict(set)

    async def connect(self, ws: WebSocket, robot_id: int): ...
    async def disconnect(self, ws: WebSocket, robot_id: int): ...
    async def broadcast_to_robot(self, robot_id: int, event: str, data: dict): ...
    async def broadcast_all(self, event: str, data: dict): ...

manager = ConnectionManager()

# WebSocket 事件类型及数据结构
WS_EVENTS = {
    # 持仓浮盈更新（行情推送触发，盘中每 10s 推送一次）
    "robot.pnl_updated": {
        "robot_id": int,
        "trade_id": int,
        "current_price": float,
        "unrealized_pnl": float,
        "pnl_ratio": float,
        "updated_at": str,          # ISO8601
    },
    # 交易状态变更（开仓/平仓后立即推送）
    "robot.trade_updated": {
        "robot_id": int,
        "trade_id": int,
        "action": "open | close",
        "stock_code": str,
        "stock_name": str,
        "price": float,
        "pnl": float | None,        # 平仓时有值
    },
    # 异步任务完成（Phase A/B、结算、回测）
    "system.task_done": {
        "task_type": "phase_a | phase_b | settlement | backtest",
        "robot_id": int | None,
        "task_id": str,
        "status": "success | failed",
        "summary": str,             # 人类可读摘要
    },
    # 市场行情快照（盘中每分钟广播）
    "market.quote": {
        "stock_code": str,
        "current_price": float,
        "pct_change": float,
        "volume": int,
        "updated_at": str,
    },
    # 心跳（服务端每 30s 主动发送）
    "ping": {},
}
```

---

## 5. 中间件与异常处理

```python
# main.py 中注册的中间件（顺序从外到内）

# 1. CORS（允许前端域名跨域）
app.add_middleware(CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS.split(","),
    allow_methods=["*"],
    allow_headers=["Authorization", "Content-Type"],
)

# 2. 请求追踪（注入 X-Trace-ID，传递给日志和响应头）
@app.middleware("http")
async def trace_middleware(request: Request, call_next):
    trace_id = request.headers.get("X-Trace-ID", str(uuid4()))
    # 存入 contextvars，日志自动附加
    token = trace_id_var.set(trace_id)
    response = await call_next(request)
    response.headers["X-Trace-ID"] = trace_id
    trace_id_var.reset(token)
    return response

# 3. 全局异常处理器
@app.exception_handler(RequestValidationError)
async def validation_error_
<function_calls>
<invoke name="Bash">
<parameter name="command">cat >> ~/Develop/personal/stock_analysis/docs/prompts/S03_API_LAYER.md << 'EOF'
handler(request, exc):
    return JSONResponse(status_code=422, content={
        "code": 422, "message": "参数校验失败",
        "detail": str(exc.errors()) if settings.DEBUG else None
    })

@app.exception_handler(Exception)
async def global_error_handler(request, exc):
    logger.error("未捕获异常", exc_info=exc, extra={"path": request.url.path})
    return JSONResponse(status_code=500, content={
        "code": 500, "message": "服务器内部错误",
        "detail": str(exc) if settings.DEBUG else None
    })
```

---

## 6. 性能要求

| 接口类型 | P95 响应时间 | 说明 |
|---------|------------|------|
| 读接口（有缓存） | ≤ 50ms | Redis 缓存命中 |
| 读接口（无缓存） | ≤ 300ms | 数据库查询 |
| 写接口 | ≤ 500ms | 含数据库写入 |
| WebSocket 推送延迟 | ≤ 1s | 从事件发生到客户端收到 |
| 回测提交 | ≤ 200ms | 异步任务，立即返回 task_id |

---

## 7. 验收标准

```bash
# 7.1 服务启动无报错
uvicorn app.main:app --host 0.0.0.0 --port 8000
# 期望：启动日志无 ERROR，/health 返回 {"status":"ok"}

# 7.2 OpenAPI 文档完整生成
curl http://localhost:8000/openapi.json | python3 -c "
import sys, json
spec = json.load(sys.stdin)
paths = list(spec['paths'].keys())
required = ['/api/v1/auth/login', '/api/v1/robots', '/api/v1/trades/positions',
            '/api/v1/market/lhb/daily', '/api/v1/backtest/run', '/health']
missing = [p for p in required if p not in paths]
assert not missing, f'缺少接口: {missing}'
print(f'✅ OpenAPI 共 {len(paths)} 个路径，关键接口全部存在')
"

# 7.3 认证流程测试
TOKEN=$(curl -s -X POST http://localhost:8000/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"test123"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['data']['access_token'])")
echo "Token: ${TOKEN:0:20}..."

# 7.4 受保护接口需要 Token
curl -s http://localhost:8000/api/v1/robots | python3 -c \
  "import sys,json; r=json.load(sys.stdin); assert r['code']==401, f'期望401，实际{r[\"code\"]}'"
echo "✅ 未认证请求正确返回 401"

# 7.5 机器人 CRUD
curl -s -X POST http://localhost:8000/api/v1/robots \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"name":"测试机器人","initial_capital":100000}' \
  | python3 -c "import sys,json; r=json.load(sys.stdin); assert r['code']==201"
echo "✅ 创建机器人成功"

# 7.6 WebSocket 连接与心跳
python3 - << 'PYEOF'
import asyncio, websockets, json
async def test():
    uri = f"ws://localhost:8000/ws/realtime?token={TOKEN}"
    async with websockets.connect(uri) as ws:
        msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=35))
        assert msg['event'] == 'ping', f"期望ping，实际{msg['event']}"
        print("✅ WebSocket 心跳正常")
asyncio.run(test())
PYEOF

# 7.7 缓存生效验证
# 第一次请求（慢）
TIME1=$(curl -o /dev/null -s -w "%{time_total}" \
  "http://localhost:8000/api/v1/market/lhb/daily?trade_date=2024-01-15" \
  -H "Authorization: Bearer $TOKEN")
# 第二次请求（应命中缓存，快很多）
TIME2=$(curl -o /dev/null -s -w "%{time_total}" \
  "http://localhost:8000/api/v1/market/lhb/daily?trade_date=2024-01-15" \
  -H "Authorization: Bearer $TOKEN")
echo "首次: ${TIME1}s，缓存命中: ${TIME2}s"

# 7.8 API 单元测试（使用 httpx + pytest）
pytest backend/tests/api/ -v --cov=app/api --cov-report=term-missing
# 覆盖率要求：api/ 目录 ≥ 80%
```

---

## 8. 禁止事项

- ❌ 接口不得直接暴露数据库 ORM 对象，必须经过 Pydantic Schema 序列化
- ❌ 不得在路由函数中写业务逻辑，必须调用 `services/` 层的函数
- ❌ `GET /health` 不得需要认证，Nginx 和 Docker 健康检查都依赖它
- ❌ WebSocket 连接必须鉴权，不得允许匿名订阅持仓数据
- ❌ 分页接口必须有 `page_size` 上限（≤200），防止一次拉取过多数据
- ❌ 不得在响应中暴露用户密码哈希或内部数据库 ID（trade_id 等 BIGINT 可暴露）
- ❌ 手动平仓接口（`POST /trades/{trade_id}/close`）必须校验该交易属于当前用户的机器人

---

## 9. 输出产物

```
backend/app/
├── main.py                        # 应用入口（中间件、路由注册、生命周期）
├── core/
│   ├── config.py                  # Settings（pydantic-settings）
│   ├── database.py                # async DB Session
│   ├── redis.py                   # Redis 连接池
│   ├── security.py                # JWT + 密码哈希
│   └── logging.py                 # JSON 结构化日志
├── api/
│   ├── deps.py                    # 公共依赖函数
│   ├── v1/
│   │   ├── router.py
│   │   ├── auth.py
│   │   ├── robots.py
│   │   ├── trades.py
│   │   ├── selection.py
│   │   ├── market.py
│   │   ├── backtest.py
│   │   └── system.py
│   └── ws/
│       └── realtime.py            # WebSocket + ConnectionManager
└── schemas/
    ├── common.py                  # APIResponse, PageResponse, ErrorResponse
    ├── auth.py
    ├── robot.py
    ├── trade.py
    ├── selection.py
    └── market.py

backend/tests/api/
├── conftest.py                    # pytest fixture：测试客户端、测试用户、mock DB
├── test_auth.py                   # 认证接口测试
├── test_robots.py                 # 机器人 CRUD 测试
├── test_trades.py                 # 持仓/历史交易测试
├── test_market.py                 # 市场数据接口测试
└── test_ws.py                     # WebSocket 连接/推送测试
```
