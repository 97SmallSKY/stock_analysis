# K 线 Modal 组件契约(STO-41)

> **目标读者**:接手 K 线 Modal(`StockDrillModal.vue`)的任何 AI / 开发者。读完本文应能**不看源码**理解整个调用链,改代码不会踩坑。

> **单一事实源**:若本文与代码不一致,以代码为准,并请立即更新本文。

---

## 1. 组件全景

```
[列表 / 表格页]                        [全局单例 store]            [展示组件]
  Signals.vue  ─┐                         useStockDrill()           StockDrillModal.vue
  LhbList.vue  ─┼──┐                       │                          │
  LhbPnl.vue   ─┘  │                       │ state.tsCode              │ props 无
                   │  <StockLink           │ state.anchorDate          │ 订阅 state
                   │    :code="ts_code"    │ state.open                │ 自己拉 kline
                   │    :name="name"       ↓                            ↓
                   └──▶:anchor-date="row.trade_date" />  ──▶  drill.open(code, anchor_date?)
```

**关键点**:
- `StockLink` 是唯一入口(不要再有别的地方直接调 `drill.open`)
- `useStockDrill()` 是**全局单例 reactive store**,整个应用只挂一个 `StockDrillModal` 实例(通常在 `App.vue`)
- `StockDrillModal` 不接 props,它订阅 store,自己决定拉什么数据

---

## 2. `anchorDate` 传递链(P19.12 新增)

**用户故事**:用户在「决策信号」看到一条 2026-03-15 的信号,点 `000001.SZ` → 希望 K 线默认视图以 **2026-03-15 为中心**,而不是最新 6 个月。

**传递链**:

```
Signals.vue / LhbList.vue / LhbPnl.vue 表格每一行
    row: { ts_code, trade_date, ... }
         │
         ▼ <StockLink :anchor-date="row.trade_date" :code="row.ts_code" />
         │
    StockLink.vue (onClick)
         │
         ▼ drill.open(ts_code, anchor_date)
         │
    useStockDrill.state.anchorDate = "2026-03-15"
         │
         ▼ (StockDrillModal watch state.open)
         │
    StockDrillModal.ensureDaterange() → computeDateRange(key, dr, anchor)
         │
         ▼
    from = anchorDate - halfDays,  to = anchorDate + halfDays
    (clamp 到 [dr.min, dr.max])
```

**关键不变量**:
- `anchorDate` 格式:`YYYY-MM-DD`(ISO),由上游表格的 `trade_date` 字段提供。若为空 `null` 则走默认「最新日期锚定」语义
- **用户点击快捷钮 / 手动拖日期后,anchor 语义立即失效**:`applyRange(key)` 调用 `computeDateRange(key, dr)` **不传 anchor**,回归「最新日期锚定」
- anchor **不持久化**,关闭 Modal 即丢弃(每次 `drill.open(...)` 重新传入)

---

## 3. `rangeKey` 枚举(8 值固定)

定义位置:`frontend/src/components/StockDrillModal.vue` 第 120 行

| key | label | 半宽天数 (`halfDaysOf`) | 锚定语义 (`computeDateRange` 无 anchor 时) |
|---|---|---:|---|
| `latest` | 最新 | 30 | anchor - 60 自然日(约 40 交易日) |
| `5d` | 5 日 | 4 | anchor - 7 天 |
| `1w` | 1 周 | 5 | anchor - 10 天 |
| `2w` | 2 周 | 10 | anchor - 20 天 |
| `1m` | 1 个月 | 15 | anchor - 1 个月 |
| `3m` | 3 个月 | 45 | anchor - 3 个月 |
| **`6m`** | 6 个月 | 90 | anchor - 6 个月 **(默认值)** |
| `1y` | 1 年 | 180 | anchor - 12 个月 |

> **增删 rangeKey 的风险**:
> 1. `halfDaysOf(key)` 的 `switch` 必须穷尽 —— 漏一个 TypeScript 会报错,这是防线
> 2. `RANGE_OPTIONS` 和 `RANGE_LABEL_OF` 必须同步
> 3. 改半宽天数会影响「从信号跳入」的视觉效果(anchor 居中时的前后宽度)

---

## 4. 参数与后端 API 的边界对应

