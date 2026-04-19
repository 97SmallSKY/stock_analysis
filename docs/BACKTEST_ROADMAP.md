# 回测框架增强路线图 · 龙虎榜量化指标体系

> **P17 系列**。本文承接 `docs/OPTIMIZATION_PLAN.md`,聚焦
> **(1)** 回测框架如何演进(评估 Backtrader / vectorbt 切换 vs 自研增量);
> **(2)** 龙虎榜购买决策应该采集哪些指标,分层列清单并附采集优先级。
>
> 写于 P16 循环 R5 之后。当前自研回测引擎 `backend/domain/backtest/` 共 830 行,账务层与 `LiveRobotEngine` 共享(回测-实盘 1:1 等价)。

---

## 一、回测框架:Backtrader / vectorbt / 自研 三方评估

### 1.1 现状(自研 800 行)

- 事件驱动,日级频率,`engine.py` 主循环 open(T)→intraday(T)→close(T)→post(T)。
- 撮合模型极简:买入=open 价,卖出=close 价,零滑点,不做部分成交/涨停不可买。
- 账务复用 `RobotSnapshot.plan_buy/plan_sell`(佣金万三 + 印花税千一 + 最低 5 元),**回测和实盘共用**。
- Provider 抽象:`list_trade_days / get_daily / list_lhb_records`。
- 产出:总收益/年化/Sharpe/MDD/胜率/盈亏比/NAV/TradeRecord/按原因归因。
- 已接 4 种任务状态、`/api/backtest/history`、前端 `/backtest` + `/backtest-run`。

### 1.2 切 Backtrader 的收益/代价

| 维度 | Backtrader | 自研 |
|---|---|---|
| 成熟度 | 十年+ 社区 / 书/教程全 | 800 行,自产自销 |
| 撮合 | 5 种订单 + 佣金/滑点/部分成交 | 仅 MOO/MOC,零滑点 |
| Analyzer | 11 种内置(Sharpe/DD/TradeAnalyzer/PyFolio) | 手写 `report.py` 188 行 |
| 多周期 | `resampledata` 自动,同 Cerebro 日+分钟+指数 | 手写 `date_trunc` SQL |
| 参数优化 | `optstrategy` 内置多进程 | 无 |
| 可视化 | `cerebro.plot()` 一键 | 依赖前端 ECharts |
| 性能 | Python 纯循环,5000 股 × 3 年 ~ 10 分钟 | 同规模 ~ 2 分钟(因 Provider 更轻) |
| **账务复用实盘** | ❌ 要么双写要么封 Broker,**失去回测-实盘 1:1 等价** | ✅ 共用 `plan_buy/plan_sell` |
| **龙虎榜范式** | ❌ bar-driven,`list_lhb_records` 要塞 extra_data 很别扭 | ✅ `ctx.data_provider` 天然契合"盘后选股" |
| A 股坑(T+1/涨停/停牌/ST) | ❌ 都要手写 | ❌ 同样要手写,但写在 `plan_buy` 已有位置 |
| 学习曲线 | `lines`/`indicators` 反直觉,2 周 | 已熟悉 |

### 1.3 vectorbt(向量化替代)

- **优势**:numpy 向量化,5000 股 × 3 年 < 10s;pandas-native 和 PG 数据源顺手;跑因子筛选批量 backtest 几百组 <1min。
- **劣势**:也不和 `RobotSnapshot` 账务共用;API 偏函数式,对事件驱动的策略(check_sell 多条件分支)表达力差;同样不合"盘后选股"范式。
- **定位**:**研究阶段做因子扫描**,不做生产回测。

### 1.4 决定(本项目):**不切 bt,补齐自研短板**

**理由**:

1. 回测-实盘共账务是当前架构核心价值,切 bt 会丢。
2. 龙虎榜事件驱动,bar-driven 框架范式错配,得不偿失。
3. bt 80% 实用能力可以通过增量 500 行工作量补齐。
4. 因子研究阶段引入 **vectorbt** 作为辅助工具,研究-生产分层。

