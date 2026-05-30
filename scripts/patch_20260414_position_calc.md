"""
修复问题1：Phase B 仓位计算错误

问题：buy_shares = int(BUY_AMOUNT / buy_price / 100) * 100
     其中 BUY_AMOUNT = TOTAL_CAPITAL (10万)
     导致：计划投入1万，实际买入9.66万

修复：从 sim_trades.buy_amount 读取计划投入金额，准确计算股数

文件：skills/stock_sim_robot/trade_runner.py
行号：1004
"""

# ══════════════════════════════════════════════════════════════════════════════
# 原代码（第 1004-1006 行）
# ══════════════════════════════════════════════════════════════════════════════
"""
        buy_shares = int(BUY_AMOUNT / buy_price / 100) * 100
        if buy_shares <= 0:
            buy_shares = 100
"""

# ══════════════════════════════════════════════════════════════════════════════
# 修复后代码
# ══════════════════════════════════════════════════════════════════════════════
"""
        # ── 修复：从 DB 读取计划投入金额，而非使用总本金 ───────────────
        _buy_amount = float(pos.get("buy_amount") or TOTAL_CAPITAL)
        buy_shares = int(_buy_amount / buy_price / 100) * 100
        if buy_shares <= 0:
            buy_shares = 100
        
        # 日志记录实际投入金额（便于审计）
        _actual_amount = buy_shares * buy_price
        logger.info(
            f"  仓位计算：计划投入={_buy_amount:.0f}元，"
            f"实际投入={_actual_amount:.0f}元（{buy_shares}股×{buy_price:.3f}元）"
        )
"""

# ══════════════════════════════════════════════════════════════════════════════
# 同时需要修复交易流水记录中的金额计算
# 文件：skills/stock_sim_robot/trade_runner.py
# 行号：~1055（log_trade_event 调用处）
# ══════════════════════════════════════════════════════════════════════════════
"""
原：log_trade_event(..., amount=buy_price * buy_shares, ...)
改：log_trade_event(..., amount=_actual_amount, ...)  # 使用实际成交金额
"""

# ══════════════════════════════════════════════════════════════════════════════
# 测试验证
# ══════════════════════════════════════════════════════════════════════════════
"""
修复后验证步骤：
1. 在 sim_trades 表中手动设置一条记录的 buy_amount=10000
2. 运行 Phase B（次日09:35）
3. 检查 buy_shares 是否接近 10000 / buy_price 的整数倍
4. 检查 sim_trade_events.amount 是否 ≈ buy_shares × buy_price

预期结果（buy_price=46.01）：
  buy_shares = int(10000 / 46.01 / 100) * 100 = 200 股
  actual_amount = 200 × 46.01 = 9202 元（接近1万，误差来自100股取整）
"""
