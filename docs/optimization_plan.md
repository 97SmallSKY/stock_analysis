# 项目优化方案

> 日期：2026-04-03（基于代码实际状态同步更新）  
> 范围：架构、代码质量、性能、可靠性、可维护性、业务功能

---

## 已落地状态说明

> 标注 `✅ 已落地` 的项目代码中已有实现，文档保留作为历史参考，无需再开发。  
> 标注 `❌ 未落地` 的项目为真实待办。  
> 标注 `⚠️ 部分落地` 的项目为部分实现，需补全。

---

## 一、架构层面优化

### 1.1 pages/ 层仍存在业务逻辑下沉问题

**状态：** ❌ 未落地

**现状：**  
pages/ 按设计应为纯展示层，但部分页面（`sim_trading.py` 2104行、`lhb_signal.py` 1163行）内部存在大量数据聚合、评分调用、条件判断等业务逻辑，违反分层约定。

**优化方向：**
- 在 `modules/` 下新增 `report_builder.py`，专门负责 "展示用数据装配"（聚合DB结果、组合多个模块输出）
- pages/ 只负责：调用 report_builder → 渲染图表/表格
- 将 `sim_trading.py` 中的 AHS 仪表盘数据聚合逻辑迁移到 `sim_trade_db.py` 或新模块

**收益：** 单个 page 文件控制在 500 行内；业务逻辑变更不需要动 pages。

---

### 1.2 `skills/stock_sim_robot/` 与 `modules/` 的依赖方向不清晰

**状态：** ⚠️ 部分落地（`fetch_signal.py` 已委托给 `modules/scorer.py`，但仍有直接 DB 读写）

**现状：**  
`skills/stock_sim_robot/` 是高层业务模块，但内部有些文件直接读写 DB、调用 AKShare、自己实现辅助逻辑，导致它同时承担了"策略执行"和"基础设施"两个职责。

**优化方向：**
- 明确 `skills/` 只调用 `modules/` 和 `utils/` 提供的接口，不直接依赖外部数据源
- 将 `fetch_signal.py` 中的 AKShare 调用下沉到 `modules/data_fetcher.py`
- `trade_log.py` 的日志写入统一经过 `utils/storage.py`

**收益：** 底层替换（如数据源切换）时只改 modules/，上层无感知。

---

### 1.3 `sim_trade_db.py` 过于臃肿（109KB）

**状态：** ❌ 未落地

**现状：**  
单文件承担表结构定义、CRUD、AHS评分、熔断检查、四档模式管理、绩效统计等所有职责，已超过 109KB。

**优化方向：**
```
sim_trade_db.py (保留DDL+基础CRUD)
sim_trade_metrics.py  (AHS评分、四档熔断逻辑、绩效统计)
sim_trade_queries.py  (复杂查询：周汇总、历史回放等)
```
- 三个文件各自职责单一，互不跨层调用
- `calc_account_health_score()`、`check_circuit_breaker()`、`get_trading_mode()` 迁移到 `sim_trade_metrics.py`

**收益：** 单文件降到 800 行以内，便于独立测试，降低改 AHS 时误改 CRUD 的风险。

---

### 1.4 缺少统一的异步/并发抽象

**状态：** ❌ 未落地

**现状：**  
`monitor_intraday.py` 用4线程手动管理并发，各处用 `threading.Thread` / `time.sleep` 拼接；行情拉取没有统一的速率控制层。

**优化方向：**
- 引入轻量的 `asyncio` + `httpx` 替换部分 `requests` 阻塞调用
- 在 `utils/` 添加 `rate_limiter.py`（令牌桶实现），统一管理对外 API 的 QPS
- 盘中监控改用 `asyncio.gather()` 替代4线程，减少线程切换开销和竞态风险

---

### 1.5 LLM 集成缺少 Provider 抽象

**状态：** ❌ 未落地