### 1.5 P17 增量路线(按优先级)

| ID | 优先级 | 模块 | 工作量估 | 交付 |
|---|---|---|---|---|
| **P17.1** | P1 | 滑点模型 `engine.slip_model` | 80 行 + 10 测试 | 成交价 = 理想价 × (1 ± slip_pct),默认 0.002;支持 `FixedSlip / VolumeBasedSlip` |
| **P17.2** | P1 | 基准对比 + Alpha/Beta/IR | 120 行 + 8 测试 | provider 加 `get_index_daily`;report 新增 `alpha/beta/tracking_error/information_ratio` |
| **P17.3** | P1 | 涨停不可买 + T+1 约束 | 60 行 + 6 测试 | `plan_buy` 新增 `prev_close` 参数,`open >= prev_close × 1.099 → reject`;`plan_sell` 强制 `open_date < biz_date` |
| **P17.4** | P1 | 停牌/退市处理 | 40 行 + 4 测试 | `get_daily` 返回 None → `mark_to_market` 延续上一有效价,`check_sell` 跳过 |
| **P17.5** | P2 | 参数优化器 `backtest/optimizer.py` | 180 行 + 10 测试 | `grid_search(strategy_cls, param_grid, metric, workers)` joblib 多进程 |
| **P17.6** | P2 | Analyzer 解耦 | 150 行 | `report.py` 每个指标抽成 `Analyzer` 类,可插拔 |
| **P17.7** | P3 | 指标采集扩展(见 §二) | 分多个子任务 | 表格驱动,按 §二 优先级分批上 |
| **P17.8** | P3 | 分钟 K 回测 | 依赖 P16.6 分钟 K 入库 | engine `bar_freq` 参数,provider 新增 `get_minute` |
| **P17.9** | P4 | 可视化脚本 | 60 行 | `scripts/plot_backtest.py` 用 matplotlib 出 PNG;独立前端之外的快速诊断 |
| **P17.10** | P4 | vectorbt 研究工具链 | 独立目录 `research/` | 研究阶段跑因子 IC/IR 扫描,不进生产 |

---

## 二、龙虎榜购买决策指标体系(量化系统级)

> 针对"龙虎榜股票次日/5 日收益预测"场景,从用户给的完整指标清单里**抽取适用 + 标注优先级 + 标注当前状态**。
> **P** 列:采集/消费优先级 P0 必做 / P1 核心 / P2 增强 / P3 长尾。
> **S** 列:当前状态 ✅ 已采 / 🟡 部分 / ❌ 未采 / 📐 可纯计算。

### 2.1 数据层(采集类)

