# 龙虎榜交易策略分析系统

基于 A 股东方财富龙虎榜数据的量化辅助决策平台，集成 LLM 选股、模拟交易机器人与多维度风控框架。

🌐 **线上地址：** http://117.72.184.8:8051

---

## 功能模块

| 页面 | 说明 |
|------|------|
| 🏠 总览 Dashboard | 今日市场情绪、涨跌停统计、操作建议、**账户健康仪表盘** |
| 🏆 主板精选策略 | 龙虎榜主板多因子筛选、回测、净值曲线 |
| 📡 实时监控 | 盘中三级行情降级（AKShare → 新浪 → 缓存）、MS 动能评分、分时段刷新 |
| 🎯 决策辅助信号 | T 日候选信号综合评分（技术 40 + 资金 35 + 历史胜率 25） |
| 📈 龙虎榜盈亏分析 | T+1/T+2/T+5 历史胜率与盈亏分析 |
| 🤖 模拟炒股 | 模拟交易全流程管理、**AHS 健康仪表盘**、**熔断告警与手动解除** |
| 🔍 每日选股 | 每日主板推荐摘要 |
| 📊 市场情绪 | 涨跌停、连板、大盘趋势指数 |
| 🔬 策略回测 | 全历史数据回测，含交易成本 |
| 📉 回测分析 | 多维度回测绩效拆解 |
| ⚙️ 数据管理 | 数据同步、字段校验、手动触发更新 |

---

## 模拟交易机器人

### 运行模式

```
# Phase A — 每日 18:45 盘后：结算持仓 + 选股开仓
/root/miniconda3/bin/python scripts/sim_robot_run.py phase_a

# Phase B — 次日开盘后：回填 T+1 买入价
/root/miniconda3/bin/python scripts/sim_robot_run.py phase_b
```

### 交易机制

| 阶段 | 时间 | 动作 |
|------|------|------|
| T 日 | 龙虎榜上榜 | 信号分析，不买入 |
| T+1 日 | 集合竞价 | 按仓位比例买入（平开最佳，高开 >7% 放弃） |
| T+2 日 | 盘中 | 三档分批止盈（+3%/+5%/+8%），-5% 止损 |

### LLM 选股

优先读取 `~/.openclaw/openclaw.json` 中的 LLM Provider（百炼/Qwen 等），降级到环境变量 `OPENAI_API_KEY` / `LLM_API_KEY`，再次降级为规则引擎。

---

## 反馈调节与仓位管理框架（v2 · 2026-04-01）

### 账户健康评分 (AHS)

对近期实战绩效打分（0~100），动态调节开仓行为：

| AHS 区间 | 状态 | 仓位系数 | 止盈目标倍率 |
|---------|------|---------|------------|
| ≥ 75 | 🟢 强势期 | ×1.2 | ×1.0（正常） |
| 60~74 | 🟢 正常期 | ×1.0 | ×1.0 |
| 50~59 | 🟡 疲弱期 | ×0.75 | ×0.9（略保守） |
| 30~49 | 🟠 修复期 | ×0.5 | ×0.75（保守） |
| < 30 | 🔴 极危险 | ×0.0→最低0.1 | ×0.75 |

**评分组成（基础50分）：**
- 近10笔胜率（±25分）
- 近10笔平均盈亏（±15分）
- 连续亏损惩罚（最多-20分）
- 今日盈亏紧急修正（±10分）

### 熔断规则

以下任一条件触发时，**当日 Phase A 自动跳过开仓**，并在页面顶部显示告警。

| 规则 | 阈值 | 影响范围 |
|------|------|---------|
| 单日亏损 | > -3% 总资金 | 当日停止开仓，次日自动恢复 |
| 连续亏损 | ≥ 3 笔 | 跳过今日开仓 |
| 周亏损 | > -5% | 本周剩余交易日停止 |
| 月回撤 | > -8% | 本月剩余交易日停止 |
| 近期最大回撤 | > -15% | 停止，建议清仓 |

**手动覆盖：** 在「🤖 模拟炒股」页面可一键开启 `SKIP_CIRCUIT_BREAKER=1`，绕过熔断（本次运行有效，次日自动失效）。也可在服务器设置环境变量实现持久覆盖。

### 相关文件

| 文件 | 职责 |
|------|------|
| `skills/stock_sim_robot/sim_trade_db.py` | `get_performance_stats()` / `calc_account_health_score()` / `check_circuit_breaker()` |
| `skills/stock_sim_robot/llm_select.py` | 动态仓位 `_default_invest_ratio()` / 动态止盈 `_calc_list_price()` |
| `skills/stock_sim_robot/trade_runner.py` | `run_phase_a()` 开仓前强制熔断检查 |
| `skills/stock_sim_robot/summary_report.py` | 飞书日报健康度摘要 |

---

## 技术栈

- **运行时：** Python 3.13 / Streamlit 1.55
- **数据库：** PostgreSQL（主）+ Excel 兼容模式
- **行情数据：** AKShare（主）/ 东方财富实时
- **LLM：** 支持 OpenAI 兼容接口（OpenClaw / 百炼 / Qwen 等）
- **可视化：** Plotly + Streamlit
- **部署：** Linux VPS（京东云），Nginx 反代，cron 保活

---

## 核心模块架构（v2.3 重构后）

