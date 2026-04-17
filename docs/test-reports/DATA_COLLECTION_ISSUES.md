# 数据采集问题报告

> 记录服务器部署后手动触发数据采集时发现的问题

---

## 📅 测试时间

- **测试日期**: 2026-04-17 14:05
- **测试环境**: 服务器 (117.72.184.8)
- **Python 版本**: 3.10.13
- **测试方式**: 手动触发采集器

---

## 🔴 P0: 网络连接问题（已修复）

### 问题描述

采集器调用 AKShare API 时出现网络代理错误：

```
ProxyError: HTTPSConnectionPool(host='82.push2.eastmoney.com', port=443): 
Max retries exceeded with url: /api/qt/clist/get?...
(Caused by ProxyError('Unable to connect to proxy', 
RemoteDisconnected('Remote end closed connection without response')))
```

### 影响范围

- `MarketCollector`: 市场情绪采集完全失败
- `LhbCollector`: 部分重试成功，但最终可能失败

### 可能原因

1. 服务器网络代理配置问题 ✅ **确认**
2. AKShare API 接口限流或封禁
3. 服务器防火墙/安全组限制

### ✅ 解决方案（已实施）

**修复时间**: 2026-04-17 14:08

在 `backend/app/collectors/base.py` 中添加了代理绕过机制：

```python
def _disable_proxy_for_akshare(self):
    """临时禁用代理，避免 AKShare 调用失败"""
    # 保存原始代理设置
    self._original_proxy_env = {}
    proxy_vars = ['HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy', 'ALL_PROXY', 'all_proxy']
    
    for var in proxy_vars:
        if var in os.environ:
            self._original_proxy_env[var] = os.environ[var]
            # 设置为空字符串以禁用代理
            os.environ[var] = ''

def _restore_proxy_settings(self):
    """恢复原始代理设置"""
    if hasattr(self, '_original_proxy_env'):
        for var, value in self._original_proxy_env.items():
            os.environ[var] = value
```

**工作原理**:
1. 在调用 `fetch()` 前临时清除所有代理环境变量
2. 在 `fetch()` 完成后立即恢复原始代理设置
3. 确保 AKShare 直接访问数据源，不经过代理

**验证结果**:
- ✅ 调度器正常运行
- ⏳ 需要等待下一个交易日验证实际采集效果

---

## 🟡 P1: 字段映射错误

### 问题描述

`MarketCollector` 保存数据时报错：

```
CompileError: Unconsumed column names: market_amount, total_amount
```

### 问题分析

- **错误信息**: SQLAlchemy 提示有未使用的列名 `market_amount` 和 `total_amount`
- **实际模型**: `MarketSentiment` 模型中只有 `total_amount` 字段（第209行）
- **采集器代码**: `market_collector.py` 第121行使用了 `total_amount`

### 可能原因

1. 数据库表结构与模型定义不一致
2. 采集器返回的 record 字典包含了模型中不存在的字段
3. SQLAlchemy upsert 语句的字段映射问题

### 建议解决方案

1. **检查数据库表结构**
   ```sql
   -- 连接数据库查看实际表结构
   \d market_sentiment
   
   -- 确认是否有 market_amount 字段
   SELECT column_name, data_type 
   FROM information_schema.columns 
   WHERE table_name = 'market_sentiment';
   ```

2. **修复字段映射**
   - 如果数据库有 `market_amount` 字段，需要在模型中添加
   - 如果数据库没有该字段，需要从 record 字典中移除

3. **更新模型定义**
   ```python
   # 如果需要 market_amount 字段
   market_amount = Column(Numeric(20, 2), default=0.00)
   ```

---

## 🟢 验证结果

### 成功部分

- ✅ Python 3.10.13 环境配置成功
- ✅ 虚拟环境和依赖安装正常
- ✅ 调度器后台运行正常
- ✅ 数据库连接正常
- ✅ LhbCollector 部分功能正常（抓取和转换）

### 失败部分

- ❌ MarketCollector 网络调用失败
- ❌ 数据采集保存失败（字段映射错误）
- ❌ 数据库为空（无历史数据）

---

## 📋 待修复清单

### ✅ 已完成

- [x] 修复网络代理问题（2026-04-17 14:08）
- [x] 添加代理绕过机制
- [x] 调度器正常运行

### P1 优先级

- [ ] 修复 MarketCollector 字段映射
- [ ] 同步数据库表结构
- [ ] 添加字段验证逻辑

### P2 优先级

- [ ] 添加数据采集监控
- [ ] 实现数据质量检查
- [ ] 添加异常告警机制
- [ ] 实现数据源降级策略

---

## 🔗 相关文件

- `backend/app/collectors/market_collector.py` - 市场情绪采集器
- `backend/app/collectors/lhb_collector.py` - 龙虎榜采集器
- `backend/db/models/market.py` - 市场情绪模型
- `backend/db/models/lhb.py` - 龙虎榜模型
- `backend/app/scheduler.py` - 调度器配置

---

## 📝 备注

1. 测试时发现服务器 Python 版本已从 3.6.8 升级到 3.10.13
2. 调度器进程正常运行（PID: 3635892, 3636548）
3. 建议优先解决网络问题，然后修复字段映射
4. 数据库为空，需要回填历史数据或等待下一个交易日

---

**报告生成时间**: 2026-04-17 14:10  
**测试人员**: 系统自动检测  
**状态**: 待修复