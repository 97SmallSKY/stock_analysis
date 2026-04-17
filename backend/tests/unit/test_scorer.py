"""
评分器单元测试

覆盖范围：
- 净买入强度各档位边界值
- 席位质量：有/无知名游资、纯机构席位
- 价格动量：正/负/涨停板特殊处理
- 综合评分加权求和精度
"""
import pytest
from backend import (
    score_change_pct,
    score_turnover,
    score_lhb_count,
    score_concentration,
    score_net_buy,
    score_institution_ratio,
    score_seat_type,
    calc_final_score,
    KNOWN_TRADERS,
    Scorer,
)


class TestScoreChangePct:
    """涨跌幅评分测试"""
    
    def test_negative_change(self):
        """负涨跌幅应返回20分"""
        assert score_change_pct(-0.05) == 20.0
        assert score_change_pct(-0.10) == 20.0
        assert score_change_pct(0) == 20.0
    
    def test_low_positive_change(self):
        """0%-3%区间线性增长"""
        assert score_change_pct(0.0) == 20.0
        assert score_change_pct(0.015) == pytest.approx(55.0)  # 中间值
        assert score_change_pct(0.03) == 70.0
    
    def test_moderate_positive_change(self):
        """3%-7%区间继续增长"""
        assert score_change_pct(0.03) == 70.0
        assert score_change_pct(0.05) == pytest.approx(85.0)
        assert score_change_pct(0.07) == 100.0
    
    def test_near_limit_up(self):
        """7%-10%区间涨停降分"""
        assert score_change_pct(0.07) == 100.0
        assert score_change_pct(0.085) == pytest.approx(90.0)
        assert score_change_pct(0.10) == 80.0
    
    def test_limit_up(self):
        """涨停板固定80分"""
        assert score_change_pct(0.10) == 80.0
        assert score_change_pct(0.11) == 80.0
        assert score_change_pct(0.15) == 80.0


class TestScoreTurnover:
    """换手率评分测试（钟形分布）"""
    
    def test_zero_turnover(self):
        """零换手应返回最低分"""
        score = score_turnover(0.0)
        assert score < 30.0
    
    def test_optimal_turnover(self):
        """最优换手率15%应得最高分"""
        score = score_turnover(0.15)
        assert score == pytest.approx(100.0)
    
    def test_high_turnover(self):
        """过高换手降分"""
        score = score_turnover(0.50)
        assert score < 50.0
    
    def test_very_high_turnover(self):
        """极高换手返回最低分"""
        score = score_turnover(1.0)
        assert score == pytest.approx(20.0)


class TestScoreNetBuy:
    """净买入评分测试（对数函数）"""
    
    def test_negative_net_buy(self):
        """净卖出应返回0分"""
        assert score_net_buy(-1_000_000) == 0.0
        assert score_net_buy(0) == 0.0
    
    def test_10_million(self):
        """1000万约40分"""
        score = score_net_buy(10_000_000)
        assert score == pytest.approx(40.0)
    
    def test_50_million(self):
        """5000万约60分"""
        score = score_net_buy(50_000_000)
        assert score == pytest.approx(60.0)
    
    def test_100_million(self):
        """1亿约80分"""
        score = score_net_buy(100_000_000)
        assert score == pytest.approx(80.0)
    
    def test_200_million(self):
        """2亿约100分"""
        score = score_net_buy(200_000_000)
        assert score == pytest.approx(100.0)
    
    def test_very_large_amount(self):
        """超大金额上限100分"""
        score = score_net_buy(1_000_000_000)
        assert score == 100.0