**现状：**  
`llm_select.py` 直接读取 `~/.openclaw/openclaw.json` → 环境变量 → 降级规则，三段式耦合在业务逻辑内部。

**优化方向：**
- 在 `utils/` 新增 `llm_client.py`，封装 Provider 探测和降级链
- `llm_select.py` 只调用 `llm_client.chat(prompt)` 这一个接口
- 支持未来扩展本地模型（Ollama）而不需要改选股逻辑

---

## 二、代码质量优化

### 2.1 配置散落，缺少环境隔离

**状态：** ❌ 未落地（`config/settings.py` 仍以 `STRATEGY_CONFIG` 字典形式存储，无 RiskConfig/AHSConfig 类）

**现状：**  
`config/settings.py` 只有基础配置，但 AHS 阈值、熔断参数、三档止盈点、资金阈值等关键参数分散在 `sim_trade_db.py`、`trade_runner.py`、`llm_select.py` 中以魔法数字形式出现。

**优化方向：**
```python
# config/settings.py 扩展为分组结构
class RiskConfig:
    stop_loss: float = -0.05
    stop_profit_levels: tuple = (0.03, 0.05, 0.08)
    max_hold_days: int = 3
    circuit_breaker_daily: float = -0.03
    circuit_breaker_weekly: float = -0.05

class AHSConfig:
    thresholds: dict = {75: 1.2, 60: 1.0, 50: 0.75, 30: 0.5, 0: 0.1}
```
- 所有魔法数字引用 `settings.RiskConfig.stop_loss`
- 支持 `.env` 文件覆盖，不同环境（开发/测试/生产）可以隔离参数

---

### 2.2 重复的 DataFrame 操作模式

**状态：** ❌ 未落地

**现状：**  
多个文件中存在相似的"过滤龙虎榜信号 → 合并价格数据 → 计算收益率"管道，代码结构重复但细节略有差异，难以统一测试。

**优化方向：**
- 在 `modules/` 提取 `pipeline.py`，封装标准管道步骤
- 具体步骤用组合而非继承，保持可组合性
- 避免在回测、展示页面各自实现一遍过滤逻辑

---

### 2.3 缺少类型注解

**状态：** ⚠️ 部分落地（`fetch_signal.py` 的 `_score_tech`、`_score_fund` 已有类型注解，但 `trade_runner.py`、`sim_trade_db.py` 等核心文件仍大量缺失）

**优化方向：**
- 为 `modules/` 和 `skills/stock_sim_robot/` 的公开接口添加 Python 类型注解
- 使用 `TypedDict` / `dataclass` 替代裸 `dict` 作为函数返回值（如评分结果）
- 引入 `mypy` 或 `pyright` 静态检查（CI 阶段运行）

---

### 2.4 错误处理不一致

**状态：** ❌ 未落地

**现状：**  
部分地方用 `try/except Exception as e: print(e)`，部分用 `loguru.logger.error()`，还有些地方静默吞掉异常返回 `None` / 空 DataFrame，调用方不知道失败原因。

**优化方向：**
- 统一错误处理约定：外部 IO（AKShare、DB）记录到 loguru + 向上抛自定义异常
- 定义项目级异常层次：
  ```python
  class StockAnalysisError(Exception): pass
  class DataFetchError(StockAnalysisError): pass
  class ScoringError(StockAnalysisError): pass
  class TradeExecutionError(StockAnalysisError): pass
  ```
- 禁止在 `modules/` 层静默 `return None`，调用方必须显式处理失败

---

### 2.5 测试覆盖率为零

**状态：** ❌ 未落地（项目根目录有 `test.py` 但无 `tests/` 目录，无系统性单元测试）

**优化方向：**
- 建立 `tests/` 目录，优先覆盖高风险模块：
  ```
  tests/
  ├── test_scorer.py          # score_tech/score_fund/score_history 边界条件
  ├── test_market_data.py     # get_t2_prices 交易日历正确性
  ├── test_circuit_breaker.py # 熔断触发/不触发场景
  ├── test_ahs.py             # AHS 分值边界
  └── test_backtest.py        # 含手续费的回测收益计算
  ```
