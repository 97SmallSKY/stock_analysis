# S04 — 前端（Vue3 Dashboard）

> **阶段目标**：构建龙虎榜量化交易系统的实时监控面板，覆盖机器人管理、选股信号、持仓盈亏、回测分析等核心页面，通过 WebSocket 实现盘中数据实时推送。

---

## 0. 前置阅读（必须先读）

- `docs/lhb-quant-v2/API.md` — 所有接口路径、请求/响应 Schema（前端唯一数据合约）
- `docs/lhb-quant-v2/ARCHITECTURE.md` 第五章 — 前端目录结构与数据流规范
- S03 产出的 OpenAPI Spec（`http://localhost:8000/openapi.json`）

---

## 1. 任务目标

1. **仪表盘总览**：机器人健康状态、今日选股、净值曲线摘要
2. **机器人管理**：CRUD、状态切换（模拟盘 → 实盘）、AHS 评分展示
3. **选股中心**：当日 Phase A/B 候选列表、评分因子可视化、历史命中率
4. **交易管理**：持仓列表（实时盈亏）、历史交易记录、手动卖出操作
5. **回测分析**：参数配置、执行进度、结果图表（净值曲线、回撤分析）
6. **市场数据**：龙虎榜明细、K 线图、席位热力图

---

## 2. 技术栈约定

| 层次 | 选型 | 版本 | 说明 |
|------|------|------|------|
| 框架 | Vue 3 + TypeScript | 3.4+ | Composition API，`<script setup>` 语法 |
| 构建 | Vite | 5.x | 开发/构建工具 |
| UI | Ant Design Vue | 4.x | 主组件库，不引入其他 UI 框架 |
| 状态管理 | Pinia | 2.x | 持久化状态（机器人、配置）用 Pinia |
| HTTP | Axios | 1.x | 统一 instance，注入 Token 和错误处理 |
| 图表 | ECharts 5 | 5.x | 净值曲线、情绪图、热力图 |
| K 线 | LightweightCharts | 4.x | 专用金融图表，不用 ECharts 替代 |
| 表格 | AG-Grid Vue3 | 31.x | 持仓表、龙虎榜大数据表格 |
| 实时状态 | Vue `reactive` | - | 实时行情/盈亏**不放入 Pinia**，避免过度响应 |

---

## 3. 项目结构（严格遵守，不得随意新增顶层目录）

```
frontend/src/
├── api/
│   ├── client.ts          # Axios 实例：baseURL、Token 注入、响应拦截、错误统一处理
│   ├── robots.ts          # /api/v1/robots/* 接口封装
│   ├── trades.ts          # /api/v1/trades/* 接口封装
│   ├── market.ts          # /api/v1/market/* 接口封装
│   ├── selection.ts       # /api/v1/selection/* 接口封装
│   ├── backtest.ts        # /api/v1/backtest/* 接口封装
│   └── ws.ts              # WebSocket 客户端单例（自动重连、心跳、频道订阅）
├── stores/
│   ├── robotStore.ts      # 机器人列表、当前选中机器人
│   ├── appStore.ts        # 全局：主题、侧边栏折叠、通知列表
│   └── wsStore.ts         # WebSocket 连接状态（connected/disconnected/reconnecting）
├── views/
│   ├── Dashboard/
│   │   └── index.vue      # 总览：机器人卡片、今日选股摘要、净值折线
│   ├── Robots/
│   │   ├── index.vue      # 机器人列表（AG-Grid）
│   │   ├── Detail.vue     # 机器人详情：AHS 仪表盘、净值曲线、持仓
│   │   └── CreateModal.vue # 创建/编辑机器人弹窗
│   ├── Selection/
│   │   ├── index.vue      # 当日选股列表（Phase A/B 切换）
│   │   └── ScoreDetail.vue # 单只股票评分因子拆解
│   ├── Trades/
│   │   ├── Positions.vue  # 当前持仓（实时盈亏，WebSocket 更新）
│   │   └── History.vue    # 历史交易记录（可筛选、导出）
│   ├── Backtest/
│   │   ├── index.vue      # 回测配置表单
│   │   └── Result.vue     # 回测结果：净值曲线、指标卡、交易明细
│   ├── Market/
│   │   ├── Lhb.vue        # 龙虎榜明细（日期选择 + AG-Grid）
│   │   ├── KLine.vue      # K 线图（LightweightCharts）
│   │   └── Seats.vue      # 席位热力图（ECharts）
│   └── System/
│       └── Settings.vue   # 系统配置（飞书 Webhook、任务开关）
├── components/
│   ├── charts/
│   │   ├── NavChart.vue       # 净值曲线（折线 + 回撤面积）
│   │   ├── AhsGauge.vue       # AHS 评分仪表盘（ECharts Gauge）
│   │   ├── SeatHeatmap.vue    # 席位活跃热力图
│   │   └── FactorRadar.vue    # 评分因子雷达图
│   ├── common/
│   │   ├── StatusBadge.vue    # 机器人/交易状态徽章（颜色映射）
│   │   ├── PriceChange.vue    # 涨跌幅显示（红/绿，带箭头）
│   │   ├── WsIndicator.vue    # WebSocket 连接状态指示灯
│   │   ├── EmptyState.vue     # 统一空状态占位
│   │   └── ConfirmModal.vue   # 危险操作二次确认弹窗
│   └── layout/
│       ├── AppLayout.vue      # 整体布局：侧边导航 + 顶部栏 + 内容区
│       └── AppHeader.vue      # 顶部栏：标题、WS 状态、通知铃铛
├── composables/
│   ├── useWebSocket.ts    # WebSocket 订阅/退订 Hook，组件卸载自动清理
│   ├── usePolling.ts      # 降级轮询 Hook（WS 断线时使用）
│   ├── useTradeFormat.ts  # 金额/价格/涨跌幅格式化（统一精度）
│   └── useConfirm.ts      # 危险操作确认 Hook（包装 Modal.confirm）
├── router/
│   └── index.ts           # 路由配置（懒加载所有页面组件）
├── utils/
│   ├── format.ts          # 数字、日期、百分比格式化
│   ├── color.ts           # 涨跌红绿色、AHS 区间色映射
│   └── trading-day.ts     # 判断今日是否交易日（前端校验用）
└── types/
    └── api.d.ts           # 与后端 API Schema 对应的 TypeScript 类型（手动维护或生成）
```