class TestScoreInstitutionRatio:
    """机构买入占比评分测试"""
    
    def test_zero_ratio(self):
        """无机构买入返回30分"""
        assert score_institution_ratio(0.0) == 30.0
    
    def test_low_ratio(self):
        """低机构占比区间"""
        assert score_institution_ratio(0.15) == pytest.approx(45.0)
        assert score_institution_ratio(0.30) == 60.0
    
    def test_medium_ratio(self):
        """中等机构占比"""
        assert score_institution_ratio(0.40) == pytest.approx(70.0)
        assert score_institution_ratio(0.50) == 80.0
    
    def test_high_ratio(self):
        """高机构占比"""
        assert score_institution_ratio(0.75) == pytest.approx(90.0)
        assert score_institution_ratio(1.0) == 100.0


class TestScoreSeatType:
    """席位类型评分测试"""
    
    def test_known_trader(self):
        """知名游资应得100分"""
        assert score_seat_type(["知名游资"]) == 100.0
        assert score_seat_type(["知名游资", "机构专用"]) == 100.0
    
    def test_institution(self):
        """机构专用应得85分"""
        assert score_seat_type(["机构专用"]) == 85.0
    
    def test_northbound(self):
        """北向资金应得75分"""
        assert score_seat_type(["沪股通"]) == 75.0
        assert score_seat_type(["深股通"]) == 75.0
    
    def test_normal_seat(self):
        """普通席位应得50分"""
        assert score_seat_type([]) == 50.0
        assert score_seat_type(["普通席位"]) == 50.0


class TestCalcFinalScore:
    """综合评分测试"""
    
    def test_weight_sum_validation(self):
        """权重和必须等于1.0"""
        with pytest.raises(ValueError):
            calc_final_score(50, 50, 50, tech_weight=0.5, fund_weight=0.3, hist_weight=0.1)
    
    def test_weighted_average(self):
        """验证加权平均计算精度"""
        score = calc_final_score(
            tech_score=100.0,
            fund_score=100.0,
            hist_score=100.0,
            tech_weight=0.40,
            fund_weight=0.35,
            hist_weight=0.25
        )
        assert score == 100.0
    
    def test_mixed_scores(self):
        """混合评分"""
        score = calc_final_score(
            tech_score=80.0,
            fund_score=60.0,
            hist_score=40.0
        )
        # 80*0.4 + 60*0.35 + 40*0.25 = 32 + 21 + 10 = 63
        assert score == pytest.approx(63.0)
    
    def test_score_bounds(self):
        """评分范围限制"""
        score = calc_final_score(150, -50, 200)
        assert 0.0 <= score <= 100.0


class TestScorer:
    """评分器类测试"""
    
    def test_init_weight_validation(self):
        """初始化时验证权重"""
        with pytest.raises(ValueError):
            Scorer(tech_weight=0.5, fund_weight=0.4, hist_weight=0.2)
    
    def test_scorer_instance(self):
        """创建评分器实例"""
        scorer = Scorer()
        assert scorer.tech_weight == 0.40
        assert scorer.fund_weight == 0.35
        assert scorer.hist_weight == 0.25
    
    @pytest.mark.asyncio
    async def test_calc_tech_score(self):
        """计算技术面得分"""
        scorer = Scorer()
        score, details = await scorer.calc_tech_score(
            change_pct=0.05,
            turnover_rate=0.15,
            lhb_count=3,
            top1_ratio=0.35
        )
        assert 0 <= score <= 100
        assert 'change_score' in details
        assert 'turnover_score' in details
    
    @pytest.mark.asyncio
    async def test_calc_fund_score(self):
        """计算资金面得分"""
        scorer = Scorer()
        score, details = await scorer.calc_fund_score(
            net_buy=50_000_000,
            institution_ratio=0.3,
            seat_names=["机构专用"]
        )
        assert 0 <= score <= 100
        assert 'net_buy_score' in details


class TestKnownTraders:
    """知名游资常量测试"""
    
    def test_known_traders_exist(self):
        """常量应包含知名席位"""
        assert len(KNOWN_TRADERS) > 0
        assert "华鑫证券上海分公司" in KNOWN_TRADERS
    
    def test_known_traders_values(self):
        """所有值应为'知名游资'"""
        for name, tag in KNOWN_TRADERS.items():
            assert tag == "知名游资"
