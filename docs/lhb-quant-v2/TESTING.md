# 龙虎榜量化交易系统 v2 - 测试策略

> 版本: 2.1
> 状态: 待实现
> 最后修订: 2026-04-16

---

## 〇、评审采纳说明

> 二次评审修订日期: 2026-04-16

### 已采纳的改进项 ✅

| 评审问题 | 采纳的改进 |
|----------|------------|
| 单元测试与评审后公式不匹配 | 分为行为测试（公式无关）和数值测试（公式版本绑定）两层 |
| 关键业务场景测试覆盖不足 | 补充冷启动、多档触发、零股、T+1、降级、状态机、风控等边界测试 |
| 集成测试缺少数据源测试 | 新增数据源适配器集成测试章节 |
| E2E测试不可复现 | 改用固定数据集，mock实时价格，确保每次运行结果一致 |
| 缺少性能测试 | 新增性能测试章节（监控延迟、评分引擎、API响应、数据库查询） |
| 测试环境定义不完整 | 补充完整 conftest.py（Redis、API Client、数据工厂等） |
| CI/CD集成缺失 | 新增 pytest.ini、Makefile 命令、分组执行策略 |
| 断言精度问题 | 统一使用 pytest.approx，定义精度规范 |
| 测试目录结构不完整 | 补充完整测试文件清单 |
| 缺少文档交叉引用 | 新增"评审问题→测试用例"映射表 |

### 不采纳的建议及原因 ❌

| 评审建议 | 不采纳原因 |
|----------|------------|
| 无 | 本次评审所有高/中优先级问题均合理，已采纳 |

---

## 一、测试分层

### 1.1 测试金字塔

```
                    ┌─────────────────┐
                    │   E2E 测试      │     5%
                    │  (端到端)       │
                    └────────┬────────┘
                             │
                    ┌────────┴────────┐
                    │   集成测试      │    15%
                    │  (模块交互)     │
                    └────────┬────────┘
                             │
         ┌───────────────────┴───────────────────┐
         │              单元测试               │    80%
         │           (核心计算逻辑)             │
         └───────────────────────────────────────┘
```

### 1.2 各层测试范围

| 层级 | 覆盖范围 | 测试工具 | 目标覆盖率 |
|------|----------|----------|------------|
| 单元测试 | 评分计算、费用计算、止损止盈判断、状态转换 | pytest | 90% |
| 集成测试 | API 端点、数据库操作、数据源适配器、缓存操作 | pytest + httpx + fakeredis | 75% |
| E2E 测试 | 完整交易流程、模拟盘→实盘、紧急操作 | pytest + 固定数据集 | 关键路径 100% |
| 性能测试 | 监控延迟、评分引擎、API响应、数据库查询 | pytest + time | 阈值校验 |

---

## 二、单元测试

### 2.1 评分引擎测试（修订版）

> **核心原则**：行为测试不依赖具体公式，数值测试绑定公式版本

```python
# tests/unit/test_scorer.py

class TestScorerBehavior:
    """
    评分引擎行为测试（公式无关）
    
    这些测试验证"方向性"和"范围"，不依赖具体公式数值。
    即使评审后修改了公式，这些测试仍然有效。
    """
    
    def test_tech_score_range(self):
        """所有技术面评分在 0-100 范围内"""
        test_inputs = [-10, -5, -3, 0, 1, 3, 5, 7, 10, 15, 20]
        for change_pct in test_inputs:
            score = calc_change_score(change_pct)
            assert 0 <= score <= 100, f"change_pct={change_pct}, score={score}"
    
    def test_tech_score_monotonic_in_normal_range(self):
        """正常范围内（0%~7%）评分单调递增"""
        scores = [calc_change_score(p) for p in [0, 1, 3, 5, 7]]
        assert scores == sorted(scores), f"非单调: {scores}"
    
    def test_negative_lower_than_positive(self):
        """负涨幅得分低于正涨幅"""
        assert calc_change_score(-3) < calc_change_score(3)
        assert calc_change_score(-5) < calc_change_score(0)
    
    def test_limit_up_not_highest(self):
        """涨停板不应得最高分（评审建议：降权处理）"""
        score_normal = calc_change_score(7.0)   # 正常涨幅
        score_limit = calc_change_score(10.0)  # 涨停
        assert score_limit <= score_normal, f"涨停分{score_limit}应低于正常{score_normal}"
    
    def test_turnover_score_range(self):
        """换手率评分在 0-100 范围内"""
        for rate in [1, 5, 10, 15, 20, 30, 50]:
            score = calc_turnover_score(rate)
            assert 0 <= score <= 100
    
    def test_fund_score_range(self):
        """资金面评分在 0-100 范围内"""
        for net_buy in [5_000_000, 50_000_000, 500_000_000]:
            score = calc_net_buy_score(net_buy)
            assert 0 <= score <= 100
    
    def test_weight_sum_is_one(self):
        """权重之和为1"""
        from app.services.scorer import TECH_WEIGHT, FUND_WEIGHT, HIST_WEIGHT
        assert abs(TECH_WEIGHT + FUND_WEIGHT + HIST_WEIGHT - 1.0) < 0.001
    
    def test_final_score_range(self):
        """最终评分在 0-100 范围内（参数化测试）"""
        import itertools
        test_cases = list(itertools.product([0, 50, 100], repeat=3))
        for tech, fund, hist in test_cases:
            score = calc_final_score(tech=tech, fund=fund, hist=hist)
            assert 0 <= score <= 100, f"inputs=({tech},{fund},{hist}), score={score}"
    
    def test_hist_score_no_data_returns_neutral(self):
        """历史面无数据 → 返回中性值50"""
        score = calc_hist_score(stock_code="999999", sample_count=0)
        assert score == 50
    
    def test_hist_score_shrinkage(self):
        """历史面样本不足 → 向中性值收缩"""
        raw_score = 80  # 假设原始评分
        for count in [1, 3, 5, 10, 20]:
            shrunk = apply_hist_shrinkage(raw_score, sample_count=count)
            assert 50 <= shrunk <= 80  # 向50收缩
            if count < 5:
                assert shrunk < 70  # 少样本时收缩明显
            if count >= 20:
                assert shrunk == pytest.approx(raw_score, abs=2)  # 充足样本基本不收缩
    
    def test_nan_input_handling(self):
        """NaN 输入不应崩溃"""
        score = calc_change_score(float('nan'))
        assert 0 <= score <= 100  # 应有默认处理


class TestScorerValues:
    """
    评分具体数值测试（公式版本：v2.1-revised）
    
    这些测试验证精确数值，与具体公式绑定。
    公式变更时必须更新这些测试！
    
    公式版本标注：在 app/services/scorer.py 头部注释
    """
    
    # 当前公式版本（修改公式时必须更新此处）
    FORMULA_VERSION = "2.1-revised"
    
    def test_change_score_specific_v21(self):
        """涨幅评分具体值（v2.1公式）"""
        # 这些值来自 STRATEGY.md 公式，公式修改时需同步更新
        assert calc_change_score(0.0) == pytest.approx(40, abs=2)
        assert calc_change_score(3.0) == pytest.approx(70, abs=3)
        assert calc_change_score(7.0) == pytest.approx(100, abs=2)
        assert calc_change_score(10.0) == pytest.approx(80, abs=3)  # 涨停降权
    
    def test_turnover_score_specific_v21(self):
        """换手率评分具体值（v2.1公式）"""
        assert calc_turnover_score(5.0) == pytest.approx(60, abs=3)
        assert calc_turnover_score(15.0) == pytest.approx(80, abs=3)
        assert calc_turnover_score(30.0) == pytest.approx(100, abs=2)


class TestScoreEdgeCases:
    """评分边界场景"""
    
    def test_all_extreme_values(self):
        """所有因子都是极端值 → 最终分数仍在 0-100"""
        score = calc_final_score(tech=0, fund=0, hist=0)
        assert 0 <= score <= 100
        
        score = calc_final_score(tech=100, fund=100, hist=100)
        assert 0 <= score <= 100
    
    def test_mixed_extreme_values(self):
        """混合极端值"""
        score = calc_final_score(tech=100, fund=0, hist=50)
        assert 0 <= score <= 100
```

