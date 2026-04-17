"""
S02 策略交易引擎 - 单元测试

测试覆盖：
1. 评分器 (scorer.py)
2. 状态机 (state_machine.py)
3. 风控模块 (risk_control.py)
4. AHS计算器 (ahs_calculator.py)
5. 结算服务 (daily_settlement.py)

运行：
pytest backend/tests/unit/test_strategy.py -v
"""
import pytest

# 导入被测试模块
from backend import (
    score_change_pct,
    score_turnover,
    score_lhb_count,
    score_concentration,
    score_net_buy,
    score_institution_ratio,
    score_seat_type,
    calc_final_score,
    Scorer,
    KNOWN_TRADERS,
)
from backend import (
    TradeStatus,
    RobotStatus,
    validate_transition,
    TRADE_TRANSITIONS,
    InvalidStateTransition,
)
from backend import (
    RiskControl,
    RiskCheckResult,
    MAX_TOTAL_POSITIONS,
    MAX_SINGLE_STOCK_RATIO,
)
from backend import (
    calculate_ahs,
    get_trading_mode,
    get_position_mult,
    AHS_THRESHOLD_STOP,
    AHS_THRESHOLD_WARNING,
)


# ============================================================
# 评分器测试
# ============================================================
class TestScorer:
    """评分器测试"""
    
    def test_score_change_pct_negative(self):
        """负涨跌幅应返回20分"""
        assert score_change_pct(-0.05) == 20.0
        assert score_change_pct(0) == 20.0
    
    def test_score_change_pct_positive(self):
        """正涨跌幅应返回相应分数"""
        assert score_change_pct(0.01) == pytest.approx(43.33, rel=0.01)
        assert score_change_pct(0.05) == pytest.approx(85.0, rel=0.01)
        assert score_change_pct(0.10) == 80.0  # 涨停降分
    
    def test_score_turnover_optimal(self):
        """最优换手率15%应得最高分"""
        score = score_turnover(0.15)
        assert score == pytest.approx(100.0, rel=0.01)
    
    def test_score_turnover_extreme(self):
        """极端换手率应返回低分"""
        assert score_turnover(0.0) < 30.0
        assert score_turnover(0.50) < 50.0
    
    def test_score_net_buy_logarithmic(self):
        """净买入评分应呈对数增长"""
        assert score_net_buy(10_000_000) == pytest.approx(40.0, rel=0.01)
        assert score_net_buy(50_000_000) == pytest.approx(60.0, rel=0.01)
        assert score_net_buy(100_000_000) == pytest.approx(80.0, rel=0.01)
    
    def test_score_net_buy_negative(self):
        """负净买入应返回0分"""
        assert score_net_buy(-1_000_000) == 0.0
    
    def test_score_institution_ratio(self):
        """机构买入占比评分"""
        assert score_institution_ratio(0.0) == 30.0
        assert score_institution_ratio(0.50) == pytest.approx(80.0, rel=0.01)
        assert score_institution_ratio(1.0) == 100.0
    
    def test_score_seat_type(self):
        """席位类型评分"""
        assert score_seat_type(["知名游资"]) == 100.0
        assert score_seat_type(["机构专用"]) == 85.0
        assert score_seat_type(["沪股通"]) == 75.0
        assert score_seat_type([]) == 50.0
    
    def test_calc_final_score_weighted(self):
        """综合评分加权求和"""
        score = calc_final_score(80.0, 60.0, 40.0)
        expected = 80.0 * 0.40 + 60.0 * 0.35 + 40.0 * 0.25
        assert score == pytest.approx(expected, rel=0.01)
    
    def test_calc_final_score_invalid_weights(self):
        """权重和不为1应抛出异常"""
        with pytest.raises(ValueError):
            calc_final_score(50, 50, 50, tech_weight=0.5, fund_weight=0.3, hist_weight=0.1)
    
    def test_known_traders_not_empty(self):
        """知名游资常量不应为空"""
        assert len(KNOWN_TRADERS) > 0
        assert "华鑫证券上海分公司" in KNOWN_TRADERS


# ============================================================
# 状态机测试
# ============================================================
class TestStateMachine:
    """状态机测试"""
    
    def test_pending_to_open_valid(self):
        """pending → open 合法"""
        assert validate_transition('pending', 'open') is True
    
    def test_pending_to_closed_invalid(self):
        """pending → closed 非法"""
        with pytest.raises(InvalidStateTransition):
            validate_transition('pending', 'closed')
    
    def test_open_to_partial_valid(self):
        """open → partial 合法"""
        assert validate_transition('open', 'partial') is True
    
    def test_open_to_pending_invalid(self):
        """open → pending 非法（不可逆）"""
        with pytest.raises(InvalidStateTransition):
            validate_transition('open', 'pending')
    
    def test_closed_terminal(self):
        """closed 是终态"""
        with pytest.raises(InvalidStateTransition):
            validate_transition('closed', 'open')
    
    def test_abandoned_terminal(self):
        """abandoned 是终态"""
        with pytest.raises(InvalidStateTransition):
            validate_transition('abandoned', 'pending')
    
    def test_all_trade_statuses(self):
        """所有交易状态枚举值"""
        assert TradeStatus.PENDING.value == 'pending'
        assert TradeStatus.OPEN.value == 'open'
        assert TradeStatus.PARTIAL.value == 'partial'
        assert TradeStatus.CLOSED.value == 'closed'
        assert TradeStatus.ABANDONED.value == 'abandoned'
        assert TradeStatus.ELIMINATED.value == 'eliminated'


