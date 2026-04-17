# AKShare API 使用指南

> **版本**: v1.0  
> **最后更新**: 2026-04-17  
> **适用范围**: S01 数据层采集器

---

## 📚 概述

本指南详细说明龙虎榜量化交易系统 v2 中使用的 AKShare API 接口、字段映射、调用限制和最佳实践。

---

## 🔗 API 列表

### 1. 龙虎榜数据

#### 1.1 龙虎榜每日明细

**API**: `ak.stock_lhb_detail_em()`

**用途**: 获取每日龙虎榜股票汇总数据

**参数**:
```python
ak.stock_lhb_detail_em(start_date='YYYYMMDD', end_date='YYYYMMDD')
```

**返回字段**:
| 字段名 | 类型 | 说明 |
|--------|------|------|
| 序号 | int | 序号 |
| 代码 | str | 股票代码 |
| 名称 | str | 股票名称 |
| 上榜日 | str | 上榜日期 (YYYY-MM-DD) |
| 解读 | str | 席位解读 |
| 收盘价 | float | 收盘价 |
| 涨跌幅 | float | 涨跌幅 (%) |
| 龙虎榜净买额 | float | 龙虎榜净买入金额 (元) |
| 龙虎榜买入额 | float | 龙虎榜买入总额 (元) |
| 龙虎榜卖出额 | float | 龙虎榜卖出总额 (元) |
| 龙虎榜成交额 | float | 龙虎榜成交总额 (元) |
| 市场总成交额 | float | 市场总成交额 (元) |
| 净买额占总成交比 | float | 净买额占比 (%) |
| 成交额占总成交比 | float | 成交额占比 (%) |
| 换手率 | float | 换手率 (%) |
| 流通市值 | float | 流通市值 (元) |
| 上榜原因 | str | 上榜原因 |
| 上榜后1日 | float | 上榜后1日涨跌幅 |
| 上榜后2日 | float | 上榜后2日涨跌幅 |
| 上榜后5日 | float | 上榜后5日涨跌幅 |
| 上榜后10日 | float | 上榜后10日涨跌幅 |

**重要说明**:
- ⚠️ **此 API 返回的是股票级别的汇总数据，不包含席位明细**
- 如需席位明细，请使用 `stock_lhb_hyyyb_em()` 或 `stock_lhb_jgmmtj_em()`
- 数据在交易日 15:05 后可用

**调用示例**:
```python
import akshare as ak
from datetime import date

# 获取单日数据
df = ak.stock_lhb_detail_em(
    start_date='20240416',
    end_date='20240416'
)

# 获取区间数据
df = ak.stock_lhb_detail_em(
    start_date='20240401',
    end_date='20240416'
)
```

---

#### 1.2 龙虎榜营业部统计

**API**: `ak.stock_lhb_hyyyb_em()`

**用途**: 获取龙虎榜营业部买卖统计

**参数**:
```python
ak.stock_lhb_hyyyb_em(start_date='YYYYMMDD', end_date='YYYYMMDD')
```

**返回字段**:
| 字段名 | 类型 | 说明 |
|--------|------|------|
| 序号 | int | 序号 |
| 营业部名称 | str | 营业部名称 |
| 上榜次数 | int | 上榜次数 |
| 买入次数 | int | 买入次数 |
| 卖出次数 | int | 卖出次数 |
| 总买入额 | float | 总买入金额 (元) |
| 总卖出额 | float | 总卖出金额 (元) |
| 净买入额 | float | 净买入金额 (元) |

**用途**: 用于分析营业部行为和胜率

---

#### 1.3 机构买卖统计

**API**: `ak.stock_lhb_jgmmtj_em()`

**用途**: 获取机构席位买卖统计

**参数**:
```python
ak.stock_lhb_jgmmtj_em(start_date='YYYYMMDD', end_date='YYYYMMDD')
```

**返回字段**:
| 字段名 | 类型 | 说明 |
|--------|------|------|
| 序号 | int | 序号 |
| 代码 | str | 股票代码 |
| 名称 | str | 股票名称 |
| 收盘价 | float | 收盘价 |
| 涨跌幅 | float | 涨跌幅 (%) |
| 买入机构次数 | int | 机构买入次数 |
| 卖出机构次数 | int | 机构卖出次数 |
| 机构净买入额 | float | 机构净买入金额 (元) |

**用途**: 用于分析机构动向

---

### 2. 市场情绪数据