### 2.2 AHS 评分测试（修订版）

```python
# tests/unit/test_ahs.py

class TestAHSBehavior:
    """
    AHS 行为测试（公式无关）
    
    参见 RISK_CONTROL.md 第三章修订后的公式：
    - 0 = 最健康，100 = 最危险
    - 使用 max_drawdown 替代 position_mult 作为输入
    """
    
    def test_ahs_score_range(self):
        """AHS 评分在 0-100 范围内"""
        test_cases = [
            (1.0, 0.10, 0, 0.0),    # 最健康
            (0.0, -0.10, 5, 0.20),  # 最危险
            (0.5, 0.0, 2, 0.05),    # 一般
        ]
        for win_rate, avg_pnl, consec_loss, max_dd in test_cases:
            score = calc_ahs_score(win_rate, avg_pnl, consec_loss, max_dd)
            assert 0 <= score <= 100, f"inputs=({win_rate},{avg_pnl},{consec_loss},{max_dd}), score={score}"
    
    def test_worse_metrics_higher_score(self):
        """更差的指标 → 更高的 AHS（更危险）"""
        healthy = calc_ahs_score(win_rate=0.7, avg_pnl=0.05, consec_loss=0, max_dd=0.0)
        unhealthy = calc_ahs_score(win_rate=0.3, avg_pnl=-0.05, consec_loss=4, max_dd=0.15)
        assert unhealthy > healthy, f"危险情况分数{unhealthy}应高于健康情况{healthy}"
    
    def test_position_mapping_monotonic(self):
        """AHS 越高，仓位系数越低"""
        scores = [0, 20, 40, 60, 80, 100]
        positions = [get_position_mult(s) for s in scores]
        assert positions == sorted(positions, reverse=True), f"非单调递减: {positions}"
    
    def test_position_with_hysteresis(self):
        """滞后带测试：进入停仓需 AHS>80，退出需 AHS≤60"""
        # 进入停仓
        assert get_position_mult(81) == 0.0
        assert get_position_mult(79) == 0.0  # 仍在带内
        
        # 恢复：需降到60以下
        assert get_position_mult(60) == 0.0   # 仍在带内
        assert get_position_mult(59) == 0.40  # 恢复到40%
    
    def test_cold_start_no_history(self):
        """冷启动：无历史数据 → 固定50分"""
        score = calc_ahs_score_for_new_robot(trade_count=0)
        assert score == 50
    
    def test_cold_start_uncertainty_penalty(self):
        """冷启动：样本不足 → 不确定性惩罚"""
        score_5 = calc_ahs_score_for_new_robot(trade_count=5, base_score=30)
        score_10 = calc_ahs_score_for_new_robot(trade_count=10, base_score=30)
        score_20 = calc_ahs_score_for_new_robot(trade_count=20, base_score=30)
        
        # 5笔样本惩罚最多，20笔无惩罚
        assert score_5 > score_10 > score_20


class TestAHSValues:
    """
    AHS 具体数值测试（公式版本：v2.1-revised）
    
    公式来源：RISK_CONTROL.md 第三章
    """
    
    FORMULA_VERSION = "2.1-revised"
    
    def test_ahs_calculation_v21(self):
        """AHS 计算具体值（v2.1公式）"""
        # 最健康情况
        score = calc_ahs_score(win_rate=1.0, avg_pnl=0.10, consec_loss=0, max_dd=0.0)
        assert score == pytest.approx(0, abs=5)
        
        # 最危险情况
        score = calc_ahs_score(win_rate=0.0, avg_pnl=-0.10, consec_loss=5, max_dd=0.20)
        assert score == pytest.approx(100, abs=5)
        
        # 一般情况
        score = calc_ahs_score(win_rate=0.5, avg_pnl=0.0, consec_loss=2, max_dd=0.05)
        assert 30 <= score <= 70
```

### 2.3 止损止盈边界测试

```python
# tests/unit/test_risk.py

class TestSellEdgeCases:
    """卖出边界场景测试"""
    
    def test_gap_up_beyond_sell3(self):
        """跳空高开超过 sell3 → 全部卖出"""
        trade = create_test_trade(buy_price=10.0, buy_shares=1000)
        # 开盘价直接超过 sell3 (10.8)
        actions = check_sell_signals(trade, current_price=11.5)
        assert actions == [{"type": "sell_3", "shares": 1000}]
    
    def test_sell1_then_stop_loss(self):
        """sell1 后触发止损"""
        trade = create_test_trade(
            buy_price=10.0, 
            buy_shares=1000,
            sell1_done=True,  # 已卖300股
            remaining_shares=700
        )
        # 价格跌到止损线以下
        actions = check_sell_signals(trade, current_price=9.4)
        assert actions == [{"type": "stop_loss", "shares": 700}]
    
    def test_sell2_then_sell3(self):
        """sell2 后 sell3 触发"""
        trade = create_test_trade(
            buy_price=10.0,
            buy_shares=1000,
            sell1_done=True,
            sell2_done=True,
            remaining_shares=400
        )
        actions = check_sell_signals(trade, current_price=10.9)  # 超过 sell3
        assert actions == [{"type": "sell_3", "shares": 400}]
    
    def test_odd_lot_calculation(self):
        """零股卖出：分档后剩余不足100股"""
        # 1000股分三档
        shares_1 = calc_sell_shares(total=1000, tier=1)  # 300
        shares_2 = calc_sell_shares(total=1000, tier=2)  # 300
        shares_3 = calc_sell_shares(total=1000, tier=3)  # 400（含零股）
        assert shares_1 == 300
        assert shares_2 == 300
        assert shares_3 == 400
        assert shares_1 + shares_2 + shares_3 == 1000
        
        # 500股分三档
        shares_1 = calc_sell_shares(total=500, tier=1)   # 150
        shares_2 = calc_sell_shares(total=500, tier=2)   # 150
        shares_3 = calc_sell_shares(total=500, tier=3)   # 200
        assert shares_1 + shares_2 + shares_3 == 500
    
    def test_limit_up_cannot_sell(self):
        """涨停板无法卖出 → 挂单等待"""
        trade = create_test_trade(buy_price=10.0, buy_shares=1000)
        # 当前涨停价，无法成交
        actions = check_sell_signals(trade, current_price=11.0, is_limit_up=True)
        # 应标记为挂单而非立即成交
        if actions:  # 如果有卖出信号
            assert actions[0].get("pending", False) is True
    
    def test_t_plus_1_restriction(self):
        """T+1 限制：买入当日不可卖出"""
        trade = create_test_trade(
            buy_price=10.0, 
            buy_shares=1000,
            buy_date=date.today()  # 今日买入
        )
        # 当日即使价格触发止盈也不卖出
        actions = check_sell_signals(trade, current_price=10.5)
        assert actions == []  # T+1 当日无操作
    
    def test_t_plus_1_next_day_can_sell(self):
        """T+1 次日可卖出"""
        yesterday = date.today() - timedelta(days=1)
        trade = create_test_trade(
            buy_price=10.0,
            buy_shares=1000,
            buy_date=yesterday
        )
        actions = check_sell_signals(trade, current_price=10.5)
        assert len(actions) > 0  # 次日可触发
    
    def test_multiple_sell_tiers_simultaneous(self):
        """多档止盈同时触发（跳空高开）"""
        trade = create_test_trade(buy_price=10.0, buy_shares=1000)
        # 开盘价直接超过 sell2
        actions = check_sell_signals(trade, current_price=10.6)
        # 应卖出 sell1 + sell2，或直接全部卖出？
        # 根据实际业务规则决定
        total_sell = sum(a.get("shares", 0) for a in actions)
        assert total_sell in [600, 1000]  # 至少卖出前两档


class TestStopLossEdgeCases:
    """止损边界场景"""
    
    def test_stop_loss_at_limit_down(self):
        """跌停板触发止损 → 挂跌停价等待"""
        trade = create_test_trade(buy_price=10.0, buy_shares=1000)
        actions = check_sell_signals(
            trade, 
            current_price=9.0, 
            is_limit_down=True
        )
        if actions:
            assert actions[0].get("type") == "stop_loss"
            assert actions[0].get("pending", False) is True
    
    def test_stop_loss_partial_execution(self):
        """止损部分成交"""
        # 如果市场流动性不足，可能只能卖出部分
        pass


class TestCommissionCalculation:
    """费用计算测试"""
    
    def test_normal_commission(self):
        """正常佣金 0.03%"""
        result = calc_commission(amount=10000, rate=0.0003)
        assert result == pytest.approx(3.0, abs=0.01)
    
    def test_minimum_commission(self):
        """最低佣金 5 元"""
        result = calc_commission(amount=5000, rate=0.0003, min_amount=5)
        assert result == pytest.approx(5.0, abs=0.01)
    
    def test_high_commission(self):
        """大额交易"""
        result = calc_commission(amount=1000000, rate=0.0003)
        assert result == pytest.approx(300.0, abs=0.1)
    
    def test_total_buy_fees(self):
        """买入总费用 = 佣金 + 过户费"""
        result = calc_total_buy_fees(amount=100000)
        expected = 100000 * (0.0003 + 0.00001)  # 30 + 1 = 31
        assert result == pytest.approx(expected, abs=0.5)
    
    def test_total_sell_fees(self):
        """卖出总费用 = 佣金 + 印花税 + 过户费"""
        result = calc_total_sell_fees(amount=100000)
        expected = 100000 * (0.0003 + 0.0005 + 0.00001)  # 30 + 50 + 1 = 81
        assert result == pytest.approx(expected, abs=0.5)
```

