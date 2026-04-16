# 龙虎榜量化交易系统 v2 - API 接口设计

> 基于 API 设计评审优化版本
> 评审日期: 2026-04-16
> 二次评审修订日期: 2026-04-16

---

## 评审建议采纳说明

### 已采纳的改进项 ✅

| 评审问题 | 采纳的改进 |
|----------|------------|
| DELETE 接口语义混乱 | 新增 `POST /robots/{id}/actions/delete` RPC 风格接口，保留原 DELETE 作为简单删除 |
| 手动交易缺乏事务保障说明 | 补充完整的事务边界、隔离级别、失败处理策略说明 |
| WebSocket 缺乏认证和权限控制 | 补充初期限制（连接数、订阅数、空闲超时）和后续安全演进规划 |
| 幂等键实现规范不完整 | 补充适用接口列表、行为定义、实现规范（格式、存储、并发控制） |
| 批量操作缺乏原子性定义 | 补充默认尽力执行模式和可选原子模式说明 |
| 错误码 3002 映射不精确 | 调整为 HTTP 409 Conflict，表示状态冲突需确认 |
| 选股触发接口缺少参数定义 | 补充完整请求体和响应格式定义 |
| 回测接口缺少请求体定义 | 补充完整请求体参数、字段约束和响应格式定义 |

### 不采纳的建议及原因 ❌

| 评审建议 | 不采纳原因 |
|----------|------------|
| WebSocket 不应在 URL 中传 token | 浏览器 WebSocket 握手无法自定义 Header，通过 URL query 参数传递是业界标准做法（如 Socket.IO、Pusher）。生产环境应使用短期有效的临时授权 token（有效期 5 分钟），而非长期 API Key，可降低泄露风险。 |

---

## 一、API 规范

### 1.1 基础信息

| 项目 | 值 |
|------|------|
| Base URL | `/api/v1` |
| Content-Type | `application/json` |
| 认证方式 | 预留认证中间件（初期无需认证） |
| 日期格式 | `YYYY-MM-DD` |
| 时间戳格式 | ISO 8601 (`2026-04-16T10:00:00+08:00`) |

### 1.2 响应格式规范

#### 1.2.1 统一响应格式

```json
{
  "code": 0,
  "message": "success",
  "data": {},
  "timestamp": "2026-04-16T10:00:00+08:00",
  "request_id": "req_abc123"
}
```

#### 1.2.2 错误响应格式

```json
{
  "code": 1001,
  "message": "参数错误",
  "errors": [
    {"field": "initial_capital", "message": "必须大于0"}
  ],
  "timestamp": "2026-04-16T10:00:00+08:00",
  "request_id": "req_abc123"
}
```

