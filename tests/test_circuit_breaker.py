
"""
熔断器单元测试(纯内存测试，不需要数据库)

状态机:
    CLOSED  --失败率>阈值-->  OPEN
    OPEN    --超时后-->       HALF_OPEN
    HALF_OPEN --探测成功-->   CLOSED
    HALF_OPEN --探测失败-->   OPEN
"""

import time
import pytest
import sys
from pathlib import Path

# 添加项目根目录到path
sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.data_gateway.circuit_breaker import CircuitBreaker, CircuitState


# ================================================================
# 1. 初始状态
# ================================================================

class TestCircuitBreakerInit:
    """初始状态验证"""

    def test_default_state_is_closed(self):
        cb = CircuitBreaker(name="test")
        assert cb.state == CircuitState.CLOSED

    def test_default_values(self):
        cb = CircuitBreaker(name="test")
        assert cb.failure_count == 0
        assert cb.success_count == 0
        assert cb.last_failure_time is None
        assert cb.last_success_time is None
        assert cb.opened_at is None

    def test_custom_config(self):
        cb = CircuitBreaker(
            name="custom",
            failure_threshold=10,
            reset_timeout=600,
            half_open_max_calls=5,
            success_threshold=3,
        )
        assert cb.failure_threshold == 10
        assert cb.reset_timeout == 600
        assert cb.half_open_max_calls == 5
        assert cb.success_threshold == 3

    def test_allow_request_when_closed(self):
        cb = CircuitBreaker(name="test")
        assert cb.allow_request() is True


# ================================================================
# 2. CLOSED 状态
# ================================================================

class TestClosedState:
    """CLOSED 状态下的行为"""

    def test_success_resets_failure_count(self):
        cb = CircuitBreaker(name="test", failure_threshold=5)
        # 先累积几次失败
        cb.record_failure("error1")
        cb.record_failure("error2")
        assert cb.failure_count == 2

        # 成功一次重置
        cb.record_success()
        assert cb.failure_count == 0
        assert cb.state == CircuitState.CLOSED

    def test_failure_increments_count(self):
        cb = CircuitBreaker(name="test", failure_threshold=5)
        cb.record_failure("error")
        assert cb.failure_count == 1
        assert cb.state == CircuitState.CLOSED

    def test_failure_records_error(self):
        cb = CircuitBreaker(name="test")
        cb.record_failure("connection timeout")
        assert cb.last_error == "connection timeout"
        assert cb.last_failure_time is not None

    def test_success_records_time(self):
        cb = CircuitBreaker(name="test")
        cb.record_success()
        assert cb.last_success_time is not None

    def test_multiple_successes_stay_closed(self):
        cb = CircuitBreaker(name="test")
        for _ in range(100):
            cb.record_success()
        assert cb.state == CircuitState.CLOSED
        assert cb.failure_count == 0


# ================================================================
# 3. CLOSED → OPEN 转换
# ================================================================

class TestClosedToOpen:
    """达到失败阈值触发熔断"""

    def test_triggers_at_threshold(self):
        cb = CircuitBreaker(name="test", failure_threshold=3)

        cb.record_failure("err1")
        assert cb.state == CircuitState.CLOSED

        cb.record_failure("err2")
        assert cb.state == CircuitState.CLOSED

        cb.record_failure("err3")  # 第3次触发
        assert cb.state == CircuitState.OPEN

    def test_does_not_trigger_below_threshold(self):
        cb = CircuitBreaker(name="test", failure_threshold=5)
        for i in range(4):
            cb.record_failure(f"err{i}")
        assert cb.state == CircuitState.CLOSED

    def test_open_blocks_requests(self):
        cb = CircuitBreaker(name="test", failure_threshold=2, reset_timeout=300)
        cb.record_failure("err1")
        cb.record_failure("err2")

        assert cb.state == CircuitState.OPEN
        assert cb.allow_request() is False

    def test_open_records_opened_at(self):
        cb = CircuitBreaker(name="test", failure_threshold=1)
        cb.record_failure("err")
        assert cb.opened_at is not None

    def test_failure_count_at_exact_threshold(self):
        cb = CircuitBreaker(name="test", failure_threshold=5)
        for i in range(5):
            cb.record_failure(f"err{i}")
        assert cb.state == CircuitState.OPEN
        assert cb.failure_count == 5

    def test_interleaved_success_resets_count(self):
        """中间穿插成功会重置失败计数"""
        cb = CircuitBreaker(name="test", failure_threshold=3)
        cb.record_failure("err1")
        cb.record_failure("err2")
        cb.record_success()  # 重置
        cb.record_failure("err3")
        cb.record_failure("err4")
        # 只连续失败了2次，不会触发
        assert cb.state == CircuitState.CLOSED
        assert cb.failure_count == 2


