"""
综合评分器 - 龙虎榜多因子策略

评分体系：
- 技术面得分 (40%): 涨跌幅、换手率、龙虎榜次数、席位集中度
- 资金面得分 (35%): 净买入额、机构买入占比、席位类型
- 历史面得分 (25%): T+1胜率、T+1平均收益、最大涨幅

核心原则：
- AHS_SCORE ∈ [0, 100]，0 = 最健康，100 = 最危险（用于风控）
- 评分越高越危险，符合风险导向设计

版本: 2.1.0
参考: docs/lhb-quant-v2/STRATEGY.md
"""
import math
from datetime import date
from decimal import Decimal
from typing import Optional
from dataclasses import dataclass
from loguru import logger


# ============================================================
# 知名游资席位常量（来自STRATEGY.md）
# ============================================================
KNOWN_TRADERS = {
    # 传统知名游资
    "华鑫证券上海分公司": "知名游资",
    "财通证券杭州体育场路": "知名游资",
    "财通证券杭州庆春路": "知名游资",
    "财通证券杭州解放东路": "知名游资",
    "华泰证券深圳益田路荣超商务中心": "知名游资",
    "华泰证券深圳益田路": "知名游资",
    "华泰证券绍兴路": "知名游资",
    "华泰证券绍兴人民东路": "知名游资",
    "华泰证券永嘉阳光大道": "知名游资",
    "华泰证券厦门厦禾路": "知名游资",
    "中信证券杭州延安路": "知名游资",
    "中信证券上海溧阳路": "知名游资",
    "中信证券上海分公司": "知名游资",
    "中信证券上海淮海中路": "知名游资",
    "中信证券上海世纪大道": "知名游资",
    "银河证券绍兴营业部": "知名游资",
    "银河证券绍兴": "知名游资",
    "银河证券厦门美湖路": "知名游资",
    "光大证券宁波解放南路": "知名游资",
    "光大证券佛山季华六路": "知名游资",
    "光大证券佛山绿景路": "知名游资",
    "光大证券杭州庆春路": "知名游资",
    "国君证券上海分公司": "知名游资",
    "国泰君安上海江苏路": "知名游资",
    "国泰君安上海陆家嘴东路": "知名游资",
    "国泰君安上海打浦路": "知名游资",
    "国泰君安顺德大良": "知名游资",
    "招商证券深圳蛇口工业七路": "知名游资",
    "招商证券北京建国路": "知名游资",
    "东方证券杭州龙井路": "知名游资",
    "东方证券上海浦东新区银城中路": "知名游资",
    "浙商证券绍兴分公司": "知名游资",
    "浙商证券临安万马路": "知名游资",
    "中天证券杭州庆春路": "知名游资",
    "中投证券杭州环球中心": "知名游资",
    "中投证券无锡清扬路": "知名游资",
    "西藏同信证券成都东城根街": "知名游资",
    "西藏东方财富证券拉萨团结路第二营业部": "知名游资",
    "西藏东方财富证券拉萨东城区江苏大道": "知名游资",
    "华福证券泉州丰泽街": "知名游资",
    "华福证券厦门湖滨南路": "知名游资",
    "华福证券厦门嘉禾路": "知名游资",
    "申万宏源证券上海闵行区东川路": "知名游资",
    "申万宏源西部证券上海普陀区延长路": "知名游资",
    "海通证券杭州解放路": "知名游资",
    "海通证券广州番禺万达广场": "知名游资",
    "广发证券泉州涂门街": "知名游资",
    "广发证券汕头梅溪东路": "知名游资",
    "广发证券吴江仲英大道": "知名游资",
    "华林证券上海永兴路": "知名游资",
    "华林证券绍兴越城区人民东路": "知名游资",
    "华林证券深圳深南东路": "知名游资",
    "华鑫证券深圳益田路": "知名游资",
    "华鑫证券深圳分公司": "知名游资",
    "华鑫证券上海松江乐都路": "知名游资",
    "东方财富证券拉萨东环路第二营业部": "知名游资",
    "东方财富证券拉萨团结路第二营业部": "知名游资",
    "开源证券西安西大街": "知名游资",
    "开源证券厦门厦禾路": "知名游资",
    "长城证券杭州文三路": "知名游资",
    "长江证券上海世纪大道": "知名游资",
    "长江证券惠州下埔路": "知名游资",
    "民生证券河南分公司": "知名游资",
    "湘财证券上海陆家嘴": "知名游资",
    "东北证券上海分公司": "知名游资",
    "东北证券杭州五星路": "知名游资",
    "西部证券上海开鲁路": "知名游资",
    "西部证券北京中关村大街": "知名游资",
    "中泰证券上海建国中路": "知名游资",
    "中泰证券上海赤峰路": "知名游资",
    "中泰证券杭州五星路": "知名游资",
    "国信证券杭州萧然北路": "知名游资",
    "国信证券深圳泰然九路": "知名游资",
    "安信证券广州猎德大道": "知名游资",
    "安信证券深圳红荔西路": "知名游资",
    "安信证券杭州富阳依江路": "知名游资",
    "兴业证券上海金陵东路": "知名游资",
    "兴业证券福州湖东路": "知名游资",
    "东兴证券福州五一中路": "知名游资",
    "东兴证券上海肇嘉浜路": "知名游资",
    "红塔证券上海骊山路": "知名游资",
    "红塔证券上海田林东路": "知名游资",
    "太平洋证券杭州体育场路": "知名游资",
    "太平洋证券广州分公司": "知名游资",
    "国盛证券宁波桑田路": "知名游资",
    "国盛证券宁波分公司": "知名游资",
    "国盛证券景德镇瓷都大道": "知名游资",
    "中金公司上海黄浦区湖滨路": "知名游资",
    "中金公司北京建国门外大街": "知名游资",
    "华泰证券总部": "知名游资",
    "中信证券总部": "知名游资",
    "中金公司上海分公司": "知名游资",
}


