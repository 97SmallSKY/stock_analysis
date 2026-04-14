"""
交易成本计算模块

A股交易费用：
- 印花税：卖出 0.05%，买入免收
- 佣金：买卖双向，万3（0.03%），单笔最低5元
- 过户费：沪市 0.001%，深市免收
- 滑点：买入 +0.1%，卖出 -0.1%（模拟市场冲击）
"""
from dataclasses import dataclass


@dataclass
class CostConfig:
    """交易费率配置，可按机器人自定义"""
    stamp_tax_rate: float = 0.0005       # 印花税 0.05%
    commission_rate: float = 0.0003      # 佣金 万3
    commission_min: float = 5.0          # 佣金最低5元
    transfer_fee_rate_sh: float = 0.00001  # 沪市过户费 0.001%
    transfer_fee_rate_sz: float = 0.0      # 深市免收
    slippage_rate: float = 0.001         # 滑点 0.1%


DEFAULT_COST_CONFIG = CostConfig()


def is_sh_stock(code: str) -> bool:
    """判断是否沪市股票：6开头（含科创板688）"""
    return code.startswith(('6', '688'))


def calc_slippage_price(price: float, direction: str, config: CostConfig = None) -> float:
    """计算滑点后的实际成交价
    
    Args:
        price: 原始价格
        direction: 'BUY' 或 'SELL'
        config: 费率配置
    
    Returns:
        滑点后的实际成交价
    """
    if config is None:
        config = DEFAULT_COST_CONFIG
    
    if direction == 'BUY':
        # 买入滑点：价格上浮
        return round(price * (1 + config.slippage_rate), 2)
    else:  # SELL
        # 卖出滑点：价格下浮
        return round(price * (1 - config.slippage_rate), 2)


def calc_buy_cost(price: float, shares: int, code: str, config: CostConfig = None) -> dict:
    """计算买入总成本
    
    Args:
        price: 买入价格（已含滑点）
        shares: 买入股数
        code: 股票代码
        config: 费率配置
    
    Returns:
        {
            'amount': 成交金额,
            'commission': 佣金,
            'transfer_fee': 过户费,
            'stamp_tax': 印花税（买入为0）,
            'total_cost': 总费用,
            'total_paid': 实际支付总额,
        }
    """
    if config is None:
        config = DEFAULT_COST_CONFIG
    
    amount = price * shares
    
    # 佣金：万3，最低5元
    commission = max(amount * config.commission_rate, config.commission_min)
    
    # 过户费：沪市收，深市免
    if is_sh_stock(code):
        transfer_fee = amount * config.transfer_fee_rate_sh
    else:
        transfer_fee = 0.0
    
    stamp_tax = 0.0  # 买入免收印花税
    
    total_cost = round(commission + transfer_fee + stamp_tax, 2)
    total_paid = round(amount + total_cost, 2)
    
    return {
        'amount': round(amount, 2),
        'commission': round(commission, 2),
        'transfer_fee': round(transfer_fee, 2),
        'stamp_tax': stamp_tax,
        'total_cost': total_cost,
        'total_paid': total_paid,
    }


def calc_sell_cost(price: float, shares: int, code: str, config: CostConfig = None) -> dict:
    """计算卖出总成本
    
    Args:
        price: 卖出价格（已含滑点）
        shares: 卖出股数
        code: 股票代码
        config: 费率配置
    
    Returns:
        {
            'amount': 成交金额,
            'commission': 佣金,
            'transfer_fee': 过户费,
            'stamp_tax': 印花税（卖出收0.05%）,
            'total_cost': 总费用,
            'total_received': 实际收到总额,
        }
    """
    if config is None:
        config = DEFAULT_COST_CONFIG
    
    amount = price * shares
    
    commission = max(amount * config.commission_rate, config.commission_min)
    
    if is_sh_stock(code):
        transfer_fee = amount * config.transfer_fee_rate_sh
    else:
        transfer_fee = 0.0
    
    stamp_tax = amount * config.stamp_tax_rate  # 卖出收 0.05%
    
    total_cost = round(commission + transfer_fee + stamp_tax, 2)
    total_received = round(amount - total_cost, 2)
    
    return {
        'amount': round(amount, 2),
        'commission': round(commission, 2),
        'transfer_fee': round(transfer_fee, 2),
        'stamp_tax': round(stamp_tax, 2),
        'total_cost': total_cost,
        'total_received': total_received,
    }


