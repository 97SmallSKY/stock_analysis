"""
AKShare 请求工具 - 带自动重试机制 + 代理感知
==============================================
解决「当前系统繁忙，请稍后再试」等服务端限流问题

AKShare 的限流错误有多种形式：
  1. Exception("当前系统繁忙，请稍后再试。")          ← 最常见
  2. JSONDecodeError（接口返回非 JSON 的错误页面）
  3. requests.exceptions.ConnectionError / Timeout
  4. HTTP 429 Too Many Requests

代理环境说明（京东云 VPS）：
  - VPS 配置了全局代理：HTTP_PROXY/HTTPS_PROXY=http://127.0.0.1:10809（新加坡中转）
  - ALL_PROXY=socks5://127.0.0.1:10808
  - AKShare 内部通过 requests 发请求，默认会走系统代理
  - 测试表明：通过代理访问新浪/东财行情接口均 < 0.5s，代理对性能无明显影响
  - 唯一风险：本地代理进程（10809/10808）挂掉时，所有 AKShare 接口全部失败

  代理降级保护（fetch_sina_quote 函数）：
    1. 先尝试通过代理（默认行为，大多数情况稳定）
    2. 若超时或连接错误，自动切换到直连（ProxyHandler={}）再试一次
    这样在代理异常时也能保证新浪实时行情可用。
"""
import time
import random
import functools
import os
import urllib.request
from typing import Callable, Any, Optional
from loguru import logger


# ============================================================
# 触发重试的关键字（大小写不敏感）
# ============================================================
RETRY_KEYWORDS = [
    # AKShare 中文限流提示
    "当前系统繁忙",
    "系统繁忙",
    "请稍后再试",
    "服务器繁忙",
    "访问过于频繁",
    "请求过于频繁",
    "接口异常",
    # HTTP / 网络层
    "too many requests",
    "rate limit",
    "429",
    "503",
    "502",
    "timeout",
    "timed out",
    "connection",
    "remotedisconnected",
    "connectionerror",
    "httperror",
    "readtimeout",
    "chunkedencodingerror",
    "connection aborted",
    "remote end closed",
    # JSON 解析失败（接口返回 HTML 错误页）
    "jsondecode",
    "expecting value",
    "invalid json",
    # 代理连接失败
    "proxy",
    "socks",
    "10809",
    "10808",
]


def is_retryable_error(e: Exception) -> bool:
    """判断该异常是否应触发重试"""
    msg = str(e).lower()
    for kw in RETRY_KEYWORDS:
        if kw.lower() in msg:
            return True
    # 特殊处理：AKShare 有时直接 raise ValueError("当前系统繁忙")
    if type(e).__name__ in ("ValueError", "RuntimeError", "Exception"):
        raw = str(e)
        if any(kw in raw for kw in ["繁忙", "频繁", "稍后", "异常"]):
            return True
    return False


# ============================================================
# 新浪实时行情：代理感知版本
# ============================================================