#### 2.1 涨停池

**API**: `ak.stock_zt_pool_em()`

**用途**: 获取每日涨停股票池

**参数**:
```python
ak.stock_zt_pool_em(date='YYYYMMDD')
```

**返回字段**:
| 字段名 | 类型 | 说明 |
|--------|------|------|
| 序号 | int | 序号 |
| 代码 | str | 股票代码 |
| 名称 | str | 股票名称 |
| 收盘价 | float | 收盘价 |
| 涨跌幅 | float | 涨跌幅 (%) |
| 换手率 | float | 换手率 (%) |
| 流通市值 | float | 流通市值 (元) |
| 涨停统计 | int | 连板天数 |
| 首次封板时间 | str | 首次封板时间 |
| 最后封板时间 | str | 最后封板时间 |
| 涨停板封单额 | float | 封单金额 (元) |
| 涨停板封单量 | int | 封单手数 |

**用途**: 用于计算市场情绪和连板统计

---

#### 2.2 强势股池

**API**: `ak.stock_zt_pool_strong_em()`

**用途**: 获取强势股池（连续涨停股票）

**参数**:
```python
ak.stock_zt_pool_strong_em(date='YYYYMMDD')
```

**返回字段**: 类似 `stock_zt_pool_em()`

**用途**: 用于识别市场龙头股

---

### 3. K线数据

#### 3.1 日线 K 线

**API**: `ak.stock_zh_a_hist()`

**用途**: 获取 A 股日线历史数据

**参数**:
```python
ak.stock_zh_a_hist(
    symbol='000001',  # 股票代码
    period='daily',   # 日线
    start_date='20240401',
    end_date='20240416',
    adjust='qfq'      # 前复权
)
```

**返回字段**:
| 字段名 | 类型 | 说明 |
|--------|------|------|
| 日期 | str | 交易日期 |
| 开盘 | float | 开盘价 |
| 收盘 | float | 收盘价 |
| 最高 | float | 最高价 |
| 最低 | float | 最低价 |
| 成交量 | int | 成交量 (手) |
| 成交额 | float | 成交额 (元) |
| 振幅 | float | 振幅 (%) |
| 涨跌幅 | float | 涨跌幅 (%) |
| 涨跌额 | float | 涨跌额 (元) |
| 换手率 | float | 换手率 (%) |

**用途**: 用于技术分析、特征工程

---

## 🔄 字段映射

### 数据库字段 ← API 字段

#### lhb_stock_daily 表

| 数据库字段 | API 字段 | 转换规则 |
|-----------|---------|---------|
| trade_date | 上榜日 | 字符串 → date |
| stock_code | 代码 | 直接使用 |
| stock_name | 名称 | 直接使用 |
| close_price | 收盘价 | 直接使用 |
| change_ratio | 涨跌幅 | 百分比 → 小数 (÷100) |
| turnover_rate | 换手率 | 百分比 → 小数 (÷100) |
| reason | 上榜原因 | 直接使用 |
| total_buy_amount | 龙虎榜买入额 | 直接使用 (已是元) |
| total_sell_amount | 龙虎榜卖出额 | 直接使用 (已是元) |
| net_buy_amount | 龙虎榜净买额 | 直接使用 (已是元) |
| total_amount | 龙虎榜成交额 | 直接使用 |
| market_amount | 市场总成交额 | 直接使用 |
| net_buy_ratio | 净买额占总成交比 | 百分比 → 小数 (÷100) |
| amount_ratio | 成交额占总成交比 | 百分比 → 小数 (÷100) |
| institution_buy_amount | (计算) | 从机构统计 API 获取 |
| institution_sell_amount | (计算) | 从机构统计 API 获取 |

#### market_sentiment 表

| 数据库字段 | 来源 | 计算规则 |
|-----------|------|---------|
| trade_date | 数据日期 | 直接使用 |
| limit_up_count | stock_zt_pool_em | COUNT(*) |
| limit_down_count | (反向计算) | 跌幅>-9.5% 的股票数 |
| rise_count | 全市场数据 | 涨幅>0 的股票数 |
| fall_count | 全市场数据 | 涨幅<0 的股票数 |
| sentiment_score | (计算) | 见 STRATEGY.md |
| market_stage | (计算) | 见 STRATEGY.md |

---

## ⚠️ 调用限制

### 频率限制

- **单个 IP**: 建议不超过 1 次/秒
- **每日总量**: 建议不超过 5000 次
- **并发限制**: 避免同时发起多个请求