- 使用 `pytest` + `pytest-mock` 对 AKShare/DB 打桩
- 最高优先级：交易日历（T+N）和评分引擎，这两处逻辑错误会导致未来函数或评分失真

---

### 2.6 各页面 T+N 收益率口径不一致

**状态：** ❌ 未落地

**现状（已知问题）：**
- 【主板精选策略】和【模拟炒股】：定义明确（T+2 冲高基于次次日最高价）
- 【龙虎榜盈亏分析】：未区分"最高价平均收益"还是"收盘价平均收益"
- 【决策辅助信号】：T+2/T+5 收益基准未注明（最高价/收盘价/开盘价），存在严重歧义

**优化方向：**
- 在每个涉及收益率的页面顶部增加口径说明文字
- 统一用 `T+1开盘价` 作为买入基准，并明确标注卖出基准
- 口径差异必须显式标注，禁止在展示层做静默假设

---

### 2.7 `use_container_width` 待统一替换（Streamlit 升级后）

**状态：** 暂缓（`pages/` 下仍有大量 `use_container_width=True`，Streamlit 1.55.0 有效但触发 deprecation warning）

**策略：**
- 当前：暂不修改，保持 `use_container_width=True` 风格一致（新增代码同样沿用）
- 版本升级后统一替换：图表类 → `width='stretch'`
- 等待批量处理，不逐个修改

---

## 三、性能优化

### 3.1 K线缓存粒度过粗

**状态：** ❌ 未落地

**现状：**  
`market_data.py` 使用进程级内存缓存，key 是 `(code, trade_date)`，每次新 trade_date 都全量重新拉取。回测时同一股票不同日期会多次拉取重叠的 K 线段。

**优化方向：**
- 改为按股票缓存完整历史段，切片时按日期过滤
- 引入 LRU 缓存（`functools.lru_cache` 或 `cachetools.TTLCache`）设置最大条目数，避免内存无限增长
- 批量回测场景：预先批量拉取所有涉及股票的 K 线，避免逐条请求

---

### 3.2 Streamlit 页面重复计算

**状态：** ❌ 未落地

**现状：**  
多个 page 用 `@st.cache_data(ttl=300)` 缓存，但缓存键未精细化；部分复杂查询每次页面刷新都重新执行。

**优化方向：**
- 将耗时的 DB 聚合查询（周绩效、AHS历史曲线）移到后台定时任务预计算，Streamlit 只读预计算结果
- 区分"盘中刷新"（短 TTL ~60s）和"盘后展示"（长 TTL ~1800s）两种缓存策略
- 大表查询加 `LIMIT` + 分页，避免全量拉取到内存

---

### 3.3 Phase A/B 串行执行耗时

**状态：** ❌ 未落地

**现状：**  
`run_phase_a()` 中对每个候选股依次调用评分（包含 AKShare 网络请求），候选数量多时整体耗时增加。

**优化方向：**
- 批量并发拉取候选股 K 线（`asyncio.gather` 或 `ThreadPoolExecutor`）
- 评分计算是纯 CPU 计算，可用 `ProcessPoolExecutor` 并行
- Phase B 的 T+1 价格回填同理可批量并发拉取

---

### 3.4 PostgreSQL 查询缺少索引声明

**状态：** ❌ 未落地（`sim_trade_db.py` 建表 DDL 中索引声明不完整）

**优化方向：**
- `sim_trades` 表增加复合索引：`(trade_date, status)`、`(code, status)`
- `lhb_detail` 表增加：`(trade_date, code)`
- `sim_audit_log` 表按 `(event_type, created_at)` 建索引
- 用 `EXPLAIN ANALYZE` 验证慢查询并补充索引