---

## 4. 具体任务

### 4.1 WebSocket 客户端（`api/ws.ts`）

```typescript
// 单例模式，全应用复用一个连接
class WsClient {
  private ws: WebSocket | null = null
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null
  private reconnectDelay = 1000   // 初始 1s，指数退避，上限 30s
  private heartbeatTimer: ReturnType<typeof setInterval> | null = null

  connect(url: string): void          // 建立连接，注册事件处理
  disconnect(): void                  // 主动断开，清理所有定时器
  subscribe(channel: string, handler: (data: unknown) => void): () => void
                                      // 返回 unsubscribe 函数，组件 onUnmounted 调用
  private startHeartbeat(): void      // 每 30s 发 ping，10s 无 pong 则重连
  private handleReconnect(): void     // 指数退避重连
}

// WebSocket 事件类型（来自 API.md）
type WsEvent =
  | { event: 'robot.trade_updated'; data: TradeUpdatedPayload }
  | { event: 'robot.pnl_updated'; data: PnlUpdatedPayload }
  | { event: 'market.quote'; data: QuotePayload }
  | { event: 'system.task_done'; data: TaskDonePayload }
  | { event: 'ping' }
  | { event: 'pong' }
```

**离线降级规则**：
- WS 断开超过 10s → 切换为轮询模式（每 10s 调用 REST API）
- WS 恢复 → 取消轮询，恢复 WS 推送
- 离线状态在顶部栏展示 `WsIndicator` 黄色警告

### 4.2 持仓页面实时盈亏（`views/Trades/Positions.vue`）

```typescript
// 持仓数据存 reactive，不存 Pinia（高频更新不需要持久化）
const positions = reactive<Map<number, Position>>(new Map())

// 订阅 WebSocket 实时更新
const unsub = wsClient.subscribe('robot.pnl_updated', (data) => {
  const p = data as PnlUpdatedPayload
  if (positions.has(p.trade_id)) {
    Object.assign(positions.get(p.trade_id)!, {
      current_price: p.current_price,
      unrealized_pnl: p.unrealized_pnl,
      pnl_ratio: p.pnl_ratio,
    })
  }
})
onUnmounted(() => unsub())

// 表格列定义（AG-Grid）
const colDefs = [
  { field: 'stock_code', headerName: '代码', width: 90 },
  { field: 'stock_name', headerName: '名称', width: 80 },
  { field: 'buy_price', headerName: '买入价', width: 80, valueFormatter: priceFormatter },
  { field: 'current_price', headerName: '现价', width: 80, cellStyle: priceChangeStyle },
  { field: 'unrealized_pnl', headerName: '浮盈', width: 100, cellStyle: pnlStyle },
  { field: 'pnl_ratio', headerName: '收益率', width: 90, valueFormatter: pctFormatter },
  { field: 'hold_days', headerName: '持仓天', width: 80 },
  { field: 'status', headerName: '状态', width: 80, cellRenderer: StatusBadgeRenderer },
  { headerName: '操作', width: 100, cellRenderer: ActionRenderer },  // 手动卖出按钮
]
```

### 4.3 AHS 评分仪表盘（`components/charts/AhsGauge.vue`）

