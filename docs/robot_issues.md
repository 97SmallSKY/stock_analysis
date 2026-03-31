# 模拟炒股 Robot 问题记录

> 本文档记录模拟炒股 Robot 已发现的 Bug、修复方案和未来优化方向。  
> 维护者在发现新问题时应按格式追加记录。  
> 最后更新：2026-03-30

---

## 代理环境分析（京东云 VPS + 新加坡代理）

> 测试日期：2026-03-30

### 代理配置现状

VPS 配置了全局代理（路由到新加坡 VPS）：
```bash
HTTP_PROXY=http://127.0.0.1:10809
HTTPS_PROXY=http://127.0.0.1:10809
ALL_PROXY=socks5://127.0.0.1:10808
no_proxy=localhost,127.0.0.1,*.feishu.cn,open.feishu.cn
```

### 实测结果（2026-03-30）

| 接口 | 测试方式 | 结果 | 延迟 |
|------|---------|------|------|
| `hq.sinajs.cn`（新浪实时） | requests 走代理 | ✅ 成功 | 0.3s |
| `hq.sinajs.cn`（新浪实时） | urllib 走代理 | ✅ 成功 | 0.3s |
| `hq.sinajs.cn`（新浪实时） | urllib 直连 | ✅ 成功 | 0.3s |
| `ak.stock_lhb_detail_em`（东财龙虎榜） | requests 走代理 | ✅ 成功 | 0.37s |
| `ak.tool_trade_date_hist_sina`（交易日历） | requests 走代理 | ✅ 成功 | 0.15s |
| `ak.stock_zh_a_daily`（K线） | requests 走代理 | ✅ 成功 | 0.38s |
| `ak.stock_zh_a_spot_em`（东财实时全量） | requests 走代理 | ⚠️ 极慢（>20s） | N/A |

### 关键结论

**结论1：代理对 AKShare 几乎无影响**  
新加坡代理的路由已针对东财/新浪等 A 股接口优化，延迟 < 0.5s，可以放心使用。

**结论2：`stock_zh_a_spot_em` 性能问题**  
该接口返回全市场 5000+ 支股票实时行情，数据量巨大（通常 5~20s），与代理无关。  
已在 `trade_runner.py` 方法2中降级为最后备用选项，实际很少触发。

**结论3：SOCKS5 支持**  
`PySocks 1.7.1` 和 `socksio 1.0.0` 均已安装，`ALL_PROXY=socks5://` 正常工作。  
之前遇到的 "Missing dependencies for SOCKS support" 是在没有 PySocks 的旧环境，VPS 上不存在此问题。

**结论4：唯一真正的风险 — 代理进程崩溃**  
如果 `127.0.0.1:10809` 代理进程挂掉，所有 `requests`/`urllib` 请求都会立即报 `ConnectionRefusedError`。  
这会导致：
- AKShare 所有接口失败（Phase A 选股失败、Phase B 买入价获取失败）
- 重试机制触发，但重试也会失败

**已采取的保护措施**：  
在 `utils/retry.py` 中实现了 `fetch_sina_quote()` 函数（代理感知双路保障）：
1. 先走系统代理（代理正常时速度最快）
2. 若连接被拒绝/超时，自动切换直连（`ProxyHandler={}`）再试一次

这样即使代理挂掉，新浪实时行情（盘中监控和 Phase B 买入价获取的第一优先级）仍可正常工作。

### 代理挂掉的运维应对

```bash
# 检查代理进程是否正常
ps aux | grep -E 'v2ray|xray|clash' | grep -v grep

# 查看代理端口是否监听
ss -tlnp | grep -E '10809|10808'

# 重启代理（根据实际使用的代理工具）
systemctl restart v2ray  # 或 xray / clash

# 快速测试代理是否恢复
curl -s --max-time 3 -o /dev/null -w '%{http_code}' \
  'http://hq.sinajs.cn/list=sh600000' && echo " OK" || echo " FAIL"
```

---

## 已修复问题

### [2026-03-30] Bug: monitor_intraday.py 在 9:00~9:29 产生无效 DB 连接

**问题描述**  
系统 crontab 配置为 `*/3 9-15 * * 1-5`，即从 9:00 开始每 3 分钟执行 `monitor_intraday.py`。  
但脚本内的时间窗口判断（`hour_min < 930`）在 9:30 之前才生效。  
由于 Python 模块导入时 `storage` 对象就会初始化并建立 PostgreSQL 连接，导致：
- 9:00~9:29 之间的每次执行都会产生 PostgreSQL 连接日志
- 日志中只有 `PostgreSQL 存储初始化` 和 `PostgreSQL 连接成功`，没有任何持仓信息
- 用户误认为监控在执行，实际上没有做任何事情

**影响范围**  
- `logs/monitor_intraday.log` 日志噪音（每3分钟一条无效连接）
- 轻微的 DB 连接资源浪费
- **不影响**买入/卖出逻辑