| 指标 | 来源表 | P | S | 用途 / 备注 |
|---|---|---|---|---|
| OHLCV 日线 | `kline_daily` | P0 | ✅ | T+1/T+5 收益、技术指标底层 |
| 复权因子 | `kline_daily.adj_factor`(如缺则建) | P0 | 🟡 | 跨除权日收益计算关键,当前 kline 未确认含复权 |
| 涨跌停标记 | 计算:`close ≥ prev_close × 1.099` | P0 | 📐 | 决定是否"次日可买",P17.3 需要 |
| 停复牌标记 | `stock_info.list_status` + kline 缺失判定 | P0 | 🟡 | mark_to_market 延续 |
| 龙虎榜主表 `lhb_stock_daily` | `lhb_stock_daily` | P0 | ✅ | 上榜基础 (52058 行 / 3+ 年) |
| 龙虎榜席位 `lhb_seats` | `lhb_seats` | P0 | ✅ | 游资/机构标签 (148775 行 / 795 日 / 全覆盖) |
| 席位 broker_type 分类 | 解析席位名 → HOT_MONEY / INSTITUTION / UNKNOWN | P0 | 🟡 | 策略 `lhb_hot_money` 依赖 |
| T+N 收益 `lhb_tn_price` | P16.5 回填 | P0 | 🟡 | 78700 行,待 kline 2025 回填完成补 |
| **Tick/盘口/大单** | 需接外部行情源 | P3 | ❌ | 分钟级以下,暂无需求 |
| 集合竞价数据(9:15-9:25 open) | akshare 可取 | P1 | ❌ | 龙虎榜次日开盘**集合竞价开盘价**是真正的买入点,误差大 |
| 北向资金持股 `northbound_hold` | akshare `stock_hk_ggt_components_em` | P1 | ❌ | 北向跟买龙虎榜股票有强 alpha 信号 |
| 融资融券余额 `margin_detail` | akshare `stock_margin_detail_sse` | P1 | ❌ | 融资余额突增 = 散户追涨确认信号 |
| 十大股东 / 户数 | akshare `stock_zh_a_gbjg_em` | P2 | ❌ | 户数骤降 = 筹码集中 |
| 大单成交(主力资金流) | akshare `stock_individual_fund_flow` | P1 | ❌ | 主力净流入 = 次日继续上涨概率 |
| 换手率 | `kline_daily.turnover_rate` 或 TuShare daily_basic | P0 | 🟡 | 龙虎榜 + 高换手 = 短线博弈 |
| 总市值 / 流通市值 | `stock_info.total_mv / float_mv` 或每日快照 | P0 | 🟡 | 小盘股 + 龙虎榜 = 游资重点 |
| 行业分类 | `stock_info.industry` 或 SW 一级行业 | P0 | 🟡 | 龙虎榜题材板块轮动分析 |
| 概念标签 | akshare 概念板块成分 | P1 | ❌ | 热点题材识别(AI / 机器人 / 新能源) |
| 估值 PE/PB/PS | TuShare daily_basic | P2 | ❌ | 去垃圾股过滤 |
| 财务 ROE/ROA | 季度财报,慢变量 | P2 | ❌ | 去基本面差的票 |
| 分析师一致预期 | Wind/Choice(付费) | P3 | ❌ | 成本收益不匹配 |
| 舆情情感 | 东财股吧爬虫 / Snownlp | P3 | ❌ | 噪声大 |
| 搜索指数 | 百度指数 API | P3 | ❌ | 噪声大 |
| 宏观指标 CPI/PPI/M2 | 国家统计局 | P3 | ❌ | 低频,对短线无助 |

### 2.2 因子层(计算类)

> 全部可由数据层派生,**新建 `feature_daily` 表**,按 `(ts_code, trade_date)` 主键,增量计算落库。