# ============================================================
# 数据类定义
# ============================================================
@dataclass
class ScoreResult:
    """评分结果"""
    tech_score: float
    fund_score: float
    hist_score: float
    final_score: float
    tech_details: dict
    fund_details: dict
    hist_details: dict


@dataclass
class HistScoreInput:
    """历史面评分输入"""
    t1_win_rate: float  # T+1胜率 [0, 1]
    t1_avg_return: float  # T+1平均收益率 [-1, +1]
    max_gain: float  # 最大涨幅 [-1, +1]
    sample_count: int  # 样本数量


# ============================================================
# 技术面评分函数
# ============================================================
def score_change_pct(change_pct: float) -> float:
    """
    涨跌幅评分（连续函数）
    
    核心假设修正：适度上涨（3%-7%）最优，涨停板反而降分
    
    Args:
        change_pct: 涨跌幅，如 0.05 表示 +5%
    
    Returns:
        0-100 分
    """
    if change_pct <= 0:
        return 20.0
    elif change_pct <= 0.03:
        # 0% → 40分，3% → 70分，线性插值
        return 40.0 + (change_pct / 0.03) * 30.0
    elif change_pct <= 0.07:
        # 3% → 70分，7% → 100分
        return 70.0 + ((change_pct - 0.03) / 0.04) * 30.0
    elif change_pct <= 0.10:
        # 7% → 100分，涨停 → 80分（涨停板降分）
        return 100.0 - ((change_pct - 0.07) / 0.03) * 20.0
    else:
        return 80.0  # 涨停板


def score_turnover(turnover_rate: float) -> float:
    """
    换手率评分（钟形分布）
    
    核心假设修正：适度换手最优（10%-20%），过高和过低都不是好信号
    
    Args:
        turnover_rate: 换手率，如 0.15 表示 15%
    
    Returns:
        0-100 分
    """
    optimal = 0.15  # 最优换手率 15%
    sigma = 0.10    # 标准差
    # 高斯钟形函数
    score = 100.0 * math.exp(-((turnover_rate - optimal) ** 2) / (2 * sigma ** 2))
    return max(20.0, min(100.0, score))


def score_lhb_count(lhb_count: int) -> float:
    """
    龙虎榜次数评分
    
    假设：适度上榜（3-5次）最优，频繁上榜可能被主力做盘
    """
    if lhb_count == 0:
        return 30.0
    elif lhb_count <= 2:
        return 50.0 + lhb_count * 10.0  # 1次=60，2次=70
    elif lhb_count <= 5:
        return 70.0 + (lhb_count - 2) * 10.0  # 3次=80，5次=100
    else:
        return max(60.0, 100.0 - (lhb_count - 5) * 5.0)  # 过多降分


