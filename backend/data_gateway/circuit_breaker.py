

import time
import logging
from enum import Enum
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


class CircuitState(str, Enum):
    """熔断器状态"""
    CLOSED = "CLOSED"  # 正常(允许请求)
    OPEN = "OPEN"  # 熔断(拒绝请求)
    HALF_OPEN = "HALF_OPEN"  # 半开(允许探测)


@dataclass
class CircuitBreaker:
    """
    熔断器实现

    状态机:
        CLOSED  --失败率>阈值-->  OPEN
        OPEN    --超时后-->       HALF_OPEN
        HALF_OPEN --探测成功-->   CLOSED
        HALF_OPEN --探测失败-->   OPEN

    使用:
        cb = CircuitBreaker(name="tushare_kline")
        if cb.allow_request():
            try:
                result = fetch_data()
                cb.record_success()
            except Exception as e:
                cb.record_failure()
    """

    name: str
    failure_threshold: int = 5  # 连续失败N次后熔断
    reset_timeout: int = 300  # 熔断后等待N秒进入半开(默认5分钟)
    half_open_max_calls: int = 3  # 半开状态最多探测N次
    success_threshold: int = 2  # 半开状态连续成功N次后关闭

    # 内部状态
    state: CircuitState = field(default=CircuitState.CLOSED)
    failure_count: int = field(default=0)
    success_count: int = field(default=0)
    half_open_calls: int = field(default=0)
    last_failure_time: Optional[float] = field(default=None)
    last_success_time: Optional[float] = field(default=None)
    opened_at: Optional[float] = field(default=None)
    last_error: Optional[str] = field(default=None)

    def allow_request(self) -> bool:
        """判断是否允许发起请求"""

        if self.state == CircuitState.CLOSED:
            return True

        if self.state == CircuitState.OPEN:
            # 检查是否超过重置超时，可以进入半开
            if self._should_try_reset():
                self._transition_to(CircuitState.HALF_OPEN)
                self.half_open_calls += 1
                return True
            return False

        if self.state == CircuitState.HALF_OPEN:
            # 半开状态限制探测次数
            if self.half_open_calls < self.half_open_max_calls:
                self.half_open_calls += 1
                return True
            return False

        return False

    def record_success(self):
        """记录请求成功"""
        self.last_success_time = time.time()

        if self.state == CircuitState.HALF_OPEN:
            self.success_count += 1
            if self.success_count >= self.success_threshold:
                self._transition_to(CircuitState.CLOSED)
                logger.info(f"[熔断器] {self.name} 恢复正常 (HALF_OPEN → CLOSED)")
        elif self.state == CircuitState.CLOSED:
            # 成功时重置失败计数
            self.failure_count = 0

    def record_failure(self, error: str = ""):
        """记录请求失败"""
        self.last_failure_time = time.time()
        self.last_error = error

        if self.state == CircuitState.HALF_OPEN:
            # 半开状态失败，立即回到熔断
            self._transition_to(CircuitState.OPEN)
            logger.warning(f"[熔断器] {self.name} 探测失败，重新熔断 (HALF_OPEN → OPEN)")

        elif self.state == CircuitState.CLOSED:
            self.failure_count += 1
            if self.failure_count >= self.failure_threshold:
                self._transition_to(CircuitState.OPEN)
                logger.warning(
                    f"[熔断器] {self.name} 触发熔断! "
                    f"连续失败{self.failure_count}次 (CLOSED → OPEN), "
                    f"将在{self.reset_timeout}秒后尝试恢复"
                )

    def _should_try_reset(self) -> bool:
        """是否应该尝试重置(从OPEN进入HALF_OPEN)"""
        if self.opened_at is None:
            return True
        return (time.time() - self.opened_at) >= self.reset_timeout

    def _transition_to(self, new_state: CircuitState):
        """状态转换"""
        old_state = self.state
        self.state = new_state

        if new_state == CircuitState.CLOSED:
            self.failure_count = 0
            self.success_count = 0
            self.half_open_calls = 0
            self.opened_at = None

        elif new_state == CircuitState.OPEN:
            self.opened_at = time.time()
            self.success_count = 0
            self.half_open_calls = 0

        elif new_state == CircuitState.HALF_OPEN:
            self.success_count = 0
            self.half_open_calls = 0

        logger.debug(f"[熔断器] {self.name}: {old_state} → {new_state}")

    def get_status(self) -> dict:
        """获取熔断器当前状态(用于监控/API展示)"""
        return {
            "name": self.name,
            "state": self.state.value,
            "failure_count": self.failure_count,
            "success_count": self.success_count,
            "failure_threshold": self.failure_threshold,
            "reset_timeout": self.reset_timeout,
            "last_failure_time": self.last_failure_time,
            "last_success_time": self.last_success_time,
            "opened_at": self.opened_at,
            "last_error": self.last_error,
            "time_until_retry": max(
                0,
                self.reset_timeout - (time.time() - self.opened_at)
                if self.opened_at else 0
            ),
        }

    def force_close(self):
        """手动关闭熔断器(运维用)"""
        self._transition_to(CircuitState.CLOSED)
        logger.info(f"[熔断器] {self.name} 手动关闭熔断")

    def force_open(self):
        """手动打开熔断器(运维用)"""
        self._transition_to(CircuitState.OPEN)
        logger.info(f"[熔断器] {self.name} 手动触发熔断")