def fetch_sina_quote(code: str, timeout: int = 8) -> dict | None:
    """
    获取新浪实时行情（代理感知，双路保障）

    策略：
      1. 先通过系统代理请求（默认行为，通常 < 0.5s）
      2. 若连接失败或超时，自动切换到直连（ProxyHandler={}）
      3. 两种方式都失败才返回 None

    之所以不直接绕过代理：在代理正常运行时，通过代理的速度实际上
    比直连更快（代理已优化路由），不应无条件绕过。

    Args:
        code:    6位股票代码（如 "603601"）
        timeout: 单次请求超时秒数

    Returns:
        dict(name, open, prev_close, high, low, last, bid1, ask1, volume, amount) 或 None

    注意：
        涨停封板时 last 是最后一笔实际成交价（可能是低开时的价格），
        真实当前市价应使用 bid1（买1挂单价）。
        推荐使用 fetch_realtime_quote() 自动处理涨停/跌停场景。
    """
    prefix = "sh" if code.startswith(("6", "9")) else "sz"
    url = f"http://hq.sinajs.cn/list={prefix}{code}"
    headers = {"Referer": "https://finance.sina.com.cn"}

    def _parse_response(raw_bytes: bytes) -> dict | None:
        raw = raw_bytes.decode("gb2312", errors="replace")
        content = raw.split('"')[1] if '"' in raw else ""
        parts = content.split(",")
        if len(parts) < 10:
            return None
        try:
            # ⚠️ 新浪 API 字段顺序（经实测验证）：
            # parts[0] = 股票名称
            # parts[1] = 今日开盘价
            # parts[2] = 昨日收盘价
            # parts[3] = 当前价（最新成交价 / 实时价格）← 不是 high！
            # parts[4] = 今日最高价                   ← 不是 low！
            # parts[5] = 今日最低价                   ← 不是 last！
            # parts[6] = 买1价（bid1）
            # parts[7] = 卖1价（ask1）
            # parts[8] = 成交量（手）
            # parts[9] = 成交额（元）
            # parts[10]~[19] = 买1~买5 手数/价格
            # parts[20]~[29] = 卖1~卖5 手数/价格
            return {
                "name":       parts[0],
                "open":       float(parts[1]),
                "prev_close": float(parts[2]),
                "last":       float(parts[3]),               # 当前价（实时最新成交价）
                "high":       float(parts[4]),               # 今日最高价
                "low":        float(parts[5]),               # 今日最低价
                "bid1":       float(parts[6]) if parts[6] else 0.0,   # 买1价（涨停时=涨停价）
                "ask1":       float(parts[7]) if parts[7] else 0.0,   # 卖1价（跌停时=跌停价）
                "volume":     int(float(parts[8])) if parts[8] else 0, # 成交量（手）
                "amount":     float(parts[9]) if parts[9] else 0.0,   # 成交额（元）
            }
        except (ValueError, IndexError):
            return None

    # ── 方式 1：走系统代理（VPS 默认，通常稳定）────────────────────
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            result = _parse_response(resp.read())
        if result and result["last"] > 0:
            return result
        # last=0 可能是停牌或数据异常，但若 bid1>0 也算有效（涨停特殊状态）
        if result and result.get("bid1", 0) > 0:
            return result
    except Exception as e:
        logger.warning(f"fetch_sina_quote [{code}] 系统代理失败，尝试直连: {e}")

    # ── 方式 2：直连（绕过代理，代理挂了时的保障）──────────────────
    try:
        direct_opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({})  # 空字典 = 完全不使用代理
        )
        req2 = urllib.request.Request(url, headers=headers)
        with direct_opener.open(req2, timeout=timeout) as resp:
            result2 = _parse_response(resp.read())
        if result2 and (result2["last"] > 0 or result2.get("bid1", 0) > 0):
            logger.info(f"fetch_sina_quote [{code}] 直连成功（代理降级）")
            return result2
    except Exception as e2:
        logger.warning(f"fetch_sina_quote [{code}] 直连也失败: {e2}")

    return None


def _calc_effective_price(quote: dict) -> float:
    """
    从行情 dict 计算有效当前价格（处理涨停/跌停及新浪 last 字段滞留问题）

    新浪行情在以下场景 last 字段会失真：
      1. 涨停封板：last 是最后一笔实际成交价（可能是低开那笔），需用 bid1 代替。
      2. last 字段滞留：在某些非涨停正常行情中，last 可能滞留在开盘初期的低价，
         而 bid1/ask1 反映了真实的当前市价。

    判断逻辑（优先级由高到低）：
      1. 涨停封板：ask1==0（无卖单）且 bid1>0 且 bid1>last → 用 bid1
      2. 跌停封板：bid1==0（无买单）且 ask1>0 且 ask1<last → 用 ask1
      3. last 偏低异常：bid1>0 且 ask1>0（正常行情），但 last 比 bid1 低超过 3%
         → 用 (bid1+ask1)/2（买卖均价），避免 last 字段滞留导致误判
      4. 正常行情：last > 0 → 用 last
      5. 兜底：bid1 → ask1 → high → 0

    Returns:
        有效当前价格（float）
    """
    last = quote.get("last", 0.0)
    bid1 = quote.get("bid1", 0.0)
    ask1 = quote.get("ask1", 0.0)
    high = quote.get("high", 0.0)

    # 涨停封板：ask1==0（无人卖）且 bid1>0 且 bid1 显著高于 last
    if ask1 == 0.0 and bid1 > 0.0 and bid1 > last:
        return bid1

    # 跌停封板：bid1==0（无人买）且 ask1>0 且 ask1 显著低于 last
    if bid1 == 0.0 and ask1 > 0.0 and ask1 < last:
        return ask1

    # 新浪 last 字段滞留检测：bid1 和 ask1 都有效，但 last 比 bid1 低超过 3%
    # 这通常发生在股票低开后反弹，last 未能实时更新的情况
    if bid1 > 0.0 and ask1 > 0.0 and last > 0.0:
        mid_price = (bid1 + ask1) / 2.0
        if last < mid_price * 0.97:  # last 比买卖均价低超过 3%，判定为滞留
            return round(mid_price, 3)

    # 正常行情：用 last；last 为 0 时用 bid1 或 high 兜底
    if last > 0.0:
        return last
    return bid1 or ask1 or high or 0.0