| 因子 | 类别 | P | S | 定义 |
|---|---|---|---|---|
| `ma5/ma10/ma20/ma60` | 趋势 | P0 | 📐 | 简单移动均线 |
| `ema12/ema26` | 趋势 | P0 | 📐 | MACD 底层 |
| `macd / macd_signal / macd_hist` | 趋势 | P0 | 📐 | 金叉死叉信号 |
| `rsi6 / rsi14` | 动量 | P0 | 📐 | 超买超卖 |
| `kdj_k / kdj_d / kdj_j` | 动量 | P0 | 📐 | 震荡指标 |
| `atr14` | 波动率 | P0 | 📐 | 止损距离的锚 |
| `boll_up / boll_mid / boll_low` | 波动率 | P1 | 📐 | 布林带通道 |
| `vol_ma5 / vol_ratio` | 成交量 | P0 | 📐 | 龙虎榜常伴放量 |
| `vwap5 / vwap20` | 成交量 | P1 | 📐 | 机构成本参考 |
| `obv` | 成交量 | P1 | 📐 | 资金流向 |
| `mfi14` | 成交量 | P1 | 📐 | 量价合一的 RSI |
| **连板数** `limit_up_streak` | 龙虎榜特征 | P0 | 📐 | **几连板** 是游资战场核心标签 |
| **首板/二板/高位板标记** | 龙虎榜特征 | P0 | 📐 | 接力 vs 补漏 的风险等级 |
| **前日是否涨停** | 龙虎榜特征 | P0 | 📐 | 强势股定义 |
| **上榜前 N 日累计涨幅** | 龙虎榜特征 | P0 | 📐 | 追高风险度量 |
| **position_to_60d_high** | 龙虎榜特征 | P0 | 📐 | 距 60 日新高多远 |
| **席位-游资净买占比** | 龙虎榜资金结构 | P0 | ✅ | 当前 score_detail 里已有 |
| **席位-机构净买占比** | 龙虎榜资金结构 | P0 | ✅ | 同上 |
| **席位-知名游资命中** | 龙虎榜资金结构 | P1 | ❌ | 建"知名游资"白名单(章盟主/方新侠/作手新一/炒股养家...) |
| **同席位历史 T+5 胜率** | 龙虎榜资金结构 | P1 | ❌ | 席位跟单质量,需要 seats 历史聚合 |
| **原因类型 encoding** | 龙虎榜特征 | P0 | 🟡 | `reason_type` 字段已分类 |
| **北向持股变动** | 另类 | P1 | ❌ | 依赖北向持仓数据采集 |
| **融资余额 1/5 日变动** | 另类 | P1 | ❌ | 依赖融资融券采集 |
| **主力资金净流入占比** | 另类 | P1 | ❌ | 需要大单数据 |
| **换手率 z-score** | 另类 | P0 | 📐 | 当日换手 / 20 日均值 |
| **行业相对强弱** | 市场结构 | P1 | 📐 | 个股 vs 所在行业涨幅差 |
| **同期市场情绪** | 市场结构 | P0 | ✅ | `market_sentiment` 表 347 行 |
| **概念板块热度** | 市场结构 | P2 | ❌ | 需概念数据 |

### 2.3 标签层(监督信号)

> 有标签才能训练机器学习模型。**新建 `label_daily` 表**,回填历史以备训练。

| 标签 | 定义 | P | S |
|---|---|---|---|
| `t1_open_pnl` | T+1 开盘价 / T 收盘价 - 1(真实可买点) | P0 | 🟡 需在 P17.x 补 |
| `t1_close_pnl` | T+1 收盘 / T 收盘 - 1 | P0 | ✅ 已有 `lhb_tn_price.n_day=1.pnl_pct` |
| `t3_max_pnl` | T+3 内最高价 / T 收盘 - 1 | P0 | ✅ `high_pct` |
| `t5_close_pnl` | T+5 收盘 / T 收盘 - 1 | P0 | ✅ `n_day=5` |
| `t10_close_pnl` | T+10 收盘 / T 收盘 - 1 | P1 | ✅ `n_day=10` |
| `is_profitable_5d` | `t5_close_pnl > 0` 二分类 | P0 | 📐 |
| `max_drawdown_5d` | 5 日内最大回撤 | P1 | 📐 |
| `days_to_peak` | 达到最高点用几天 | P2 | 📐 |

### 2.4 回测/策略评估指标

> 全部走 `backend/domain/backtest/report.py`,按优先级补齐。

