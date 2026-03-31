# 系统架构改造技术方案

> **版本：** v3.0  
> **起草时间：** 2026-03-30  
> **涵盖范围：** 行情数据修复 · 审计日志 · 价格轨迹 · 选股全记录 · LLM自然语言策略 · 代理优化 · 页面导航重构 · 评分说明

---

## 目录

1. [问题背景](#1-问题背景)
2. [需求确认汇总](#2-需求确认汇总)
3. [行情数据修复](#3-行情数据修复涨停板问题)
4. [限流与接口兜底策略](#4-限流与接口兜底策略)
5. [代理配置优化](#5-代理配置优化)
6. [审计日志设计](#6-审计日志设计)
7. [新增数据库表](#7-新增数据库表)
8. [LLM 职责边界](#8-llm-职责边界)
9. [LLM 自然语言策略](#9-llm-自然语言策略)
10. [monitor 执行引擎改造](#10-monitor-执行引擎改造)
11. [页面改造方案](#11-页面改造方案)
12. [T+N 日期定义规范](#12-tn-日期定义规范低优先级)
13. [实施步骤与优先级](#13-实施步骤与优先级)
14. [验证方案](#14-验证方案)
15. [开发规范](#15-开发规范)

---

## 1. 问题背景

### 1.1 神剑股份涨停板未卖出事件（2026-03-30 复盘）

**现象：** 神剑股份（002361）今日涨停至 15.00 元（+10%），远超所有卖出档位，但监控全天未触发任何卖出。

**根因：** 新浪行情 API 在涨停封板状态下返回异常数据：

```
字段名    数值     说明
open:    13.370   今日开盘价（低开）
last:    13.210   ⚠️ 最后一笔实际成交价（低开那笔），非当前价
high:    15.000   今日最高（= 涨停价）
low:     15.000   今日最低（= 涨停价，全天封板）
bid1:    15.000   ✅ 买1排队价 = 真实当前市价
ask1:    0.000    ✅ 卖1=0 代表封板无卖单
```

监控代码 `_get_current_price()` 只取 `last` 字段，导致计算涨幅为 `-1.2%`，远低于任何档位触发条件，永远不触发。

**正确逻辑：** 涨停封板时，真实可交易价格 = `bid1`（买1挂单价）。

### 1.2 其他已发现问题

| 问题 | 影响 |
|------|------|
| AKShare 东财接口通过代理失败（ProxyError）| Phase B 四层 fallback 降级到低优先级方法 |
| 盘中监控仅记录触发事件，无每次价格快照 | 无法事后还原价格走势，审计困难 |
| 无选股过程记录（候选股对比信息丢失） | 无法评估 LLM 选股质量 |
| 缺少结构化审计日志（黑盒操作）| 无法从页面追溯每次买卖决策过程 |
| 策略参数固定为三档，不支持自定义规则 | 无法针对不同股票特征灵活配置 |
| 页面导航无固定路由，顺序混乱 | 决策效率低 |

---

## 2. 需求确认汇总

| 需求 | 最终决策 |
|------|---------|
| 选股记录存储 | Phase A 时同步把**入选股 + Top3候选对比摘要**写入新表 `sim_selection_log`，同时写结构化 DB + 详细文件日志 |
| 价格轨迹预测 | 在价格折线图上叠加 **MA5/MA10 均线** |
| 策略语言风格 | 自由描述，LLM尽量解析为结构化 JSON 规则 |
| 策略确认方式 | LLM解析 → **网页展示解析结果** → 人工点确认 → 生效（仅网页，不需飞书） |
| 新策略与三档关系 | **完全替换三档逻辑**，但保留系统级 `-X%` 硬兜底止损（始终生效，不被覆盖） |
| LLM 策略执行 | LLM 一次性解析为结构化 JSON，运行时纯逻辑判断（不重复调 LLM） |
| 触发条件范围 | 涨跌幅 + 时间/状态条件 + RSI/MACD/均线等技术指标 |
| 审计日志存储 | **DB 存结构化记录**（可页面查询）+ **文件日志保留原始详情** |
| 审计日志分片 | 按天/小时保存 |
| 策略自动生成 | **每次选股后 LLM 自动草拟策略**，在页面展示，人工可修改后确认 |
| 代理处理 | **仅对 AKShare 东财接口绕过代理**（设置 `NO_PROXY`），其他流量保留代理 |
| 页面导航 | 改为 Streamlit **多页 app**，URL 变成 `/总览` `/模拟炒股` 等（可接受） |
| 评分说明展示 | 在决策辅助信号页面**内嵌可折叠「评分说明」区块**（点击展开看公式） |
| T+N 日期定义 | **低优先级**，后续统一处理 |

---

## 3. 行情数据修复（涨停板问题）

### 3.1 为什么新浪 bid1/ask1 可以完全解决问题

新浪行情 API 返回的 32 个字段中，关键字段含义：

| 字段下标 | 字段名 | 涨停封板时的值 | 说明 |
|---------|--------|--------------|------|
| [5] | `last` | 13.21（低开成交价）| ⚠️ 最后一笔实际成交价，封板后无成交时不再更新 |
| [6] | `bid1` | 15.00（涨停价）| ✅ 买1挂单价 = 当前市价（想买的人愿意出的最高价）|
| [7] | `ask1` | 0.00 | ✅ 卖1=0 代表无人愿意卖（封板状态）|

**涨停判断逻辑（有效价格修正）：**
```python
def _calc_effective_price(quote: dict) -> float:
    last = quote["last"]
    bid1 = quote.get("bid1", 0.0)
    ask1 = quote.get("ask1", 0.0)
    
    # 涨停封板：ask1==0（无卖单）且 bid1 > last（买方挂在涨停）
    if ask1 == 0.0 and bid1 > 0.0 and bid1 > last:
        return bid1   # 用买1价（= 涨停价）
    
    # 跌停封板：bid1==0（无买单）且 ask1 < last（卖方挂在跌停）
    if bid1 == 0.0 and ask1 > 0.0 and ask1 < last:
        return ask1   # 用卖1价（= 跌停价）
    
    # 正常行情：用 last
    return last if last > 0.0 else (bid1 or ask1 or quote["high"])
```

**为什么不换用 AKShare 东财接口：**

AKShare `stock_zh_a_spot_em` 是全市场接口（5000+只股票），每次请求量极大。VPS 上代理会对东财域名（`push2.eastmoney.com`）产生 ProxyError（已测试证实）。而新浪接口是单只股票轻量 HTTP，更适合盘中每3分钟的高频监控场景。修复 `bid1/ask1` 字段解析即可完全解决涨停板问题。

### 3.2 修改内容：`utils/retry.py`

```python
def fetch_sina_quote(code: str, timeout: int = 8) -> dict | None:
    """修复后返回字段增加 bid1 / ask1"""
    ...
    return {
        "name":       parts[0],
        "open":       float(parts[1]),
        "prev_close": float(parts[2]),
        "high":       float(parts[3]),
        "low":        float(parts[4]),
        "last":       float(parts[5]),
        "bid1":       float(parts[6]),   # ← 新增
        "ask1":       float(parts[7]),   # ← 新增
        "volume":     int(float(parts[8])) if parts[8] else 0,   # ← 新增
        "amount":     float(parts[9]) if parts[9] else 0.0,      # ← 新增
    }

def fetch_realtime_quote(code: str, timeout: int = 8) -> dict | None:
    """
    新包装函数：在 fetch_sina_quote 基础上增加 effective_price 字段。
    
    effective_price = 真实当前可交易价格（涨停/跌停场景下修正）
    """
    quote = fetch_sina_quote(code, timeout=timeout)
    if quote is None:
        return None
    quote["effective_price"] = _calc_effective_price(quote)
    return quote
```

---

## 4. 限流与接口兜底策略

### 4.1 交易高峰期（09:35）限流问题

**Phase B（09:35 回填开盘价）的四层 fallback（已实现）：**

```
方法1: 新浪实时行情 fetch_sina_quote → 取 open 字段
方法2: AKShare stock_zh_a_spot_em（东财全量，代理失败时跳过）
方法3: AKShare stock_zh_a_hist_min_em（分钟K线）
方法4: AKShare stock_zh_a_daily（日K线，09:35 可能未入库）
```

**限流失败时的兜底：**
- Phase B 有 09:35 + 09:45 两次 cron 触发（已有重试机制）
- Phase B 失败计数持久化到 DB，连续失败 ≥3 次发飞书报警（已实现）
- **新增**：失败时写入 `sim_audit_log`（event_type=`phase_b_fail`），方便页面追踪

**盘中监控（monitor_intraday）的单只行情兜底：**
```
方法1: fetch_sina_quote（主路，轻量HTTP）
方法2: 若新浪也失败，记录失败到 audit_log，跳过该持仓（3分钟后重试）
全部失败: 写 audit_log 标记 event_type=quote_fail，不崩溃退出
```

**"全部失效"时：**
- monitor 脚本捕获所有异常，不崩溃，写日志后静默退出
- 3 分钟后 crontab 重新触发（自动恢复）
- 若某持仓连续多次获取失败，写 `sim_audit_log` 并在页面标红提示

---

## 5. 代理配置优化

### 5.1 问题：东财接口走代理失败

VPS 当前环境变量：
```bash
HTTP_PROXY=http://127.0.0.1:10809
HTTPS_PROXY=http://127.0.0.1:10809
```

东财域名 `push2.eastmoney.com` 通过代理时 RemoteDisconnected，但直连 VPS 在国内，理论上可直接访问。

### 5.2 解决方案：设置 NO_PROXY

在 VPS 的 `~/.zshrc` 或 `/etc/environment` 中追加：
```bash
export NO_PROXY="localhost,127.0.0.1,*.feishu.cn,open.feishu.cn,*.eastmoney.com,push2.eastmoney.com,hq.sinajs.cn"
export no_proxy="localhost,127.0.0.1,*.feishu.cn,open.feishu.cn,*.eastmoney.com,push2.eastmoney.com,hq.sinajs.cn"
```

同时在 `.env.example` 中更新代理配置说明：
```env
# AKShare 东财接口需要绕过代理（VPS 在国内，直连更快）
# 在系统环境变量中设置：
# NO_PROXY=localhost,127.0.0.1,*.feishu.cn,*.eastmoney.com,hq.sinajs.cn
```

**此改动影响范围：**
- 新浪行情（`hq.sinajs.cn`）：绕过代理，走直连（更快，现有双路保障可简化）
- 东财接口（`push2.eastmoney.com`）：绕过代理，Phase B fallback 方法2 恢复可用
- 其他流量（Google等）：继续走代理

---

## 6. 审计日志设计

### 6.1 审计日志原则

- **不能是黑盒**：每次重要操作（选股/买入/卖出/止损/策略确认）都必须留记录
- **双轨制**：DB 存结构化记录（供页面查询），文件日志保留原始详情
- **按天分片**：文件日志按 `YYYYMMDD` 命名，DB 记录含 `event_date` 字段

### 6.2 `sim_audit_log` 表设计

```sql
CREATE TABLE IF NOT EXISTS sim_audit_log (
    id              SERIAL PRIMARY KEY,
    event_date      VARCHAR(8) NOT NULL,    -- YYYYMMDD，用于按天查询
    event_time      TIMESTAMP NOT NULL,     -- 精确到秒
    event_type      VARCHAR(32) NOT NULL,   -- 事件类型（见下表）
    trade_id        INT,                    -- 关联 sim_trades.id（可为 NULL）
    code            VARCHAR(6),             -- 股票代码（可为 NULL）
    name            VARCHAR(20),            -- 股票名称
    summary         TEXT NOT NULL,          -- 人类可读摘要（一句话说明发生了什么）
    detail          JSONB,                  -- 详细数据（原始数值、中间状态等）
    result          VARCHAR(16),            -- success / failed / skipped
    operator        VARCHAR(32) DEFAULT 'robot',  -- robot / user / system
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_audit_log_date ON sim_audit_log(event_date);
CREATE INDEX IF NOT EXISTS idx_audit_log_trade ON sim_audit_log(trade_id);
CREATE INDEX IF NOT EXISTS idx_audit_log_type ON sim_audit_log(event_type);
```

### 6.3 事件类型定义（`event_type`）

| 事件类型 | 触发场景 | 关键字段 |
|---------|---------|---------|
| `phase_a_select` | Phase A 选股完成 | trade_id, code, signal_score, llm_reason |
| `phase_a_skip` | Phase A 无满足条件股票 | detail: {reason, candidates_count} |
| `phase_b_buy` | Phase B 回填买入价成功 | trade_id, code, buy_price, buy_source |
| `phase_b_fail` | Phase B 获取开盘价失败 | trade_id, code, fail_count, methods_tried |
| `monitor_check` | 盘中监控检查一只股票（每3分钟） | trade_id, code, price, effective_price, change_pct |
| `monitor_sell` | 监控触发卖出（分批/止损） | trade_id, code, tier, price, profit_pct |
| `monitor_stop_loss` | 监控触发止损 | trade_id, code, price, change_pct |
| `quote_fail` | 行情获取失败 | code, methods_tried, error |
| `strategy_confirm` | 用户在页面确认 NL 策略 | trade_id, nl_text_preview, rules_count |
| `strategy_draft` | LLM 自动草拟策略 | trade_id, nl_text_preview |
| `force_close` | 持仓超期强制平仓 | trade_id, code, days_held, close_price |

### 6.4 文件日志分片策略

在 `logs/audit/` 目录下按天分片：
```
logs/
├── audit/
│   ├── 20260330.log    # 每天一个文件
│   ├── 20260331.log
│   └── ...
├── sim_robot.log       # Phase A/B 完整日志（保留）
└── monitor_intraday.log  # 监控日志（保留）
```

---

## 7. 新增数据库表

### 7.1 `sim_price_log`（价格轨迹记录）

每次 monitor 执行时，对每只持仓**无论是否触发**都写一条记录：

```sql
CREATE TABLE IF NOT EXISTS sim_price_log (
    id          SERIAL PRIMARY KEY,
    trade_id    INT    NOT NULL,
    code        VARCHAR(6) NOT NULL,
    checked_at  TIMESTAMP NOT NULL,       -- 检查时间（每3分钟一条）
    price       FLOAT NOT NULL,           -- effective_price（涨停修正后）
    price_raw   FLOAT,                    -- last（新浪原始，用于对比）
    high        FLOAT,                    -- 当日最高
    low         FLOAT,                    -- 当日最低
    bid1        FLOAT,                    -- 买1价
    ask1        FLOAT,                    -- 卖1价
    change_pct  FLOAT,                    -- 相对买入价涨幅%
    rsi14       FLOAT,                    -- RSI(14)（按需计算，策略有要求时）
    macd_hist   FLOAT,                    -- MACD柱状值（按需计算）
    sell1_price FLOAT,                    -- 当时档1目标价快照
    sell3_price FLOAT,                    -- 当时档3目标价快照
    stop_price  FLOAT,                    -- 当时止损价快照
    event       VARCHAR(32)               -- NULL / 止损 / 第1档 / 第2档 / 第3档
);
CREATE INDEX IF NOT EXISTS idx_price_log_trade ON sim_price_log(trade_id, checked_at);
```

### 7.2 `sim_selection_log`（选股过程记录）

Phase A 选股完成后，将**入选股 + Top3候选**写入此表：

```sql
CREATE TABLE IF NOT EXISTS sim_selection_log (
    id              SERIAL PRIMARY KEY,
    trade_date      VARCHAR(8) NOT NULL,   -- T 日（龙虎榜日期）
    trade_id        INT,                   -- 关联 sim_trades.id（仅入选股有值）
    rank            INT NOT NULL,          -- 1=入选，2=次优，3=第三候选
    code            VARCHAR(6) NOT NULL,
    name            VARCHAR(20),
    signal_score    FLOAT,                 -- 综合评分（0-100）
    t2_win_rate     FLOAT,                 -- T+2历史胜率
    close_price     FLOAT,                 -- T日收盘价
    reason_tech     TEXT,                  -- 技术面分析（从 LLM 响应提取）
    reason_fund     TEXT,                  -- 资金面分析
    reason_history  TEXT,                  -- 历史胜率分析
    llm_rank_reason TEXT,                  -- LLM 对该股排名理由（含与其他候选对比分析）
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_selection_log_date ON sim_selection_log(trade_date);
```

### 7.3 `sim_nl_strategy`（自然语言策略）

```sql
CREATE TABLE IF NOT EXISTS sim_nl_strategy (
    id              SERIAL PRIMARY KEY,
    trade_id        INT NOT NULL,          -- 绑定到具体持仓
    nl_text         TEXT NOT NULL,         -- 用户输入的自然语言（或LLM草拟）
    parsed_rules    JSONB,                 -- LLM 解析后的结构化规则 JSON
    hard_stop_pct   FLOAT DEFAULT -5.0,    -- 硬兜底止损线（始终生效，不被覆盖）
    status          VARCHAR(16) DEFAULT 'draft',  -- draft / active / expired
    source          VARCHAR(16) DEFAULT 'llm_auto', -- llm_auto / user_input
    confirmed_at    TIMESTAMP,             -- 人工确认时间
    confirmed_by    VARCHAR(32),           -- 确认人（目前固定为 'user'）
    parse_error     TEXT,                  -- LLM 解析失败时的错误信息
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_nl_strategy_trade ON sim_nl_strategy(trade_id, status);
```

---

## 8. LLM 职责边界

**核心原则：LLM 只做自然语言 → 结构化 JSON 的翻译，不做数值计算。**

### 8.1 LLM 负责

- 将用户输入的自然语言策略（如"涨停后次日 +5% 止盈，-3% 止损"）解析为 `parsed_rules` JSON
- 基于入选股的技术特征草拟策略建议（`draft_nl_strategy`）
- 生成选股理由（已有，`reason_tech/fund/history/seat`）

### 8.2 LLM 不负责

- **不计算 RSI/MACD 等技术指标**（由 Python 在 monitor 执行时实时计算）
- **不判断是否触发卖出**（由 `monitor_intraday.py` 按 JSON 规则纯逻辑判断）
- **不实时调用**（策略解析是一次性的，运行时不再调用 LLM）

### 8.3 技术指标计算时机

| 指标 | 计算时机 | 方法 |
|------|---------|------|
| `change_pct` | 每次 monitor 检查时，用 `effective_price / buy_price - 1` | Python 直接计算 |
| `RSI(14)` | 仅当 `parsed_rules` 中有 `rsi` 类型条件时 | 拉日K线后 ta 库计算 |
| `MACD hist` | 仅当 `parsed_rules` 中有 `macd_hist` 类型条件时 | 拉日K线后 ta 库计算 |
| `MA5/MA10` | 仅在页面价格轨迹图渲染时 | pandas rolling().mean() |

---

## 9. LLM 自然语言策略

### 9.1 `parsed_rules` JSON Schema

```json
{
  "version": 1,
  "rules": [
    {
      "id": "r1",
      "condition": {"type": "change_pct", "op": ">=", "value": 5.0},
      "action": {"type": "sell_all"},
      "priority": 1,
      "description": "涨幅达5%全仓卖出"
    }
  ],
  "hard_stop_pct": -5.0,
  "parsed_at": "2026-03-30T18:45:00",
  "parse_model": "qwen3-max-2026-01-23"
}
```

### 9.2 支持的条件类型（`condition.type`）

| 类型 | 说明 | 示例 |
|------|------|------|
| `change_pct` | 相对买入价涨跌幅 | `{"type":"change_pct","op":">=","value":5.0}` |
| `price` | 绝对价格 | `{"type":"price","op":">=","value":15.0}` |
| `rsi` | RSI指标（需拉K线计算）| `{"type":"rsi","op":">","value":80}` |
| `macd_hist` | MACD柱状值 | `{"type":"macd_hist","op":"<","value":0}` |
| `ma_cross` | 均线交叉/突破 | `{"type":"ma_cross","fast":5,"slow":10,"direction":"below"}` |
| `time_range` | 时间段条件 | `{"type":"time_range","after":"14:00"}` |
| `is_limit_up` | 涨停状态 | `{"type":"is_limit_up","value":true}` |
| `is_limit_down` | 跌停状态 | `{"type":"is_limit_down","value":true}` |
| `days_held` | 持仓自然日数 | `{"type":"days_held","op":">=","value":3}` |
| `AND` | 逻辑与 | `{"type":"AND","conditions":[...]}` |
| `OR` | 逻辑或 | `{"type":"OR","conditions":[...]}` |

### 9.3 支持的动作类型（`action.type`）

| 类型 | 说明 | 附加字段 |
|------|------|---------|
| `sell_all` | 全仓卖出 | — |
| `sell_pct` | 卖出百分比 | `pct`: 0~100 |
| `sell_shares` | 卖出固定股数 | `shares`: 正整数（100 的倍数）|
| `hold` | 明确持有，跳过后续规则 | — |

### 9.4 LLM 草拟策略 Prompt 要点

调用 `draft_nl_strategy()` 时，Prompt 包含：
- 入选股代码/名称/当日收盘价
- 技术特征摘要（趋势方向、动量、是否近高位）
- 历史 T+2 胜率
- 近期同类股票的亏损教训（从 `docs/loss_lessons.md` 提取）

要求 LLM 输出：
1. 一段自然语言策略建议（100-200字）
2. 对应的 `parsed_rules` JSON（符合上述 schema）

---

## 10. monitor 执行引擎改造

### 10.1 执行优先级

```
1. 硬兜底止损（hard_stop_pct）   ← 始终检查，不被覆盖
2. NL 策略规则（若有 active 策略）← 按 priority 顺序逐条匹配
3. 三档分批卖出（fallback）       ← 无 active NL 策略时使用
```

### 10.2 每次 monitor 执行流程

```
for 每只活跃持仓:
  1. fetch_realtime_quote(code)
     → 失败: 写 audit_log(quote_fail), 跳过, continue
  
  2. 计算 effective_price, change_pct
  
  3. 若 NL 策略中有技术指标条件（rsi/macd）:
     → 拉分钟/日K线, 计算指标
  
  4. 写 sim_price_log（无论是否触发，都记录）
  
  5. 写 audit_log(monitor_check) - INFO 级
  
  6. 检查 hard_stop_pct:
     if change_pct <= hard_stop_pct:
       → mark_stop_loss
       → 写 audit_log(monitor_stop_loss)
       → 推送飞书
       → continue（止损后不再检查其他条件）
  
  7a. 若有 active NL 策略:
      → 按 parsed_rules JSON 逐条判断
      → 命中 → 执行 action → 写 audit_log(monitor_sell)
  
  7b. 否则（fallback 三档）:
      → 检查 sell1/sell2/sell3 价格
      → 命中 → mark_batch_sold → 写 audit_log(monitor_sell)
```

### 10.3 进程独立性保障

```python
# monitor_intraday.py 顶部改造
try:
    from skills.stock_sim_robot.sim_trade_db import (
        get_active_batch_positions,
        mark_batch_sold,
        ...
    )
except Exception as e:
    logger.error(f"DB 模块导入失败: {e}")
    sys.exit(1)   # 有意义地退出，不崩溃抛出未捕获异常

# 所有 DB 写操作都有 try/except
# 行情获取失败 → 跳过该持仓，不退出整个脚本
```

---

## 11. 页面改造方案

### 11.1 导航重构：Streamlit 多页模式

**当前架构：** `app.py` 单文件，通过 `st.radio` 模拟导航，URL 始终是根路径。

**新架构：** 保持 `app.py` 作为首页（总览），创建 `pages/` 目录下的多页文件。

> 注意：Streamlit 多页模式下，URL 会变成 `/总览`、`/模拟炒股` 等。由于 Streamlit 内置 Emoji 路由 bug，**页面文件名不使用 Emoji 前缀**，改用数字前缀控制顺序：

```
app.py                    → 主页（URL: /）
pages/
├── 1_总览.py             → URL: /总览
├── 2_模拟炒股.py         → URL: /模拟炒股
├── 3_主板精选策略.py      → URL: /主板精选策略
├── 4_决策辅助信号.py      → URL: /决策辅助信号
├── 5_龙虎榜盈亏分析.py    → URL: /龙虎榜盈亏分析
├── 6_市场情绪.py         → URL: /市场情绪
├── 7_每日选股.py         → URL: /每日选股
├── 8_策略回测.py         → URL: /策略回测
├── 9_回测分析.py         → URL: /回测分析
└── 10_数据管理.py        → URL: /数据管理
```

**页面顺序设计原则（决策流导向）：**

| 序号 | 页面 | 位置理由 |
|------|------|---------|
| 首页 | 总览 Dashboard | 每日第一眼：市场情绪 + 当前持仓状态 |
| 2 | 🤖 模拟炒股 | 核心功能，操作最频繁 |
| 3 | 🏆 主板精选策略 | 选股依据，胜率数据参考 |
| 4 | 🎯 决策辅助信号 | 当日个股评分决策 |
| 5 | 📈 龙虎榜盈亏分析 | 历史数据验证 |
| 6 | 📊 市场情绪 | 宏观情绪参考 |
| 7 | 📡 每日选股 | 轻量选股工具 |
| 8 | 🔬 策略回测 | 研究用途 |
| 9 | 📉 回测分析 | 研究用途 |
| 10 | ⚙️ 数据管理 | 运维工具 |

### 11.2 总览页改造：快捷入口 + 持仓摘要

在总览页新增：
1. **今日持仓状态卡片**（实时获取 sim_trades open 记录）
2. **快捷入口按钮组**（主板精选、决策辅助、模拟炒股）
3. **今日操作日志**（今日 `sim_audit_log` 最近 10 条）

### 11.3 模拟炒股页面新增 Tab

在 `sim_trading.py` 新增 3 个 Tab：

**Tab A：「📈 价格轨迹」**
- 下拉选择持仓/已平仓股票
- Plotly 折线图：
  - 蓝线：`effective_price` 走势
  - 叠加 MA5（橙色）/ MA10（紫色）均线
  - 绿色虚线：买入价
  - 各档卖出价（橙/黄/红虚线）
  - 红色虚线：硬止损线
  - 事件标注：▲ 止盈档位 / ✕ 止损

**Tab B：「🤖 策略工坊」**
- 当前策略状态（三档模式 / NL 策略 active/draft）
- LLM 草拟策略展示（`nl_text` 人读 + `parsed_rules` JSON 预览）
- 「确认启用」/ 「修改重解析」按钮
- 手动输入策略文本框 → 「解析策略」→ 展示结果 → 「确认启用」
- 硬止损线调整（独立显示，始终生效）

**Tab C：「📋 操作审计」**
- 时间线倒序展示 `sim_audit_log`
- 支持按 trade_id / 事件类型 / 日期过滤
- 每行可展开看 `detail` JSON

**Tab D（已有扩展）：「🏆 选股过程」**
- 读取 `sim_selection_log`，展示当次选股的 Top3 对比
- 每只候选股的 4 维度评分 + LLM 排名理由

### 11.4 决策辅助信号页面：评分说明区块

在页面顶部（筛选条件之后、评分表之前）新增可折叠区块：

```python
with st.expander("📊 评分算法说明", expanded=False):
    st.markdown("""
    ## 四维度综合评分（0-100 分）

    ### 技术面（最高 40 分）
    
    | 指标 | 分值 | 计算说明 |
    |------|------|---------|
    | 均线趋势 | 0-10 | MA5>MA10>MA20 → 满分；反向每层 -3 分 |
    | RSI(14) | 0-8  | 40-65 区间满分；>75 或 <30 扣分 |
    | MACD金叉 | 0-8  | hist > 0 且由负转正 → 8 分 |
    | 近20日涨幅位置 | 0-7 | 在近20日低位（0-30%）→ 7 分 |
    | 量能 | 0-7  | 量比 > 1.5 → 7 分；< 0.5 → 0 分 |
    
    **公式：** 技术面得分 = min(40, 均线分 + RSI分 + MACD分 + 位置分 + 量能分)

    ### 资金面（最高 35 分）
    
    | 指标 | 分值 | 计算说明 |
    |------|------|---------|
    | 龙虎榜净买额占比 | 0-15 | 净买额 / 流通市值，比例越高分越高 |
    | 机构席位 | 0-10 | 有机构净买入 → +8 分 |
    | 知名游资席位 | 0-10 | 赵老哥/拉萨天团等 → 按权重加分 |
    
    ### 历史胜率（最高 25 分）
    
    基于该股历史上榜后 T+2/T+5 实际收益数据：
    - T+2 胜率 × 15 + T+5 胜率 × 10（归一化到 0-25 分）

    ### 情绪联动（±15 分加减分）
    
    - 今日涨停家数 > 100：+5 分
    - 大盘今日上涨：+3 分  
    - 今日跌停家数 > 50：-5 分
    """)
```

---

## 12. T+N 日期定义规范（低优先级）

**标准定义（以龙虎榜为基准）：**

| 标识 | 含义 | 示例 |
|------|------|------|
| T 日 | 龙虎榜上榜当日 | 2026-03-26（周三）|
| T+1 | 上榜次日（开盘集合竞价买入）| 2026-03-27（周四）|
| T+2 | T 日后第2个交易日 | 2026-03-28（周五）|
| T+5 | T 日后第5个交易日 | ≈ 下周三 |
| T+10 | T 日后第10个交易日 | ≈ 两周后 |

**注意：** T+N 均以**交易日**计算，跳过节假日和周末。

**需要修复的页面（低优先级，后续批量处理）：**
- `pages/lhb_returns.py`：确认 "T+1 买入" 标注是否正确
- `pages/mainboard_strategy.py`：确认 "T+2 日内冲高" 定义
- `pages/lhb_signal.py`：确认 "T+2 胜率" 计算逻辑

---

## 13. 实施步骤与优先级

### Phase 1（最高优先级 · 影响当前持仓）

| 步骤 | 文件 | 内容 |
|------|------|------|
| 1.1 | `utils/retry.py` | 修复 `fetch_sina_quote` bid1/ask1 字段 + 新增 `fetch_realtime_quote` |
| 1.2 | VPS 系统配置 | 更新 NO_PROXY 环境变量（绕过东财代理）|
| 1.3 | `skills/stock_sim_robot/sim_trade_db.py` | 新增 `sim_price_log` + `sim_audit_log` 表 DDL |
| 1.4 | `skills/stock_sim_robot/monitor_intraday.py` | 改造为使用 `effective_price`，写 price_log 和 audit_log |

### Phase 2（高优先级 · 完善记录）

| 步骤 | 文件 | 内容 |
|------|------|------|
| 2.1 | `skills/stock_sim_robot/sim_trade_db.py` | 新增 `sim_selection_log` + `sim_nl_strategy` 表 DDL |
| 2.2 | `skills/stock_sim_robot/trade_runner.py` | Phase A 记录 Top3 候选 + 写 audit_log + 草拟 NL 策略 |
| 2.3 | `skills/stock_sim_robot/llm_select.py` | 新增 `draft_nl_strategy()` + `parse_nl_strategy()` |
| 2.4 | `pages/sim_trading.py` | 新增「📈 价格轨迹」「📋 操作审计」「🏆 选股过程」Tab |

### Phase 3（中优先级 · 体验改善）

| 步骤 | 文件 | 内容 |
|------|------|------|
| 3.1 | 新建 `pages/1_总览.py` 等 | 页面路由重构（多页模式）|
| 3.2 | `pages/sim_trading.py` | 新增「🤖 策略工坊」Tab（NL 策略管理）|
| 3.3 | `pages/lhb_signal.py` | 新增可折叠评分说明区块 |
| 3.4 | `README.md` | 更新文档 |

### Phase 4（低优先级 · 后续处理）

| 步骤 | 文件 | 内容 |
|------|------|------|
| 4.1 | 各页面 | T+N 日期定义统一修复 |

---

## 14. 验证方案

每个 Phase 完成后必须验证：

### Phase 1 验证
```bash
# 1. 验证行情修复（涨停场景）
ssh jd "cd ~/stock_analysis && /root/miniconda3/bin/python -c \"
from utils.retry import fetch_realtime_quote
q = fetch_realtime_quote('002361')  # 神剑股份
print('effective_price:', q['effective_price'])
print('raw last:', q['last'])
print('bid1:', q['bid1'], 'ask1:', q['ask1'])
\""

# 2. 验证建表
ssh jd "cd ~/stock_analysis && /root/miniconda3/bin/python -c \"
from skills.stock_sim_robot.sim_trade_db import create_tables
create_tables()
print('建表成功')
\""

# 3. dry-run 监控（验证 price_log 和 audit_log 是否正确写入）
ssh jd "cd ~/stock_analysis && MONITOR_FORCE_RUN=1 \
  /root/miniconda3/bin/python skills/stock_sim_robot/monitor_intraday.py --dry-run"

# 4. 查看 price_log 数据
ssh jd "cd ~/stock_analysis && /root/miniconda3/bin/python -c \"
import sys; sys.path.insert(0, '.')
from dotenv import load_dotenv; load_dotenv('.env')
from utils.storage import storage
from sqlalchemy import text
engine = storage._get_engine()
import pandas as pd
df = pd.read_sql(text('SELECT * FROM sim_price_log ORDER BY checked_at DESC LIMIT 5'), engine)
print(df.to_string())
\""

# 5. 查看 audit_log 数据
ssh jd "cd ~/stock_analysis && /root/miniconda3/bin/python -c \"
import sys; sys.path.insert(0, '.')
from dotenv import load_dotenv; load_dotenv('.env')
from utils.storage import storage
from sqlalchemy import text
engine = storage._get_engine()
import pandas as pd
df = pd.read_sql(text(\"SELECT event_time, event_type, code, summary, result FROM sim_audit_log ORDER BY event_time DESC LIMIT 10\"), engine)
print(df.to_string())
\""
```

### 极限情况测试
1. **新浪接口超时**：设置 `timeout=0.001` 验证降级路径
2. **涨停板**：验证 `bid1=15.0, ask1=0, last=13.2` 时 `effective_price=15.0`
3. **跌停板**：验证 `bid1=0, ask1=12.0, last=13.5` 时 `effective_price=12.0`
4. **无持仓**：验证 monitor 静默退出，不写任何记录
5. **DB 连接失败**：验证 monitor 不崩溃，写文件日志后退出

---

## 15. 开发规范

1. **MVP 优先**：每个 Phase 先实现核心功能，验证通过后再进行下一阶段
2. **测试先行**：每次修改后必须执行 Phase 对应的验证步骤
3. **极限测试**：主要分支（接口失败/涨停/无持仓）都要验证
4. **文档同步**：每个 Phase 完成后更新 README.md 的相关章节
5. **最小改动**：优先局部修改，避免无关重构影响稳定运行的代码
6. **审计优先**：所有涉及资金操作的代码改动都必须同时写 audit_log
7. **向后兼容**：新增字段用 `ALTER TABLE ADD COLUMN IF NOT EXISTS`，不破坏现有数据
