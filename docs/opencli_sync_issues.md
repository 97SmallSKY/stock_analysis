# OpenCLI 数据同步问题记录

> 本文档记录了在使用 opencli 和东方财富 API 进行数据采集时遇到的问题，
> 以及已知限制。需要用户后续处理的问题标记为 **需处理**。

## 2026-05-06 采集问题记录

### 问题 1: opencli eastmoney longhu --date 参数不起作用 **[需处理]**

**严重程度**: 高

**描述**: `opencli eastmoney longhu --date YYYY-MM-DD` 参数在设计上是"开始交易日"，
但实际实现使用的是 `TRADE_DATE >= 'sinceDate'` 过滤条件（大于等于），而非等于指定日期。
这意味着无论指定什么日期，API 都会返回从该日期到**今天**的所有数据的前 N 条。

**影响**:
- 无法通过 opencli 获取指定日期的龙虎榜数据
- 历史回补不能依赖 opencli 命令

**临时方案**: 已在 `scripts/opencli_sync.py` 中添加 `--mode backfill-api` 模式，
直接调用东方财富 API 使用 `TRADE_DATE = 'YYYY-MM-DD'` 精确过滤。

**根本修复建议**: 修改 opencli 的 `clis/eastmoney/longhu.js` 源码，
将 `filter` 参数改为 `(TRADE_DATE='${sinceDate}')` 而非 `(TRADE_DATE>='${sinceDate}')`。

相关源码位置: `/opt/homebrew/lib/node_modules/@jackwener/opencli/clis/eastmoney/longhu.js`

### 问题 2: opencli eastmoney 命令 --limit 上限为 100 **[需处理]**

**严重程度**: 中

**描述**: `opencli eastmoney longhu --limit 100` 最大返回 100 条记录。
当一天有超过 100 只股票上榜时（如热门交易日），无法获取完整数据。

**影响**: opencli daily 模式可能丢失部分龙虎榜数据。

**临时方案**: `--mode backfill-api` 使用东方财富 API 的 `pageSize=5000`，可获取一天全部数据。

**根本修复建议**: 修改 longhu.js 中的 `pageSize` 参数，使其支持更大的值，
或支持分页（`pageNumber` 参数）。

### 问题 3: opencli 多个命令 fetch failed **[需处理]**

**严重程度**: 中

**描述**: 以下 opencli 命令在当前环境下返回 "fetch failed" / "other side closed" 错误：

| 命令 | 错误 |
|---|---|
| `eastmoney rank` | fetch failed |
| `eastmoney money-flow` | fetch failed |
| `eastmoney kline` | fetch failed |
| `eastmoney sectors` | fetch failed |

**影响**: 无法通过 opencli 获取涨跌排行、主力资金流入、K线数据、板块排行等数据。

**建议**: 使用 `OPENCLI_DIAGNOSTIC=1` 进行诊断，或直接使用东方财富 API 替代。

### 问题 4: lhb_stock_daily 外键约束导致写入失败 **[已自动解决]**

**严重程度**: 中

**描述**: `lhb_stock_daily` 表有对 `stock_info` 表的外键约束。
当龙虎榜中出现 `stock_info` 中不存在的股票代码（如北交所新股、B股）时，
写入会失败。

**解决方案**: 已在脚本中添加 `_ensure_stock_info()` 方法，
写入 `lhb_stock_daily` 前自动补充缺失的 `stock_info` 记录。

**潜在问题**: 自动补充的 `stock_info` 记录缺少 `area`、`industry`、`list_date` 等字段。
后续可能需要通过 TuShare 或其他数据源补全这些信息。

### 问题 5: market_sentiment 表缺少 index_close 列 **[需处理]**

**严重程度**: 低

**描述**: `market_sentiment` 表中没有 `index_close`（指数收盘价）列，
无法存储通过 `eastmoney index-board` 获取的指数行情数据。

**建议**: 如需在市场情绪表中记录指数收盘价，可添加以下列：

```sql
ALTER TABLE market_sentiment
    ADD COLUMN sh_close numeric(12,4),    -- 上证指数收盘价
    ADD COLUMN sz_close numeric(12,4),    -- 深证成指收盘价
    ADD COLUMN cy_close numeric(12,4),    -- 创业板指收盘价
    ADD COLUMN northbound_net numeric(16,2); -- 北向资金净流入（万元）
```

### 问题 6: 回补数据的 reason_type 映射不完整 **[需处理]**

**严重程度**: 低

**描述**: 东方财富 API 返回的 `TRADE_MARKET` 字段使用中文描述（如"深交所主板"），
脚本将其映射为英文枚举值。但映射表不完整，部分市场类型被标记为 "OTHER"。

**当前映射**:
```python
market_map = {
    "深交所主板": "SZ_MAIN",
    "深交所创业板": "SZ_CYB",
    "深交所风险警示板": "SZ_WARNING",
    "沪市A股": "SH_MAIN",
    "沪市科创板": "SH_KCB",
    "北证A股": "BJ_A",
}
# 其他 → "OTHER"
```

**建议**: 补充完整的市场类型映射表。可通过统计 API 返回的所有 `TRADE_MARKET` 值来完善。

---

## 数据采集范围

### 已完成
- 2021-05-06 ~ 2023-01-02 龙虎榜数据回补（通过东方财富 API）
- 2023-01-03 ~ 2026-05-06 龙虎榜数据（服务器端采集脚本）
- 2026-05-06 当天数据（通过 opencli + API 同步）

### 待补充（需用户确认）
- `market_sentiment` 表的指数收盘价数据
- `lhb_seats` 龙虎榜席位明细数据（东方财富 API 不直接提供）
- `stock_info` 缺失的 industry/area/list_date 等字段
- `kline_daily` K 线日线数据（opencli kline 命令不可用）