### 2.4 状态机测试

```python
# tests/unit/test_state_machine.py

class TestTradeStateTransitions:
    """交易状态转换测试"""
    
    @pytest.mark.parametrize("from_status,to_status,expected", [
        ("pending", "open", True),
        ("open", "partial", True),
        ("partial", "closed", True),
        ("open", "closed", True),
        ("pending", "closed", False),  # 非法：跳过 open
        ("closed", "open", False),     # 非法：已关闭不能重开
        ("pending", "partial", False), # 非法：跳过 open
    ])
    def test_valid_transitions(self, from_status, to_status, expected):
        """合法/非法状态转换"""
        result = is_valid_transition(from_status, to_status)
        assert result == expected
    
    def test_transition_with_illegal_status(self):
        """非法状态值"""
        with pytest.raises(ValueError):
            is_valid_transition("invalid", "open")
    
    def test_all_legal_paths_covered(self):
        """所有合法路径"""
        legal_paths = [
            ["pending", "open", "partial", "closed"],
            ["pending", "open", "closed"],
            ["pending", "cancelled"],
        ]
        for path in legal_paths:
            for i in range(len(path) - 1):
                assert is_valid_transition(path[i], path[i+1])
    
    def test_concurrent_transition_protection(self):
        """并发状态转换保护"""
        # 使用数据库锁测试
        pass


class TestStateMachineIntegration:
    """状态机集成测试"""
    
    async def test_transition_persists(self, db_session):
        """状态转换持久化"""
        trade = await create_test_trade(db_session, status="pending")
        
        await transition_trade_status(db_session, trade.id, "open")
        
        await db_session.refresh(trade)
        assert trade.status == "open"
    
    async def test_illegal_transition_raises(self, db_session):
        """非法状态转换抛异常"""
        trade = await create_test_trade(db_session, status="closed")
        
        with pytest.raises(IllegalTransitionError):
            await transition_trade_status(db_session, trade.id, "open")
```

### 2.5 风控边界测试

```python
# tests/unit/test_risk_limits.py

class TestHardLimits:
    """系统级硬性限制测试"""
    
    def test_single_position_limit_reject(self):
        """单笔超过总资产20% → 拒绝"""
        robot = create_test_robot(total_assets=100000)
        result = check_buy_limits(robot, buy_amount=25000)
        assert result.rejected is True
        assert "单笔上限" in result.reason
        assert result.limit_value == 20000
    
    def test_single_position_limit_pass(self):
        """单笔刚好20% → 通过"""
        robot = create_test_robot(total_assets=100000)
        result = check_buy_limits(robot, buy_amount=20000)
        assert result.rejected is False
    
    def test_daily_trade_limit_reject(self):
        """单日累计超过50% → 拒绝"""
        robot = create_test_robot(total_assets=100000)
        mock_today_buys(robot.id, total_buy=45000)
        
        result = check_buy_limits(robot, buy_amount=10000)
        assert result.rejected is True
        assert "单日上限" in result.reason
    
    def test_concentration_limit_reject(self):
        """单只持仓超过30% → 拒绝"""
        robot = create_test_robot(total_assets=100000)
        # 已持有某股票25000元
        mock_existing_position(robot.id, stock_code="000001", value=25000)
        
        # 再买入10000元同一股票
        result = check_buy_limits(robot, buy_amount=10000, stock_code="000001")
        assert result.rejected is True
        assert "集中度" in result.reason
    
    def test_cumulative_loss_stop(self):
        """累计亏损触发停机"""
        robot = create_test_robot(
            initial_capital=100000,
            current_assets=78000  # 缩水22%
        )
        result = check_cumulative_loss(robot)
        assert result.should_stop is True
        assert result.loss_pct == pytest.approx(0.22, abs=0.01)
    
    def test_cumulative_loss_hysteresis(self):
        """累计亏损恢复滞后"""
        robot = create_test_robot(
            initial_capital=100000,
            current_assets=82000,  # 缩水18%
            trading_mode="stopped"
        )
        # 缩水18%，在恢复区间（15%-20%）
        result = check_cumulative_loss(robot)
        # 应该保持停止状态（滞后带）
        assert result.should_remain_stopped is True


class TestAHSPositionMapping:
    """AHS 仓位映射测试"""
    
    @pytest.mark.parametrize("ahs_score,expected_position", [
        (0, 1.00),    # 最健康 → 100%
        (15, 1.00),   # 正常区间
        (30, 0.85),   # 谨慎区间
        (50, 0.70),   # 保守区间
        (70, 0.40),   # 警戒区间
        (85, 0.00),   # 停止开仓
        (100, 0.00),  # 最危险
    ])
    def test_position_mapping(self, ahs_score, expected_position):
        """AHS → 仓位系数映射"""
        position = get_position_mult(ahs_score)
        assert position == pytest.approx(expected_position, abs=0.05)
    
    def test_hysteresis_entry_exit(self):
        """滞后带进入/退出"""
        # 进入停仓：AHS > 80
        pos = get_position_mult(81)
        assert pos == 0.0
        
        # 在滞后带内：75 < AHS < 85
        pos = get_position_mult(78)
        assert pos == 0.0  # 仍停止
        
        # 退出停仓：AHS ≤ 60
        pos = get_position_mult(59)
        assert pos == pytest.approx(0.40, abs=0.05)


class TestPositionCalculation:
    """仓位计算测试"""
    
    def test_exact_100_shares(self):
        """资金刚好够买100股"""
        robot = create_test_robot(cash_balance=1000)
        price = 10.0
        shares = calc_max_shares(robot, price)
        assert shares == 100
    
    def test_insufficient_for_100_shares(self):
        """资金不足100股"""
        robot = create_test_robot(cash_balance=900)
        price = 10.0
        shares = calc_max_shares(robot, price)
        assert shares == 0
    
    def test_pending_frozen_cash(self):
        """pending 冻结资金后可用不足"""
        robot = create_test_robot(cash_balance=20000)
        # 已有一个 pending 交易，冻结10000
        mock_pending_trade(robot.id, frozen_amount=10000)
        
        # 剩余可用 10000，可买 1000 股 @ 10元
        shares = calc_max_shares(robot, price=10.0, consider_pending=True)
        assert shares == 1000
    
    def test_ahs_zero_position(self):
        """AHS 仓位系数为 0 → 不能开仓"""
        robot = create_test_robot(cash_balance=100000)
        with mock.patch('get_position_mult', return_value=0.0):
            shares = calc_max_shares(robot, price=10.0, ahs_score=90)
            assert shares == 0
    
    def test_star_market_200_shares(self):
        """科创板最小单位200股"""
        shares = calc_max_shares_for_board(board="star", price=10.0, cash=5000)
        assert shares in [0, 200, 400, ...]  # 只能是200的倍数
```