---

## 四、可靠性优化

### 4.1 Phase B 失败无重试补偿

**状态：** ❌ 未落地（无 `scripts/fix_phase_b.py` 补偿脚本）

**现状：**  
`phase_b_fail_count` 字段记录失败次数，但没有自动重试逻辑；若 T+1 开盘价拉取失败，订单会永久停留在 `buy_price=NULL` 状态。

**优化方向：**
- `run_phase_b()` 失败后写入 `phase_b_status=failed`
- 添加 `scripts/fix_phase_b.py` 补偿脚本：每天9:40运行，自动重试 `phase_b_fail_count >= 1` 的订单
- 盘中监控检测到 `buy_price=NULL` 的持仓自动跳过，避免按 NULL 价格计算止盈止损

---

### 4.2 飞书日报生成失败会阻断主流程

**状态：** ❌ 未落地

**现状：**  
`summary_report.py` 的飞书发送和主流程在同一个调用链，飞书 API 超时会导致 Phase A 延迟或失败。

**优化方向：**
- 飞书发送改为独立异步任务，与主流程解耦
- 主流程只负责写 DB，日报生成放到单独的 cron（如 19:30）

---

### 4.3 数据库连接池

**状态：** ✅ 已落地（`utils/storage.py` 已使用 SQLAlchemy `create_engine(pool_size=5, max_overflow=10, pool_timeout=30, pool_pre_ping=True)`）

---

### 4.4 交易日判断依赖外部 API

**状态：** ❌ 未落地（`modules/market_data.py` 无本地日历缓存逻辑）

**现状：**  
`_get_nth_trading_day()` 依赖 AKShare 提供交易日历，网络故障时会导致 T+N 计算失败。

**优化方向：**
- 定期（每月一次）将 AKShare 交易日历同步到本地 DB 或本地文件（`data/trading_calendar.csv`）
- `market_data.py` 优先读本地日历，本地不存在时才请求 AKShare
- 减少关键业务路径对外部 API 的实时依赖

---

## 五、业务功能待落地项

### 5.1 四档分级降速熔断框架

**状态：** ✅ 已落地（2026-04-01）

**已实现内容：**
- `sim_trade_db.py`：`_MODE_CONFIG`、`get_trading_mode()`、`set_trading_mode()`、`calc_position_size()`、`check_circuit_breaker()` 已全部实现四档逻辑
- `sim_config` 表持久化存储当前档位，支持 UPSERT
- `pages/sim_trading.py` 已展示四档状态横幅（🟢/🟡/🟠/🔴）及手动干预面板
- 自动恢复：连赢2笔自动升一级；危机模式80+分信号仍可参与

**总控 Prompt 需同步更新：** §9c 中"代码尚未落地"标注已过时，需将状态改为 ✅ 已落地。

---

### 5.2 亏损原因自动归类

**状态：** ✅ 已落地（`skills/stock_sim_robot/trade_log.py`）

**已实现内容：**
- `_analyze_loss_reason(trade)` 函数：自动分析亏损原因并生成改进建议
- 平仓后自动写入 `sim_trade_lessons` 表（含 `loss_reason`、`lesson` 字段）
- 飞书日报中包含亏损原因和教训摘要

**与规划的差异：** 原规划将 `loss_reason` 存入 `sim_trades` 表的字段，实际实现为独立的 `sim_trade_lessons` 表，功能完整但存储方式不同。

**总控 Prompt 需同步更新：** `sim_trades.loss_reason` 字段相关描述需更正为 `sim_trade_lessons` 表。

---

### 5.3 营业部动态评分

**状态：** ❌ 未落地

**现状：**  
`fetch_signal._score_fund()` 委托给 `modules/scorer.score_fund()`，仅实现净买入强度和对倒检测，无席位评分函数（`_score_seat` 不存在）。`dept_performance` 表未建。

