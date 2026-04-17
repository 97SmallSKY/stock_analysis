"""
状态机单元测试

覆盖范围：
- 所有状态转换路径覆盖率 100%
- 非法转换抛出正确异常
- 终态不可逆验证
"""
import pytest
from backend import (
    TRADE_TRANSITIONS,
    ROBOT_TRANSITIONS,
    TradeStatus,
    RobotStatus,
    SellReason,
    AbandonReason,
    InvalidStateTransition,
    TerminalStateError,
    validate_transition,
    is_terminal_status,
    get_allowed_transitions,
)


class TestTradeTransitions:
    """交易状态转换测试"""
    
    def test_pending_to_open(self):
        """pending → open 合法"""
        assert validate_transition('pending', 'open') is True
    
    def test_pending_to_abandoned(self):
        """pending → abandoned 合法"""
        assert validate_transition('pending', 'abandoned') is True
    
    def test_open_to_partial(self):
        """open → partial 合法"""
        assert validate_transition('open', 'partial') is True
    
    def test_open_to_closed(self):
        """open → closed 合法"""
        assert validate_transition('open', 'closed') is True
    
    def test_open_to_abandoned(self):
        """open → abandoned 合法"""
        assert validate_transition('open', 'abandoned') is True
    
    def test_open_to_eliminated(self):
        """open → eliminated 合法"""
        assert validate_transition('open', 'eliminated') is True
    
    def test_partial_to_partial(self):
        """partial → partial 合法（自转换）"""
        assert validate_transition('partial', 'partial') is True
    
    def test_partial_to_closed(self):
        """partial → closed 合法"""
        assert validate_transition('partial', 'closed') is True
    
    def test_partial_to_abandoned(self):
        """partial → abandoned 合法"""
        assert validate_transition('partial', 'abandoned') is True
    
    def test_partial_to_eliminated(self):
        """partial → eliminated 合法"""
        assert validate_transition('partial', 'eliminated') is True
    
    def test_closed_terminal(self):
        """closed 是终态，不可转换"""
        assert validate_transition('closed', 'open') is False
        with pytest.raises(InvalidStateTransition):
            validate_transition('closed', 'open', TRADE_TRANSITIONS)
    
    def test_abandoned_terminal(self):
        """abandoned 是终态，不可转换"""
        assert validate_transition('abandoned', 'open') is False
        with pytest.raises(InvalidStateTransition):
            validate_transition('abandoned', 'pending', TRADE_TRANSITIONS)
    
    def test_eliminated_terminal(self):
        """eliminated 是终态，不可转换"""
        assert validate_transition('eliminated', 'open') is False
        with pytest.raises(InvalidStateTransition):
            validate_transition('eliminated', 'closed', TRADE_TRANSITIONS)
    
    def test_invalid_pending_to_partial(self):
        """pending → partial 非法"""
        with pytest.raises(InvalidStateTransition) as exc_info:
            validate_transition('pending', 'partial', TRADE_TRANSITIONS)
        
        assert exc_info.value.current == 'pending'
        assert exc_info.value.target == 'partial'
        assert 'open' in exc_info.value.allowed
    
    def test_invalid_open_to_pending(self):
        """open → pending 非法（不可逆）"""
        with pytest.raises(InvalidStateTransition):
            validate_transition('open', 'pending', TRADE_TRANSITIONS)


class TestRobotTransitions:
    """机器人状态转换测试"""
    
    def test_paper_to_running(self):
        """paper_trading → running 合法"""
        assert validate_transition('paper_trading', 'running', ROBOT_TRANSITIONS) is True
    
    def test_running_to_paused(self):
        """running → paused 合法"""
        assert validate_transition('running', 'paused', ROBOT_TRANSITIONS) is True
    
    def test_running_to_stopped(self):
        """running → stopped 合法"""
        assert validate_transition('running', 'stopped', ROBOT_TRANSITIONS) is True
    
    def test_running_to_eliminated(self):
        """running → eliminated 合法"""
        assert validate_transition('running', 'eliminated', ROBOT_TRANSITIONS) is True
    
    def test_stopped_to_running(self):
        """stopped → running 合法（可恢复）"""
        assert validate_transition('stopped', 'running', ROBOT_TRANSITIONS) is True
    
    def test_eliminated_to_stopped(self):
        """eliminated → stopped 合法（两步恢复）"""
        assert validate_transition('eliminated', 'stopped', ROBOT_TRANSITIONS) is True
    
    def test_stopped_to_eliminated_forbidden(self):
        """stopped → eliminated 禁止"""
        with pytest.raises(InvalidStateTransition):
            validate_transition('stopped', 'eliminated', ROBOT_TRANSITIONS)


