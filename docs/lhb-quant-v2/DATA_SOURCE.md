# 龙虎榜量化交易系统 v2 - 数据源配置

> 本文档定义数据源配置、降级策略、缓存规则和数据质量校验规范。

---

## 〇、评审采纳说明

> 二次评审修订日期: 2026-04-16

### 已采纳的改进项 ✅

| 评审问题 | 采纳的改进 |
|----------|------------|
| LHB_DETAIL 降级链单一数据源 | 补充 Sina 和东方财富作为备选数据源，与其他文档统一 |
| 熔断器参数矛盾（50%失败率+30s窗口） | 改为基于连续失败次数的熔断策略，按数据类型区分参数 |
| 数据质量校验规则不完整 | 补充分数据类型的完整校验规则表（K线、龙虎榜、席位、实时行情） |
| 频率限制管理完全缺失 | 新增频率限制管理章节，补充令牌桶限流器和批量采集策略 |
| 数据源字段规范化未定义 | 新增数据规范化规范章节，定义内部统一格式和各数据源映射规则 |
| 缺少数据源监控指标 | 新增数据源监控章节，定义指标和日志规范 |
| 配置硬编码问题 | 新增可配置化方案章节，支持运行时动态修改 |
| 缓存与数据库一致性未定义 | 补充缓存一致性规范，引用 ARCHITECTURE.md 一致性原则 |
| 缓存 Key 命名规范不完整 | 补充环境隔离、版本号等完整规范 |
| 降级链代码缺少关键细节 | 补充完整的降级链管理器代码（含超时、日志、校验、兜底） |
| 缺少数据采集时序 | 新增数据采集时序章节，定义每日时间线和依赖关系 |

### 部分采纳的建议 ⚠️

| 评审建议 | 采纳情况及原因 |
|----------|----------------|
| 缓存一致性完全未定义 | 部分采纳。ARCHITECTURE.md 7.2节已定义 Redis 一致性原则，本文件补充具体写入/读取策略并引用架构文档 |
| 与 DATABASE.md 不一致（LHB 数据源只有 akshare） | 实际上 ARCHITECTURE.md 已定义 `AKShare → Sina → 东方财富`，本次修改已统一 |

### 不采纳的建议及原因 ❌

| 评审建议 | 不采纳原因 |
|----------|------------|
| 引入 TuShare 龙虎榜接口（pro.top_list）作为备选 | TuShare 龙虎榜接口需要较高积分权限（5000+积分），对于个人用户门槛过高，不作为通用备选方案。仅作为可选的高级数据源 |
| 缓存与 HTTP 缓存（Cache-Control）需统一说明 | HTTP 缓存属于 API 层设计，数据源文档聚焦数据采集层缓存策略，二者职责不同，不应在此文档混用 |

---

## 一、数据源概览

| 数据源 | 类型 | 说明 | 频率限制 |
|--------|------|------|----------|
| TuShare | API | 专业金融数据 | 200次/分钟 |
| AKShare | API | 开源金融数据 | 无限制（建议200次/分钟） |
| Sina | HTTP | 实时行情 | 无限制 |

---

## 二、数据类型映射

| 数据类型 | 主数据源 | 备选1 | 备选2 | 风险等级 | 无数据影响 |
|----------|----------|-------|-------|----------|-----------|
| DAILY_KLINE | TuShare | AKShare | Sina | 🟢 低 | 评分降级，使用缓存 |
| LHB_DETAIL | AKShare | Sina | 东方财富 | 🟡 中 | 选股延迟，等待数据发布 |
| LHB_SEATS | AKShare | 东方财富 | - | 🟡 中 | 席位分析缺失 |
| REALTIME_QUOTE | AKShare | Sina | 缓存 | 🟡 中 | 监控延迟，使用过期数据 |
| MARKET_SENTIMENT | AKShare | Sina | - | 🟢 低 | 情绪指标缺失 |
| STOCK_INFO | TuShare | AKShare | - | 🟢 低 | 使用历史缓存 |

> **风险等级说明**：
> - 🔴 高：系统核心功能完全中断
> - 🟡 中：功能降级但可运行
> - 🟢 低：辅助功能缺失

---

## 三、TuShare 配置

### 3.1 获取 Token

访问 https://tushare.pro/register 注册账号获取 Token

### 3.2 配置

```bash
TUSHARE_TOKEN=your_token_here
```

### 3.3 API 调用示例

```python
import tushare as ts

pro = ts.pro_api('your_token')

# 日K线
df = pro.daily(
    ts_code='000001.SZ',
    start_date='20240101',
    end_date='20240131'
)

# 龙虎榜（需要5000+积分）
# df = pro.top_list(trade_date='20240115')
```

### 3.4 接口权限说明

| 接口 | 积分要求 | 说明 |
|------|----------|------|
| daily | 免费 | 日K线数据 |
| daily_basic | 免费 | 每日指标 |
| trade_cal | 免费 | 交易日历 |
| top_list | 5000+ | 龙虎榜列表 |
| top_inst | 5000+ | 龙虎榜机构明细 |

---

## 四、AKShare 配置

### 4.1 安装

```bash
pip install akshare
```

### 4.2 API 调用示例

```python
import akshare as ak

# 日K线
df = ak.stock_zh_a_hist(
    symbol='000001',
    period='daily',
    start_date='20240101',
    end_date='20240131'
)

# 龙虎榜
df = ak.stock_lhb_detail_em(
    start_date='20240115',
    end_date='20240115'
)

# 龙虎榜席位明细
df = ak.stock_lhb_hsgt_em(
    symbol='000001',
    start_date='20240115',
    end_date='20240115'
)
```

---

## 五、Sina 配置

### 5.1 接口说明

Sina 提供实时行情 HTTP 接口，无需认证，支持批量查询。

### 5.2 接口地址

```
实时行情：https://hq.sinajs.cn/list={stock_codes}
```

### 5.3 API 调用示例