---

## 三、集成测试

### 3.1 API 端点测试

```python
# tests/integration/api/test_robots.py

class TestRobotsAPI:
    """机器人管理 API 测试"""
    
    async def test_create_robot(self, client):
        """创建机器人"""
        response = await client.post("/api/v1/robots", json={
            "name": "Test Robot",
            "strategy_name": "lhb_multifactor",
            "initial_capital": 100000
        })
        assert response.status_code == 201
        data = response.json()
        assert data["code"] == 0
        assert "id" in data["data"]
    
    async def test_create_robot_validation(self, client):
        """创建机器人参数校验"""
        response = await client.post("/api/v1/robots", json={
            "name": "",  # 非法：空名称
            "initial_capital": -100  # 非法：负数
        })
        assert response.status_code == 400
        data = response.json()
        assert data["code"] == 1001
        assert "errors" in data
    
    async def test_delete_robot_with_positions(self, client, robot_factory, trade_factory):
        """删除有持仓的机器人"""
        robot = await robot_factory()
        await trade_factory(robot_id=robot.id, status="open")
        
        response = await client.delete(f"/api/v1/robots/{robot.id}")
        assert response.status_code == 422
        data = response.json()
        assert data["code"] == 3002  # 危险操作需确认
    
    async def test_delete_robot_confirmed(self, client, robot_factory, trade_factory):
        """确认删除有持仓的机器人"""
        robot = await robot_factory()
        await trade_factory(robot_id=robot.id, status="open")
        
        response = await client.delete(f"/api/v1/robots/{robot.id}?confirm=true")
        assert response.status_code == 200


class TestTradesAPI:
    """交易 API 测试"""
    
    async def test_close_trade(self, client, trade_factory):
        """平仓单个交易"""
        trade = await trade_factory(status="open")
        
        response = await client.post(f"/api/v1/trades/{trade.id}/close", json={
            "reason": "测试平仓"
        })
        assert response.status_code == 200
        data = response.json()
        assert data["data"]["status"] == "closed"
    
    async def test_close_trade_idempotent(self, client, trade_factory):
        """平仓幂等性"""
        trade = await trade_factory(status="open")
        
        response1 = await client.post(f"/api/v1/trades/{trade.id}/close", json={"reason": "1"})
        response2 = await client.post(f"/api/v1/trades/{trade.id}/close", json={"reason": "2"})
        
        assert response1.status_code == 200
        assert response2.status_code == 200  # 幂等，不报错
```

### 3.2 数据库操作测试

```python
# tests/integration/test_database.py

class TestDatabaseOperations:
    """数据库操作测试"""
    
    async def test_cash_ledger_integrity(self, db_session, robot_factory):
        """现金流水完整性"""
        robot = await robot_factory(initial_capital=100000)
        
        # 执行一系列操作
        await execute_buy(robot.id, amount=50000)
        await execute_sell(robot.id, amount=30000)
        
        # 验证现金流水
        ledger = await get_cash_ledger(db_session, robot.id)
        assert sum(entry.amount for entry in ledger) == pytest.approx(-20000, abs=0.1)
        
        # 验证余额
        await db_session.refresh(robot)
        assert robot.cash_balance == pytest.approx(50000, abs=0.1)
    
    async def test_trade_audit_trail(self, db_session, trade_factory):
        """交易审计跟踪"""
        trade = await trade_factory(status="open")
        
        # 执行卖出
        await execute_sell(trade.id, "sell_1")
        
        # 验证 trade_events
        events = await get_trade_events(db_session, trade.id)
        assert len(events) >= 1
        assert events[0].event_type == "sell_1"
        
        # 验证 trade_executions
        executions = await get_executions(db_session, trade.id)
        assert len(executions) >= 1
    
    async def test_concurrent_update_protection(self, db_session, trade_factory):
        """并发更新保护"""
        trade = await trade_factory(status="open")
        
        # 模拟两个并发卖出
        async def sell_task():
            async with db_session.begin():
                await execute_sell_with_lock(trade.id, "sell_1")
        
        results = await asyncio.gather(
            sell_task(), sell_task(),
            return_exceptions=True
        )
        
        # 一个成功，一个幂等或失败
        successes = [r for r in results if not isinstance(r, Exception)]
        assert len(successes) == 1
```

### 3.3 数据源适配器集成测试

```python
# tests/integration/test_data_providers.py

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

class TestTushareIntegration:
    """TuShare 适配器集成测试"""
    
    @pytest.mark.skipif(not os.getenv("TUSHARE_TOKEN"), reason="无 TuShare Token")
    @pytest.mark.requires_tushare
    async def test_fetch_kline(self, tushare_provider):
        """获取K线数据"""
        df = await tushare_provider.fetch(
            "DAILY_KLINE", 
            stock_code="000001",
            start_date="2025-01-01", 
            end_date="2025-01-10"
        )
        assert len(df) > 0
        assert "close_price" in df.columns  # 规范化后的字段名
        assert df["close_price"].notna().all()
    
    @pytest.mark.requires_tushare
    async def test_rate_limit_respected(self, tushare_provider):
        """频率限制不被触发"""
        for i in range(5):
            df = await tushare_provider.fetch(
                "DAILY_KLINE",
                stock_code="000001",
                start_date="2025-01-01",
                end_date="2025-01-02"
            )
            assert len(df) > 0
            await asyncio.sleep(0.3)  # 间隔300ms
    
    @pytest.mark.requires_tushare
    async def test_normalization_correctness(self, tushare_provider):
        """数据规范化正确性"""
        df = await tushare_provider.fetch(
            "DAILY_KLINE",
            stock_code="000001",
            start_date="2025-01-01",
            end_date="2025-01-10"
        )
        
        # 验证字段名规范化
        assert "stock_code" in df.columns
        assert "trade_date" in df.columns
        assert "open_price" in df.columns
        assert "close_price" in df.columns
        
        # 验证数据格式
        assert df["stock_code"].str.match(r"^\d{6}$").all()
        assert df["close_price"].dtype in [float, "float64"]
        assert (df["high_price"] >= df["low_price"]).all()


class TestAKShareIntegration:
    """AKShare 适配器集成测试"""
    
    @pytest.mark.requires_akshare
    async def test_fetch_lhb(self, akshare_provider):
        """获取龙虎榜数据"""
        df = await akshare_provider.fetch("LHB_DETAIL", trade_date="2025-01-02")
        assert len(df) > 0
        assert "stock_code" in df.columns
        assert "net_buy_amount" in df.columns
    
    @pytest.mark.requires_akshare
    async def test_fetch_realtime_quote(self, akshare_provider):
        """获取实时行情"""
        df = await akshare_provider.fetch("REALTIME_QUOTE", stock_code="000001")
        assert len(df) > 0
        assert "current_price" in df.columns
        assert "change_ratio" in df.columns


class TestProviderManagerIntegration:
    """降级链管理器集成测试"""
    
    async def test_fallback_chain(self, provider_manager):
        """降级链端到端测试"""
        result = await provider_manager.get_data(
            "DAILY_KLINE",
            stock_code="000001",
            start_date="2025-01-01",
            end_date="2025-01-02"
        )
        assert result is not None
        assert len(result.data) > 0
        assert result.source in ["tushare", "akshare", "sina"]
    
    async def test_primary_timeout_fallback(self, provider_manager):
        """主数据源超时 → 降级"""
        with patch.object(provider_manager.providers["tushare"], "fetch", 
                          side_effect=asyncio.TimeoutError):
            result = await provider_manager.get_data("DAILY_KLINE", stock_code="000001")
            assert result.source != "tushare"
    
    async def test_all_providers_fail_with_cache(self, provider_manager, test_redis):
        """所有数据源失败 → 返回缓存"""
        # 先写入缓存
        await test_redis.set("prod:mkt:DAILY_KLINE:000001", '{"data": [...]}')
        
        with patch_all_providers_fail(provider_manager):
            result = await provider_manager.get_data("DAILY_KLINE", stock_code="000001")
            assert result.stale is True
            assert result.data is not None
    
    async def test_all_providers_fail_no_cache_raises(self, provider_manager):
        """所有数据源失败且无缓存 → 抛异常"""
        with patch_all_providers_fail(provider_manager):
            with pytest.raises(NoProviderAvailableError):
                await provider_manager.get_data("DAILY_KLINE", stock_code="999999")
    
    async def test_circuit_breaker_skip(self, provider_manager):
        """熔断器打开 → 跳过该数据源"""
        # 连续失败触发熔断
        for _ in range(3):
            with patch.object(provider_manager.providers["tushare"], "fetch",
                              side_effect=Exception("API Error")):
                try:
                    await provider_manager.get_data("DAILY_KLINE", stock_code="000001")
                except:
                    pass
        
        # 确认 tushare 被跳过
        with patch_provider_call_tracker() as tracker:
            await provider_manager.get_data("DAILY_KLINE", stock_code="000001")
            assert "tushare" not in tracker.called_providers
    
    async def test_data_validation_rejection(self, provider_manager):
        """数据校验失败 → 丢弃异常数据"""
        # Mock 返回异常数据
        with patch.object(provider_manager.providers["akshare"], "fetch",
                          return_value=create_invalid_kline_data()):
            result = await provider_manager.get_data("DAILY_KLINE", stock_code="000001")
            # 应降级到下一个数据源而非返回异常数据
            assert result.source != "akshare" or result.validation_passed is False
```