```typescript
// AHS 区间色映射（与 RISK_CONTROL.md 保持一致）
const AHS_COLOR_MAP = [
  { max: 20,  color: '#52c41a', label: '正常' },    // 绿
  { max: 40,  color: '#a0d911', label: '谨慎' },    // 黄绿
  { max: 60,  color: '#faad14', label: '保守' },    // 橙
  { max: 80,  color: '#fa8c16', label: '警戒' },    // 深橙
  { max: 100, color: '#f5222d', label: '停止开仓' }, // 红
]

// ECharts Gauge 配置
const option = computed(() => ({
  series: [{
    type: 'gauge',
    min: 0, max: 100,
    axisLine: {
      lineStyle: {
        width: 20,
        color: AHS_COLOR_MAP.map(({ max, color }) => [max / 100, color])
      }
    },
    detail: { formatter: '{value}', fontSize: 28 },
    data: [{ value: props.ahsScore, name: 'AHS 风险分' }]
  }]
}))
```

### 4.4 回测结果页（`views/Backtest/Result.vue`）

回测提交后通过轮询 `GET /api/v1/backtest/{task_id}` 获取进度（间隔 2s）：

```typescript
const { data, stop } = usePolling(
  () => api.getBacktestResult(taskId.value),
  { interval: 2000, stopCondition: (r) => ['done', 'failed'].includes(r.status) }
)
watch(data, (r) => {
  if (r?.status === 'done') {
    stop()
    renderCharts(r.result)
  }
})
```

回测完成后渲染：
1. **净值曲线**（ECharts 折线图，含策略净值 vs 沪深300基准）
2. **回撤面积图**（与净值曲线叠加，红色半透明）
3. **绩效指标卡**：年化收益率、夏普比率、最大回撤、胜率、盈亏比
4. **交易明细表**（AG-Grid，含买入/卖出价格、持仓天数、收益率）

---

## 5.
<function_calls>
<invoke name="Bash">
<parameter name="command">cat >> ~/Develop/personal/stock_analysis/docs/prompts/S04_FRONTEND.md << 'PROMPT_EOF'
┴────────────────────────────┤
│  LhbHeatmap（游资近 20 日活跃度热力图，全宽）        │
└─────────────────────────────────────────────────┘
```

**交互要求**：
- 日期选择器默认显示最新交易日，不可选未来日期
- LhbTable 支持按 `net_buy_amount` / `turnover_ratio` 排序，虚拟滚动支持 1000+ 行不卡顿
- 点击股票行展开侧边详情（近 30 日龙虎榜历史折线图）
- 游资排行柱状图：正值绿色，负值红色，鼠标悬停显示具体金额（万元单位）

### 4.2 信号面板（`/dashboard/signals`）

**布局**：
```
┌───────────────────────────────────────────────┐
│  今日信号 (N 只)    历史胜率统计卡片              │
├───────────────────────────────────────────────┤
│  SignalTable：股票代码 | 名称 | 评分 | 触发条件  │
│               连板天数 | 游资净买入 | 情绪指数   │
├───────────────────────────────────────────────┤
│  点击行 → SignalScoreCard 展开（雷达图展示各维度得分）│
└───────────────────────────────────────────────┘
```

**信号评分雷达图**（ECharts radar）维度：
- 游资强度（净买入金额得分）
- 连板质量（连板天数 × 晋级率）
- 市场情绪（当日情绪指数）
- 个股流动性（换手率得分）
- 历史成功率（相似信号历史胜率）

**实时更新**：收到 `signal.new` WebSocket 事件时，列表顶部插入新信号，并显示 3 秒高亮动画

### 4.3 持仓面板（`/dashboard/positions`）

**布局**：
```
┌─────────────────────────────────────────────────────┐
│  总资产: ¥xxx  持仓市值: ¥xxx  今日盈亏: +¥xxx(+x%)  │
│  RiskStatusCard（风控状态：正常/预警/危险）            │
├─────────────────────────────────────────────────────┤
│  PositionTable：                                     │
│  股票 | 成本价 | 现价 | 持仓天数 | 盈亏金额 | 盈亏% │
│        | 止损价 | 目标价 | 状态 | 操作（平仓按钮）   │
└─────────────────────────────────────────────────────┘
```

**实时更新规则**：
- 收到 `position.updated` 事件时，对应行数据原地刷新（不重新请求全量）
- 收到 `risk.alert` 事件时，RiskStatusCard 颜色变化 + 弹出 Toast 通知
- 盈亏颜色：盈利红色（A 股习惯），亏损绿色
- 平仓按钮：点击弹出确认对话框，确认后调用 `DELETE /api/v1/trading/orders/{id}`，操作中显示 loading 态

### 4.4 组合面板（`/dashboard/portfolio`）

**指标卡片**（`MetricCard` 组件，2×3 网格）：

| 卡片 | 数据字段 | 格式 |
|------|---------|------|
| 累计收益率 | `total_return` | `+23.5%`（红/绿色） |
| 年化收益率 | `annualized_return` | `+18.2%` |
| 夏普比率 | `sharpe_ratio` | `1.85` |
| 最大回撤 | `max_drawdown` | `-12.3%`（橙色警示） |
| 胜率 | `win_rate` | `62.5%` |
| 盈亏比 | `profit_loss_ratio` | `2.3 : 1` |

**净值曲线图**（`NavChart`，全宽）：
- 主轴：净值折线（蓝色）+ 基准沪深 300（灰色虚线）
- 副轴：回撤面积（红色半透明，填充到 0 轴）
- 时间范围切换按钮：近 1 月 / 近 3 月 / 近 6 月 / 成立以来
- Tooltip 显示：日期、净值、当日涨跌幅、回撤幅度

### 4.5 市场情绪面板（`/dashboard/market`）

**组件构成**：
- `SentimentChart`：近 30 日情绪指数 + 涨停数折线图（双 Y 轴）
- `LimitUpLadder`：当日连板梯队柱状图（首板/2板/3板/4板+）
- 涨跌停统计数字卡片：今日涨停数 / 跌停数 / 炸板率

---

## 5. 通用规范

### 5.1 数字格式化（`lib/format.ts`，全局统一）

```typescript
// 金额：自动换算单位
formatAmount(5200000)   → "520.0万"
formatAmount(1500000000) → "15.0亿"