```python
import requests

# 单只股票行情
url = "https://hq.sinajs.cn/list=sh600000"
headers = {"Referer": "https://finance.sina.com.cn"}
response = requests.get(url, headers=headers)

# 解析返回数据
# var hq_str_sh600000="浦发银行,12.50,12.45,12.60,12.75,12.40,12.58,12.59,12345678,123456789,..."
data = response.text.split('=')[1].strip('"').split(',')
stock_name = data[0]
open_price = float(data[1])
prev_close = float(data[2])
current_price = float(data[3])
high_price = float(data[4])
low_price = float(data[5])
volume = int(data[8])
amount = float(data[9])

# 批量查询（最多100只）
codes = "sh600000,sz000001,sz000002"
url = f"https://hq.sinajs.cn/list={codes}"
response = requests.get(url, headers=headers)
```

### 5.4 返回字段说明

| 索引 | 字段 | 说明 |
|------|------|------|
| 0 | name | 股票名称 |
| 1 | open | 开盘价 |
| 2 | prev_close | 昨收价 |
| 3 | price | 当前价格 |
| 4 | high | 最高价 |
| 5 | low | 最低价 |
| 8 | volume | 成交量（股） |
| 9 | amount | 成交额（元） |

---

## 六、降级策略

### 6.1 降级链管理器

```python
import asyncio
import time
from typing import List, Dict, Optional
from dataclasses import dataclass
import logging

logger = logging.getLogger(__name__)

@dataclass
class ProviderResult:
    data: any
    provider: str
    stale: bool = False
    errors: List[str] = None

class DataProviderManager:
    """数据源降级链管理器"""
    
    CHAINS = {
        'DAILY_KLINE': ['tushare', 'akshare', 'sina'],
        'LHB_DETAIL': ['akshare', 'sina', 'eastmoney'],
        'LHB_SEATS': ['akshare', 'eastmoney'],
        'REALTIME_QUOTE': ['akshare', 'sina', 'cache'],
        'MARKET_SENTIMENT': ['akshare', 'sina'],
        'STOCK_INFO': ['tushare', 'akshare'],
    }
    
    def __init__(self, config, redis_client, alerter, validator):
        self.config = config
        self.redis = redis_client
        self.alerter = alerter
        self.validator = validator
        self.providers = {}          # name → Provider 实例
        self.breakers = {}          # name → CircuitBreaker 实例
        self.rate_limiters = {}      # name → RateLimiter 实例
    
    async def get_data(self, data_type: str, **params) -> ProviderResult:
        """获取数据，按降级链依次尝试"""
        chain = self.CHAINS.get(data_type, [])
        errors = []
        
        for provider_name in chain:
            provider = self.providers.get(provider_name)
            breaker = self.breakers.get(provider_name)
            limiter = self.rate_limiters.get(provider_name)
            
            if not provider:
                continue
            
            # 1. 检查熔断
            if breaker and breaker.is_open():
                logger.debug(f"跳过已熔断数据源: {provider_name}")
                continue
            
            try:
                # 2. 限流等待
                if limiter:
                    await limiter.acquire()
                
                # 3. 带超时请求
                timeout = self.config.get_timeout(provider_name)
                result = await asyncio.wait_for(
                    provider.fetch(data_type, **params),
                    timeout=timeout
                )
                
                # 4. 数据规范化
                normalized = provider.normalize(data_type, result)
                
                # 5. 数据质量校验
                validation_errors = self.validator.validate(data_type, normalized)
                if validation_errors:
                    logger.warning("数据校验有异常", extra={
                        "provider": provider_name,
                        "data_type": data_type,
                        "errors": validation_errors,
                    })
                    # WARNING 级别不阻断，继续使用数据
                
                # 6. 记录成功
                if breaker:
                    breaker.record_success()
                
                logger.info("数据获取成功", extra={
                    "provider": provider_name,
                    "data_type": data_type,
                    "rows": len(normalized) if hasattr(normalized, '__len__') else 1,
                })
                
                return ProviderResult(
                    data=normalized,
                    provider=provider_name,
                    errors=validation_errors if validation_errors else None
                )
                
            except asyncio.TimeoutError:
                error_msg = f"{provider_name}: 请求超时（{timeout}s）"
                errors.append(error_msg)
                if breaker:
                    breaker.record_failure()
                logger.warning(error_msg)
                
            except Exception as e:
                error_msg = f"{provider_name}: {type(e).__name__}: {e}"
                errors.append(error_msg)
                if breaker:
                    breaker.record_failure()
                logger.warning(error_msg)
        
        # 所有数据源都失败
        logger.error("所有数据源不可用", extra={
            "data_type": data_type,
            "errors": errors,
        })
        
        # 尝试缓存兜底
        cached = await self._get_stale_cache(data_type, **params)
        if cached is not None:
            logger.warning("使用过期缓存数据", extra={"data_type": data_type})
            return ProviderResult(
                data=cached,
                provider='cache',
                stale=True
            )
        
        # 触发告警
        await self.alerter.send(
            level='CRITICAL',
            title=f"数据源全部不可用: {data_type}",
            content="\n".join(errors),
        )
        
        raise NoProviderAvailableError(data_type, errors)
    
    async def _get_stale_cache(self, data_type: str, **params) -> Optional[any]:
        """获取过期缓存数据（兜底）"""
        cache_key = self._build_cache_key(data_type, **params)
        try:
            cached = await self.redis.get(cache_key)
            if cached:
                logger.info(f"从缓存获取数据: {cache_key}")
                return cached
        except Exception as e:
            logger.warning(f"缓存读取失败: {e}")
        return None
    
    def _build_cache_key(self, data_type: str, **params) -> str:
        """构建缓存 Key"""
        # 见缓存 Key 命名规范
        env = self.config.get('ENV', 'prod')
        stock_code = params.get('stock_code', '')
        date = params.get('trade_date', '')
        return f"{env}:mkt:{data_type.lower()}:{stock_code}:{date}".rstrip(':')
```

---

## 七、熔断器配置

### 7.1 基于连续失败次数的熔断器