def fetch_realtime_quote(code: str, timeout: int = 8) -> dict | None:
    """
    获取实时行情，自动处理涨停/跌停特殊场景。

    在 fetch_sina_quote 基础上增加 effective_price 字段：
      - 涨停封板：effective_price = bid1（买1挂单价 = 涨停价）
      - 跌停封板：effective_price = ask1（卖1挂单价 = 跌停价）
      - 正常行情：effective_price = last（最新成交价）

    监控模块应使用 effective_price 而非 last 来判断触发条件，
    以确保涨停板场景下能正确触发卖出。

    Args:
        code:    6位股票代码（如 "002361"）
        timeout: 单次请求超时秒数

    Returns:
        dict(name, open, prev_close, high, low, last, bid1, ask1,
             volume, amount, effective_price) 或 None
    """
    quote = fetch_sina_quote(code, timeout=timeout)
    if quote is None:
        return None
    quote["effective_price"] = _calc_effective_price(quote)
    return quote


def is_retryable_error(e: Exception) -> bool:
    """判断该异常是否应触发重试"""
    msg = str(e).lower()
    for kw in RETRY_KEYWORDS:
        if kw.lower() in msg:
            return True
    # 特殊处理：AKShare 有时直接 raise ValueError("当前系统繁忙")
    if type(e).__name__ in ("ValueError", "RuntimeError", "Exception"):
        raw = str(e)
        if any(kw in raw for kw in ["繁忙", "频繁", "稍后", "异常"]):
            return True
    return False


def retry_on_busy(
    max_retries: int = 5,
    base_delay: float = 3.0,
    backoff: float = 2.0,
    max_delay: float = 60.0,
    jitter: float = 1.0,
):
    """
    装饰器：在遇到「系统繁忙」等错误时自动重试

    Args:
        max_retries: 最大重试次数（默认 5）
        base_delay: 初始等待秒数（默认 3）
        backoff: 指数退避系数（默认 2x）
        max_delay: 最大等待上限，防止无限等（默认 60s）
        jitter: 随机抖动秒数，避免惊群（默认 1s）

    Example:
        @retry_on_busy(max_retries=3, base_delay=3.0)
        def fetch_data():
            return ak.stock_lhb_detail_em(...)
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            delay = base_delay
            last_exc = None

            for attempt in range(max_retries + 1):
                try:
                    result = func(*args, **kwargs)
                    if attempt > 0:
                        logger.info(f"✅ {func.__name__} 重试成功（第 {attempt} 次）")
                    return result

                except Exception as e:
                    last_exc = e

                    if attempt >= max_retries:
                        logger.error(
                            f"❌ {func.__name__} 已重试 {max_retries} 次，最终失败: {e}"
                        )
                        raise

                    if is_retryable_error(e):
                        wait = min(delay + random.uniform(0, jitter), max_delay)
                        logger.warning(
                            f"⏳ [{func.__name__}] 服务繁忙 "
                            f"[{attempt + 1}/{max_retries}]，"
                            f"{wait:.1f}s 后重试 | {type(e).__name__}: {e}"
                        )
                        time.sleep(wait)
                        delay = min(delay * backoff, max_delay)
                    else:
                        # 非限流错误直接抛出，不重试
                        raise

            raise last_exc

        return wrapper
    return decorator


def safe_akshare_call(
    func: Callable,
    *args,
    max_retries: int = 5,
    base_delay: float = 3.0,
    backoff: float = 2.0,
    max_delay: float = 60.0,
    fallback: Any = None,
    **kwargs,
) -> Any:
    """
    安全调用 AKShare 接口，内置重试与降级

    遇到「当前系统繁忙」等限流错误时自动等待重试；
    若重试全部失败，返回 fallback 值（默认 None），不抛出异常。

    Args:
        func: AKShare 函数
        *args: 位置参数
        max_retries: 最大重试次数
        base_delay: 初始等待秒数
        backoff: 指数退避系数
        max_delay: 单次最大等待秒数
        fallback: 全部失败时的降级返回值
        **kwargs: 关键字参数

    Example:
        df = safe_akshare_call(
            ak.stock_lhb_detail_em,
            start_date="20240101",
            end_date="20240101",
        )
    """
    delay = base_delay

    for attempt in range(max_retries + 1):
        try:
            result = func(*args, **kwargs)
            if attempt > 0:
                logger.info(f"✅ {func.__name__} 第 {attempt} 次重试成功")
            return result

        except Exception as e:
            if attempt >= max_retries:
                logger.error(
                    f"❌ {func.__name__} 失败（已重试 {max_retries} 次），"
                    f"返回 fallback={fallback!r} | {e}"
                )
                return fallback

            if is_retryable_error(e):
                wait = min(delay + random.uniform(0, 1.0), max_delay)
                logger.warning(
                    f"⏳ [{func.__name__}] 服务繁忙 "
                    f"[{attempt + 1}/{max_retries}]，"
                    f"{wait:.1f}s 后重试 | {type(e).__name__}: {e}"
                )
                time.sleep(wait)
                delay = min(delay * backoff, max_delay)
            else:
                # 非限流错误：记录后直接返回 fallback，不重试
                logger.debug(f"⚠️ [{func.__name__}] 非重试错误: {type(e).__name__}: {e}")
                return fallback

    return fallback