def score_concentration(top1_ratio: float) -> float:
    """
    席位集中度评分
    
    假设修正：需要区分"谁在集中"
    - 知名游资/机构集中 = 好（高分）
    - 散户集中 = 差（低分）
    
    由于当前无法区分席位身份，暂用保守策略：
    - 过度集中（>60%）风险较高 → 降分
    - 适度集中（30%-50%）较优
    
    Args:
        top1_ratio: 第一大席位买入占比
    
    Returns:
        0-100 分
    """
    if top1_ratio <= 0.20:
        return 60.0  # 过于分散，信号不明确
    elif top1_ratio <= 0.40:
        return 100.0  # 适度集中，最优区间
    elif top1_ratio <= 0.60:
        return 80.0  # 略高，仍有信号
    else:
        return 50.0  # 过度集中，风险高


# ============================================================
# 资金面评分函数
# ============================================================
def score_net_buy(net_buy: float) -> float:
    """
    净买入额评分（对数函数）
    
    使用对数函数消除断裂，金额越大增长越慢
    
    Args:
        net_buy: 净买入额（元）
    
    Returns:
        0-100 分
    """
    if net_buy <= 0:
        return 0.0
    # 1000万=40分，5000万=60分，1亿=80分，2亿=100分
    score = 20.0 * math.log10(net_buy / 1_000_000)
    return max(0.0, min(100.0, score))


def score_institution_ratio(institution_ratio: float) -> float:
    """
    机构买入占比评分（连续函数）
    
    Args:
        institution_ratio: 机构买入占比 [0, 1]
    
    Returns:
        0-100 分
    """
    if institution_ratio <= 0:
        return 30.0
    elif institution_ratio <= 0.30:
        return 30.0 + (institution_ratio / 0.30) * 30.0  # 0-30%: 30-60分
    elif institution_ratio <= 0.50:
        return 60.0 + ((institution_ratio - 0.30) / 0.20) * 20.0  # 30-50%: 60-80分
    else:
        return 80.0 + min(20.0, (institution_ratio - 0.50) * 40.0)  # >50%: 80-100分


def score_seat_type(seat_tags: list) -> float:
    """
    席位类型评分
    
    Args:
        seat_tags: 席位标签列表，如 ["机构专用", "知名游资"]
    
    Returns:
        50-100 分
    """
    if "知名游资" in seat_tags:
        return 100.0
    elif "机构专用" in seat_tags:
        return 85.0
    elif "沪股通" in seat_tags or "深股通" in seat_tags:
        return 75.0
    else:
        return 50.0  # 普通席位


# ============================================================
# 历史面评分函数
# ============================================================
def calc_hist_score_with_shrinkage(
    t1_win_rate: float,
    t1_avg_return: float,
    max_gain: float,
    sample_count: int = 20
) -> float:
    """
    历史面评分（带样本量收缩）
    
    解决冷启动问题：新系统/新股票历史面数据为空
    
    Args:
        t1_win_rate: T+1 胜率 [0, 1]
        t1_avg_return: T+1 平均收益率 [-1, +1]
        max_gain: 最大涨幅 [-1, +1]
        sample_count: 样本数量
    
    Returns:
        0-100 分（已收缩）
    """
    # 先计算原始分数
    raw_score = _calc_raw_hist_score(t1_win_rate, t1_avg_return, max_gain)
    
    # 计算置信度（收缩系数）
    confidence = _calc_shrinkage_confidence(sample_count)
    
    # 向中性值收缩
    neutral = 50.0
    shrunk_score = neutral + (raw_score - neutral) * confidence
    
    return max(0.0, min(100.0, shrunk_score))


def _calc_raw_hist_score(
    t1_win_rate: float,
    t1_avg_return: float,
    max_gain: float
) -> float:
    """
    历史面原始评分（无样本收缩）
    
    计算公式：
    hist_score = t1_win_rate_score × 0.40
               + t1_avg_return_score × 0.35
               + max_gain_score × 0.25
    """
    # T+1 胜率评分
    win_rate_score = t1_win_rate * 100.0
    
    # T+1 平均收益评分（归一化到0-100）
    # 假设 -10% = 0分，+10% = 100分
    avg_return_score = max(0.0, min(100.0, (t1_avg_return + 0.10) * 500.0))
    
    # 最大涨幅评分
    # 假设 0% = 0分，+20% = 100分
    max_gain_score = max(0.0, min(100.0, max_gain * 500.0))
    
    # 加权求和
    hist_score = (
        win_rate_score * 0.40 +
        avg_return_score * 0.35 +
        max_gain_score * 0.25
    )
    
    return hist_score