```python
import time
from enum import Enum
from dataclasses import dataclass

class CircuitState(Enum):
    CLOSED = 'closed'      # 正常状态
    OPEN = 'open'          # 熔断状态
    HALF_OPEN = 'half_open'  # 半开状态（探测恢复）

@dataclass
class CircuitBreaker:
    """基于连续失败次数的熔断器"""
    
    failure_threshold: int = 3      # 连续失败次数阈值
    recovery_timeout: int = 300     # 恢复等待时间（秒）
    half_open_max_calls: int = 1    # 半开状态最多探测次数
    
    def __post_init__(self):
        self.consecutive_failures = 0
        self.state = CircuitState.CLOSED
        self.last_failure_time = None
    
    def is_open(self) -> bool:
        """检查熔断器是否打开"""
        if self.state == CircuitState.OPEN:
            elapsed = time.time() - self.last_failure_time
            if elapsed >= self.recovery_timeout:
                self.state = CircuitState.HALF_OPEN
                return False
            return True
        return False
    
    def record_success(self):
        """记录成功"""
        self.consecutive_failures = 0
        self.state = CircuitState.CLOSED
    
    def record_failure(self):
        """记录失败"""
        self.consecutive_failures += 1
        self.last_failure_time = time.time()
        if self.consecutive_failures >= self.failure_threshold:
            self.state = CircuitState.OPEN
```

### 7.2 按数据类型的熔断参数

| 数据类型 | 连续失败阈值 | 恢复等待时间 | 说明 |
|----------|--------------|--------------|------|
| REALTIME_QUOTE | 5次 | 60秒 | 行情要求快速恢复 |
| DAILY_KLINE | 3次 | 300秒 | 非实时，可等待 |
| LHB_DETAIL | 3次 | 180秒 | 重要但非实时 |
| LHB_SEATS | 3次 | 180秒 | 席位明细 |
| MARKET_SENTIMENT | 3次 | 300秒 | 辅助数据 |
| STOCK_INFO | 2次 | 600秒 | 变更频率低 |

### 7.3 请求超时配置

| 数据源 | 默认超时 | 说明 |
|--------|----------|------|
| TuShare | 10秒 | API 响应通常较快 |
| AKShare | 15秒 | 部分接口较慢 |
| Sina | 5秒 | 实时行情要求快速 |
| 东方财富 | 10秒 | |

---

## 八、缓存规则

### 8.1 缓存 TTL

| 数据类型 | TTL | 存储位置 | 说明 |
|----------|-----|----------|------|
| DAILY_KLINE | 6小时 | Redis | 日级别数据 |
| LHB_DETAIL | 24小时 | Redis | 当日数据收盘后稳定 |
| LHB_SEATS | 24小时 | Redis | 席位明细 |
| REALTIME_QUOTE | 12秒 | Redis | 实时行情快照 |
| MARKET_SENTIMENT | 5分钟 | Redis | 情绪指标 |
| STOCK_INFO | 7天 | Redis | 基础信息变化频率低 |

### 8.2 缓存 Key 命名规范

#### 格式定义

```
{env}:{module}:{data_type}:{dimensions}
```

#### 具体规则

| 数据类型 | Key 格式 | 示例 |
|----------|----------|------|
| 日K线 | {env}:mkt:kline:{code}:{adjust}:{date} | prod:mkt:kline:000001:qfq:20240115 |
| 龙虎榜日数据 | {env}:mkt:lhb:{date} | prod:mkt:lhb:20240115 |
| 龙虎榜席位 | {env}:mkt:lhb_seat:{date}:{code} | prod:mkt:lhb_seat:20240115:000001 |
| 实时行情 | {env}:mkt:quote:{code} | prod:mkt:quote:000001 |
| 市场情绪 | {env}:mkt:sentiment:{date} | prod:mkt:sentiment:20240115 |
| 股票信息 | {env}:mkt:stock:{code} | prod:mkt:stock:000001 |
| 幂等键 | {env}:idem:{key} | prod:idem:create-robot-001 |
| 分布式锁 | {env}:lock:{resource} | prod:lock:trade:robot_1 |
| 空值缓存 | {env}:null:{data_type}:{key} | prod:null:kline:999999 |

#### 规范要求

- `env`：从环境变量读取（prod/staging/dev）
- 所有 key 使用冒号分隔
- `stock_code` 统一 6 位纯数字
- `date` 统一 `YYYYMMDD` 格式（key 中不用横线，节省空间）
- `adjust`：qfq（前复权）、hfq（后复权）、none（不复权）

### 8.3 缓存一致性规范

> 引用 ARCHITECTURE.md 7.2节一致性原则

#### 写入策略

| 数据类型 | 写入策略 | 说明 |
|----------|----------|------|
| DAILY_KLINE | Write-through | 写DB成功后立即更新缓存 |
| LHB_DETAIL | Write-through | 写DB成功后立即更新缓存 |
| LHB_SEATS | Write-through | 写DB成功后立即更新缓存 |
| REALTIME_QUOTE | Cache-only | 仅写缓存，不入库（快照定期批量入库） |
| MARKET_SENTIMENT | Write-through | 写DB成功后更新缓存 |
| STOCK_INFO | Write-through | 写DB成功后更新缓存 |

#### 读取策略

| 场景 | 行为 |
|------|------|
| 缓存命中且未过期 | 直接返回 |
| 缓存未命中 | 查数据库 → 写入缓存 → 返回 |
| 缓存过期 + 数据源可用 | 请求数据源 → 更新缓存 → 返回 |
| 缓存过期 + 数据源不可用 | 返回过期数据 + 标记 `stale=true` |

#### 缓存穿透防护

- 不存在的 key 缓存空值，TTL=60秒
- Key 格式校验（stock_code 必须 6 位数字）
- 布隆过滤器预检查（可选，高并发场景）

#### 交易决策用缓存的规则

| 决策类型 | 缓存使用规则 |
|----------|--------------|
| 止损判断 | 允许使用 ≤12 秒内的缓存数据 |
| 止盈判断 | 允许使用 ≤12 秒内的缓存数据 |
| 买入决策 | 必须使用实时数据（不用缓存） |
| 卖出决策 | 允许使用 ≤12 秒内的缓存数据 |

---

## 九、数据质量校验

### 9.1 K线数据校验规则