// 涨跌幅：带符号，红涨绿跌（A股习惯）
formatPct(0.0523)  → "+5.23%"   // 红色
formatPct(-0.0312) → "-3.12%"   // 绿色

// 日期
formatDate("2024-01-15")  → "2024-01-15 (周一)"
```

### 5.2 WebSocket Hook（`hooks/useWebSocket.ts`）

```typescript
// 单例连接，自动重连（指数退避：1s, 2s, 4s, 8s, 最大 30s）
// 连接状态：CONNECTING | CONNECTED | DISCONNECTED | ERROR
// RealtimeBadge 组件显示当前状态，DISCONNECTED 时显示橙色闪烁
const { status, on, off } = useWebSocket()
on('position.updated', (data) => updatePositionInStore(data))
```

### 5.3 错误处理规范

- HTTP 401 → 自动跳转 `/login`（清除本地 Token）
- HTTP 422（风控拒绝）→ Toast 显示具体拒绝原因，不跳转
- HTTP 500 → Toast 显示"服务异常，请稍后重试"
- 网络超时 → SWR 自动重试 3 次，仍失败则显示 ErrorBoundary 降级 UI

---

## 6. 验收标准

```bash
# 6.1 前端构建无报错、无 TypeScript 类型错误
cd frontend && npm run build

# 6.2 本地开发服务启动
npm run dev  # 访问 http://localhost:3000

# 6.3 Lighthouse 性能分数
# 首屏（/dashboard）Performance ≥ 80，Accessibility ≥ 90

# 6.4 核心组件单测
npm run test -- --coverage
# 覆盖率要求：lib/format.ts 100%，hooks/*.ts ≥ 80%

# 6.5 E2E 验证关键流程（Playwright）
npx playwright test tests/e2e/dashboard.spec.ts
# 覆盖：登录 → 查看龙虎榜 → 查看持仓 → 手动平仓确认弹窗
```

---

## 7. 禁止事项

- ❌ 不得在组件里直接调用 `fetch`，必须通过 `lib/api.ts` 的 axios 实例
- ❌ 不得手写 API 响应类型，必须从 `types/api.d.ts`（openapi-typescript 生成）引用
- ❌ 不得使用 ECharts 以外的图表库（禁止引入 recharts / chart.js / d3）
- ❌ 涨跌颜色不得用西方习惯（红跌绿涨），A 股必须红涨绿跌
- ❌ 不得在客户端组件里直接读取 `process.env`，通过 `NEXT_PUBLIC_` 前缀变量或服务端组件传递
- ❌ 持仓平仓操作必须有二次确认弹窗，不得单击直接执行

---

## 8. 输出产物

```
frontend/app/**/*.tsx             ← 6 个页面
frontend/components/**/*.tsx      ← 所有 UI 组件
frontend/hooks/*.ts               ← 数据 Hooks
frontend/lib/api.ts               ← HTTP 客户端
frontend/lib/ws.ts                ← WebSocket 客户端
frontend/lib/format.ts            ← 格式化工具
frontend/store/index.ts           ← Zustand 全局状态
frontend/types/api.d.ts           ← 自动生成类型（CI 中执行生成）
访问地址（本地开发）：http://localhost:3000
```