### 3.4 缓存集成测试

```python
# tests/integration/test_cache.py

class TestRedisCache:
    """Redis 缓存集成测试"""
    
    async def test_set_get(self, test_redis):
        """基本读写"""
        await test_redis.set("test_key", "test_value")
        value = await test_redis.get("test_key")
        assert value == "test_value"
    
    async def test_ttl(self, test_redis):
        """过期时间"""
        await test_redis.set("test_key", "test_value", ex=1)
        await asyncio.sleep(1.5)
        value = await test_redis.get("test_key")
        assert value is None
    
    async def test_key_pattern(self, test_redis):
        """Key 规范验证"""
        key = "prod:mkt:DAILY_KLINE:000001"
        assert ":" in key  # 多段分隔
        assert key.startswith("prod:")  # 环境前缀
```

---

## 四、E2E 测试（可复现版本）

### 4.1 固定数据集策略

> **核心原则**：E2E 测试使用固定数据集，不依赖外部状态，确保每次运行结果一致

```python
# tests/e2e/conftest.py

import pytest
from datetime import date

@pytest.fixture(autouse=True)
async def setup_fixed_dataset(db_session):
    """
    准备固定的测试数据集
    确保每次 E2E 测试运行结果一致
    """
    # 1. 固定的交易日历（覆盖测试周期）
    await insert_trade_calendar(db_session, [
        {"trade_date": "2025-01-02", "is_open": True},
        {"trade_date": "2025-01-03", "is_open": True},
        {"trade_date": "2025-01-06", "is_open": True},  # 周一
    ])
    
    # 2. 固定的龙虎榜数据（确保能选出至少1只股票）
    # 设计：000001 满足所有筛选条件
    await insert_lhb_data(db_session, [
        {
            "trade_date": "2025-01-02",
            "stock_code": "000001",
            "stock_name": "测试股A",
            "close_price": 10.00,
            "change_ratio": 0.05,        # 涨幅5%，正常区间
            "turnover_rate": 0.15,       # 换手率15%，适中
            "net_buy_amount": 80_000_000, # 净买入8000万，高
            "total_buy_amount": 120_000_000,
            "total_sell_amount": 40_000_000,
        },
        {
            "trade_date": "2025-01-02",
            "stock_code": "000002",
            "stock_name": "测试股B",
            "close_price": 20.00,
            "change_ratio": 0.10,        # 涨停，应降权
            "turnover_rate": 0.30,       # 换手率30%，过高
            "net_buy_amount": 50_000_000,
            "total_buy_amount": 100_000_000,
            "total_sell_amount": 50_000_000,
        },
    ])
    
    # 3. 固定的K线数据
    await insert_kline_data(db_session, [
        # 000001 的历史K线
        {"trade_date": "2025-01-02", "stock_code": "000001", 
         "open_price": 9.80, "close_price": 10.00, 
         "high_price": 10.10, "low_price": 9.70, "volume": 1000000},
        {"trade_date": "2025-01-03", "stock_code": "000001",
         "open_price": 10.10, "close_price": 10.50,
         "high_price": 10.60, "low_price": 10.00, "volume": 1200000},
        {"trade_date": "2025-01-06", "stock_code": "000001",
         "open_price": 10.50, "close_price": 11.00,
         "high_price": 11.10, "low_price": 10.40, "volume": 1500000},
    ])
    
    # 4. 固定的机器人配置
    await insert_robot(db_session, {
        "id": 1,
        "name": "E2E-Test-Robot",
        "strategy_id": 1,
        "initial_capital": 100000,
        "cash_balance": 100000,
        "status": "running",
    })
    
    yield
    
    # 清理（可选，测试数据库会自动回滚）
```

### 4.2 完整交易流程 E2E

```python
# tests/e2e/test_trading_flow.py

class TestTradingFlowE2E:
    """完整交易流程 E2E 测试（固定数据集）"""
    
    async def test_phase_a_selection(self, client, mock_clock):
        """Phase A 选股 → 应选出 000001"""
        # 设置当前时间为 Phase A 执行时间
        mock_clock.set("2025-01-02 18:50:00")
        
        response = await client.post("/api/v1/selection/trigger", json={
            "robot_id": 1,
            "trade_date": "2025-01-02",
        })
        assert response.status_code == 200
        
        # 验证选股结果
        selections = await get_selections_for_date("2025-01-02", robot_id=1)
        assert len(selections) >= 1
        # 000001 应被选中（满足条件），000002 应被过滤（涨停降权）
        assert any(s.stock_code == "000001" for s in selections)
    
    async def test_phase_b_buy(self, client, mock_clock, mock_realtime_price):
        """Phase B 买入 → 000001 变为 open"""
        # 先执行 Phase A
        await self.test_phase_a_selection(client, mock_clock)
        
        # 设置开盘价（固定价格，不依赖实时数据）
        mock_realtime_price.set({"000001": 10.10})
        mock_clock.set("2025-01-03 09:30:00")
        
        response = await client.post("/api/v1/actions/phase-b", json={
            "robot_id": 1,
            "trade_date": "2025-01-03",
        })
        
        assert response.status_code == 200
        trades = await get_open_trades(robot_id=1)
        assert len(trades) == 1
        assert trades[0].stock_code == "000001"
        assert trades[0].status == "open"
        assert trades[0].buy_price == pytest.approx(10.10, abs=0.01)
    
    async def test_monitor_sell1_trigger(self, client, mock_clock, mock_realtime_price):
        """盘中监控 → 触发 sell1"""
        await self.test_phase_b_buy(client, mock_clock, mock_realtime_price)
        
        trade = (await get_open_trades(robot_id=1))[0]
        sell1_price = trade.buy_price * 1.03  # 10.10 * 1.03 ≈ 10.40
        
        # 价格涨到 sell1
        mock_realtime_price.set({"000001": 10.50})
        mock_clock.set("2025-01-03 10:30:00")
        
        await trigger_monitoring(robot_id=1)
        
        await trade.refresh_from_db()
        assert trade.status == "partial"
        
        executions = await get_executions(trade.id)
        sell1_execs = [e for e in executions if e.execution_type == "sell_1"]
        assert len(sell1_execs) == 1
        assert sell1_execs[0].shares == 300  # 约 1/3
    
    async def test_settlement(self, client, mock_clock):
        """结算 → 净值计算"""
        await self.test_phase_b_buy(client, mock_clock, mock_realtime_price)
        
        mock_clock.set("2025-01-03 15:05:00")
        await trigger_settlement(robot_id=1)
        
        nav = await get_latest_nav(robot_id=1)
        assert nav is not None
        assert nav.total_assets > 0
    
    async def test_full_day_flow(self, client, mock_clock, mock_realtime_price):
        """完整一天流程"""
        # Day 1: Phase A 选股
        mock_clock.set("2025-01-02 18:50:00")
        await client.post("/api/v1/selection/trigger", json={
            "robot_id": 1, "trade_date": "2025-01-02"
        })
        
        # Day 2: Phase B 买入 + 监控 + 结算
        mock_realtime_price.set({"000001": 10.10})
        mock_clock.set("2025-01-03 09:30:00")
        await client.post("/api/v1/actions/phase-b", json={
            "robot_id": 1, "trade_date": "2025-01-03"
        })
        
        # 监控（价格涨触发 sell1）
        mock_realtime_price.set({"000001": 10.50})
        mock_clock.set("2025-01-03 10:30:00")
        await trigger_monitoring(robot_id=1)
        
        # 结算
        mock_clock.set("2025-01-03 15:05:00")
        await trigger_settlement(robot_id=1)
        
        # 验证最终状态
        nav = await get_latest_nav(robot_id=1)
        assert nav.total_assets > 100000  # 应该盈利
```