def _calc_shrinkage_confidence(sample_count: int) -> float:
    """
    计算样本量收缩置信度
    
    | 样本数 | 置信度 | 处理方式 |
    |--------|--------|----------|
    | 0 次   | 0%     | 历史面得分 = 50（中性值）|
    | 1-4 次 | ≤15%   | 向中性值收缩 85% |
    | 5-9 次 | 15%-40%| 向中性值收缩 60% |
    | 10-19次| 40%-80%| 向中性值收缩 20% |
    | ≥20 次 | 100%   | 完全信任实际计算值 |
    """
    if sample_count == 0:
        return 0.0
    elif sample_count < 5:
        return sample_count / 20.0 * 0.3  # 最大0.15（收缩85%）
    elif sample_count < 10:
        return 0.15 + (sample_count - 5) / 5.0 * 0.25  # 0.15-0.40
    elif sample_count < 20:
        return 0.40 + (sample_count - 10) / 10.0 * 0.40  # 0.40-0.80
    else:
        return 1.0


# ============================================================
# 综合评分
# ============================================================
def calc_final_score(
    tech_score: float,
    fund_score: float,
    hist_score: float,
    tech_weight: float = 0.40,
    fund_weight: float = 0.35,
    hist_weight: float = 0.25
) -> float:
    """
    综合评分加权求和
    
    Args:
        tech_score: 技术面得分 [0, 100]
        fund_score: 资金面得分 [0, 100]
        hist_score: 历史面得分 [0, 100]
        tech_weight: 技术面权重（默认40%）
        fund_weight: 资金面权重（默认35%）
        hist_weight: 历史面权重（默认25%）
    
    Returns:
        加权综合得分 [0, 100]
    
    Raises:
        ValueError: 权重和不等于1.0
    """
    # 参数校验：权重和必须等于1.0
    if abs(tech_weight + fund_weight + hist_weight - 1.0) > 0.001:
        raise ValueError(
            f"权重和必须等于1.0，当前: {tech_weight + fund_weight + hist_weight:.4f}"
        )
    
    final_score = (
        tech_score * tech_weight +
        fund_score * fund_weight +
        hist_score * hist_weight
    )
    
    return max(0.0, min(100.0, final_score))