| 规则 | 校验内容 | 严重级别 | 处理方式 |
|------|----------|----------|----------|
| 空值检查 | open/high/low/close/volume 非空 | ERROR | 丢弃该条 |
| K线逻辑 | high≥low, high≥open, high≥close, low≤open, low≤close | ERROR | 丢弃该条 |
| 涨跌幅范围 | abs(change) ≤ 30% | WARNING | 标记异常，不丢弃 |
| 价格为正 | close > 0 | ERROR | 丢弃该条 |
| 数据量 | 全市场 ≥ 100条 | ERROR | 触发告警，疑似数据源故障 |
| 日期合理 | trade_date ≤ today | ERROR | 丢弃该条 |
| 重复检查 | (date, code) 唯一 | WARNING | 取最新一条 |

### 9.2 龙虎榜数据校验规则

| 规则 | 校验内容 | 严重级别 | 处理方式 |
|------|----------|----------|----------|
| 必填字段 | date/code/name/close/net_buy 非空 | ERROR | 丢弃该条 |
| 代码格式 | 6位数字 | ERROR | 丢弃该条 |
| 金额非负 | total_buy≥0, total_sell≥0 | ERROR | 丢弃该条 |
| 金额一致 | net_buy ≈ total_buy - total_sell（允许1元误差） | WARNING | 标记异常 |
| 数据量 | 每日 1-200 条 | WARNING | 偏少触发告警 |
| 涨跌幅范围 | abs(change_ratio) ≤ 30% | WARNING | 标记异常 |

### 9.3 席位数据校验规则

| 规则 | 校验内容 | 严重级别 | 处理方式 |
|------|----------|----------|----------|
| 排名范围 | rank 1-5 | ERROR | 丢弃该条 |
| 方向值 | direction in ('buy', 'sell') | ERROR | 丢弃该条 |
| 金额为正 | amount > 0 | ERROR | 丢弃该条 |
| 名称非空 | seat_name 非空 | WARNING | 标记"未知席位" |
| 排名唯一 | (date,code,direction,rank) 唯一 | ERROR | 取最新一条 |

### 9.4 实时行情校验规则

| 规则 | 校验内容 | 严重级别 | 处理方式 |
|------|----------|----------|----------|
| 价格为正 | price > 0 | ERROR | 使用上一次有效价格 |
| 偏离度 | 相对昨收 ≤ 22%（覆盖科创/创业20%涨跌幅） | WARNING | 标记异常 |
| 时效性 | 延迟 ≤ 60秒 | WARNING | 标记 stale |

### 9.5 数据校验器代码

```python
from typing import List
from datetime import date
import pandas as pd
import re

class DataValidator:
    """数据质量校验器"""
    
    def validate(self, data_type: str, df: pd.DataFrame) -> List[str]:
        """根据数据类型调用对应校验方法"""
        validators = {
            'DAILY_KLINE': self.validate_kline,
            'LHB_DETAIL': self.validate_lhb,
            'LHB_SEATS': self.validate_lhb_seats,
            'REALTIME_QUOTE': self.validate_realtime_quote,
        }
        validator = validators.get(data_type)
        if validator:
            return validator(df)
        return []
    
    def validate_kline(self, df: pd.DataFrame) -> List[str]:
        errors = []
        
        # 1. 空值检查
        required_cols = ['open_price', 'high_price', 'low_price', 'close_price', 'volume']
        for col in required_cols:
            if col in df.columns:
                null_count = df[col].isna().sum()
                if null_count > 0:
                    errors.append(f"{col} 存在 {null_count} 个空值")
        
        # 2. K线逻辑一致性
        if all(col in df.columns for col in ['high_price', 'low_price', 'open_price', 'close_price']):
            invalid = df[
                (df['high_price'] < df['low_price']) |
                (df['high_price'] < df['open_price']) |
                (df['high_price'] < df['close_price']) |
                (df['low_price'] > df['open_price']) |
                (df['low_price'] > df['close_price'])
            ]
            if len(invalid) > 0:
                errors.append(f"{len(invalid)} 条K线数据逻辑异常（high/low/open/close关系错误）")
        
        # 3. 涨跌幅范围
        if 'change_ratio' in df.columns:
            extreme = df[df['change_ratio'].abs() > 0.30]
            if len(extreme) > 0:
                errors.append(f"{len(extreme)} 条涨跌幅超过30%，需人工确认")
        
        # 4. 价格合理性
        if 'close_price' in df.columns:
            zero_price = df[df['close_price'] <= 0]
            if len(zero_price) > 0:
                errors.append(f"{len(zero_price)} 条收盘价≤0")
        
        # 5. 数据完整性
        if len(df) < 100:
            errors.append(f"数据量异常：仅 {len(df)} 条，预期 4000+")
        
        # 6. 日期合理性
        if 'trade_date' in df.columns:
            future = df[df['trade_date'] > date.today()]
            if len(future) > 0:
                errors.append(f"{len(future)} 条数据日期为未来日期")
        
        return errors
    
    def validate_lhb(self, df: pd.DataFrame) -> List[str]:
        errors = []
        
        # 1. 空值检查
        required_cols = ['trade_date', 'stock_code', 'stock_name', 'close_price', 'net_buy_amount']
        for col in required_cols:
            if col in df.columns:
                null_count = df[col].isna().sum()
                if null_count > 0:
                    errors.append(f"{col} 存在 {null_count} 个空值")
        
        # 2. 股票代码格式
        if 'stock_code' in df.columns:
            invalid_code = df[~df['stock_code'].astype(str).str.match(r'^\d{6}$')]
            if len(invalid_code) > 0:
                errors.append(f"{len(invalid_code)} 条股票代码格式错误")
        
        # 3. 金额逻辑一致性
        if all(col in df.columns for col in ['total_buy_amount', 'total_sell_amount']):
            invalid_amount = df[
                (df['total_buy_amount'] < 0) | (df['total_sell_amount'] < 0)
            ]
            if len(invalid_amount) > 0:
                errors.append(f"{len(invalid_amount)} 条买卖金额为负数")
            
            # 净买入应等于买入-卖出（允许小误差）
            if 'net_buy_amount' in df.columns:
                diff = (df['net_buy_amount'] - (df['total_buy_amount'] - df['total_sell_amount'])).abs()
                mismatch = df[diff > 1]
                if len(mismatch) > 0:
                    errors.append(f"{len(mismatch)} 条净买入与买卖差额不一致")
        
        # 4. 数据量合理性
        if len(df) > 200:
            errors.append(f"龙虎榜数量异常偏多：{len(df)} 条，预期 30-100")
        elif len(df) == 0:
            errors.append("龙虎榜数据为空")
        
        return errors
    
    def validate_lhb_seats(self, df: pd.DataFrame) -> List[str]:
        errors = []
        
        # 1. 排名范围
        if 'rank' in df.columns:
            invalid_rank = df[~df['rank'].between(1, 5)]
            if len(invalid_rank) > 0:
                errors.append(f"{len(invalid_rank)} 条席位排名不在1-5范围内")
        
        # 2. 方向校验
        if 'direction' in df.columns:
            invalid_dir = df[~df['direction'].isin(['buy', 'sell'])]
            if len(invalid_dir) > 0:
                errors.append(f"{len(invalid_dir)} 条方向值非法")
        
        # 3. 金额为正
        if 'amount' in df.columns:
            invalid_amount = df[df['amount'] <= 0]
            if len(invalid_amount) > 0:
                errors.append(f"{len(invalid_amount)} 条席位金额≤0")
        
        # 4. 席位名称非空
        if 'seat_name' in df.columns:
            empty_name = df[df['seat_name'].astype(str).str.strip() == '']
            if len(empty_name) > 0:
                errors.append(f"{len(empty_name)} 条席位名称为空")
        
        # 5. 排名唯一
        if all(col in df.columns for col in ['trade_date', 'stock_code', 'direction', 'rank']):
            dup = df.duplicated(subset=['trade_date', 'stock_code', 'direction', 'rank'])
            if dup.any():
                errors.append(f"{dup.sum()} 条席位排名重复")
        
        return errors
    
    def validate_realtime_quote(self, data: dict) -> List[str]:
        errors = []
        
        # 1. 价格为正
        if data.get('price', 0) <= 0:
            errors.append("实时价格≤0")
        
        # 2. 价格合理范围
        if 'prev_close' in data and data['prev_close'] > 0:
            change = abs(data['price'] - data['prev_close']) / data['prev_close']
            if change > 0.22:
                errors.append(f"价格偏离昨收 {change:.1%}，超过合理范围")
        
        # 3. 时间合理性
        if 'timestamp' in data:
            from datetime import datetime
            age = (datetime.now() - data['timestamp']).total_seconds()
            if age > 60:
                errors.append(f"行情数据延迟 {age:.0f} 秒")
        
        return errors
```

