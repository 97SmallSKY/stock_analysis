"""机器人状态机.

对应 DB `robots.status` (enum `robot_status`). 可能取值:
    IDLE         空闲, 可启动
    RUNNING      运行中, 可暂停 / 手动停止
    PAUSED       手动暂停, 可恢复
    COOL_DOWN    冷静期(爆亏触发 daily_loss_limit), 不能下单
    STOPPED      停用(管理员下线)
    ERROR        运行异常, 人工介入

只允许以下转换:
    IDLE      -> RUNNING / STOPPED
    RUNNING   -> PAUSED / COOL_DOWN / STOPPED / ERROR
    PAUSED    -> RUNNING / STOPPED
    COOL_DOWN -> IDLE (冷静期结束) / STOPPED
    STOPPED   -> IDLE (重新上线)
    ERROR     -> IDLE (修复后) / STOPPED
"""
from __future__ import annotations

from enum import Enum
from typing import Set


class RobotState(str, Enum):
    IDLE = "IDLE"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    COOL_DOWN = "COOL_DOWN"
    STOPPED = "STOPPED"
    ERROR = "ERROR"


class IllegalStateTransition(Exception):
    pass


_ALLOWED: dict[RobotState, Set[RobotState]] = {
    RobotState.IDLE: {RobotState.RUNNING, RobotState.STOPPED},
    RobotState.RUNNING: {
        RobotState.PAUSED, RobotState.COOL_DOWN,
        RobotState.STOPPED, RobotState.ERROR,
    },
    RobotState.PAUSED: {RobotState.RUNNING, RobotState.STOPPED},
    RobotState.COOL_DOWN: {RobotState.IDLE, RobotState.STOPPED},
    RobotState.STOPPED: {RobotState.IDLE},
    RobotState.ERROR: {RobotState.IDLE, RobotState.STOPPED},
}


class RobotStateMachine:
    """纯函数式状态机.

    实例化时绑定当前状态, `transition(next)` 返回新的 RobotStateMachine
    (不原地修改, 便于和不可变快照配合使用).
    """
    __slots__ = ("_state",)

    def __init__(self, state: RobotState | str = RobotState.IDLE):
        if isinstance(state, str):
            state = RobotState(state)
        self._state = state

    @property
    def state(self) -> RobotState:
        return self._state

    def can_transit(self, target: RobotState | str) -> bool:
        if isinstance(target, str):
            target = RobotState(target)
        return target in _ALLOWED.get(self._state, set())

    def transition(self, target: RobotState | str) -> "RobotStateMachine":
        if isinstance(target, str):
            target = RobotState(target)
        if target == self._state:
            return self
        if not self.can_transit(target):
            raise IllegalStateTransition(
                f"不能从 {self._state.value} 切换到 {target.value}"
            )
        return RobotStateMachine(target)

    # 常用语义化方法
    def start(self) -> "RobotStateMachine":
        return self.transition(RobotState.RUNNING)

    def pause(self) -> "RobotStateMachine":
        return self.transition(RobotState.PAUSED)

    def resume(self) -> "RobotStateMachine":
        return self.transition(RobotState.RUNNING)

    def stop(self) -> "RobotStateMachine":
        return self.transition(RobotState.STOPPED)

    def cool_down(self) -> "RobotStateMachine":
        return self.transition(RobotState.COOL_DOWN)

    def error(self) -> "RobotStateMachine":
        return self.transition(RobotState.ERROR)

    def reset_to_idle(self) -> "RobotStateMachine":
        return self.transition(RobotState.IDLE)

    def __eq__(self, other):  # noqa: D401
        if isinstance(other, RobotStateMachine):
            return self._state == other._state
        if isinstance(other, (RobotState, str)):
            return self._state == RobotState(other) if isinstance(other, str) else self._state == other
        return NotImplemented

    def __repr__(self):  # pragma: no cover
        return f"RobotStateMachine({self._state.value})"