> 改前端 `limit` / `from` / `to` **前**必须对照后端 `backend/api/kline.py` 的 `Query(..., le=, ge=)`。
> 详见 [CLAUDE.md §11 前后端接口契约同步](../../CLAUDE.md#11-前后端接口契约同步p1913-血泪--sto-40)

| 前端调用 | 后端路由 | 参数边界 | 前端默认策略 |
|---|---|---|---|
| `fetchKlineDaily({ ts_code, from?, to?, limit })` | `GET /api/kline/daily` | `limit: 1..5000` / `ts_code` required | 靠 `from/to` 限定区间,不主动拉 5000 |
| `fetchKlineWeekly({ ts_code, from?, to?, limit })` | `GET /api/kline/weekly` | `limit: 1..2000` | 同上 |
| `fetchKlineMinute({ ts_code, trade_date })` | `GET /api/kline/minute` | `ts_code` + `trade_date` required | 需要 `pickedDate.value`,否则显示「请先选日期」空态 |
| `fetchKlineDaterange(ts_code, freq)` | `GET /api/kline/daterange/{ts_code}/{freq}` | `freq ∈ {daily, weekly, minute}` | 打开 Modal / 切 tab 时调一次,缓存在 `drRange` |

**计算规则**:
- `dateFrom` / `dateTo` 由 `computeDateRange(rangeKey, drRange, anchorOverride?)` 计算
- `dr.max` 是**本股最新有数据的日期**(不是今天!),停牌股 `dr.max` 可能早于今天
- clamp 到 `[dr.min, dr.max]`,避免拉不存在的区间

---

## 5. 点击日 K 蜡烛的行为(P19.12)

用户点击日 K 图任何一根蜡烛 → `pickedDate.value = <该蜡烛的 trade_date>` → 切到「分钟 K」tab 时自动拉该日的分钟数据。

**流程**:

```
用户点击蜡烛
    │
    ▼ echarts 'click' 事件
    │
pickedDate.value = "2026-03-15"
    │
    ▼ 用户手动切到「分钟 K」tab
    │
minuteTabLabel 显示 "分钟 K · 2026-03-15"
    │
    ▼ tab change watcher
    │
fetchKlineMinute({ ts_code, trade_date: "20260315" })
```

- **未点击任何蜡烛就切到分钟 K** → 空态 `no_pick`(「请先选择日期」)
- **切换股票(drill.open 新代码)** → `resetPicked()` 清空 `pickedDate`

---

## 6. 空态原因分类(`emptyReason` 状态机)

| 值 | 触发条件 | 文案 |
|---|---|---|
| `null` | 正常(有数据) | 不显示空态 |
| `no_source_table` | 后端返回「数据源尚未接入」(分钟 K 当前状态) | 数据源尚未接入 |
| `out_of_range` | 本股 `drRange` 为 null 或所选区间外 | 暂无行情数据 |
| `no_pick` | 切到分钟 K 但未 `pickedDate` | 请先选择日期 |
| `fetch_failed` | HTTP / 网络错误 | 网络或服务异常 |

---

## 7. 关键文件速查

| 文件 | 职责 |
|---|---|
| `frontend/src/composables/useStockDrill.ts` | 全局单例 store(`state.tsCode` / `state.anchorDate` / `state.open`) |
| `frontend/src/components/StockLink.vue` | 唯一入口组件,调 `drill.open(code, anchor_date)` |
| `frontend/src/components/StockDrillModal.vue` | 展示组件,订阅 store,执行拉数据 + 渲染 echarts |
| `frontend/src/components/DateRangeQuickPick.vue` | 快捷钮 UI(外部页面用,如 `LhbPnl`),**不在 Modal 内**(Modal 内的钮是 inline 定义) |
| `frontend/src/utils/date_range.ts` | `addDays / addMonths / toYmd` 日期工具 + 外部页面用的 `calcRange(key)` |
| `frontend/src/api/kline.ts` | HTTP 客户端 + TypeScript 类型定义 |
| `backend/api/kline.py` | 后端 FastAPI 路由 + 参数边界(单一事实源) |

---

## 8. 常见改动 checklist

### 加一个新的 `rangeKey`(例如 `2y`)
1. `StockDrillModal.vue`:
   - `type RangeKey` 加上 `'2y'`
   - `RANGE_OPTIONS` 加条目
   - `computeDateRange` switch 加 `case '2y': from = addMonths(anchor, -24); break`
   - `halfDaysOf` switch 加 `case '2y': return 365`
   - `RANGE_LABEL_OF` 加映射
2. 检查:`npx vue-tsc --noEmit` 必须 0 error(穷尽检查防漏改)
3. 检查:`npx vitest run` 相关 test 全绿
4. 若对应的 `limit` 有可能超过后端 `le=5000`,需同步后端边界 → 跨后端改动:参考 CLAUDE.md §11

### 改前端 `limit` 默认值
1. `grep -n 'Query' backend/api/kline.py` 确认后端 `le=`
2. 新 limit 必须 ≤ `le`,否则所有请求 422(P19.13 事故类型)
3. 补/更新契约测试 `tests/test_api_kline.py`

### 改 `anchorDate` 传递链
1. 所有 `<StockLink>` 调用处都要传 `:anchor-date`,否则回退「最新日期锚定」(不算 bug 但弱化功能)
2. `useStockDrill.state.anchorDate` 类型 `string | null`,不要传 Date 对象
3. 格式必须 `YYYY-MM-DD`(ISO),若表格原始数据是 `YYYYMMDD` 请先转换

---

## 9. 相关 issue / 历史改动

- **P19.2**:引入 `/api/kline/daterange`,解决 "`dr.max` 比今天早很多时拉空" 问题
- **P19.3**:引入 `rangeKey` 快捷钮
- **P19.12**:引入 `anchorDate` 链路(从信号/龙虎榜跳入的居中展示)
- **P19.13**(事故):前端 `limit=2000` 超过后端 `le=1000` 导致全挂 → CLAUDE.md §11 确立前后端契约同步红线
- **STO-40**:把 P19.13 教训写入 CLAUDE.md + 补越界 case 测试范式
- **STO-41**(本文档):把 K 线 Modal 契约文档化

---

**最后更新**:2026-04-21(STO-41)