# ============================================================
# 风控模块测试
# ============================================================
class TestRiskControl:
    """风控模块测试"""
    
    def test_max_positions_constant(self):
        """最大持仓数常量"""
        assert MAX_TOTAL_POSITIONS == 5
    
    def test_max_single_stock_ratio(self):
        """单只股票上限"""
        assert MAX_SINGLE_STOCK_RATIO == 0.40
    
    def test_risk_check_result_passed(self):
        """风控检查结果"""
        result = RiskCheckResult(passed=True)
        assert result.passed is True
        assert result.reason is None
    
    def test_risk_check_result_failed(self):
        """风控检查失败"""
        result = RiskCheckResult(passed=False, reason="资金不足")
        assert result.passed is False
        assert result.reason == "资金不足"


# ============================================================
# AHS计算器测试
# ============================================================
class TestAHSCalculator:
    """AHS计算器测试"""
    
    def test_calculate_ahs_perfect(self):
        """完美表现应得低分（健康）"""
        score = calculate_ahs(
            win_rate=0.80,
            avg_pnl=0.10,
            consecutive_loss=0,
            max_drawdown=0.05
        )
        assert score < 30.0  # 健康
    
    def test_calculate_ahs_terrible(self):
        """糟糕表现应得高分（危险）"""
        score = calculate_ahs(
            win_rate=0.20,
            avg_pnl=-0.10,
            consecutive_loss=5,
            max_drawdown=0.20
        )
        assert score > 70.0  # 危险
    
    def test_calculate_ahs_bounds(self):
        """AHS分数应在0-100之间"""
        score = calculate_ahs(0.5, 0.0, 2, 0.1)
        assert 0 <= score <= 100
    
    def test_get_trading_mode_normal(self):
        """低分应返回正常模式"""
        mode = get_trading_mode(20.0, 'normal')
        assert mode == 'normal'
    
    def test_get_trading_mode_stopped(self):
        """高分应返回停止模式"""
        mode = get_trading_mode(85.0, 'normal')
        assert mode == 'stopped'
    
    def test_get_trading_mode_hysteresis(self):
        """滞后带：当前模式为stopped时，需要降到60才能恢复"""
        mode = get_trading_mode(65.0, 'stopped')
        assert mode == 'stopped'  # 仍需等待
        
        mode = get_trading_mode(55.0, 'stopped')
        assert mode == 'cautious'  # 恢复
    
    def test_get_position_mult_values(self):
        """仓位系数值"""
        assert get_position_mult('normal') == 1.00
        assert get_position_mult('cautious') == 0.85
        assert get_position_mult('conservative') == 0.70
        assert get_position_mult('warning') == 0.40
        assert get_position_mult('stopped') == 0.00
    
    def test_ahs_thresholds(self):
        """AHS阈值常量"""
        assert AHS_THRESHOLD_STOP == 81
        assert AHS_THRESHOLD_WARNING == 61


# ============================================================
# 综合场景测试
# ============================================================
class TestIntegration:
    """综合场景测试"""
    
    def test_scorer_instance(self):
        """创建评分器实例"""
        scorer = Scorer()
        assert scorer.tech_weight == 0.40
        assert scorer.fund_weight == 0.35
        assert scorer.hist_weight == 0.25
    
    def test_scorer_custom_weights(self):
        """自定义权重"""
        scorer = Scorer(tech_weight=0.50, fund_weight=0.30, hist_weight=0.20)
        assert scorer.tech_weight == 0.50
    
    def test_scorer_invalid_weights(self):
        """无效权重应抛出异常"""
        with pytest.raises(ValueError):
            Scorer(tech_weight=0.5, fund_weight=0.4, hist_weight=0.2)
    
    def test_state_machine_transitions_complete(self):
        """状态机转换完整性"""
        # 验证所有定义的转换
        assert 'open' in TRADE_TRANSITIONS['pending']
        assert 'abandoned' in TRADE_TRANSITIONS['pending']
        assert 'partial' in TRADE_TRANSITIONS['open']
        assert 'closed' in TRADE_TRANSITIONS['open']
        assert len(TRADE_TRANSITIONS['closed']) == 0  # 终态


if __name__ == '__main__':
    pytest.main(['-xvs', __file__])
