# 代码审查与修复记录

> 日期：2026-04-17
> 范围：`backend/data_gateway/` 全部 + `tests/` + 配置文件
> 审查人：Claude

---

## 1. 严重 bug (已修复)

### 1.1 🔴 `base_adapter.py` 缩进错误导致三个类方法不存在

**症状**：`get_method_for_category` / `supports` / `__repr__` 被 12 空格缩进嵌套在 `_safe_request` 的 for 循环体内，Python 在每次循环迭代中**重新定义**这些局部函数，但它们从未成为类的属性。

```python
# 修复前
def _safe_request(...):
    for attempt in range(...):
        try: ...
        except: ...
        return None                   # ← 写在 for 循环里，永远跑不到第二次重试

        def get_method_for_category   # ← 12 空格缩进，嵌套在循环内的局部函数
        def supports                  # ← 同上
        def __repr__                  # ← 同上
```

**影响**：
- `adapter.supports(cat)` / `adapter.get_method_for_category(cat)` 直接 `AttributeError`。
- `_safe_request` 的重试逻辑从不生效，第一次异常后直接返回 None。

**修复**：`base_adapter.py` 整体重写，抽出 `_CATEGORY_METHOD_MAP` 作为类属性，`supports()` 基于 "子类是否覆写了对应方法" 判断。重试循环 `return None` 移至循环外。

---

### 1.2 🔴 `sina_adapter.py` 缩进错误导致整个类是空壳

**症状**：`_do_initialize` / `health_check` / `get_realtime_quote` / `get_daily_kline` / `get_lhb_detail` / 所有 `_*` 工具方法**全部用 8 空格缩进**，实际都是 `__init__` 的局部函数，不在类作用域。

```python
# 修复前
class SinaAdapter(BaseDataAdapter):
    def __init__(self, config=None):
        super().__init__(...)
        # backend/data_gateway/adapters/sina_adapter.py (续)

        def _do_initialize(self):     # ← 局部函数,非类方法
            ...
        def health_check(self):       # ← 局部函数
            ...
```

**影响**：`SinaAdapter()` 能实例化但 `adapter.initialize()` 会因抽象方法 `_do_initialize` 未实现抛 `TypeError: Can't instantiate abstract class`；健康检查、所有数据获取方法全部 `AttributeError`。

**修复**：整份文件重写，所有方法提升到类作用域；同时在 `_build_session()` 中显式 `trust_env=False` + `proxies={}` 绕过 SG 代理。

---

### 1.3 🟡 `CircuitBreaker.allow_request` 在 OPEN→HALF_OPEN 转换时丢失首次计数

**症状**：
```python
if self.state == CircuitState.OPEN:
    if self._should_try_reset():
        self._transition_to(CircuitState.HALF_OPEN)
        return True    # ← 直接返回，half_open_calls 没 +1
```

**影响**：半开探测实际允许 `half_open_max_calls + 1` 次，`test_half_open_resets_counters` / `test_half_open_allows_limited_calls` 用例失败。

**修复**：转换后立即 `self.half_open_calls += 1`。

---

## 2. 一致性 / 反模式 (已修复)

### 2.1 Router 直接访问 `adapter._initialized`
改为通过 `adapter.initialized` 属性（基类已暴露）。

### 2.2 `CrawlerAdapter` 未绕过代理
东方财富接口位于境内，但 `requests.Session()` 默认读 `HTTP_PROXY` 环境变量。已补 `trust_env=False` + `proxies={}`。

### 2.3 默认优先级链与 v2 方案不一致
TuShare 120 积分只能日 K，但原 `DEFAULT_PRIORITY_CHAINS` 中 `FINANCIAL_DATA` / `STOCK_INFO` / `TRADE_CALENDAR` / `INDEX_DAILY` 仍把 TuShare 排在首位。运行时必然触发失败再降级到 AKShare，浪费一次请求。已调整为 AKShare 主链。

---

## 3. 待修 (W1-W3 跟进)

### 3.1 🟡 启动代理清理未接入入口
`backend/core/startup.py` 已提供 `bootstrap()`，但目前没有 API / Celery / scheduler 入口脚本去调用它。等 W1 建好 FastAPI 入口后，需在 `main.py` 顶部 `from backend.core.startup import bootstrap; bootstrap()`。