| 指标 | P | S | 备注 |
|---|---|---|---|
| 总收益率 / 年化收益 | P0 | ✅ | 已有 |
| 最大回撤 | P0 | ✅ | 已有 |
| Sharpe | P0 | ✅ | 已有 |
| Calmar | P0 | ❌ | 年化 / MDD,1 行公式 |
| Sortino | P1 | ❌ | 只关心下行波动,更贴合交易心理 |
| Information Ratio | P1 | ❌ | P17.2 基准后可算 |
| Alpha / Beta | P1 | ❌ | P17.2 |
| Tracking Error | P1 | ❌ | P17.2 |
| 胜率 / 盈亏比 / Profit Factor | P0 | ✅ | 已有胜率,PF 补 |
| 平均持仓天数 | P0 | ✅ | TradeRecord 已有 `hold_days` |
| 换手率 | P0 | ❌ | buy_amount / avg_aum |
| Expectancy | P1 | ❌ | 胜率 × 平均盈利 - 败率 × 平均亏损 |
| 最大连赢连亏 | P1 | ❌ | TradeRecord 一次遍历 |
| VaR 95% / CVaR | P2 | ❌ | 日收益分位数 |
| Skewness / Kurtosis | P2 | ❌ | scipy.stats 一行 |
| 月度 / 年度收益分布热力图 | P2 | ❌ | 前端需求 |
| IC / RankIC(因子) | P1 | ❌ | 因子研究阶段,vectorbt 方便 |
| Brinson 归因 | P3 | ❌ | 多组合才有意义 |
| 行业归因 | P2 | ❌ | 现有 `attribution.py` 可扩 |

### 2.5 信号 / 仓位 / 止损(策略模块)

| 能力 | P | S | 备注 |
|---|---|---|---|
| 信号强度 0-1 连续值 | P0 | ✅ | score / 100 |
| 信号置信度 | P1 | ❌ | 需要模型 predict_proba |
| 多信号融合权重 | P2 | ❌ | 等 P17.6 Analyzer 解耦后设计 |
| 固定百分比止损 | P0 | ✅ | 策略 DEFAULT_PARAMS.stop_loss_pct |
| ATR 动态止损 | P1 | ❌ | 需 feature_daily.atr |
| 移动止损 Trailing | P1 | ❌ | 需 highest_price 跟踪 |
| 时间止损 | P0 | ✅ | max_hold_days |
| 分批止盈 | P2 | ❌ | 需 `plan_sell` 支持部分卖出 |
| Kelly 仓位 | P2 | ❌ | 需要胜率 + 盈亏比稳定估计 |
| ATR 动态仓位 | P1 | ❌ | 当前是固定 per_position_pct |
| 等权重 / 市值加权 | P0 | ✅ | per_position_pct 即等权 |
| 最大持仓数上限 | P0 | ✅ | max_positions |
| 行业中性 | P2 | ❌ | 需 industry feature + 组合优化 |

### 2.6 执行 / 交易质量(实盘)

| 能力 | P | S | 备注 |
|---|---|---|---|
| 滑点(预期 vs 实际) | P0 | ❌ | **P17.1 必做** |
| TWAP / VWAP 执行 | P2 | ❌ | 当前按开盘一次性成交,散户可接受 |
| 涨停开盘买不到 | P0 | ❌ | **P17.3 必做** |
| T+1 卖出约束 | P0 | ❌ | **P17.3 必做** |
| 冰山单 / 条件单 | P3 | ❌ | 散户无需 |
| 成交率 / 撤单率 | P2 | ❌ | 实盘接 broker 后才有意义 |

### 2.7 风险管理

| 能力 | P | S | 备注 |
|---|---|---|---|
| 仓位集中度(单股上限) | P0 | ✅ | per_position_pct |
| 行业集中度 | P1 | ❌ | 组合级风控 |
| 日内亏损熔断 | P0 | ✅ | `RiskEvaluator.daily_pnl_pct_min` |
| 单日最大下单数 | P0 | ✅ | `max_buys_per_tick` |
| 流动性风险(日成交额覆盖) | P1 | ❌ | 下单金额 / (20 日均成交额 × 阈值) |
| 相关性矩阵 | P2 | ❌ | 持仓间 30 日收益相关 > 0.8 告警 |
| 压力测试 / 蒙特卡洛 | P3 | ❌ | 长期规划 |
| VaR 95% | P2 | ❌ | 历史模拟法即可 |

### 2.8 机器学习 / AI(可选增强)

> 等指标层采全、标签回填完后才有意义。**暂不列入 P17 主线**,放 P18+。