---

## 十、频率限制管理

### 10.1 限流策略

| 数据源 | 限制 | 实现方式 | 批量采集策略 |
|--------|------|----------|-------------|
| TuShare | 200次/分钟 | 令牌桶 | 每次请求间隔 300ms |
| AKShare | 建议200次/分钟 | 令牌桶 | 每次请求间隔 300ms |
| Sina | 建议100次/分钟 | 令牌桶 | 每次请求间隔 600ms |
| 东方财富 | 建议100次/分钟 | 令牌桶 | 每次请求间隔 600ms |

### 10.2 令牌桶限流器

```python
import asyncio
from asyncio import Lock

class RateLimiter:
    """令牌桶限流器"""
    
    def __init__(self, rate: float, per: float = 60.0):
        """
        rate: 允许的请求数
        per: 时间窗口（秒）
        """
        self.interval = per / rate  # 两次请求之间的最小间隔
        self.last_request = 0.0
        self._lock = Lock()
    
    async def acquire(self):
        """获取令牌，如果需要则等待"""
        async with self._lock:
            now = asyncio.get_event_loop().time()
            wait_time = self.last_request + self.interval - now
            if wait_time > 0:
                await asyncio.sleep(wait_time)
            self.last_request = asyncio.get_event_loop().time()

# 使用示例
tushare_limiter = RateLimiter(rate=180, per=60)  # 留10%余量
akshare_limiter = RateLimiter(rate=180, per=60)
sina_limiter = RateLimiter(rate=90, per=60)
```

### 10.3 批量采集优化

```python
async def batch_fetch_klines(stock_codes: List[str], batch_size: int = 50):
    """批量采集K线数据，避免触发限流"""
    results = []
    total = len(stock_codes)
    
    for i in range(0, total, batch_size):
        batch = stock_codes[i:i + batch_size]
        logger.info(f"采集进度: {i}/{total} ({i*100//total}%)")
        
        tasks = [fetch_kline_with_limit(code) for code in batch]
        batch_results = await asyncio.gather(*tasks, return_exceptions=True)
        results.extend([r for r in batch_results if not isinstance(r, Exception)])
        
        # 批次间暂停，避免连续高频请求
        if i + batch_size < total:
            await asyncio.sleep(5)
    
    return results

async def fetch_kline_with_limit(stock_code: str):
    """带限流的K线数据获取"""
    await tushare_limiter.acquire()  # 等待限流
    return await tushare_provider.fetch('DAILY_KLINE', stock_code=stock_code)
```

---

## 十一、数据规范化规范

### 11.1 内部统一格式

| 字段 | 内部格式 | 类型 | 说明 |
|------|----------|------|------|
| stock_code | 000001 | str(6位纯数字) | 不含市场后缀 |
| trade_date | 2024-01-15 | date | ISO 格式 |
| open_price | 12.500 | Decimal(10,3) | 三位小数 |
| close_price | 12.500 | Decimal(10,3) | 三位小数 |
| high_price | 12.800 | Decimal(10,3) | 三位小数 |
| low_price | 12.300 | Decimal(10,3) | 三位小数 |
| volume | 50000 | int | 单位：股 |
| amount | 125000.00 | Decimal(16,2) | 单位：元 |
| change_ratio | 0.0523 | Decimal(8,4) | 小数形式，非百分比 |

### 11.2 各数据源字段映射

#### TuShare 映射