### 时间窗口

| API | 数据可用时间 | 建议采集时间 |
|-----|------------|------------|
| stock_lhb_detail_em | 交易日 15:05 后 | 15:10 - 16:00 |
| stock_zt_pool_em | 交易日 15:05 后 | 15:10 - 16:00 |
| stock_zh_a_hist | 交易日 15:30 后 | 16:00 - 17:00 |

### 错误处理

```python
import time
from loguru import logger

def safe_akshare_call(func, *args, max_retries=3, **kwargs):
    """安全的 AKShare 调用"""
    for attempt in range(max_retries):
        try:
            result = func(*args, **kwargs)
            return result
        except Exception as e:
            if attempt < max_retries - 1:
                wait_time = 2 ** attempt  # 指数退避
                logger.warning(f"AKShare 调用失败，{wait_time}秒后重试: {e}")
                time.sleep(wait_time)
            else:
                logger.error(f"AKShare 调用失败，已达最大重试次数: {e}")
                raise
```

---

## 📊 最佳实践

### 1. 数据缓存

```python
import hashlib
import pickle
from pathlib import Path

def cached_akshare_call(func, cache_dir='cache', *args, **kwargs):
    """带缓存的 AKShare 调用"""
    # 生成缓存键
    key = hashlib.md5(f"{func.__name__}_{args}_{kwargs}".encode()).hexdigest()
    cache_file = Path(cache_dir) / f"{key}.pkl"
    
    if cache_file.exists():
        with open(cache_file, 'rb') as f:
            return pickle.load(f)
    
    # 调用 API
    result = func(*args, **kwargs)
    
    # 保存缓存
    cache_dir.mkdir(exist_ok=True)
    with open(cache_file, 'wb') as f:
        pickle.dump(result, f)
    
    return result
```

### 2. 批量采集

```python
async def batch_collect(collectors, trade_date):
    """批量采集多个数据源"""
    tasks = []
    for collector in collectors:
        task = asyncio.create_task(collector.run(trade_date=trade_date))
        tasks.append(task)
    
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    # 处理异常
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            logger.error(f"{collectors[i].__class__.__name__} 采集失败: {result}")
    
    return results
```

### 3. 数据验证

```python
def validate_lhb_data(df):
    """验证龙虎榜数据质量"""
    issues = []
    
    # 检查必要字段
    required_columns = ['代码', '名称', '上榜日', '收盘价', '涨跌幅']
    for col in required_columns:
        if col not in df.columns:
            issues.append(f"缺少必要字段: {col}")
    
    # 检查数据范围
    if not df.empty:
        if df['涨跌幅'].abs().max() > 20:
            issues.append("涨跌幅异常 (>20%)")
        if df['收盘价'].min() < 0:
            issues.append("收盘价异常 (<0)")
    
    return issues
```

---

## 🔧 故障排查

### 常见问题

#### 1. API 返回空数据

**原因**: 
- 非交易日
- 数据尚未发布
- API 调用参数错误

**解决**:
```python
# 检查是否为交易日
import chinese_calendar as cc
if not cc.is_workday(date.today()):
    print("今日非交易日")
```

#### 2. 调用频率过高

**症状**: 
- 返回 HTTP 429 错误
- 连接超时

**解决**:
```python
# 添加延迟
time.sleep(1)  # 每次调用间隔 1 秒

# 使用代理
# 设置环境变量
# export HTTP_PROXY=http://proxy:port
# export HTTPS_PROXY=http://proxy:port
```

#### 3. 字段名变更

**原因**: AKShare 更新导致字段名变更

**解决**:
```python
# 打印实际字段名
df = ak.stock_lhb_detail_em(...)
print(df.columns.tolist())

# 更新字段映射
FIELD_MAPPING = {
    '旧字段名': '新字段名',
    ...
}
```

---

## 📚 参考文档

- [AKShare 官方文档](https://akshare.akfamily.xyz/)
- [DATA_SOURCE.md](../lhb-quant-v2/DATA_SOURCE.md) - 数据源规范
- [STRATEGY.md](../lhb-quant-v2/STRATEGY.md) - 策略计算公式
- [S01_BUGFIX_REPORT.md](test-reports/S01_BUGFIX_REPORT.md) - Bug 修复报告

---

**维护者**: Zhang Xiaotian  
**最后更新**: 2026-04-17