| 能力 | 备注 |
|---|---|
| 特征工程 | SHAP / Permutation Importance |
| 模型:LightGBM / XGBoost | 短线二分类 `is_profitable_5d` |
| 模型:LSTM / Transformer | 序列建模,收益有限,成本高 |
| Walk-Forward Analysis | 避免过拟合 |
| 分布漂移检测 PSI | 模型上线后监控 |

---

## 三、龙虎榜专属指标采集落地路线(P17 → P18)

### 3.1 第一批(P17.7a,高 ROI 低成本)

纯 SQL / 纯 Python 可派生,不依赖新数据源:

1. 新建 `feature_daily` 表(schema 同 kline_daily 分区)。
2. `backend/domain/features/builder.py` 批量计算:
   - 技术指标:ma5/10/20/60 / macd / rsi14 / atr14 / boll / vol_ratio / turnover_z
   - 龙虎榜特征:limit_up_streak / position_to_60d_high / 3d_cum_pnl / 5d_cum_pnl
3. CLI `python -m backend.cli features --from --to`,Makefile `make features`。
4. `/api/lhb?with_features=true` LEFT JOIN 透出。
5. Signals / LhbList 页消费(和 TN 列逻辑一致)。

### 3.2 第二批(P17.7b,依赖外部数据源)

1. 北向持股:建表 `northbound_hold(ts_code, trade_date, shares, ratio)`,akshare `stock_hk_ggt_components_em` 采集,每日定时。
2. 融资余额:建表 `margin_detail`,akshare `stock_margin_detail_sse/szse` 采集。
3. 主力资金流:建表 `fund_flow(ts_code, trade_date, main_net, retail_net)`,akshare `stock_individual_fund_flow`。
4. 集合竞价开盘价:kline_daily 已有 `open`,无需新采。
5. 估值快照:`daily_basic(ts_code, trade_date, pe, pb, ps, total_mv, float_mv, turnover_rate)`,TuShare `daily_basic` 每日采。
6. 所有新表:加入 `make ingest-all` 流水线,加 `/healthz` 覆盖检查。

### 3.3 第三批(P17.7c,龙虎榜特有)

1. **知名游资席位白名单** `broker_whitelist(broker_name, alias, category, active_start_date)`,手工维护 Top 50 知名游资。
2. **席位跟单胜率**:SQL 聚合 `lhb_seats JOIN lhb_tn_price`,算每个席位近 90 日 T+5 胜率 / 平均收益,落 `seat_performance_daily`。
3. **连板接力识别**:结合涨跌停日历,标记每只股票每日连板数。

### 3.4 第四批(P18,机器学习)

等前三批落地后,训练 LightGBM 二分类 `label=is_profitable_5d`,feature=feature_daily 全字段。`/api/lhb?with_ml_score=true` 透出模型预测概率,前端 Signals 页加新列。

---

## 四、与现有 P16 循环的衔接

- P16.8 kline 2025 回填跑完 → 触发 P16.9 chain 自动跑 tn_price + score。
- 之后才开始 P17 系列(当前所有新因子/新指标都需要 kline 完整)。
- P17.1 滑点 / P17.3 涨停+T+1 可以**并行启动**,不依赖数据层。
- P17.2 基准 + P17.4 停牌 依赖 `get_index_daily` / `list_status`,需要先补采一小块数据。

---

## 五、决策点(未来里程碑)

| 里程碑 | 触发条件 |
|---|---|
| 保留自研 | 默认,除非下列 2 条都触发 |
| 引入 bt 作为研究工具 | 当需要频繁原型策略(一周 > 3 个新策略) |
| 引入 vectorbt | 当因子库规模 > 20,需要批量 IC 扫描 |
| 考虑重写为 bt | 不会发生(账务层绑定太深) |
| 分钟级回测 | 当 P16.6 分钟 K 落库后 |

---

_本文档由 P17 方案任务产出,后续每个 P17.x 子任务应回写对应指标的 "S" 状态从 ❌/🟡 → ✅。_