def calc_trade_profit(
    buy_price: float, sell_price: float, 
    shares: int, code: str,
    config: CostConfig = None
) -> dict:
    """计算单笔交易完整盈亏（含所有成本）
    
    Args:
        buy_price: 买入价格（未含滑点）
        sell_price: 卖出价格（未含滑点）
        shares: 交易股数
        code: 股票代码
        config: 费率配置
    
    Returns:
        {
            'buy_slippage_price': 买入滑点价,
            'sell_slippage_price': 卖出滑点价,
            'buy_cost': 买入成本明细,
            'sell_cost': 卖出成本明细,
            'gross_profit': 毛利润（不含费用）,
            'total_fees': 总费用（买+卖）,
            'net_profit': 净利润,
            'net_profit_pct': 净利润率(%),
        }
    """
    if config is None:
        config = DEFAULT_COST_CONFIG
    
    # 计算滑点后的实际成交价
    buy_slippage_price = calc_slippage_price(buy_price, 'BUY', config)
    sell_slippage_price = calc_slippage_price(sell_price, 'SELL', config)
    
    # 计算成本
    buy = calc_buy_cost(buy_slippage_price, shares, code, config)
    sell = calc_sell_cost(sell_slippage_price, shares, code, config)
    
    gross_profit = sell['amount'] - buy['amount']
    total_fees = buy['total_cost'] + sell['total_cost']
    net_profit = gross_profit - total_fees
    net_profit_pct = (net_profit / buy['total_paid']) * 100
    
    return {
        'buy_slippage_price': buy_slippage_price,
        'sell_slippage_price': sell_slippage_price,
        'buy_cost': buy,
        'sell_cost': sell,
        'gross_profit': round(gross_profit, 2),
        'total_fees': round(total_fees, 2),
        'net_profit': round(net_profit, 2),
        'net_profit_pct': round(net_profit_pct, 4),
    }


# === 单元测试 ===
if __name__ == '__main__':
    # 测试沪市买入
    print("=== 测试沪市买入 ===")
    buy = calc_buy_cost(10.0, 1000, '600001')
    print(f"价格=10.0, 股数=1000")
    print(f"  成交金额: {buy['amount']}")
    print(f"  佣金: {buy['commission']} (万3，最低5元，实际3元，取5)")
    print(f"  过户费: {buy['transfer_fee']} (沪市0.001%)")
    print(f"  总费用: {buy['total_cost']}")
    print(f"  实际支付: {buy['total_paid']}")
    assert buy['commission'] == 5.0, f"佣金应为5元，实际{buy['commission']}"
    assert buy['transfer_fee'] == 0.1, f"过户费应为0.1元，实际{buy['transfer_fee']}"
    
    # 测试深市卖出
    print("\n=== 测试深市卖出 ===")
    sell = calc_sell_cost(10.0, 1000, '000001')
    print(f"价格=10.0, 股数=1000")
    print(f"  成交金额: {sell['amount']}")
    print(f"  佣金: {sell['commission']}")
    print(f"  过户费: {sell['transfer_fee']} (深市免收)")
    print(f"  印花税: {sell['stamp_tax']} (卖出0.05%)")
    print(f"  总费用: {sell['total_cost']}")
    print(f"  实际收到: {sell['total_received']}")
    assert sell['stamp_tax'] == 5.0, f"印花税应为5元，实际{sell['stamp_tax']}"
    assert sell['transfer_fee'] == 0.0, f"深市过户费应为0，实际{sell['transfer_fee']}"
    
    # 测试完整盈亏计算
    print("\n=== 测试完整盈亏计算 ===")
    profit = calc_trade_profit(10.0, 10.5, 1000, '600001')
    print(f"买入价=10.0, 卖出价=10.5, 股数=1000")
    print(f"  买入滑点价: {profit['buy_slippage_price']} (+0.1%)")
    print(f"  卖出滑点价: {profit['sell_slippage_price']} (-0.1%)")
    print(f"  毛利润: {profit['gross_profit']}")
    print(f"  总费用: {profit['total_fees']}")
    print(f"  净利润: {profit['net_profit']}")
    print(f"  净利润率: {profit['net_profit_pct']:.2f}%")
    
    print("\n✅ 所有测试通过")