```python
class TushareProvider(BaseProvider):
    """TuShare 数据源适配器"""
    
    FIELD_MAP_KLINE = {
        'ts_code': 'stock_code',
        'trade_date': 'trade_date',
        'open': 'open_price',
        'high': 'high_price',
        'low': 'low_price',
        'close': 'close_price',
        'vol': 'volume',
        'amount': 'amount',
        'pct_chg': 'change_ratio',
    }
    
    def normalize_kline(self, raw_df: pd.DataFrame) -> pd.DataFrame:
        """规范化 K 线数据"""
        df = raw_df.rename(columns=self.FIELD_MAP_KLINE)
        
        # 去掉市场后缀 .SZ/.SH
        df['stock_code'] = df['stock_code'].str[:6]
        
        # 日期格式转换
        df['trade_date'] = pd.to_datetime(df['trade_date'])
        
        # 百分比转小数
        if 'change_ratio' in df.columns:
            df['change_ratio'] = df['change_ratio'] / 100
        
        return df
    
    def normalize_lhb(self, raw_df: pd.DataFrame) -> pd.DataFrame:
        """规范化龙虎榜数据"""
        # TuShare 龙虎榜字段映射
        field_map = {
            'trade_date': 'trade_date',
            'ts_code': 'stock_code',
            'name': 'stock_name',
            'close': 'close_price',
            'pct_change': 'change_ratio',
            'turnover_rate': 'turnover_rate',
            'buy_amount': 'total_buy_amount',
            'sell_amount': 'total_sell_amount',
            'net_buy': 'net_buy_amount',
        }
        df = raw_df.rename(columns=field_map)
        df['stock_code'] = df['stock_code'].str[:6]
        df['trade_date'] = pd.to_datetime(df['trade_date'])
        df['change_ratio'] = df['change_ratio'] / 100
        return df
```

#### AKShare 映射

```python
class AkshareProvider(BaseProvider):
    """AKShare 数据源适配器"""
    
    FIELD_MAP_KLINE = {
        '日期': 'trade_date',
        '开盘': 'open_price',
        '最高': 'high_price',
        '最低': 'low_price',
        '收盘': 'close_price',
        '成交量': 'volume',
        '成交额': 'amount',
        '涨跌幅': 'change_ratio',
    }
    
    def normalize_kline(self, raw_df: pd.DataFrame) -> pd.DataFrame:
        """规范化 K 线数据"""
        df = raw_df.rename(columns=self.FIELD_MAP_KLINE)
        df['trade_date'] = pd.to_datetime(df['trade_date'])
        # AKShare 涨跌幅已是小数形式，无需转换
        return df
    
    def normalize_lhb(self, raw_df: pd.DataFrame) -> pd.DataFrame:
        """规范化龙虎榜数据（东方财富源）"""
        field_map = {
            '日期': 'trade_date',
            '代码': 'stock_code',
            '名称': 'stock_name',
            '收盘价': 'close_price',
            '涨跌幅': 'change_ratio',
            '换手率': 'turnover_rate',
            '买入金额': 'total_buy_amount',
            '卖出金额': 'total_sell_amount',
            '净买金额': 'net_buy_amount',
        }
        df = raw_df.rename(columns=field_map)
        df['trade_date'] = pd.to_datetime(df['trade_date'])
        return df
```

#### Sina 映射

```python
class SinaProvider(BaseProvider):
    """Sina 数据源适配器"""
    
    def parse_realtime_quote(self, response_text: str, stock_code: str) -> dict:
        """解析实时行情数据"""
        # 去掉 var hq_str_xxx="..." 包装
        data_str = response_text.split('=')[1].strip('";\n')
        fields = data_str.split(',')
        
        if len(fields) < 10:
            raise ValueError(f"无效的 Sina 行情数据: {response_text}")
        
        return {
            'stock_code': stock_code,
            'stock_name': fields[0],
            'open_price': float(fields[1]) if fields[1] else None,
            'prev_close': float(fields[2]) if fields[2] else None,
            'price': float(fields[3]) if fields[3] else None,
            'high_price': float(fields[4]) if fields[4] else None,
            'low_price': float(fields[5]) if fields[5] else None,
            'volume': int(fields[8]) if fields[8] else 0,
            'amount': float(fields[9]) if fields[9] else 0,
            'timestamp': datetime.now(),
        }
    
    def normalize_quote(self, raw_data: dict) -> dict:
        """规范化实时行情"""
        # 计算涨跌幅
        if raw_data.get('prev_close') and raw_data['prev_close'] > 0:
            raw_data['change_ratio'] = (
                raw_data['price'] - raw_data['prev_close']
            ) / raw_data['prev_close']
        return raw_data
```

---

## 十二、数据源监控

### 12.1 监控指标

| 指标 | 维度 | 用途 |
|------|------|------|
| 请求总数 | provider, data_type, status(success/fail) | 成功率计算 |
| 请求延迟 | provider, data_type | 性能监控 |
| 熔断器状态 | provider | 0=关闭, 1=打开, 2=半开 |
| 降级次数 | data_type, from_provider, to_provider | 降级频率 |
| 数据新鲜度 | data_type | 距最新数据的时间差 |
| 校验失败数 | data_type, rule | 数据质量趋势 |

### 12.2 日志规范

#### 日志级别

| 级别 | 场景 |
|------|------|
| INFO | 数据获取成功、数据源切换（降级/恢复） |
| WARNING | 熔断状态变更、数据校验失败、请求超时、接近阈值 |
| ERROR | 所有数据源不可用、缓存兜底失败 |

#### 日志格式示例

```python
# 数据源降级
logger.info("数据源降级", extra={
    "data_type": "DAILY_KLINE",
    "from_provider": "tushare",
    "to_provider": "akshare",
    "reason": "连续3次超时",
    "circuit_state": "open",
})

# 数据校验失败
logger.warning("数据校验失败", extra={
    "data_type": "LHB_DETAIL",
    "provider": "akshare",
    "trade_date": "2026-04-16",
    "errors": ["2条金额为负数", "净买入与买卖差额不一致"],
    "total_rows": 85,
    "invalid_rows": 2,
})

# 所有数据源不可用
logger.error("所有数据源不可用", extra={
    "data_type": "REALTIME_QUOTE",
    "providers_tried": ["akshare", "sina"],
    "errors": {
        "akshare": "ConnectionTimeout after 15s",
        "sina": "CircuitBreaker OPEN",
    },
    "fallback": "使用12秒前缓存数据",
})
```