# ================================================================
# 4. OPEN → HALF_OPEN 转换
# ================================================================

class TestOpenToHalfOpen:
    """超时后从OPEN进入HALF_OPEN"""

    def test_stays_open_before_timeout(self):
        cb = CircuitBreaker(name="test", failure_threshold=1, reset_timeout=300)
        cb.record_failure("err")
        assert cb.state == CircuitState.OPEN
        assert cb.allow_request() is False

    def test_transitions_after_timeout(self):
        cb = CircuitBreaker(name="test", failure_threshold=1, reset_timeout=1)
        cb.record_failure("err")
        assert cb.state == CircuitState.OPEN

        # 等待超过reset_timeout
        time.sleep(1.1)

        assert cb.allow_request() is True
        assert cb.state == CircuitState.HALF_OPEN

    def test_half_open_allows_limited_calls(self):
        cb = CircuitBreaker(
            name="test",
            failure_threshold=1,
            reset_timeout=0,  # 立即进入半开
            half_open_max_calls=2,
        )
        cb.record_failure("err")

        # 进入HALF_OPEN
        assert cb.allow_request() is True  # 第1次探测
        assert cb.allow_request() is True  # 第2次探测
        assert cb.allow_request() is False  # 超过限制

    def test_half_open_resets_counters(self):
        cb = CircuitBreaker(name="test", failure_threshold=1, reset_timeout=0)
        cb.record_failure("err")

        cb.allow_request()  # 触发 OPEN → HALF_OPEN
        assert cb.success_count == 0
        assert cb.half_open_calls == 1


# ================================================================
# 5. HALF_OPEN → CLOSED (探测成功)
# ================================================================

class TestHalfOpenToClosed:
    """半开状态探测成功后恢复"""

    def test_recovers_after_success_threshold(self):
        cb = CircuitBreaker(
            name="test",
            failure_threshold=1,
            reset_timeout=0,
            success_threshold=2,
        )
        cb.record_failure("err")

        cb.allow_request()  # → HALF_OPEN
        assert cb.state == CircuitState.HALF_OPEN

        cb.record_success()  # 第1次成功
        assert cb.state == CircuitState.HALF_OPEN

        cb.record_success()  # 第2次成功 → CLOSED
        assert cb.state == CircuitState.CLOSED

    def test_closed_resets_all_counters(self):
        cb = CircuitBreaker(
            name="test",
            failure_threshold=1,
            reset_timeout=0,
            success_threshold=1,
        )
        cb.record_failure("err")
        cb.allow_request()  # → HALF_OPEN
        cb.record_success()  # → CLOSED

        assert cb.failure_count == 0
        assert cb.success_count == 0
        assert cb.half_open_calls == 0
        assert cb.opened_at is None

    def test_fully_functional_after_recovery(self):
        """恢复后完全正常工作"""
        cb = CircuitBreaker(
            name="test",
            failure_threshold=2,
            reset_timeout=0,
            success_threshold=1,
        )
        # 熔断
        cb.record_failure("err1")
        cb.record_failure("err2")
        assert cb.state == CircuitState.OPEN

        # 恢复
        cb.allow_request()
        cb.record_success()
        assert cb.state == CircuitState.CLOSED

        # 恢复后可以正常请求
        assert cb.allow_request() is True
        cb.record_success()
        assert cb.state == CircuitState.CLOSED