### 4.3 模拟盘到实盘切换 E2E

```python
# tests/e2e/test_paper_to_live.py

class TestPaperToLiveE2E:
    """模拟盘到实盘切换 E2E 测试"""
    
    async def test_paper_trading_qualification(self, client, mock_clock):
        """模拟盘评估 → 满足准入条件"""
        robot = await create_paper_robot(initial_capital=100000)
        
        # 模拟运行 20 个交易日
        for day in range(20):
            await run_paper_trading_day(robot.id, day_offset=day)
        
        # 检查准入条件
        stats = await get_robot_stats(robot.id)
        assert stats.trade_count >= 10
        assert stats.win_rate >= 0.5
        assert stats.max_drawdown <= 0.15
        assert stats.sharpe_ratio >= 1.0
    
    async def test_switch_to_live(self, client):
        """切换到实盘"""
        robot = await create_qualified_paper_robot()
        
        response = await client.post(f"/api/v1/robots/{robot.id}/switch-to-live")
        assert response.status_code == 200
        
        await robot.refresh_from_db()
        assert robot.status == "running"
        assert robot.running_start_date is not None
        assert robot.mode == "live_trading"
```

---

## 五、性能测试

### 5.1 监控延迟测试

```python
# tests/performance/test_latency.py

import time
import pytest

class TestMonitorPerformance:
    """盘中监控性能测试"""
    
    async def test_monitor_cycle_under_8s(self, db_session):
        """单次监控周期 < 8秒（10秒间隔 - 2秒余量）"""
        # 准备：5个机器人，每个持有3只股票 = 15只股票
        await setup_positions(db_session, robot_count=5, positions_per_robot=3)
        
        start = time.monotonic()
        await run_monitor_cycle()
        elapsed = time.monotonic() - start
        
        assert elapsed < 8.0, f"监控周期耗时 {elapsed:.2f}秒，超过8秒阈值"
        
        # 记录性能基线
        pytest.performance_baseline = {
            "monitor_cycle": elapsed
        }
    
    async def test_price_fetch_batch(self, provider_manager):
        """实时行情批量获取（15只）< 2秒"""
        stocks = [f"00000{i}" for i in range(15)]
        
        start = time.monotonic()
        prices = await provider_manager.get_realtime_quotes(stocks)
        elapsed = time.monotonic() - start
        
        assert elapsed < 2.0
        assert len(prices) == 15
    
    async def test_sell_signal_calculation(self):
        """卖出信号计算（15只）< 1秒"""
        trades = [create_test_trade() for _ in range(15)]
        
        start = time.monotonic()
        for trade in trades:
            check_sell_signals(trade, current_price=10.0)
        elapsed = time.monotonic() - start
        
        assert elapsed < 1.0


class TestScorerPerformance:
    """评分引擎性能测试"""
    
    async def test_scorer_batch_performance(self):
        """评分引擎：100只候选股 < 2秒"""
        candidates = generate_test_candidates(count=100)
        
        start = time.monotonic()
        scores = await score_all_candidates(candidates)
        elapsed = time.monotonic() - start
        
        assert elapsed < 2.0, f"评分耗时 {elapsed:.2f}秒"
        assert len(scores) == 100
    
    async def test_single_score_performance(self):
        """单只股票评分 < 50ms"""
        for _ in range(100):
            start = time.monotonic()
            score = calc_final_score(tech=50, fund=60, hist=40)
            elapsed = time.monotonic() - start
            assert elapsed < 0.05, f"单次评分耗时 {elapsed*1000:.2f}ms"


class TestAPIPerformance:
    """API 性能测试"""
    
    async def test_robots_list_under_200ms(self, client, robot_factory):
        """机器人列表 API < 200ms"""
        for i in range(20):
            await robot_factory(name=f"perf-test-{i}")
        
        start = time.monotonic()
        response = await client.get("/api/v1/robots")
        elapsed = time.monotonic() - start
        
        assert response.status_code == 200
        assert elapsed < 0.2, f"API 响应 {elapsed:.3f}秒"
    
    async def test_nav_query_under_500ms(self, client, db_session):
        """净值查询（1年数据）< 500ms"""
        robot_id = 1
        await insert_nav_data(db_session, robot_id, days=365)
        
        start = time.monotonic()
        response = await client.get(
            f"/api/v1/robots/{robot_id}/nav"
            "?start_date=2024-01-01&end_date=2025-01-01"
        )
        elapsed = time.monotonic() - start
        
        assert response.status_code == 200
        assert elapsed < 0.5, f"净值查询 {elapsed:.3f}秒"
    
    async def test_trades_list_with_filter(self, client, db_session):
        """交易列表（带筛选）< 300ms"""
        await insert_trades(db_session, robot_id=1, count=100)
        
        start = time.monotonic()
        response = await client.get(
            "/api/v1/trades?robot_id=1&status=open"
        )
        elapsed = time.monotonic() - start
        
        assert response.status_code == 200
        assert elapsed < 0.3


class TestDatabasePerformance:
    """数据库性能测试"""
    
    async def test_kline_query_performance(self, db_session):
        """K线查询（单只股票1年）< 100ms"""
        start = time.monotonic()
        result = await db_session.execute(
            select(KlineDaily)
            .where(KlineDaily.stock_code == "000001")
            .where(KlineDaily.trade_date >= date(2024, 1, 1))
            .order_by(KlineDaily.trade_date)
        )
        rows = result.all()
        elapsed = time.monotonic() - start
        
        assert elapsed < 0.1
        assert len(rows) > 200  # 约250个交易日
    
    async def test_lhb_aggregation_performance(self, db_session):
        """龙虎榜聚合查询 < 200ms"""
        await insert_large_lhb_dataset(db_session, days=30, stocks=50)
        
        start = time.monotonic()
        result = await db_session.execute("""
            SELECT stock_code, SUM(net_buy_amount) as total_net_buy
            FROM lhb_stock_daily
            WHERE trade_date BETWEEN '2024-12-01' AND '2024-12-31'
            GROUP BY stock_code
            ORDER BY total_net_buy DESC
            LIMIT 20
        """)
        elapsed = time.monotonic() - start
        
        assert elapsed < 0.2
```

---

## 六、测试环境配置

### 6.1 完整版 conftest.py