### 12.3 告警规则

| 告警 | 条件 | 级别 | 通知方式 |
|------|------|------|----------|
| 单数据源熔断 | 任意数据源熔断器打开 | WARNING | 飞书 |
| 多数据源熔断 | 同类型数据所有源熔断 | CRITICAL | 飞书 + 短信 |
| 数据延迟 | 核心数据（K线/龙虎榜）超过预期发布时间30分钟 | WARNING | 飞书 |
| 数据质量异常 | 校验失败率 > 10% | WARNING | 飞书 |
| 缓存兜底 | 连续使用缓存数据超过3次 | WARNING | 飞书 |

---

## 十三、可配置化方案

### 13.1 app_config 表结构

```sql
CREATE TABLE app_config (
    key VARCHAR(100) PRIMARY KEY,
    value TEXT NOT NULL,
    value_type VARCHAR(20) NOT NULL DEFAULT 'string',
    description TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE app_config IS '运行时配置表';

CREATE INDEX idx_config_key ON app_config(key);
```

### 13.2 初始配置数据

```sql
INSERT INTO app_config (key, value, value_type, description) VALUES
-- 降级链配置
  ('PROVIDER_CHAIN_DAILY_KLINE', '["tushare","akshare","sina"]', 'json', 'K线降级链'),
  ('PROVIDER_CHAIN_LHB_DETAIL', '["akshare","sina","eastmoney"]', 'json', '龙虎榜降级链'),
  ('PROVIDER_CHAIN_LHB_SEATS', '["akshare","eastmoney"]', 'json', '席位降级链'),
  ('PROVIDER_CHAIN_REALTIME_QUOTE', '["akshare","sina","cache"]', 'json', '行情降级链'),
  ('PROVIDER_CHAIN_MARKET_SENTIMENT', '["akshare","sina"]', 'json', '情绪降级链'),
  
-- 熔断器配置
  ('CIRCUIT_FAILURE_THRESHOLD_KLINE', '3', 'int', 'K线熔断连续失败次数'),
  ('CIRCUIT_FAILURE_THRESHOLD_LHB', '3', 'int', '龙虎榜熔断连续失败次数'),
  ('CIRCUIT_FAILURE_THRESHOLD_QUOTE', '5', 'int', '行情熔断连续失败次数'),
  ('CIRCUIT_RECOVERY_TIMEOUT_SHORT', '60', 'int', '短恢复超时（秒）'),
  ('CIRCUIT_RECOVERY_TIMEOUT_LONG', '300', 'int', '长恢复超时（秒）'),
  
-- 缓存 TTL 配置
  ('CACHE_TTL_DAILY_KLINE', '21600', 'int', 'K线缓存TTL(秒)'),
  ('CACHE_TTL_LHB_DETAIL', '86400', 'int', '龙虎榜缓存TTL(秒)'),
  ('CACHE_TTL_REALTIME_QUOTE', '12', 'int', '行情缓存TTL(秒)'),
  ('CACHE_TTL_MARKET_SENTIMENT', '300', 'int', '情绪缓存TTL(秒)'),
  
-- 限流配置
  ('RATE_LIMIT_TUSHARE', '180', 'int', 'TuShare每分钟请求上限'),
  ('RATE_LIMIT_AKSHARE', '180', 'int', 'AKShare每分钟请求上限'),
  ('RATE_LIMIT_SINA', '90', 'int', 'Sina每分钟请求上限'),
  
-- 请求超时
  ('REQUEST_TIMEOUT_TUSHARE', '10', 'int', 'TuShare请求超时(秒)'),
  ('REQUEST_TIMEOUT_AKSHARE', '15', 'int', 'AKShare请求超时(秒)'),
  ('REQUEST_TIMEOUT_SINA', '5', 'int', 'Sina请求超时(秒)');
```

### 13.3 配置加载与刷新

```python
import json
from typing import Any
import logging

logger = logging.getLogger(__name__)

class ConfigManager:
    """运行时配置管理器"""
    
    def __init__(self, db_session, cache_ttl: int = 300):
        self.db = db_session
        self.cache_ttl = cache_ttl
        self._cache = {}
        self._last_refresh = 0
    
    async def get(self, key: str, default: Any = None) -> Any:
        """获取配置值"""
        # 刷新缓存
        await self._maybe_refresh()
        
        if key in self._cache:
            return self._cache[key]
        return default
    
    async def _maybe_refresh(self):
        """检查并刷新缓存"""
        import time
        now = time.time()
        if now - self._last_refresh > self.cache_ttl:
            await self._refresh()
    
    async def _refresh(self):
        """从数据库刷新配置"""
        try:
            rows = await self.db.execute(
                "SELECT key, value, value_type FROM app_config"
            )
            for row in rows:
                key, value, value_type = row
                self._cache[key] = self._parse_value(value, value_type)
            self._last_refresh = time.time()
            logger.debug(f"配置缓存已刷新，共 {len(self._cache)} 项")
        except Exception as e:
            logger.warning(f"配置刷新失败: {e}")
    
    def _parse_value(self, value: str, value_type: str) -> Any:
        """解析配置值"""
        if value_type == 'int':
            return int(value)
        elif value_type == 'float':
            return float(value)
        elif value_type == 'bool':
            return value.lower() in ('true', '1', 'yes')
        elif value_type == 'json':
            return json.loads(value)
        return value
    
    async def set(self, key: str, value: Any) -> bool:
        """设置配置值（立即生效）"""
        value_type = self._detect_type(value)
        value_str = json.dumps(value) if value_type == 'json' else str(value)
        
        await self.db.execute(
            """
            INSERT INTO app_config (key, value, value_type, updated_at)
            VALUES ($1, $2, $3, NOW())
            ON CONFLICT (key) DO UPDATE 
            SET value = $2, value_type = $3, updated_at = NOW()
            """,
            key, value_str, value_type
        )
        
        # 立即更新缓存
        self._cache[key] = value
        logger.info(f"配置已更新: {key} = {value}")
        return True
    
    def _detect_type(self, value: Any) -> str:
        """检测值类型"""
        if isinstance(value, bool):
            return 'bool'
        elif isinstance(value, int):
            return 'int'
        elif isinstance(value, float):
            return 'float'
        elif isinstance(value, (list, dict)):
            return 'json'
        return 'string'
```