**优化方向：**
- 新建 `dept_performance` 表：`dept_name, t1_win_rate, t3_win_rate, t5_win_rate, update_date`
- `daily_update.py` 月频更新营业部历史胜率
- `modules/scorer.py` 新增 `score_seat()` 函数，接入动态评分

---

### 5.4 北向资金验证因子

**状态：** ❌ 未落地

**现状：**  
`ak.stock_hsgt_individual_em()` 接口尚未接入任何模块。

**优化方向：**
- 在 `modules/data_fetcher.py` 新增 `fetch_northbound_holding()` 函数
- 在 `modules/scorer.py` 中新增 `score_northbound()` 评分方法
- 作为辅助因子，权重建议5~10分，同样需要降级路径（无数据时默认0分）

---

### 5.5 教训管理库与 Walk-Forward 回测

**状态：** ⚠️ 部分落地

**教训库：** ✅ 已部分落地
- `sim_trade_lessons` 表已建（见 `trade_log.py`），自动写入亏损分析和教训
- 尚缺：`pages/sim_trading.py` 中的教训查看/人工录入入口

**Walk-Forward 回测：** ❌ 未落地
- `modules/analyzer.py` 有 `simulate_with_stoploss()` 支持回测成本扣除，但无滚动回测框架
- 需新增 `run_walkforward_backtest()` 函数，滚动窗口训练期12个月/测试期3个月

---

### 5.6 每日盈亏汇总预计算

**状态：** ❌ 未落地

**现状：**  
`daily_pnl` 表未建，`sim_trading.py` 净值曲线每次实时聚合计算。

**优化方向：**
- 新建 `daily_pnl` 表：`trade_date, total_pnl, realized_pnl, unrealized_pnl, hold_count, new_open_count`
- `daily_update.py` 盘后（Step 最后）自动写入当日汇总
- `sim_trading.py` 净值曲线直接读 `daily_pnl`，不再实时聚合

---

## 六、可维护性优化

### 6.1 文档与代码不同步风险

**状态：** ❌ 未落地（`pre-commit` 钩子未配置；历史文档未归档）

**优化方向：**
- 将 `AGENTS.md` 中的禁止项转化为 `pre-commit` 钩子（grep 检测 `.iloc[1]`、`natural day +1` 等模式）
- 归档历史方案文档（移入 `docs/archive/`），保持 `docs/` 根目录只有当前有效文档
- 关键配置参数（止损比例、AHS阈值等）在代码注释中注明"修改请同步更新 AGENTS.md"

---

### 6.2 脚本入口职责模糊

**状态：** ❌ 未落地（`scripts/` 下目前无子目录分层）

**优化方向：**
```
scripts/
├── cron/       # cron 定时执行入口（phase_a, phase_b, daily_update）
├── tools/      # 一次性修复工具（fix_reasons, backfill_features）
└── dev/        # 开发调试脚本（不应在生产运行）
```
- cron 脚本加幂等保护（通过 lockfile 防止同时运行两个实例）

---

### 6.3 日志缺少结构化与可观测性

**状态：** ❌ 未落地

**现状：**  
各模块用 loguru 写文本日志，但日志格式不统一（部分用 emoji，部分是纯文本），生产问题排查需要逐行 grep。

**优化方向：**
- 统一日志格式：`{timestamp} | {level} | {module} | {trade_id} | {message}`
- 关键事件（开仓/平仓/熔断/AHS变化）写结构化日志（JSON 格式），便于后续接入日志分析平台
- 添加 `trade_id` 字段，使同一笔交易的全生命周期日志可关联查询

---

### 6.4 依赖版本锁定不严格

**状态：** ❌ 未落地

**现状：**  
`requirements.txt` 使用 `>=` 下界约束，线上依赖可能因自动升级导致行为变化。

**优化方向：**
- 用 `pip freeze > requirements.lock` 生成精确版本锁定文件
- 开发用 `requirements.txt`（宽松），部署用 `requirements.lock`（精确）
- 配合手动季度审查来升级依赖