### 3.2 🟡 Router 未显式调用 CB.allow_request
当前依赖 adapter 方法内 `_safe_request` 做熔断。若未来某 adapter 方法未走 `_safe_request`，Router 不会拦截。建议 W2 在 Router.fetch 循环加一层：
```python
if not adapter.circuit_breaker.allow_request():
    continue
```

### 3.3 🟡 `docker-compose.postgres.yml` 硬编码凭证
```yaml
POSTGRES_USER: lhb_admin
POSTGRES_PASSWORD: lhb_quant_2024  # 硬编码
```
应改为 `${POSTGRES_USER}` / `${POSTGRES_PASSWORD}` 从 `.env` 读取。W1 处理。

### 3.4 🟡 `.env` 与 `.env.example` 字段不一致
- `.env.example` 有 `REDIS_*` / `BACKEND_*` / `SECRET_KEY` / `FEISHU_WEBHOOK_URL`，实际 `.env` 没有。
- `.env` 有 `DINGTALK_WEBHOOK` / `EMAIL_*`，`.env.example` 没有。
- `.env.example` 用 `POSTGRES_PASSWORD=your_secure_password_here`，`.env` 是真实密码。

已重写 `.env.example` 与 `.env` 保持 1:1，并补充 `PG_HOST=localhost` 的生产默认值。

### 3.5 🟡 `.env` 明文包含生产 TuShare token
`.gitignore` 已排除 `.env`，但 token 泄露风险仍存在（git reflog / 本地备份 / 意外 `git add -f`）。建议：
- 短期：检查 `git log --all -p -- .env` 是否泄露过；必要时 rotate token。
- 长期：引入 [`direnv`](https://direnv.net/) 或 1Password / Vault 管理敏感凭证，`.env` 只存占位。

### 3.6 🟡 `akshare_adapter.get_lhb_seats` 逻辑问题
函数里同时调用 `stock_lhb_stock_detail_em(symbol="近一月")` (返回近一月数据，不是当日！) 和遍历当日每只个股的 `stock_lhb_stock_statistic_em`。前者返回的数据语义与"当日席位"不符，应该删除或替换为正确的 API (如 `stock_lhb_hyyyb_em` 机构专用席位 / `stock_lhb_yybph_em` 营业部排行)。W4 龙虎榜入库时重新梳理。

### 3.7 🟢 测试中的硬编码日期 `20240115` 可能失效
多个 `tests/test_data_fetch.py` 用例假设 `20240115` 有龙虎榜数据。一旦 AKShare 缓存策略变化会 flaky。W3 改为动态拉最近 5 个交易日中有数据的那天。

### 3.8 🟢 `tests/conftest.py` 手写 `.env` 解析
已有 `python-dotenv` 更稳健。W1 引入 `pyproject.toml` 后切换。

### 3.9 🟢 `data_sources/` 目录与 `backend/data_gateway/` 功能重叠
`data_sources/adapters/` 与 `backend/data_gateway/adapters/` 并存。前者应是旧代码，确认后删除（用户说"之前的代码全部删除"，但这个目录还在）。

### 3.10 🟢 `backend/__init__.py` 为空字符串文件
正常；但 `backups/` / `pids/` 两个空目录应加到 `.gitignore`。

---

## 4. 补齐缺口 (v2 方案要求，但代码还没做)

| 模块 | 位置 | 状态 |
|---|---|---|
| `proxy_policy.py` / `startup.py` | `backend/core/startup.py` | ✅ 已建 |
| `PriorityConfig` | `backend/data_gateway/priority_config.py` | ⬜ W2 |
| `CacheManager` | `backend/data_gateway/cache_manager.py` | ⬜ W2 |
| `HealthChecker` | `backend/data_gateway/health_checker.py` | ⬜ W3 |
| `data_fetch_log` / `adapter_health` / `data_source_config` 三张表 | Alembic migration | ⬜ W1 |
| TuShare adapter 收窄为只 DAILY_KLINE | `tushare_adapter.py` | ⬜ W2 (加 `_supported_categories` 限制) |
| Router 补 CB.allow_request 显式检查 | `router.py` | ⬜ W2 |

---

## 5. 验证

```bash
$ pytest tests/test_circuit_breaker.py -q
47 passed in 3.38s
```

```bash
$ python -c "from backend.data_gateway.adapters import SinaAdapter; \
             print(hasattr(SinaAdapter,'get_realtime_quote'), \
                   hasattr(SinaAdapter,'_do_initialize'))"
True True
```