#### 1.2.3 分页响应格式

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "items": [],
    "total": 100,
    "page": 1,
    "page_size": 20,
    "has_more": true
  },
  "timestamp": "2026-04-16T10:00:00+08:00"
}
```

### 1.3 错误码定义

| code | HTTP Status | 说明 |
|------|-------------|------|
| 0 | 200 | 成功 |
| 1001 | 400 | 参数错误 |
| 1002 | 404 | 资源不存在 |
| 1003 | 409 | 资源冲突（如重复创建） |
| 2001 | 401 | 认证失败 |
| 2002 | 403 | 无权限 |
| 3001 | 422 | 业务逻辑错误 |
| 3002 | 422 | 危险操作需确认 |
| 4001 | 429 | 请求过于频繁 |
| 5001 | 500 | 服务器内部错误 |

### 1.4 字段规范

| 类型 | 格式 | 示例 |
|------|------|------|
| **百分比** | 小数形式 (0-1) | `0.0523` 表示 5.23% |
| **价格** | 三位小数 | `12.500` |
| **金额** | 元，两位小数 | `12000.00` |
| **数量** | 整数 | `1000` |
| **日期** | `YYYY-MM-DD` | `2026-04-16` |
| **时间戳** | ISO 8601 | `2026-04-16T10:00:00+08:00` |

### 1.5 幂等性

关键写接口支持幂等键：

```http
POST /api/v1/robots
X-Idempotency-Key: unique-request-id-123
```

相同 `X-Idempotency-Key` 的请求在 24 小时内返回相同结果。

#### 1.5.1 适用接口

| 接口 | 幂等键 | 说明 |
|------|--------|------|
| POST /robots | 推荐 | 防重复创建 |
| POST /robots/{id}/trades/manual-buy | **必须** | 防重复买入（资金风险） |
| POST /robots/{id}/trades/{trade_id}/manual-sell | **必须** | 防重复卖出（持仓风险） |
| POST /robots/{id}/actions/close-all | **必须** | 防重复平仓 |
| POST /robots/{id}/actions/delete | **必须** | 防重复删除 |
| POST /selection/trigger | 自动 | 以 robot_id+date 为自然幂等键 |
| POST /backtest/run | 推荐 | 防重复提交 |
| PUT /robots/{id} | 不需要 | PUT 本身幂等 |

#### 1.5.2 行为定义

| 场景 | 行为 |
|------|------|
| 相同 key + 相同 body | 返回缓存结果，HTTP 200 |
| 相同 key + 不同 body | 返回 HTTP 409 Conflict（code: 1003） |
| 过期后重复请求 | 作为新请求处理 |
| 并发相同 key | 只有一个执行，其余阻塞等待结果 |

#### 1.5.3 实现规范

- **格式**：UUID v4 或自定义字符串，最长 64 字符
- **TTL**：24 小时
- **存储**：Redis（key: `idempotency:{key}`, value: response JSON）
- **并发控制**：使用 Redis SETNX 实现分布式锁

### 1.6 HTTP 缓存头

| 数据类型 | Cache-Control | 说明 |
|----------|---------------|------|
| 历史数据（K线、已结束交易） | `max-age=86400` | 可缓存一天 |
| 热点数据（Dashboard、情绪） | `no-cache` | 每次拉取 |
| 实时数据（持仓、行情） | 不设置 | 不缓存 |

### 1.7 大数据量限制

| 接口类型 | 最大返回条数 | 限制说明 |
|----------|-------------|----------|
| 列表查询 | 500 条/页 | 强制分页 |
| K线查询 | 最大 365 天跨度 | 超出自动截断 |
| 净值曲线 | 最大 1000 点 | 自动降采样 |

---

## 二、API 端点设计

### 2.1 接口分类

```
REST 资源类:   /api/v1/{resource}              (查询、CRUD)
RPC 动作类:     /api/v1/actions/{action}        (触发操作)
聚合查询类:     /api/v1/aggregations/{agg}      (跨资源统计)
```

### 2.2 市场数据 API

#### GET `/api/v1/market/lhb/daily`

龙虎榜日数据

**请求参数**:
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| trade_date | string | 否 | 交易日期，默认最近有数据的交易日 |
| stock_code | string | 否 | 股票代码 |
| sort_by | string | 否 | 排序字段：net_buy/change_pct/turnover_rate |
| sort_order | string | 否 | asc/desc，默认 desc |
| page | int | 否 | 页码，默认 1 |
| page_size | int | 否 | 每页数量，默认 20，最大 100 |

**响应**: 分页格式，`items` 数组

---

#### GET `/api/v1/market/lhb/seats`

席位明细

**请求参数**:
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| trade_date | string | 是 | 交易日期 YYYY-MM-DD |
| stock_code | string | 否 | 股票代码 |
| page | int | 否 | 页码，默认 1 |
| page_size | int | 否 | 每页数量，默认 50 |

---

#### GET `/api/v1/market/lhb/tn-prices`

T+N 价格数据

**请求参数**:
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| trade_date | string | 是 | 信号日期 YYYY-MM-DD |
| stock_code | string | 否 | 股票代码 |
| n_days | int | 否 | 查询特定 N：1/2/5/10，不传返回全部 |

**响应**:
```json
{
  "code": 0,
  "data": [
    {
      "trade_date": "2026-04-15",
      "stock_code": "000001",
      "n_days": 1,
      "close_price": 12.80,
      "high_ret": 0.052,
      "close_ret": 0.024
    }
  ]
}
```

---

#### GET `/api/v1/market/kline/daily`

日K线数据

**请求参数**:
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| stock_code | string | 是 | 股票代码 |
| start_date | string | 否 | 开始日期，默认一年前 |
| end_date | string | 否 | 结束日期，默认今天 |
| adjust | string | 否 | 复权类型：raw/qfq/hfq，默认 qfq |

**约束**: 跨度最大 365 天，超出返回 1001 错误

**缓存**: `Cache-Control: max-age=3600`

---

#### GET `/api/v1/market/sentiment`

市场情绪

**请求参数**:
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| trade_date | string | 否 | 交易日期，默认最新 |

---

### 2.3 机器人管理 API

#### GET `/api/v1/robots`

机器人列表

**请求参数**:
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| status | string | 否 | running/paused/ended/eliminated |
| strategy_name | string | 否 | 策略名称过滤 |
| page | int | 否 | 页码 |
| page_size | int | 否 | 每页数量 |

**响应**:
```json
{
  "code": 0,
  "data": {
    "items": [
      {
        "id": 1,
        "name": "Alpha-1",
        "strategy_name": "lhb_multifactor",
        "status": "running",
        "initial_capital": 100000.00,
        "current_cash": 75000.00,
        "max_positions": 3,
        "current_positions": 2,
        "total_nav": 1.0523,
        "total_profit": 5230.00,
        "created_at": "2026-04-01T10:00:00+08:00"
      }
    ],
    "total": 5,
    "page": 1,
    "page_size": 20
  }
}
```

---

#### POST `/api/v1/robots`

创建机器人

**请求头**:
| 头 | 必填 | 说明 |
|-----|------|------|
| X-Idempotency-Key | 否 | 幂等键 |

**请求体**:
```json
{
  "name": "Alpha-2",
  "strategy_name": "lhb_multifactor",
  "initial_capital": 100000.00,
  "max_positions": 3,
  "max_hold_days": 7,
  "stop_loss_pct": -0.05,
  "sell1_pct": 0.03,
  "sell2_pct": 0.05,
  "sell3_pct": 0.08,
  "sell1_ratio": 0.333,
  "sell2_ratio": 0.333,
  "note": "测试机器人"
}
```

**字段校验**:
| 字段 | 约束 |
|------|------|
| name | 必填，1-50 字符，不能重复 |
| strategy_name | 必填，必须是已存在的策略 |
| initial_capital | 必填，10000-10000000 之间 |
| stop_loss_pct | 必填，必须为负数，-0.5 到 -0.01 |
| sell1_pct/sell2_pct/sell3_pct | 必填，必须递增，0.01 到 0.5 |

**响应**: 返回创建的机器人信息，包含 `id`

---

#### GET `/api/v1/robots/{id}`

机器人详情

**响应**: 机器人完整信息（见 2.2.3）

---

#### PUT `/api/v1/robots/{id}`

更新机器人

**请求体**:
```json
{
  "status": "paused",
  "max_positions": 2,
  "note": "暂停交易"
}
```

**危险操作保护**:
- `status: ended` 需要确认无未平仓持仓
- `status: eliminated` 不可通过此接口修改

---

#### DELETE `/api/v1/robots/{id}`

删除机器人（简单删除，无参数）

**前置检查**:
- 检查是否有 `status IN ('open', 'partial', 'pending')` 的持仓
- 有则返回 3002 错误（HTTP 409），响应头包含确认信息

**响应头（有持仓时）**:
```http
HTTP 409 Conflict
X-Confirmation-Required: true
X-Confirmation-Action: force_delete
X-Confirmation-Message: 该机器人有2个未平仓持仓，确认删除？
```

---

#### POST `/api/v1/robots/{id}/actions/delete`

删除机器人（带参数，RPC 风格）

> 用于需要强制删除或记录原因的场景，推荐使用此接口

**请求头**:
| 头 | 必填 | 说明 |
|-----|------|------|
| X-Idempotency-Key | **必须** | 幂等键 |

**请求体**:
```json
{
  "force": true,
  "reason": "测试数据清理"
}
```

**字段校验**:
| 字段 | 约束 |
|------|------|
| force | 可选，默认 false |
| reason | force=true 时必填，1-200 字符 |

**审计日志**: 记录删除操作（原因、操作人、时间）

---

#### POST `/api/v1/robots/batch`

**批量操作机器人**

**请求体**:
```json
{
  "action": "pause",
  "robot_ids": [1, 2, 3],
  "atomic": false
}
```

**支持动作**: `pause` / `resume` / `delete`

**字段校验**:
| 字段 | 约束 |
|------|------|
| action | 必填，pause/resume/delete |
| robot_ids | 必填，1-100 个元素 |
| atomic | 可选，默认 false |

#### 批量操作语义

**默认行为：尽力执行（Best-effort）**
- 逐个执行，失败不影响其他
- 响应中明确标注每个操作的结果
- HTTP 状态码：
  - 全部成功 → 200
  - 部分成功 → 207 Multi-Status
  - 全部失败 → 422

**原子模式：atomic=true**
- 先校验所有 robot_id 的前置条件
- 全部通过后在事务中批量执行
- 任一失败则全部回滚，返回 422

**响应**:
```json
{
  "code": 0,
  "data": {
    "results": [
      {"robot_id": 1, "success": true},
      {"robot_id": 2, "success": false, "error": "有未平仓持仓"},
      {"robot_id": 3, "success": true}
    ],
    "summary": {"total": 3, "success": 2, "failed": 1}
  }
}
```

---

#### GET `/api/v1/robots/{id}/positions`

当前持仓（便捷接口）

> 等价于 `GET /api/v1/robots/{id}/trades?status=open`

**响应**:
```json
{
  "code": 0,
  "data": [
    {
      "trade_id": 101,
      "stock_code": "000001",
      "stock_name": "平安银行",
      "buy_date": "2026-04-14",
      "buy_price": 12.000,
      "buy_shares": 1000,
      "buy_amount": 12000.00,
      "current_price": 12.800,
      "current_value": 12800.00,
      "profit": 800.00,
      "profit_pct": 0.0667,
      "hold_days": 2,
      "lianban_count": 0,
      "sell_signals": ["sell1_pending"],
      "stop_loss_price": 11.400,
      "sell1_price": 12.360,
      "sell2_price": 12.600,
      "sell3_price": 12.960
    }
  ]
}
```

---

#### GET `/api/v1/robots/{id}/trades`

交易记录

**请求参数**:
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| status | string | 否 | pending/open/partial/closed/abandoned |
| stock_code | string | 否 | 股票代码过滤 |
| sell_type | string | 否 | stop_loss/sell1/sell2/sell3/expire/manual |
| start_date | string | 否 | 开始日期 |
| end_date | string | 否 | 结束日期 |
| sort_by | string | 否 | 排序字段 |
| sort_order | string | 否 | asc/desc |
| page | int | 否 | 页码，默认 1 |
| page_size | int | 否 | 每页数量，默认 20，最大 100 |

---

#### GET `/api/v1/robots/{id}/nav`

净值曲线

**请求参数**:
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| start_date | string | 否 | 开始日期 |
| end_date | string | 否 | 结束日期 |

**约束**: 最多返回 1000 点，超出自动按周降采样

**缓存**: `Cache-Control: max-age=300`

---

#### GET `/api/v1/robots/{id}/ahs`

AHS 历史

**请求参数**:
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| start_date | string | 否 | 开始日期 |
| end_date | string | 否 | 结束日期 |
| page | int | 否 | 页码 |
| page_size | int | 否 | 每页数量 |

---

### 2.4 手动交易 API ⭐ P0

#### POST `/api/v1/robots/{id}/trades/manual-buy`

手动买入

**请求头**:
| 头 | 必填 | 说明 |
|-----|------|------|
| X-Idempotency-Key | **必须** | 幂等键 |

**请求体**:
```json
{
  "stock_code": "000001",
  "stock_name": "平安银行",
  "price": 12.500,
  "shares": 1000,
  "reason": "人工判断买入"
}
```

**字段校验**:
| 字段 | 约束 |
|------|------|
| stock_code | 必填，6 位数字 |
| price | 必填，正数，三位小数 |
| shares | 必填，100 的整数倍 |
| reason | 必填，1-200 字符 |

**前置检查**:
- 检查是否停牌
- 检查资金是否充足
- 检查是否超过最大持仓数
- 检查 AHS 评分是否允许开仓

#### 事务保障 ⭐

**手动买入涉及的多表写操作**:
1. `INSERT robot_trades` (status=open)
2. `INSERT cash_ledger` (event_type=buy_cost)
3. `UPDATE robots SET current_cash = current_cash - amount`
4. `INSERT trade_events` (event_type=manual_buy)
5. `INSERT audit_log`

**事务边界**: 整个操作在单一数据库事务中执行

| 属性 | 值 |
|------|------|
| 隔离级别 | READ COMMITTED |
| 失败策略 | 全部回滚 |
| 并发控制 | SELECT ... FOR UPDATE 锁定机器人资金行 |
| 幂等保证 | X-Idempotency-Key 去重 |

---

#### POST `/api/v1/robots/{id}/trades/{trade_id}/manual-sell`

手动卖出

**请求头**:
| 头 | 必填 | 说明 |
|-----|------|------|
| X-Idempotency-Key | **必须** | 幂等键 |

**请求体**:
```json
{
  "shares": 500,
  "price": 13.000,
  "reason": "人工判断卖出"
}
```

**前置检查**:
- 检查持仓是否足够
- 检查是否为可卖出状态

#### 事务保障 ⭐

**手动卖出涉及的多表写操作**:
1. `UPDATE robot_trades` (status=partial/closed, sell_*)
2. `INSERT cash_ledger` (event_type=sell_revenue)
3. `UPDATE robots SET current_cash = current_cash + amount`
4. `INSERT trade_events` (event_type=manual_sell)
5. `INSERT audit_log`

**事务边界**: 整个操作在单一数据库事务中执行

| 属性 | 值 |
|------|------|
| 隔离级别 | READ COMMITTED |
| 失败策略 | 全部回滚 |
| 并发控制 | SELECT ... FOR UPDATE 锁定交易记录行 |
| 幂等保证 | X-Idempotency-Key 去重 |

---

#### POST `/api/v1/robots/{id}/actions/close-all`

一键平仓

**请求体**:
```json
{
  "reason": "紧急平仓"
}
```

**审计日志**: 必须记录平仓原因

---

### 2.5 策略管理 API

#### GET `/api/v1/strategies`

策略列表

---

#### GET `/api/v1/strategies/{name}/params`

策略参数

---

#### PUT `/api/v1/strategies/{name}/params`

更新策略参数

**请求体**:
```json
{
  "params": [
    {"key": "min_net_buy", "value": "60000000"},
    {"key": "min_tech_score", "value": "55"}
  ],
  "effective_mode": "immediate",  // immediate / next_trading_day
  "effective_date": "2026-04-20"  // 当 effective_mode=next_trading_day 时必填
}
```

**审计日志**: 记录参数变更（旧值、新值、操作人）

---

### 2.6 选股 API

#### GET `/api/v1/selection/daily`

每日选股结果

**请求参数**:
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| trade_date | string | 否 | 交易日期，默认今日 |
| robot_id | int | 否 | 机器人ID |
| decision | string | 否 | selected/rejected/filtered，不传返回全部 |
| page | int | 否 | 页码 |
| page_size | int | 否 | 每页数量 |
| sort_by | string | 否 | signal_score/tech_score/fund_score/hist_score |
| sort_order | string | 否 | asc/desc |

---

#### POST `/api/v1/selection/trigger`

触发选股（手动）

> 异步接口，立即返回 task_id

**请求头**:
| 头 | 必填 | 说明 |
|-----|------|------|
| X-Idempotency-Key | 推荐 | 幂等键，不传则以 robot_id+trade_date 作为自然幂等键 |

**请求体**:
```json
{
  "robot_id": 1,
  "trade_date": "2026-04-16",
  "force_rerun": false
}
```

**字段校验**:
| 字段 | 约束 |
|------|------|
| robot_id | 必填，必须存在且状态为 running |
| trade_date | 可选，默认最近交易日，格式 YYYY-MM-DD |
| force_rerun | 可选，默认 false，为 true 时覆盖已有结果 |

**前置检查**:
- 检查是否为交易日（节假日返回 3001）
- 检查该 robot_id + trade_date 是否已有选股结果
- 检查机器人状态是否为 running

**响应**:
```json
{
  "code": 0,
  "data": {
    "task_id": "sel_abc123",
    "status": "accepted",
    "estimated_seconds": 30,
    "poll_url": "/api/v1/selection/tasks/sel_abc123"
  }
}
```

**幂等性**: 相同 robot_id + trade_date 返回已有结果，不重复执行（force_rerun=true 除外）

---

#### GET `/api/v1/selection/tasks/{task_id}`

查询选股任务进度

**响应**:
```json
{
  "code": 0,
  "data": {
    "task_id": "sel_abc123",
    "status": "completed",
    "progress": 100,
    "started_at": "2026-04-16T18:50:00+08:00",
    "completed_at": "2026-04-16T18:50:30+08:00",
    "result": {
      "selected_count": 5,
      "rejected_count": 10,
      "filtered_count": 20
    }
  }
}
```

---

### 2.7 回测 API

#### POST `/api/v1/backtest/run`

提交回测任务

> 异步接口，立即返回 task_id

**请求头**:
| 头 | 必填 | 说明 |
|-----|------|------|
| X-Idempotency-Key | 推荐 | 幂等键，防重复提交 |

**请求体**:
```json
{
  "strategy_name": "lhb_multifactor",
  "start_date": "2025-01-01",
  "end_date": "2025-12-31",
  "initial_capital": 100000.00,
  "params_override": {
    "min_net_buy": "60000000",
    "stop_loss_pct": "-0.03"
  },
  "benchmark": "000300.SH",
  "max_positions": 3,
  "commission_rate": 0.0003,
  "slippage": 0.001
}
```

**字段校验**:
| 字段 | 约束 |
|------|------|
| strategy_name | 必填，必须已存在 |
| start_date | 必填，不早于数据最早日期 |
| end_date | 必填，不晚于昨日 |
| 日期跨度 | 最短 30 天，最长 3 年 |
| initial_capital | 必填，10000-10000000 之间 |
| params_override | 可选，覆盖策略默认参数 |
| benchmark | 可选，默认沪深300 |
| max_positions | 可选，默认策略配置值，1-10 之间 |
| commission_rate | 可选，默认 0.0003，0-0.01 之间 |
| slippage | 可选，默认 0.001，0-0.05 之间 |

**响应**:
```json
{
  "code": 0,
  "data": {
    "task_id": "bt_abc123",
    "status": "pending",
    "queue_position": 1,
    "estimated_seconds": 300,
    "poll_url": "/api/v1/backtest/bt_abc123"
  }
}
```

**约束**: 同时最多 2 个回测任务运行中，超出排队

---

#### GET `/api/v1/backtest/{task_id}`

查询回测状态/结果

**响应**:
```json
{
  "code": 0,
  "data": {
    "task_id": "bt_abc123",
    "status": "completed",
    "progress": 100,
    "created_at": "2026-04-16T10:00:00+08:00",
    "started_at": "2026-04-16T10:00:01+08:00",
    "completed_at": "2026-04-16T10:05:30+08:00",
    "result": {
      "total_trades": 120,
      "win_rate": 0.58,
      "total_profit": 18500.00,
      "total_profit_pct": 0.185,
      "max_drawdown": -0.082,
      "sharpe_ratio": 1.85,
      "nav_curve_truncated": false
    }
  }
}
```

---

#### GET `/api/v1/backtest/tasks`

回测任务列表

**请求参数**:
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| status | string | 否 | pending/running/completed/failed |
| page | int | 否 | 页码 |
| page_size | int | 否 | 每页数量 |

---

#### DELETE `/api/v1/backtest/{task_id}`

取消/删除回测任务

- `pending` 状态: 直接取消
- `running` 状态: 取消并清理
- `completed`/`failed` 状态: 删除记录

---

#### GET `/api/v1/backtest/compare`

多组回测结果对比

**请求参数**:
```
GET /api/v1/backtest/compare?task_ids=bt_abc123,bt_def456,bt_ghi789
```

**响应**:
```json
{
  "code": 0,
  "data": {
    "tasks": [...],
    "comparison": {
      "metrics": ["total_trades", "win_rate", "sharpe_ratio", "max_drawdown"],
      "best": {"task_id": "bt_def456", "metric": "sharpe_ratio", "value": 2.15}
    }
  }
}
```

---

### 2.8 看板 API（拆分自大接口）

#### GET `/api/v1/aggregations/robots-summary`

机器人汇总

```json
{
  "code": 0,
  "data": {
    "total": 5,
    "running": 3,
    "paused": 1,
    "eliminated": 1
  }
}
```

---

#### GET `/api/v1/aggregations/today-trades`

今日交易汇总

```json
{
  "code": 0,
  "data": {
    "date": "2026-04-16",
    "selected": 8,
    "pending": 5,
    "completed": 3,
    "total_profit": 2500.00,
    "best": {"robot_id": 1, "stock_code": "000001", "profit": 800.00},
    "worst": {"robot_id": 2, "stock_code": "600000", "profit": -200.00}
  }
}
```

---

#### GET `/api/v1/aggregations/portfolio-nav`

全局合并净值曲线

> 所有运行中机器人的合并净值

**请求参数**: 同 `/robots/{id}/nav`

---

#### GET `/api/v1/aggregations/global-stats`

全局统计

```json
{
  "code": 0,
  "data": {
    "total_robots": 5,
    "active_robots": 3,
    "total_trades": 150,
    "win_rate": 0.62,
    "total_profit": 25000.00,
    "avg_profit_per_trade": 166.67,
    "best_robot": {"id": 1, "name": "Alpha-1", "total_profit": 15000.00},
    "worst_robot": {"id": 3, "name": "Alpha-3", "total_profit": -2000.00}
  }
}
```

---

### 2.9 审计日志 API ⭐ P1

#### GET `/api/v1/audit/trades/{trade_id}/events`

交易事件流

```json
{
  "code": 0,
  "data": [
    {"event_type": "phase_a", "event_time": "...", "price": 12.000, "description": "选股买入"},
    {"event_type": "monitor", "event_time": "...", "price": 12.500, "description": "触发 sell1"},
    {"event_type": "sell1", "event_time": "...", "price": 12.360, "shares": 333, "description": "卖出1/3"}
  ]
}
```

---

#### GET `/api/v1/audit/robots/{id}/log`

机器人审计日志

**请求参数**:
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| event_type | string | 否 | 事件类型过滤 |
| start_date | string | 否 | 开始日期 |
| end_date | string | 否 | 结束日期 |
| page | int | 否 | 页码 |
| page_size | int | 否 | 每页数量 |

---

#### GET `/api/v1/selection/log`

选股决策日志（含被拒绝的股票）

**请求参数**:
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| robot_id | int | 否 | 机器人ID |
| trade_date | string | 否 | 交易日期 |
| decision | string | 否 | selected/rejected/filtered |
| page | int | 否 | 页码 |
| page_size | int | 否 | 每页数量 |

---

### 2.10 系统 API

#### GET `/api/v1/system/health`

健康检查

**响应**:
```json
{
  "code": 0,
  "data": {
    "status": "healthy",
    "version": "2.0.0",
    "uptime_seconds": 86400,
    "services": {
      "database": {"status": "ok", "latency_ms": 5},
      "redis": {"status": "ok", "latency_ms": 1}
    },
    "data_providers": {
      "tushare": {"status": "ok", "latency_ms": 150},
      "akshare": {"status": "degraded", "latency_ms": 2000, "failure_rate": 0.1}
    }
  }
}
```

---

#### GET `/api/v1/system/data-health`

数据质量检查

```json
{
  "code": 0,
  "data": {
    "freshness": [
      {"table": "lhb_stock_daily", "latest_date": "2026-04-15", "lag_hours": 12, "status": "ok"},
      {"table": "market_snapshots", "latest_date": "2026-04-16T10:50:00", "lag_minutes": 10, "status": "ok"},
      {"table": "market_sentiment", "latest_date": "2026-04-15", "lag_hours": 24, "status": "warning"}
    ],
    "consistency": [
      {"check": "lhb无K线匹配", "issue_count": 0, "status": "ok"},
      {"check": "交易无现金流水", "issue_count": 2, "status": "warning"}
    ]
  }
}
```

---

#### GET `/api/v1/system/scheduler-status`

定时任务执行状态

```json
{
  "code": 0,
  "data": {
    "tasks": [
      {"name": "phase_a", "last_run": "2026-04-15T18:50:00", "next_run": "2026-04-16T18:50:00", "status": "completed", "duration_sec": 5},
      {"name": "monitor", "last_run": "2026-04-16T10:50:00", "next_run": "2026-04-16T10:50:10", "status": "running"}
    ]
  }
}
```

---

#### POST `/api/v1/system/notify/test`

测试通知

---

#### GET `/api/v1/system/config`

获取系统配置

---

#### PUT `/api/v1/system/config`

更新系统配置

**危险操作保护**:
- 修改 `SKIP_CIRCUIT_BREAKER`、`MONITOR_INTERVAL_SEC` 等关键参数需要审计日志
- 返回操作前后的值变更

---

### 2.11 数据管理 API

#### POST `/api/v1/actions/sync`

触发数据同步

> 异步接口，幂等（相同参数返回已有结果）

---

#### POST `/api/v1/actions/backfill`

数据回填

> 异步接口

**约束**: 大量回填任务限制在非交易时段执行

---

## 三、WebSocket API ⭐ P1

### 3.1 连接

```
ws://host:port/ws?token=xxx
```

> **安全说明**：token 在 URL query 参数中传递是 WebSocket 握手的标准做法（浏览器无法自定义 Header）。生产环境应使用短期有效的临时授权 token（有效期 5 分钟），而非长期 API Key。

### 3.2 安全规范

#### 初期（v1.0）- 开发/内网环境

| 限制项 | 限制值 | 说明 |
|--------|--------|------|
| 认证 | 无 | 内网环境免认证 |
| 单 IP 连接数 | 最多 5 个 | 防止单机滥用 |
| 单连接订阅数 | 最多 20 个股票 + 10 个机器人 | 控制推送量 |
| 空闲超时 | 5 分钟无活动断开 | 释放资源 |
| 消息速率 | 每秒最多 10 条消息 | 防刷屏 |

**超限响应**：
```json
{
  "type": "error",
  "code": "CONNECTION_LIMIT_EXCEEDED",
  "message": "单IP最多5个连接",
  "current": 5,
  "limit": 5
}
```

```json
{
  "type": "error",
  "code": "SUBSCRIPTION_LIMIT_EXCEEDED",
  "message": "单连接最多订阅20个股票行情",
  "current": 20,
  "limit": 20
}
```

#### 后续（v1.1+）- 生产环境规划

| 功能 | 说明 |
|------|------|
| 认证 | token 校验通过后才允许订阅 |
| 权限控制 | 按用户权限控制可订阅的 robot_id 范围 |
| 加密 | wss:// 强制 TLS |
| 审计 | 记录连接/订阅/异常事件 |

### 3.3 心跳协议

- 客户端每 30 秒发送 `ping`
- 服务端回复 `pong`
- 连续 3 次未收到 `pong` 则断开连接

### 3.3 订阅机制

**客户端 → 服务端**

```json
{"action": "subscribe", "channel": "quotes", "params": {"stock_codes": ["000001", "600000"]}}
{"action": "subscribe", "channel": "pnl", "params": {"robot_ids": [1, 2]}}
{"action": "unsubscribe", "channel": "quotes"}
{"action": "ping"}
```

**服务端 → 客户端**

```json
{"type": "subscribed", "channel": "quotes", "subscribers": 1}
{"type": "error", "message": "Invalid channel"}
{"type": "pong"}
```

### 3.4 推送消息格式

```json
{
  "type": "price_update",
  "seq": 12345,
  "timestamp": "2026-04-16T10:50:00+08:00",
  "data": {...}
}
```

| 字段 | 说明 |
|------|------|
| type | 消息类型 |
| seq | 序列号（递增，用于检测丢失） |
| timestamp | 服务端时间戳 |
| data | 消息数据 |

### 3.5 通道规格

| 通道 | 推送频率 | 说明 |
|------|----------|------|
| quotes | 1秒/次 | 只推送已订阅的股票 |
| pnl | 10秒/次 | 只推送已订阅的机器人 |
| orders | 事件触发 | 订单状态变更 |
| monitor | 事件触发 | 监控告警 |

### 3.6 消息类型

#### price_update
```json
{
  "type": "price_update",
  "seq": 12345,
  "timestamp": "2026-04-16T10:50:00+08:00",
  "data": {
    "stock_code": "000001",
    "price": 12.800,
    "change_pct": 0.0240,
    "high": 13.000,
    "low": 12.500,
    "volume": 5000000,
    "source": "akshare"
  }
}
```

#### pnl_update
```json
{
  "type": "pnl_update",
  "seq": 12346,
  "timestamp": "2026-04-16T10:50:00+08:00",
  "data": {
    "robot_id": 1,
    "total_nav": 1.0523,
    "today_profit": 500.00,
    "positions": [
      {"trade_id": 101, "stock_code": "000001", "profit": 800.00, "profit_pct": 0.0667}
    ]
  }
}
```

#### order_event
```json
{
  "type": "order_event",
  "seq": 12347,
  "timestamp": "2026-04-16T10:50:00+08:00",
  "data": {
    "event_type": "buy",
    "robot_id": 1,
    "trade_id": 101,
    "stock_code": "000001",
    "price": 12.500,
    "shares": 1000,
    "status": "filled"
  }
}
```

---

## 四、API 版本演进

### 4.1 版本策略

- **破坏性变更**: 升大版本 (v1 → v2)
- **非破坏性变更**: 在当前版本内迭代
- **旧版本保留**: 至少 3 个月后废弃
- **响应头**: `X-API-Version: 1.0.0`

### 4.2 破坏性变更定义

- 删除或重命名字段
- 改变字段类型
- 改变字段语义
- 删除接口

### 4.3 非破坏性变更

- 新增字段
- 新增接口
- 新增可选参数

### 4.4 废弃机制

```
Deprecation: true
Sunset: 2026-07-01
```

---

## 五、测试示例

### 5.1 curl 示例

```bash
# 获取机器人列表
curl "http://localhost:8000/api/v1/robots?status=running" \
  -H "Accept: application/json"