```
modules/
├── scorer.py       ← 统一评分引擎（技术面/资金面/历史胜率，单一权威实现）
├── market_data.py  ← 统一行情获取层（K线缓存/T+N日历/T+2价格）
├── analyzer.py     ← 回测框架（委托 scorer + market_data）
├── data_fetcher.py ← 龙虎榜/涨跌停/资金流数据拉取
├── feature_engine.py
├── ms_scorer.py
└── realtime_fetcher.py

pages/              ← Streamlit 页面（仅展示层，不含核心逻辑）
skills/stock_sim_robot/
├── fetch_signal.py ← 信号抓取（调用 scorer + market_data）
├── trade_runner.py ← 模拟交易核心
└── ...
```

---

## 本地开发

```bash
# 安装依赖
pip install -r requirements.txt

# 启动（本地调试）
streamlit run app.py --server.port 8051

# 连接远程服务器（生产环境）
ssh jd
cd ~/stock_analysis
```

---

## 更新日志

### v2.4（2026-04-01）— 性能优化方案文档同步（文档先行）

> 本版本先完成文档对齐（README + docs），代码改造将在后续 Phase 5 实施并回填验证数据。

- **新增** 性能瓶颈总览：回测慢、监控慢、页面慢、存储层慢四类问题
- **新增** 优先级路线图：P0（立刻见效）/ P1（结构优化）/ P2（中长期）
- **新增** 监控提速策略：并发抓取行情 + 批量/降频写 `sim_price_log` + 事件日志实时写
- **新增** 回测提速策略：移除固定 sleep、交易日语义修复、成本扣除统一参数化
- **新增** 端到端验证口径：Phase A → Phase B → Monitor → 页面数据一致性

#### 性能优化路线图（Phase 5 预告）

| 优先级 | 优化点 | 目标收益 |
|--------|--------|----------|
| P0 | `monitor_intraday.py` 行情并发抓取 + 批量/降频写库 | 监控单轮耗时显著下降，避免 cron 叠压 |
| P0 | `analyzer.py` 移除硬编码 sleep + 回测口径修复 | 同区间回测耗时目标下降 ≥30% |
| P0 | SQL 索引与分页查询优化 | 页面首屏加载明显提速 |
| P1 | 指标计算向量化 + 回测并行化 | 中大样本回测进一步提速 |
| P2 | 增量回测与事件驱动监控 | 降低长期运行成本 |

#### 回测与监控验证口径（实施后执行）

1. **回测性能**：同时间区间对比改造前后总耗时、单日平均耗时、结果偏差可解释性。
2. **监控性能**：持仓 1/5/10 场景下单轮耗时、行情成功率、`sim_price_log` 写入量下降比例。
3. **端到端一致性**：Phase A 选股开仓 → Phase B 回填 → Monitor 卖出/止损 → `sim_trades/sim_trade_events/sim_price_log/sim_audit_log` 与页面一致。


- **修复** `pages/sim_trading.py` / `pages/dashboard.py` / `pages/mainboard_strategy.py` 中 `@st.cache_data`
  在 Python 3.13 下的 semaphore 泄漏风险，统一迁移为 `session_state + TTL` 缓存
- **增强** `sim_trades` 与 `sim_selection_log`：新增 `lianban_count` / `lianban_score` 字段并完成迁移
- **打通** 连板字段传递链路：`fetch_signal → llm_select → trade_runner → sim_trade_db → summary_report`
- **优化** Phase A 飞书日报：新增连板信息（X板 / 技术加分）展示

### v2.2（2026-04-01）— 评分引擎统一 + 连板因子 + 行情层重构

- **新建** `modules/scorer.py` — 统一评分引擎，消除 3 处重复实现（~280 行代码精简）
- **新增** 连板高度短线因子：`score_tech()` 的 `lianban_count` 参数（+0/+4/+7/+10 分）
- **新建** `modules/market_data.py` — 统一行情获取层（K 线/T+2 价格/交易日历）
- **改造** `pages/lhb_signal.py` — 评分函数改为调用 `scorer`，删除内置重复实现
- **改造** `skills/stock_sim_robot/fetch_signal.py` — 同上；`get_mainboard_signals` 新增连板池接入
- **改造** `modules/analyzer.py` — `_kline_get` 委托给 `market_data.get_kline`
- **新增** `docs/重构进度_202604.md` — 技术重构工作进度文档

### v2.1（2026-04-01）— 反馈调节 + 动态仓位管理框架

- **新增** `get_performance_stats()` — 近期绩效统计（胜率/盈亏/连亏/回撤）
- **新增** `calc_account_health_score()` — AHS 账户健康评分（0~100）
- **新增** `check_circuit_breaker()` — 5条熔断规则代码落地
- **改造** `_default_invest_ratio()` — 引入 AHS 系数（0.5x~1.2x）动态调节仓位
- **改造** `_calc_list_price()` — 低 AHS 时保守化止盈目标（最低×0.75）
- **改造** `run_phase_a()` — 开仓前强制熔断检查，AHS 注入决策链
- **新增** 飞书日报账户健康度摘要区块
- **新增** `sim_trading.py` AHS 健康仪表盘 + 熔断告警 + 手动覆盖开关
- **新增** Dashboard 首页熔断状态展示

### v2.0（2026-03-31）— LLM 选股策略重构

- LLM 仅负责选股和挂牌价建议，仓位决策移至独立规则引擎
- 清理 `_SYSTEM_PROMPT` 中的仓位指令，减少 LLM token 消耗

### v1.5（2026-03-31）— 实时监控仪表盘

- 新增 `pages/realtime_monitor.py`：三级行情降级链（AKShare → 新浪 → 缓存）
- MS 动能评分公式（V×0.40 + P×0.35 + B×0.25）
- 分时段刷新策略（集合竞价 10s / 开盘 15s）

---

## 免责声明

本系统仅供学习研究，不构成投资建议。量化策略存在历史回测过拟合风险，实盘操作请谨慎。