---

## 十四、数据采集时序

### 14.1 交易日时间线

```
09:15  Phase B 买入确认
       └─ 需要：REALTIME_QUOTE（实时行情）
       
09:30-15:00  盘中监控（每10秒）
       └─ 需要：REALTIME_QUOTE（实时行情）
       
14:50  Phase A 选股
       └─ 需要：前一日 LHB_DETAIL + 当日盘中表现
       └─ 注意：当日龙虎榜数据通常收盘后才发布
       
15:30  日结算
       └─ 需要：DAILY_KLINE（当日收盘数据）
       └─ 注意：数据源通常 16:00 后更新当日 K 线
       └─ 处理：使用收盘快照或等待 16:30 执行
       
17:00  龙虎榜数据采集
       └─ 采集：LHB_DETAIL + LHB_SEATS
       └─ 数据源：AKShare（东方财富数据通常 16:30 后发布）
       └─ 重试：最多 3 次，每次间隔 10 分钟
       
17:30  K线数据同步
       └─ 采集：DAILY_KLINE（全市场或增量）
       └─ 数据源：TuShare → AKShare → Sina（降级链）
       
18:00  T+N 价格计算
       └─ 依赖：17:30 K线同步完成
       └─ 计算：历史持仓 T+N 收益率
       
18:30  市场情绪计算
       └─ 依赖：17:00 龙虎榜 + 17:30 K线
       └─ 计算：涨停家数、跌停家数、涨跌比等
```

### 14.2 任务依赖关系

```
17:00 sync_lhb ──────┐
                      ├──→ 18:00 calc_tn_prices
17:30 sync_kline ────┤
                      └──→ 18:30 calc_sentiment
```

### 14.3 Celery 任务链配置

```python
# backend/app/tasks/scheduler.py

from celery import chain

# 方案A：固定时间 + 充足间隔（简单）
@celery_app.task
def daily_data_pipeline():
    """每日数据流水线"""
    chain(
        sync_lhb.s(),         # 17:00 执行
        sync_kline.s(),       # 17:30 执行（内部延迟）
        calc_tn_prices.s(),   # 18:00 执行
        calc_sentiment.s(),   # 18:30 执行
    ).apply_async()

# 方案B：任务链（推荐，保证顺序）
@celery_app.task(bind=True)
def sync_lhb(self):
    """采集龙虎榜数据"""
    # ... 采集逻辑 ...
    return {'lhb_count': count}

@celery_app.task(bind=True)
def sync_kline(self, prev_result):
    """采集 K 线数据"""
    # ... 采集逻辑 ...
    return {'kline_count': count, **prev_result}

@celery_app.task(bind=True)
def calc_tn_prices(self, prev_result):
    """计算 T+N 价格"""
    # ... 计算逻辑 ...
    return {'tn_count': count, **prev_result}

@celery_app.task(bind=True)
def calc_sentiment(self, prev_result):
    """计算市场情绪"""
    # ... 计算逻辑 ...
    return {'sentiment': data, **prev_result}

# 触发任务链
chain(
    sync_lhb.s(),
    sync_kline.s(),
    calc_tn_prices.s(),
    calc_sentiment.s()
).apply_async()
```

### 14.4 风险点与应对

| 风险点 | 应对策略 |
|--------|----------|
| 15:30 日结算时 K 线可能未更新 | 延迟到 16:30 执行，或使用收盘快照 |
| 17:00 龙虎榜数据可能延迟发布 | 最多重试 3 次，每次间隔 10 分钟 |
| 18:00 T+N 计算依赖 K 线完成 | 使用任务链保证顺序，前置任务失败则跳过 |
| 非交易日所有采集任务应跳过 | 查询交易日历表判断 |

---

## 十五、异常定义

```python
# backend/app/core/exceptions.py

class ProviderError(AppException):
    """数据源异常基类"""
    code = 5001
    http_status = 503
    message = "数据源请求失败"

class ProviderTimeoutError(ProviderError):
    """数据源超时"""
    code = 5002
    message = "数据源请求超时"

class ProviderRateLimitError(ProviderError):
    """数据源限流"""
    code = 5003
    message = "数据源请求频率超限"

class ProviderResponseError(ProviderError):
    """数据源响应异常"""
    code = 5004
    message = "数据源返回数据格式异常"

class NoProviderAvailableError(ProviderError):
    """所有数据源不可用"""
    code = 5005
    http_status = 503
    message = "所有数据源均不可用"

class DataValidationError(AppException):
    """数据校验异常"""
    code = 6001
    http_status = 422
    message = "数据质量校验失败"
```

---

## 十六、配置汇总表

### 16.1 数据源连接配置

| 配置项 | 环境变量 | 说明 |
|--------|----------|------|
| TuShare Token | TUSHARE_TOKEN | TuShare API Token |
| 东方财富 Token | EASTMONEY_TOKEN | 可选，东方财富 API |

### 16.2 Redis 连接配置

| 配置项 | 环境变量 | 默认值 |
|--------|----------|--------|
| Redis Host | REDIS_HOST | localhost |
| Redis Port | REDIS_PORT | 6379 |
| Redis DB | REDIS_DB | 0 |
| Redis Password | REDIS_PASSWORD | - |

### 16.3 数据采集开关

| 配置项 | 环境变量 | 默认值 |
|--------|----------|--------|
| 启用 TuShare | ENABLE_TUSHARE | true |
| 启用 AKShare | ENABLE_AKSHARE | true |
| 启用 Sina | ENABLE_SINA | true |
| 启用东方财富 | ENABLE_EASTMONEY | false |

---

*文档版本: 2.1*
*最后修订: 2026-04-16*