# 创建机器人（带幂等键）
curl -X POST "http://localhost:8000/api/v1/robots" \
  -H "Content-Type: application/json" \
  -H "X-Idempotency-Key: create-robot-001" \
  -d '{"name": "Alpha-2", "strategy_name": "lhb_multifactor", "initial_capital": 100000}'

# 批量暂停机器人
curl -X POST "http://localhost:8000/api/v1/robots/batch" \
  -H "Content-Type: application/json" \
  -d '{"action": "pause", "robot_ids": [1, 2, 3]}'

# WebSocket 订阅
wscat -c "ws://localhost:8000/ws"
> {"action": "subscribe", "channel": "quotes", "params": {"stock_codes": ["000001"]}}
```

---

## 六、已采纳的评审优化

### 首次评审（2026-04-16）

| 评审项 | 采纳情况 |
|--------|----------|
| REST + RPC 风格分离 | ✅ 已采纳 |
| 批量操作接口 | ✅ 已采纳 |
| Dashboard 拆分轻量化 | ✅ 已采纳 |
| 分页/排序/过滤规范化 | ✅ 已采纳 |
| 响应格式统一（百分比小数） | ✅ 已采纳 |
| Pydantic 严格校验 | ✅ 已采纳 |
| 危险操作保护 | ✅ 已采纳 |
| 幂等性设计 | ✅ 已采纳 |
| WebSocket 协议完善 | ✅ 已采纳 |
| 手动交易接口 | ✅ 已采纳 |
| 审计日志接口 | ✅ 已采纳 |
| 数据质量监控接口 | ✅ 已采纳 |
| 回测任务管理 | ✅ 已采纳 |
| 缓存策略 | ✅ 已采纳 |
| 大数据量保护 | ✅ 已采纳 |

### 二次评审修订（2026-04-16）

| 评审项 | 采纳情况 |
|--------|----------|
| DELETE 接口语义修正 | ✅ 已采纳（新增 RPC 风格删除接口） |
| 手动交易事务保障 | ✅ 已采纳（补充完整事务规范） |
| WebSocket 安全规范 | ✅ 已采纳（补充限流和演进规划） |
| 幂等性实现规范 | ✅ 已采纳（补充完整实现细节） |
| 批量操作原子性定义 | ✅ 已采纳（新增 atomic 参数） |
| 错误码 HTTP Status 映射 | ✅ 已采纳（3002 改为 409） |
| 选股触发接口参数定义 | ✅ 已采纳 |
| 回测接口请求体定义 | ✅ 已采纳 |
| WebSocket token 安全性 | ❌ 不采纳（见文件开头说明） |