```python
# tests/conftest.py

import pytest
import asyncio
import os
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from fakeredis.aioredis import FakeRedis

from app.main import create_app
from app.models.base import Base
from app.core.deps import get_db, get_redis


# ---- 事件循环 ----
@pytest.fixture(scope="session")
def event_loop():
    """事件循环（session 级别）"""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


# ---- 测试数据库 ----
@pytest.fixture(scope="session")
async def test_engine():
    """测试数据库引擎"""
    TEST_DB_URL = os.getenv(
        "TEST_DB_URL",
        "postgresql+asyncpg://test:test@localhost:5432/lhb_quant_test"
    )
    
    engine = create_async_engine(TEST_DB_URL, echo=False, pool_pre_ping=True)
    
    # 创建所有表
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    
    yield engine
    
    # 清理
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture
async def db_session(test_engine):
    """数据库会话（每个测试独立，自动回滚）"""
    async_session = async_sessionmaker(test_engine, expire_on_commit=False)
    async with async_session() as session:
        async with session.begin_nested():  # 嵌套事务
            yield session
        # 自动回滚


# ---- 测试 Redis ----
@pytest.fixture
async def test_redis():
    """使用 FakeRedis 替代真实 Redis"""
    redis = FakeRedis()
    yield redis
    await redis.flushall()
    await redis.close()


# ---- 测试应用 ----
@pytest.fixture
async def app(db_session, test_redis):
    """创建测试应用实例"""
    app = create_app()
    
    # 覆盖依赖注入
    async def override_db():
        yield db_session
    
    async def override_redis():
        yield test_redis
    
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_redis] = override_redis
    
    yield app
    
    app.dependency_overrides.clear()


@pytest.fixture
async def client(app):
    """异步 HTTP 测试客户端"""
    async with AsyncClient(app=app, base_url="http://test") as client:
        yield client


# ---- 测试数据工厂 ----
@pytest.fixture
def robot_factory(db_session):
    """机器人工厂"""
    async def _create(
        name="Test Robot",
        status="running",
        mode="paper_trading",
        initial_capital=100000,
        cash_balance=None,
        **kwargs
    ):
        from app.models.robot import Robot
        robot = Robot(
            name=name,
            status=status,
            mode=mode,
            strategy_id=1,
            initial_capital=initial_capital,
            cash_balance=cash_balance or initial_capital,
            **kwargs
        )
        db_session.add(robot)
        await db_session.flush()
        await db_session.refresh(robot)
        return robot
    return _create


@pytest.fixture
def trade_factory(db_session):
    """交易工厂"""
    async def _create(
        robot_id,
        stock_code="000001",
        status="pending",
        signal_date=None,
        buy_price=None,
        buy_shares=None,
        **kwargs
    ):
        from app.models.robot import RobotTrade
        from datetime import date
        trade = RobotTrade(
            robot_id=robot_id,
            stock_code=stock_code,
            stock_name=f"测试股{stock_code}",
            status=status,
            signal_date=signal_date or date.today(),
            buy_price=buy_price,
            buy_shares=buy_shares,
            **kwargs
        )
        db_session.add(trade)
        await db_session.flush()
        await db_session.refresh(trade)
        return trade
    return _create


@pytest.fixture
def lhb_factory(db_session):
    """龙虎榜数据工厂"""
    async def _create(
        trade_date,
        stock_code="000001",
        net_buy_amount=80_000_000,
        change_ratio=0.05,
        **kwargs
    ):
        from app.models.market import LhbStockDaily
        record = LhbStockDaily(
            trade_date=trade_date,
            stock_code=stock_code,
            stock_name=f"测试股{stock_code}",
            close_price=10.0,
            change_ratio=change_ratio,
            net_buy_amount=net_buy_amount,
            total_buy_amount=net_buy_amount * 1.5,
            total_sell_amount=net_buy_amount * 0.5,
            **kwargs
        )
        db_session.add(record)
        await db_session.flush()
        return record
    return _create


# ---- Mock 工具 ----
@pytest.fixture
def mock_clock():
    """时间 Mock 工具"""
    class MockClock:
        def __init__(self):
            self._current_time = None
        
        def set(self, time_str: str):
            """设置当前时间"""
            self._current_time = time_str
        
        def get(self):
            return self._current_time
    
    return MockClock()


@pytest.fixture
def mock_realtime_price():
    """实时价格 Mock 工具"""
    class MockPriceProvider:
        def __init__(self):
            self._prices = {}
        
        def set(self, prices: dict):
            """设置价格 {stock_code: price}"""
            self._prices.update(prices)
        
        def get(self, stock_code: str):
            return self._prices.get(stock_code, 10.0)
    
    return MockPriceProvider()


# ---- pytest 配置 ----
def pytest_configure(config):
    """注册自定义 marker"""
    config.addinivalue_line("markers", "slow: 标记慢速测试")
    config.addinivalue_line("markers", "integration: 标记集成测试")
    config.addinivalue_line("markers", "e2e: 标记端到端测试")
    config.addinivalue_line("markers", "performance: 标记性能测试")
    config.addinivalue_line("markers", "requires_tushare: 需要 TuShare Token")
    config.addinivalue_line("markers", "requires_akshare: 需要网络访问")
```

---

## 七、CI/CD 集成

### 7.1 pytest.ini 配置

```ini
# pytest.ini
[pytest]
asyncio_mode = auto
testpaths = tests
python_files = test_*.py
python_classes = Test*
python_functions = test_*

markers =
    slow: 慢速测试（蒙特卡洛等）
    integration: 集成测试
    e2e: 端到端测试
    performance: 性能测试
    requires_tushare: 需要 TuShare Token
    requires_akshare: 需要网络访问

# 超时设置
timeout = 60
timeout_method = thread

# 日志
log_cli = true
log_cli_level = WARNING
log_cli_format = %(asctime)s | %(levelname)s | %(message)s

# 异步模式
asyncio_mode = auto

# 覆盖率（如果使用 pytest-cov）
addopts = --strict-markers --tb=short
```

### 7.2 测试分组执行策略

```yaml
## 测试分组与执行时机

### 分组定义（pytest markers）
| marker | 说明 | 运行时机 | 预估耗时 |
|--------|------|----------|----------|
| 无标记 | 单元测试 | 每次提交 | < 30秒 |
| @integration | 集成测试 | 每次提交 | < 2分钟 |
| @e2e | 端到端测试 | PR 合并前 | < 5分钟 |
| @performance | 性能测试 | 每日定时 | < 10分钟 |
| @requires_tushare | 需要真实 Token | 手动触发 | < 5分钟 |
| @slow | 慢速测试 | 每周定时 | < 30分钟 |

### 执行命令
```

```bash
# 快速测试（每次提交）- CI step 1
pytest tests/ \
    -m "not slow and not performance and not requires_tushare" \
    --timeout=60 \
    --tb=short \
    -q

# 完整测试（PR 合并前）- CI step 2
pytest tests/ \
    -m "not slow and not requires_tushare" \
    --timeout=120 \
    --tb=short

# 性能测试（每日凌晨）- CI 定时任务
pytest tests/ -m "performance" --timeout=300

# 全量测试（每周日）- CI 定时任务
pytest tests/ --timeout=600

# 覆盖率报告（PR 合并前）
pytest tests/ \
    --cov=app \
    --cov-report=html \
    --cov-report=term \
    --cov-fail-under=85

# 本地快速验证（开发时）
pytest tests/unit/ -x -q
```

### 7.3 Makefile 集成

```makefile
# Makefile（测试相关命令）
.PHONY: test test-unit test-integration test-e2e test-perf test-all test-cov test-fast

# 快速测试（单元+集成，适合每次提交）
test:                    ## 快速测试（单元+集成，<2分钟）
	pytest tests/ -m "not slow and not performance and not requires_tushare" -x --timeout=60 -q

# 仅单元测试
test-unit:               ## 仅单元测试（<30秒）
	pytest tests/unit/ -m "not slow" -x --timeout=30 -q

# 仅集成测试
test-integration:        ## 仅集成测试
	pytest tests/integration/ --timeout=120

# E2E测试
test-e2e:                ## 端到端测试
	pytest tests/e2e/ --timeout=300

# 性能测试
test-perf:               ## 性能测试
	pytest tests/performance/ --timeout=300

# 全量测试
test-all:                ## 全量测试
	pytest tests/ --timeout=600

# 快速本地验证
test-fast:               ## 快速本地验证
	pytest tests/unit/ -x -q --tb=line

# 覆盖率报告
test-cov:                ## 覆盖率报告（目标85%）
	pytest tests/ --cov=app --cov-report=html --cov-report=term --cov-fail-under=85
	@echo "覆盖率报告: htmlcov/index.html"

# 特定marker测试
test-slow:               ## 慢速测试（蒙特卡洛等）
	pytest tests/ -m "slow" --timeout=600

test-tushare:            ## 需要TuShare Token的测试
	pytest tests/ -m "requires_tushare" --timeout=300

# 帮助
help:                    ## 显示帮助
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'
```