# ================================================================
# 6. HALF_OPEN → OPEN (探测失败)
# ================================================================

class TestHalfOpenToOpen:
    """半开状态探测失败，回到OPEN"""

    def test_failure_in_half_open_reopens(self):
        cb = CircuitBreaker(
            name="test",
            failure_threshold=1,
            reset_timeout=0,
        )
        cb.record_failure("err1")
        cb.allow_request()  # → HALF_OPEN
        assert cb.state == CircuitState.HALF_OPEN

        cb.record_failure("err2")  # → OPEN
        assert cb.state == CircuitState.OPEN

    def test_reopened_blocks_requests(self):
        cb = CircuitBreaker(
            name="test",
            failure_threshold=1,
            reset_timeout=300,  # 长超时
        )
        cb.record_failure("err1")

        # 手动设置opened_at为过去，模拟超时
        cb.opened_at = time.time() - 301
        cb.allow_request()  # → HALF_OPEN

        cb.record_failure("err2")  # → OPEN
        assert cb.state == CircuitState.OPEN
        assert cb.allow_request() is False  # 又被阻止


# ================================================================
# 7. 完整状态机循环
# ================================================================

class TestFullCycle:
    """完整的 CLOSED → OPEN → HALF_OPEN → CLOSED/OPEN 循环"""

    def test_full_recovery_cycle(self):
        cb = CircuitBreaker(
            name="full_cycle",
            failure_threshold=3,
            reset_timeout=0,
            success_threshold=2,
            half_open_max_calls=3,
        )

        # 阶段1: CLOSED
        assert cb.state == CircuitState.CLOSED
        cb.record_success()
        cb.record_success()
        assert cb.state == CircuitState.CLOSED

        # 阶段2: 触发熔断 → OPEN
        cb.record_failure("err1")
        cb.record_failure("err2")
        cb.record_failure("err3")
        assert cb.state == CircuitState.OPEN

        # 阶段3: 超时后 → HALF_OPEN
        cb.allow_request()
        assert cb.state == CircuitState.HALF_OPEN

        # 阶段4: 探测成功 → CLOSED
        cb.record_success()
        cb.record_success()
        assert cb.state == CircuitState.CLOSED

        # 阶段5: 恢复后再次正常使用
        assert cb.allow_request() is True
        cb.record_success()

    def test_full_failure_cycle(self):
        cb = CircuitBreaker(
            name="fail_cycle",
            failure_threshold=2,
            reset_timeout=0,
            success_threshold=2,
        )

        # CLOSED → OPEN
        cb.record_failure("err1")
        cb.record_failure("err2")
        assert cb.state == CircuitState.OPEN

        # OPEN → HALF_OPEN
        cb.allow_request()
        assert cb.state == CircuitState.HALF_OPEN

        # HALF_OPEN → OPEN (探测失败)
        cb.record_failure("err3")
        assert cb.state == CircuitState.OPEN

        # 再次 OPEN → HALF_OPEN → CLOSED
        cb.allow_request()
        cb.record_success()
        cb.record_success()
        assert cb.state == CircuitState.CLOSED

    def test_multiple_cycles(self):
        """多次熔断恢复循环"""
        cb = CircuitBreaker(
            name="multi",
            failure_threshold=1,
            reset_timeout=0,
            success_threshold=1,
        )

        for i in range(5):
            # 熔断
            cb.record_failure(f"cycle{i}")
            assert cb.state == CircuitState.OPEN

            # 恢复
            cb.allow_request()
            cb.record_success()
            assert cb.state == CircuitState.CLOSED


# ================================================================
# 8. 手动控制
# ================================================================