# ============================================================
# 评分器类
# ============================================================
class Scorer:
    """
    综合评分器
    
    封装所有评分逻辑，提供统一接口
    """
    
    def __init__(
        self,
        tech_weight: float = 0.40,
        fund_weight: float = 0.35,
        hist_weight: float = 0.25
    ):
        """
        初始化评分器
        
        Args:
            tech_weight: 技术面权重
            fund_weight: 资金面权重
            hist_weight: 历史面权重
        """
        # 验证权重
        if abs(tech_weight + fund_weight + hist_weight - 1.0) > 0.001:
            raise ValueError("权重和必须等于1.0")
        
        self.tech_weight = tech_weight
        self.fund_weight = fund_weight
        self.hist_weight = hist_weight
        
        logger.info(
            f"[Scorer] 初始化完成，权重: tech={tech_weight:.0%}, "
            f"fund={fund_weight:.0%}, hist={hist_weight:.0%}"
        )
    
    def calc_tech_score(
        self,
        change_pct: float,
        turnover_rate: float,
        lhb_count: int,
        top1_ratio: float
    ) -> tuple[float, dict]:
        """
        计算技术面得分
        
        Returns:
            (tech_score, details): 得分和明细
        """
        change_score = score_change_pct(change_pct)
        turnover_score = score_turnover(turnover_rate)
        lhb_count_score = score_lhb_count(lhb_count)
        concentration_score = score_concentration(top1_ratio)
        
        details = {
            'change_pct': change_pct,
            'change_score': change_score,
            'turnover_rate': turnover_rate,
            'turnover_score': turnover_score,
            'lhb_count': lhb_count,
            'lhb_count_score': lhb_count_score,
            'top1_ratio': top1_ratio,
            'concentration_score': concentration_score,
        }
        
        # 加权求和
        tech_score = (
            change_score * 0.30 +
            turnover_score * 0.25 +
            lhb_count_score * 0.25 +
            concentration_score * 0.20
        )
        
        details['tech_score'] = tech_score
        
        return tech_score, details
    
    def calc_fund_score(
        self,
        net_buy: float,
        institution_ratio: float,
        seat_names: list[str]
    ) -> tuple[float, dict]:
        """
        计算资金面得分
        
        Args:
            net_buy: 净买入额（元）
            institution_ratio: 机构买入占比
            seat_names: 买入席位名称列表
        
        Returns:
            (fund_score, details): 得分和明细
        """
        net_buy_score = score_net_buy(net_buy)
        institution_score = score_institution_ratio(institution_ratio)
        
        # 判断席位类型
        seat_tags = []
        for name in seat_names:
            if name in KNOWN_TRADERS:
                seat_tags.append("知名游资")
            elif "机构专用" in name:
                seat_tags.append("机构专用")
            elif "沪股通" in name or "深股通" in name:
                seat_tags.append("北向资金")
        
        seat_type_score = score_seat_type(seat_tags)
        
        details = {
            'net_buy': net_buy,
            'net_buy_score': net_buy_score,
            'institution_ratio': institution_ratio,
            'institution_score': institution_score,
            'seat_names': seat_names,
            'seat_tags': seat_tags,
            'seat_type_score': seat_type_score,
        }
        
        # 加权求和
        fund_score = (
            net_buy_score * 0.45 +
            institution_score * 0.30 +
            seat_type_score * 0.25
        )
        
        details['fund_score'] = fund_score
        
        return fund_score, details
    
    def calc_hist_score(
        self,
        t1_win_rate: float,
        t1_avg_return: float,
        max_gain: float,
        sample_count: int = 20
    ) -> tuple[float, dict]:
        """
        计算历史面得分（带样本收缩）
        """
        hist_score = calc_hist_score_with_shrinkage(
            t1_win_rate, t1_avg_return, max_gain, sample_count
        )
        
        details = {
            't1_win_rate': t1_win_rate,
            't1_avg_return': t1_avg_return,
            'max_gain': max_gain,
            'sample_count': sample_count,
            'raw_score': _calc_raw_hist_score(t1_win_rate, t1_avg_return, max_gain),
            'shrinkage_confidence': _calc_shrinkage_confidence(sample_count),
            'hist_score': hist_score,
        }
        
        return hist_score, details
    
    def score(
        self,
        change_pct: float,
        turnover_rate: float,
        lhb_count: int,
        top1_ratio: float,
        net_buy: float,
        institution_ratio: float,
        seat_names: list[str],
        t1_win_rate: float = 0.5,
        t1_avg_return: float = 0.0,
        max_gain: float = 0.0,
        sample_count: int = 0
    ) -> ScoreResult:
        """
        计算综合评分
        
        Returns:
            ScoreResult: 包含各维度得分和最终得分
        """
        tech_score, tech_details = self.calc_tech_score(
            change_pct, turnover_rate, lhb_count, top1_ratio
        )
        
        fund_score, fund_details = self.calc_fund_score(
            net_buy, institution_ratio, seat_names
        )
        
        hist_score, hist_details = self.calc_hist_score(
            t1_win_rate, t1_avg_return, max_gain, sample_count
        )
        
        final_score = calc_final_score(
            tech_score, fund_score, hist_score,
            self.tech_weight, self.fund_weight, self.hist_weight
        )
        
        return ScoreResult(
            tech_score=tech_score,
            fund_score=fund_score,
            hist_score=hist_score,
            final_score=final_score,
            tech_details=tech_details,
            fund_details=fund_details,
            hist_details=hist_details
        )


# ============================================================
# 工具函数
# ============================================================
def is_known_trader(seat_name: str) -> bool:
    """判断是否为知名游资席位"""
    return seat_name in KNOWN_TRADERS


def get_trader_tag(seat_name: str) -> Optional[str]:
    """获取席位标签"""
    return KNOWN_TRADERS.get(seat_name)