**修复方案**  
在 `monitor_intraday.py` 文件顶部增加时间预检函数 `_is_trading_hours_now()`，在 `__main__` 模式下提前判断时间窗口，非交易时段（非工作日 / 9:30前 / 15:05后）直接 `sys.exit(0)`，完全避免模块导入和 DB 连接。

`run_monitor()` 函数内部的时间判断保留，用于被其他模块 import 直接调用的场景（兼容性）。

**修复文件**  
- `skills/stock_sim_robot/monitor_intraday.py`：增加顶部时间预检

---

### [2026-03-30] Bug: run_phase_b trade_date 推算不兼容法定节假日

**问题描述**  
`run_phase_b(trade_date=None)` 中推算上一个交易日（T 日）的逻辑：
```python
yesterday = date.today() - timedelta(days=1)
while yesterday.weekday() >= 5:
    yesterday -= timedelta(days=1)
```
只跳过周六/周日，**不跳过法定节假日**（五一、国庆、春节等）。

**复现场景**  
五一假期（例如 5月1日~5月5日）后，5月6日（周一）执行 Phase B：
- 上述代码会找到 5月2日（周五）作为 trade_date
- 但 5月2日是法定节假日，没有龙虎榜数据，也没有 open 持仓
- 导致 Phase B 找不到对应记录，买入价无法回填

**影响范围**  
- 节假日后第一个工作日的 Phase B 执行可能找不到持仓记录
- 开仓记录的 buy_price 长期为 NULL，等 Phase A 重试时才能发现异常

**修复方案**  
新增 `_get_last_trading_day()` 函数，优先使用 AKShare 交易日历接口（`ak.tool_trade_date_hist_sina`）获取精确的上一个交易日；接口失败时降级为跳周末逻辑。

**修复文件**  
- `skills/stock_sim_robot/trade_runner.py`：新增 `_get_last_trading_day()`，替换 Phase B 的 `trade_date` 推算

---

### [2026-03-27] Bug: Phase A 结算时 T+2 价格获取失败导致持仓长期不结算

**问题描述**  
2026-03-27 日志：
```
WARNING | 603601 T+2 价格获取失败，跳过结算
```
再升科技（603601）在 2026-03-26 开仓后，T+2（3月28日）结算时价格获取失败，导致持仓无法自动平仓。

**根本原因**  
- `_get_t2_prices()` 通过新浪 K 线接口获取历史数据
- 接口偶发超时或数据延迟时返回 None
- 当前逻辑遇到 None 直接 `continue`（跳过结算），没有重试机制

**临时处理**  
2026-03-27 下午 Phase A 执行时 `days_held >= MAX_HOLD_DAYS * 2` 条件未触发（因使用自然日计算），最终股票在 2026-03-27 的 Phase A 中因 T+2 未结算而被跳过。

**待优化**  
- 在 Phase A 结算循环中，对 T+2 价格获取失败的持仓增加 K 线重试（sleep + retry）
- 当持仓超过指定天数（如 5 个自然日）仍未结算，自动以最新收盘价强制平仓

**相关代码**  
- `skills/stock_sim_robot/trade_runner.py` → `run_phase_a()` 循环内的 `_get_t2_prices()` 调用

---

## 已知但未修复的问题

### [K线接口] 北交所股票无法获取价格

**现象**：代码以 `8xxxxx / 4xxxxx` 开头的北交所股票，新浪 K 线接口不支持，`_fetch_kline()` 返回 None。  
**影响**：买入价/T+2价格获取失败，相关持仓无法正常结算。  
**处理**：`_is_mainboard()` 函数已通过代码前缀过滤排除北交所股票，不会出现在候选池中。

---

### [AKShare 代理] 东财实时行情受 VPS 代理影响

**现象**：VPS 上访问东方财富实时接口（`push2.eastmoney.com`）受全局代理限制，偶发超时。  
**影响**：`_get_realtime_open_price()` 方法 2（东财）失败，但方法 1（新浪）通常可用。  
**处理**：实时行情获取实现了4层 fallback，新浪为最高优先级（不经代理），基本不影响正常使用。

---

## 优化待办

| 优先级 | 描述 | 状态 |
|--------|------|------|
| 高 | Phase A 结算增加 T+2 价格获取重试（sleep+retry） | ✅ 已实现（2026-03-30） |
| 高 | 持仓超过5自然日未结算时，强制以最新收盘价平仓 | ✅ 已实现（2026-03-30） |
| 中 | Phase B 在 buy_price 获取失败时，记录失败次数并在超过3次后飞书报警 | ✅ 已实现（2026-03-30） |
| 中 | 在 Phase A 选股前将 get_recent_lessons() 结果加入 LLM prompt | ✅ 已实现（2026-03-30） |
| 低 | 量比历史数据接入（东财接口限制） | ✅ 已解决：直接从新浪日线接口提取换手率计算近5日均值，彻底摆脱对东财和龙虎榜历史的依赖（2026-03-30） |
| 低 | 多策略对比回测 | 未实现 |