---

## 七、优先级矩阵

| 优化项 | 影响 | 实施难度 | 优先级 | 实际状态 |
|--------|------|----------|--------|---------|
| 2.5 添加核心模块测试 | 高（防止评分/日历 Bug） | 中 | P0 | ❌ 未落地 |
| 4.1 Phase B 失败补偿 | 高（生产数据正确性） | 低 | P0 | ❌ 未落地 |
| 4.3 DB 连接池 | 高（生产稳定性） | 低 | P0 | ✅ 已落地 |
| 5.1 四档分级降速框架 | 高（风控精细化） | 高 | P1 | ✅ 已落地 |
| 5.2 亏损原因自动归类 | 高（自适应风控） | 中 | P1 | ✅ 已落地 |
| 5.3 营业部动态评分 | 高（因子质量） | 中 | P1 | ❌ 未落地 |
| 2.1 配置集中化 | 中（参数一致性） | 低 | P1 | ❌ 未落地 |
| 4.4 本地交易日历 | 中（离线可用性） | 低 | P1 | ❌ 未落地 |
| 1.3 sim_trade_db 拆分 | 中（可维护性） | 中 | P1 | ❌ 未落地 |
| 2.3 类型注解 | 中（IDE 体验+重构安全） | 中 | P1 | ⚠️ 部分落地 |
| 3.4 PostgreSQL 索引 | 中（查询性能） | 低 | P1 | ❌ 未落地 |
| 2.6 T+N 收益率口径统一 | 中（分析正确性） | 低 | P1 | ❌ 未落地 |
| 5.4 北向资金因子 | 中（因子覆盖） | 中 | P2 | ❌ 未落地 |
| 5.5 Walk-Forward 回测 | 中（策略迭代） | 高 | P2 | ❌ 未落地 |
| 5.5 教训库前端入口 | 低（使用体验） | 低 | P2 | ⚠️ 部分落地 |
| 5.6 每日盈亏预计算 | 中（页面性能） | 低 | P2 | ❌ 未落地 |
| 1.1 pages 层下沉清理 | 中（分层架构） | 高 | P2 | ❌ 未落地 |
| 1.5 LLM Provider 抽象 | 低（扩展性） | 中 | P2 | ❌ 未落地 |
| 3.1 K线缓存优化 | 低（回测速度） | 中 | P2 | ❌ 未落地 |
| 2.7 use_container_width 替换 | 低（兼容升级） | 低 | P2 | 暂缓 |
| 6.2 脚本目录重组 | 低（工程规范） | 低 | P3 | ❌ 未落地 |
| 6.3 结构化日志 | 低（可观测性） | 中 | P3 | ❌ 未落地 |

---

> P0：立即处理，影响生产正确性  
> P1：近期处理，提升稳定性和可维护性  
> P2：中期处理，架构改善与功能扩展  
> P3：有空处理，锦上添花

---

## 附：需同步更新总控 Prompt 的地方

以下总控 Prompt 中的描述与代码现状不符，建议同步修正：

| 位置 | 当前描述 | 实际状态 | 建议修正 |
|------|---------|---------|---------|
| §9c 分级降速框架 | "代码尚未落地" | ✅ 2026-04-01 已落地 | 改为"✅ 已落地：`sim_trade_db.py`" |
| 已落地功能速查 - 分级降速框架 | `❌ P1` | ✅ 已落地 | 改为 `✅` 并注明代码位置 |
| sim_trades 表字段 - 待落地 `loss_reason` | "P1 计划中" | ✅ 已以 `sim_trade_lessons` 表形式落地 | 更新字段说明 |
| 已落地功能速查 - 亏损原因自动归类 | `❌ P1` | ✅ 已以 `trade_log.py` + `sim_trade_lessons` 表落地 | 改为 `✅` |