class TestTerminalStatus:
    """终态判断测试"""
    
    def test_closed_is_terminal(self):
        """closed 是终态"""
        assert is_terminal_status('closed') is True
    
    def test_abandoned_is_terminal(self):
        """abandoned 是终态"""
        assert is_terminal_status('abandoned') is True
    
    def test_eliminated_is_terminal(self):
        """eliminated 是终态"""
        assert is_terminal_status('eliminated') is True
    
    def test_pending_not_terminal(self):
        """pending 不是终态"""
        assert is_terminal_status('pending') is False
    
    def test_open_not_terminal(self):
        """open 不是终态"""
        assert is_terminal_status('open') is False
    
    def test_partial_not_terminal(self):
        """partial 不是终态"""
        assert is_terminal_status('partial') is False


class TestGetAllowedTransitions:
    """获取允许转换目标列表测试"""
    
    def test_pending_allowed(self):
        """pending 允许的目标"""
        allowed = get_allowed_transitions('pending')
        assert 'open' in allowed
        assert 'abandoned' in allowed
        assert len(allowed) == 2
    
    def test_open_allowed(self):
        """open 允许的目标"""
        allowed = get_allowed_transitions('open')
        assert 'partial' in allowed
        assert 'closed' in allowed
        assert 'abandoned' in allowed
        assert 'eliminated' in allowed
    
    def test_closed_allowed_empty(self):
        """closed 无允许目标"""
        allowed = get_allowed_transitions('closed')
        assert len(allowed) == 0


class TestExceptions:
    """异常类测试"""
    
    def test_invalid_transition_to_dict(self):
        """异常转字典"""
        exc = InvalidStateTransition('pending', 'closed', ['open', 'abandoned'])
        result = exc.to_dict()
        
        assert result['error'] == 'invalid_state_transition'
        assert result['current'] == 'pending'
        assert result['target'] == 'closed'
        assert 'open' in result['allowed']


class TestEnums:
    """枚举类测试"""
    
    def test_trade_status_values(self):
        """交易状态枚举值"""
        assert TradeStatus.PENDING.value == 'pending'
        assert TradeStatus.OPEN.value == 'open'
        assert TradeStatus.PARTIAL.value == 'partial'
        assert TradeStatus.CLOSED.value == 'closed'
        assert TradeStatus.ABANDONED.value == 'abandoned'
        assert TradeStatus.ELIMINATED.value == 'eliminated'
    
    def test_sell_reason_values(self):
        """卖出原因枚举值"""
        assert SellReason.STOP_LOSS.value == 'stop_loss'
        assert SellReason.EXPIRE.value == 'expire'
        assert SellReason.TAKE_PROFIT_1.value == 'take_profit_1'
        assert SellReason.MANUAL.value == 'manual'
    
    def test_abandon_reason_values(self):
        """放弃原因枚举值"""
        assert AbandonReason.SUSPENDED.value == 'suspended'
        assert AbandonReason.ST_DETECTED.value == 'st_detected'
        assert AbandonReason.LIMIT_UP.value == 'limit_up'


class TestStateTransitionCoverage:
    """状态转换路径覆盖率测试"""
    
    @pytest.mark.parametrize("current,targets", [
        ('pending', ['open', 'abandoned']),
        ('open', ['partial', 'closed', 'abandoned', 'eliminated']),
        ('partial', ['partial', 'closed', 'abandoned', 'eliminated']),
        ('closed', []),
        ('abandoned', []),
        ('eliminated', []),
    ])
    def test_all_transitions(self, current, targets):
        """验证所有定义的转换"""
        for target in targets:
            assert validate_transition(current, target) is True
        
        # 验证未定义的转换抛出异常
        all_statuses = ['pending', 'open', 'partial', 'closed', 'abandoned', 'eliminated']
        for status in all_statuses:
            if status not in targets and status != current:
                with pytest.raises(InvalidStateTransition):
                    validate_transition(current, status)