class TestManualControl:
    """手动强制开关熔断器"""

    def test_force_open(self):
        cb = CircuitBreaker(name="test")
        assert cb.state == CircuitState.CLOSED

        cb.force_open()
        assert cb.state == CircuitState.OPEN
        assert cb.allow_request() is False

    def test_force_close(self):
        cb = CircuitBreaker(name="test", failure_threshold=1)
        cb.record_failure("err")
        assert cb.state == CircuitState.OPEN

        cb.force_close()
        assert cb.state == CircuitState.CLOSED
        assert cb.allow_request() is True
        assert cb.failure_count == 0

    def test_force_close_resets_everything(self):
        cb = CircuitBreaker(name="test", failure_threshold=1)
        cb.record_failure("err")
        cb.force_close()

        assert cb.failure_count == 0
        assert cb.success_count == 0
        assert cb.half_open_calls == 0
        assert cb.opened_at is None


# ================================================================
# 9. get_status API
# ================================================================

class TestGetStatus:
    """状态查询API"""

    def test_status_when_closed(self):
        cb = CircuitBreaker(name="test_api")
        status = cb.get_status()

        assert status["name"] == "test_api"
        assert status["state"] == "CLOSED"
        assert status["failure_count"] == 0
        assert status["time_until_retry"] == 0


    def test_status_when_open(self):
        cb = CircuitBreaker(name="test_api", failure_threshold=1, reset_timeout=300)
        cb.record_failure("timeout error")
        status = cb.get_status()

        assert status["state"] == "OPEN"
        assert status["failure_count"] == 1
        assert status["last_error"] == "timeout error"
        assert status["time_until_retry"] > 0
        assert status["time_until_retry"] <= 300
        assert status["opened_at"] is not None

    def test_status_when_half_open(self):
        cb = CircuitBreaker(name="test_api", failure_threshold=1, reset_timeout=0)
        cb.record_failure("err")
        cb.allow_request()  # → HALF_OPEN
        status = cb.get_status()

        assert status["state"] == "HALF_OPEN"

    def test_status_has_all_fields(self):
        cb = CircuitBreaker(name="full_status")
        status = cb.get_status()

        required_fields = [
            "name", "state", "failure_count", "success_count",
            "failure_threshold", "reset_timeout",
            "last_failure_time", "last_success_time",
            "opened_at", "last_error", "time_until_retry",
        ]
        for field in required_fields:
            assert field in status, f"status 缺少字段: {field}"

    def test_status_time_until_retry_decreases(self):
        cb = CircuitBreaker(name="test", failure_threshold=1, reset_timeout=5)
        cb.record_failure("err")

        status1 = cb.get_status()
        time.sleep(1)
        status2 = cb.get_status()

        assert status2["time_until_retry"] < status1["time_until_retry"]


# ================================================================
# 10. 边界条件
# ================================================================

class TestEdgeCases:
    """边界条件和异常场景"""

    def test_threshold_of_one(self):
        """阈值为1，一次失败就熔断"""
        cb = CircuitBreaker(name="test", failure_threshold=1)
        cb.record_failure("err")
        assert cb.state == CircuitState.OPEN

    def test_very_large_threshold(self):
        """极大阈值"""
        cb = CircuitBreaker(name="test", failure_threshold=10000)
        for i in range(9999):
            cb.record_failure(f"err{i}")
        assert cb.state == CircuitState.CLOSED

        cb.record_failure("final")
        assert cb.state == CircuitState.OPEN

    def test_zero_reset_timeout(self):
        """超时为0，立即可以进入半开"""
        cb = CircuitBreaker(name="test", failure_threshold=1, reset_timeout=0)
        cb.record_failure("err")
        assert cb.state == CircuitState.OPEN

        assert cb.allow_request() is True
        assert cb.state == CircuitState.HALF_OPEN

    def test_success_in_open_state_ignored(self):
        """OPEN状态下收到success不应该改变状态"""
        cb = CircuitBreaker(name="test", failure_threshold=1, reset_timeout=300)
        cb.record_failure("err")
        assert cb.state == CircuitState.OPEN

        # OPEN状态下不会有请求到达，但如果意外调用success
        cb.record_success()
        # 状态不应该变(因为不在CLOSED也不在HALF_OPEN)
        assert cb.state == CircuitState.OPEN

    def test_rapid_open_close_cycles(self):
        """快速开关循环不会出错"""
        cb = CircuitBreaker(
            name="rapid",
            failure_threshold=1,
            reset_timeout=0,
            success_threshold=1,
        )
        for _ in range(100):
            cb.record_failure("err")
            assert cb.state == CircuitState.OPEN
            cb.allow_request()
            cb.record_success()
            assert cb.state == CircuitState.CLOSED

    def test_concurrent_failures(self):
        """连续快速失败"""
        cb = CircuitBreaker(name="test", failure_threshold=3)
        for i in range(10):
            cb.record_failure(f"err{i}")

        assert cb.state == CircuitState.OPEN
        # 失败计数应该是触发时的值，之后的失败在OPEN状态下处理
        assert cb.failure_count >= 3

    def test_empty_error_message(self):
        cb = CircuitBreaker(name="test", failure_threshold=1)
        cb.record_failure("")
        assert cb.last_error == ""
        assert cb.state == CircuitState.OPEN

    def test_long_error_message(self):
        cb = CircuitBreaker(name="test", failure_threshold=1)
        long_msg = "x" * 10000
        cb.record_failure(long_msg)
        assert cb.last_error == long_msg