---

## 八、断言规范

### 8.1 浮点数精度规范

```yaml
## 断言精度规范

### 金额计算
- pytest.approx(expected, abs=0.01)
  - 精度：1分钱
  - 示例：assert result == pytest.approx(12345.67, abs=0.01)

### 比率计算
- pytest.approx(expected, abs=0.0001)
  - 精度：万分之一（0.01%）
  - 示例：assert win_rate == pytest.approx(0.58, abs=0.0001)

### 百分比
- pytest.approx(expected, abs=0.01)
  - 精度：1%
  - 示例：assert pct == pytest.approx(58.5, abs=0.01)

### 评分
- pytest.approx(expected, abs=1.0)
  - 精度：1分
  - 示例：assert score == pytest.approx(75, abs=1)

### 范围断言
- assert 0 <= value <= 100
- assert value > 0
- assert len(list) >= 1
```

### 8.2 集合断言规范

```python
# 存在性断言
assert "field" in response_data
assert stock_code in [s.stock_code for s in selections]

# 子集断言
required_fields = {"id", "name", "status"}
assert required_fields.issubset(set(actual_fields))

# 列表长度
assert len(trades) >= 1
assert len(selections) > 0

# 有序性断言
assert scores == sorted(scores)
assert prices == sorted(prices, reverse=True)
```

---

## 九、测试目录结构

```yaml
tests/
├── conftest.py                     # 全局 fixture（完整版见 §6.1）
├── unit/                           # 单元测试（80%）
│   ├── test_scorer.py             # 评分引擎（行为+数值）
│   ├── test_commission.py         # 费用计算
│   ├── test_risk.py               # 止损止盈判断（含边界）
│   ├── test_ahs.py                # AHS 评分（行为+数值）
│   ├── test_position.py           # 仓位计算
│   ├── test_state_machine.py      # 状态机转换
│   ├── test_validators.py         # 数据校验
│   ├── test_circuit_breaker.py    # 熔断器
│   └── test_rate_limiter.py       # 限流器
├── integration/                    # 集成测试（15%）
│   ├── conftest.py                # 集成测试 fixture
│   ├── test_database.py           # 数据库操作
│   ├── test_data_providers.py     # 数据源适配器
│   ├── test_cache.py              # 缓存操作
│   └── api/                       # API 端点测试
│       ├── conftest.py
│       ├── test_robots.py
│       ├── test_trades.py
│       ├── test_market.py
│       ├── test_selection.py
│       ├── test_system.py
│       └── test_websocket.py
├── e2e/                           # 端到端测试（5%）
│   ├── conftest.py                # E2E fixture（固定数据集）
│   ├── test_trading_flow.py       # 完整交易流程
│   ├── test_paper_to_live.py      # 模拟盘→实盘
│   └── test_emergency.py          # 紧急操作流程
├── performance/                   # 性能测试
│   ├── test_latency.py            # 延迟测试
│   └── test_throughput.py         # 吞吐量测试
├── backtest/                      # 回测测试
│   ├── test_regression.py         # 回归测试
│   └── test_monte_carlo.py        # 蒙特卡洛
└── fixtures/                      # 测试数据
    ├── mock_data.py               # Mock 数据生成器
    ├── sample_lhb.json            # 龙虎榜样本数据
    ├── sample_kline.json          # K线样本数据
    └── sample_seats.json          # 席位样本数据
```

---

## 十、测试覆盖与文档对照

### 10.1 评审问题覆盖映射

```yaml
## 评审问题 → 测试用例映射表

| 评审文档 | 评审问题 | 优先级 | 对应测试 | 状态 |
|----------|----------|--------|----------|------|
| STRATEGY | 评分阶梯断裂 | P0 | test_scorer.TestScorerBehavior | 🔲 |
| STRATEGY | 历史面冷启动 | P0 | test_scorer.TestScoreEdgeCases | 🔲 |
| STRATEGY | 涨停板降权 | P0 | test_scorer.test_limit_up_not_highest | 🔲 |
| STRATEGY | 多档止盈同时触发 | P0 | test_risk.test_multiple_sell_tiers | 🔲 |
| STRATEGY | T+1限制 | P0 | test_risk.test_t_plus_1_* | 🔲 |
| STRATEGY | 零股卖出 | P1 | test_risk.test_odd_lot_calculation | 🔲 |
| RISK_CONTROL | AHS公式数学问题 | P0 | test_ahs.TestAHSBehavior | 🔲 |
| RISK_CONTROL | 恢复振荡 | P0 | test_ahs.test_position_with_hysteresis | 🔲 |
| RISK_CONTROL | 冷启动 | P1 | test_ahs.test_cold_start_* | 🔲 |
| RISK_CONTROL | 硬限制拒绝 | P0 | test_risk_limits.TestHardLimits | 🔲 |
| STATE_MACHINE | 非法状态转换 | P0 | test_state_machine.test_valid_transitions | 🔲 |
| STATE_MACHINE | 并发保护 | P1 | test_state_machine.test_concurrent_* | 🔲 |
| DATA_SOURCE | 主数据源超时降级 | P0 | test_data_providers.test_primary_timeout_fallback | 🔲 |
| DATA_SOURCE | 所有数据源失败缓存 | P0 | test_data_providers.test_all_providers_fail_* | 🔲 |
| DATA_SOURCE | 熔断器跳过 | P1 | test_data_providers.test_circuit_breaker_skip | 🔲 |
| API | 幂等性 | P1 | test_robots.test_*_idempotent | 🔲 |
| DATABASE | 资金对账 | P1 | test_database.test_cash_ledger_integrity | 🔲 |
```

### 10.2 文档对照

```yaml
## 测试模块验证的文档

| 测试模块 | 验证的文档章节 |
|----------|---------------|
| test_scorer | STRATEGY.md 第二章 评分体系 |
| test_commission | STRATEGY.md 第六章 费用计算 |
| test_risk | RISK_CONTROL.md 第四、五章 交易保护 |
| test_ahs | RISK_CONTROL.md 第三章 AHS 仓位管理 |
| test_state_machine | STATE_MACHINE.md 第一、二章 状态定义 |
| test_data_providers | DATA_SOURCE.md 第五、六章 数据获取 |
| test_robots (API) | API.md 2.3节 机器人接口 |
| test_trading_flow (E2E) | TRADING_FLOW.md 全文 |
| test_latency | RUNBOOK.md 第六节 监控指标 |
```

---

## 十一、版本历史

| 版本 | 日期 | 修改内容 |
|------|------|----------|
| 1.0.0 | 2026-04-16 | 初始版本 |
| 2.1.0 | 2026-04-16 | 评审修订：行为/数值测试分离、补充边界场景、数据源集成测试、固定数据集E2E、性能测试、完整conftest、CI/CD集成 |

---

## 十二、参考文档

| 文档 | 关系 |
|------|------|
| [STRATEGY.md](./STRATEGY.md) | 评分公式、费用计算定义 |
| [RISK_CONTROL.md](./RISK_CONTROL.md) | AHS 公式、风控参数定义 |
| [STATE_MACHINE.md](./STATE_MACHINE.md) | 状态转换规则 |
| [DATA_SOURCE.md](./DATA_SOURCE.md) | 数据源降级链、熔断参数 |
| [API.md](./API.md) | 端点契约 |
| [DATABASE.md](./DATABASE.md) | 表结构、约束定义 |
| [TRADING_FLOW.md](./TRADING_FLOW.md) | 交易时序、流程定义 |
| [RUNBOOK.md](./RUNBOOK.md) | 性能阈值、监控指标 |

---

*文档版本: 2.1*
*最后修订: 2026-04-16*
