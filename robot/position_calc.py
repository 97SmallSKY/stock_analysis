"""
仓位计算模块

职责：根据 Phase A 的决策金额计算买入股数
替代原来 trade_runner.py 中的硬编码 BUY_AMOUNT
"""
import logging
from .trade_cost import calc_slippage_price, calc_buy_cost, DEFAULT_COST_CONFIG

logger = logging.getLogger(__name__)

DEFAULT_POSITION_RATIO = 0.1   # 默认仓位 10%
MAX_SINGLE_TRADE_RATIO = 0.3   # 单笔最大 30%


def calculate_buy_shares(
    planned_amount: float,
    raw_price: float,
    stock_code: str,
    total_capital: float = 100000,
) -> dict:
    """计算买入股数（含滑点和费用）
    
    Args:
        planned_amount: Phase A 决策的买入金额（来自 sim_trades.buy_amount）
        raw_price: 原始价格（T+1 开盘价，未含滑点）
        stock_code: 股票代码
        total_capital: 总资金（默认10万）
    
    Returns:
        {
            'shares': 买入股数（100的整数倍）,
            'slippage_price': 滑点后成交价,
            'planned_amount': 计划金额（可能被修正）,
            'actual_amount': 实际成交金额（不含费用）,
            'cost_detail': 成本明细（含佣金、过户费等）,
            'valid': 是否有效（False 表示无法买入）,
            'reason': 无效原因,
        }
    """
    result = {
        'shares': 0,
        'slippage_price': 0,
        'planned_amount': planned_amount,
        'actual_amount': 0,
        'cost_detail': {},
        'valid': False,
        'reason': '',
    }
    
    # Step 1: 校验 planned_amount
    if planned_amount is None or planned_amount <= 0:
        planned_amount = total_capital * DEFAULT_POSITION_RATIO
        logger.warning(f"buy_amount 无效，使用默认仓位: {planned_amount:.0f}")
        result['planned_amount'] = planned_amount
    
    # Step 2: 单笔上限
    max_amount = total_capital * MAX_SINGLE_TRADE_RATIO
    if planned_amount > max_amount:
        logger.warning(f"buy_amount {planned_amount:.0f} 超过单笔上限 {max_amount:.0f}，截断")
        planned_amount = max_amount
        result['planned_amount'] = planned_amount
    
    # Step 3: 计算滑点后价格
    slippage_price = calc_slippage_price(raw_price, 'BUY')
    result['slippage_price'] = slippage_price
    
    # Step 4: 预估费用，倒推可用于买股票的金额
    # 买入总支出 = 股票金额 + 佣金 + 过户费
    # 假设佣金≈万3，过户费（沪市）≈0.001%
    fee_ratio = DEFAULT_COST_CONFIG.commission_rate
    if stock_code.startswith(('6', '688')):
        fee_ratio += DEFAULT_COST_CONFIG.transfer_fee_rate_sh
    # 预留费用后的可用金额
    available_for_stock = planned_amount / (1 + fee_ratio)
    
    # Step 5: 计算股数（向下取整到100）
    raw_shares = available_for_stock / slippage_price
    shares = int(raw_shares / 100) * 100
    
    # Step 6: 至少100股
    if shares < 100:
        min_cost = calc_buy_cost(slippage_price, 100, stock_code)
        if planned_amount >= min_cost['total_paid']:
            shares = 100
        else:
            result['reason'] = f"资金不足: 需要{min_cost['total_paid']:.0f}, 可用{planned_amount:.0f}"
            logger.warning(f"{stock_code} {result['reason']}")
            return result
    
    # Step 7: 计算实际成本
    cost = calc_buy_cost(slippage_price, shares, stock_code)
    
    # Step 8: 如果总支出超过计划金额，减100股
    while cost['total_paid'] > planned_amount and shares >= 200:
        shares -= 100
        cost = calc_buy_cost(slippage_price, shares, stock_code)
    
    if cost['total_paid'] > planned_amount and shares < 200:
        result['reason'] = f"调整后仍超出预算: 需要{cost['total_paid']:.0f}, 预算{planned_amount:.0f}"
        logger.warning(f"{stock_code} {result['reason']}")
        return result
    
    # Step 9: 填充结果
    result['shares'] = shares
    result['actual_amount'] = cost['amount']
    result['cost_detail'] = cost
    result['valid'] = True
    
    logger.info(
        f"📊 {stock_code} 仓位计算: "
        f"计划={planned_amount:.0f}, 价格={raw_price}→{slippage_price}, "
        f"股数={shares}, 实际={cost['total_paid']:.0f}"
    )
    
    return result


# === 单元测试 ===
if __name__ == '__main__':
    print("=== 测试正常买入 ===")
    result = calculate_buy_shares(10000, 10.0, '600001', 100000)
    print(f"计划金额: {result['planned_amount']}")
    print(f"滑点价: {result['slippage_price']}")
    print(f"股数: {result['shares']}")
    print(f"实际金额: {result['actual_amount']}")
    print(f"总支出: {result['cost_detail'].get('total_paid', 0)}")
    print(f"有效: {result['valid']}")
    assert result['valid'], "应该有效"
    assert result['shares'] > 0, "股数应大于0"
    
    print("\n=== 测试大金额截断 ===")
    result = calculate_buy_shares(50000, 10.0, '600001', 100000)
    print(f"计划金额: {result['planned_amount']} (应该被截断到30000)")
    assert result['planned_amount'] == 30000, f"应截断到30%，实际{result['planned_amount']}"
    
    print("\n=== 测试资金不足 ===")
    result = calculate_buy_shares(500, 100.0, '600001', 100000)  # 100元每股，500元买不到100股
    print(f"有效: {result['valid']}")
    print(f"原因: {result['reason']}")
    assert not result['valid'], "应该无效"
    
    print("\n✅ 所有测试通过")