# ================================================================
# 11. 并发安全基础测试
# ================================================================

class TestConcurrencySafety:
    """基础并发场景(单线程模拟)"""

    def test_alternating_success_failure(self):
        """交替成功失败不会触发熔断"""
        cb = CircuitBreaker(name="test", failure_threshold=3)
        for _ in range(100):
            cb.record_failure("err")
            cb.record_success()  # 每次成功重置

        assert cb.state == CircuitState.CLOSED
        assert cb.failure_count == 0

    def test_burst_failures_then_recovery(self):
        """突发失败后慢慢恢复"""
        cb = CircuitBreaker(
            name="burst",
            failure_threshold=5,
            reset_timeout=0,
            success_threshold=3,
        )

        # 突发失败
        for i in range(5):
            cb.record_failure(f"burst_err{i}")
        assert cb.state == CircuitState.OPEN

        # 进入半开
        cb.allow_request()
        assert cb.state == CircuitState.HALF_OPEN

        # 慢慢恢复
        cb.record_success()
        assert cb.state == CircuitState.HALF_OPEN
        cb.record_success()
        assert cb.state == CircuitState.HALF_OPEN
        cb.record_success()
        assert cb.state == CircuitState.CLOSED


# ================================================================
# 12. 多实例独立性
# ================================================================

class TestMultipleInstances:
    """多个熔断器实例互不影响"""

    def test_instances_are_independent(self):
        cb1 = CircuitBreaker(name="adapter_1", failure_threshold=2)
        cb2 = CircuitBreaker(name="adapter_2", failure_threshold=2)

        # cb1 熔断
        cb1.record_failure("err1")
        cb1.record_failure("err2")
        assert cb1.state == CircuitState.OPEN

        # cb2 不受影响
        assert cb2.state == CircuitState.CLOSED
        assert cb2.allow_request() is True

    def test_three_independent_breakers(self):
        """模拟3个数据源各自独立熔断"""
        tushare_cb = CircuitBreaker(name="tushare", failure_threshold=3)
        akshare_cb = CircuitBreaker(name="akshare", failure_threshold=3)
        sina_cb = CircuitBreaker(name="sina", failure_threshold=3)

        # tushare 熔断
        for i in range(3):
            tushare_cb.record_failure(f"err{i}")
        assert tushare_cb.state == CircuitState.OPEN

        # akshare 部分失败但未熔断
        akshare_cb.record_failure("err1")
        akshare_cb.record_failure("err2")
        assert akshare_cb.state == CircuitState.CLOSED

        # sina 全部正常
        sina_cb.record_success()
        assert sina_cb.state == CircuitState.CLOSED

        # 各自独立
        assert tushare_cb.state == CircuitState.OPEN
        assert akshare_cb.state == CircuitState.CLOSED
        assert sina_cb.state == CircuitState.CLOSED
