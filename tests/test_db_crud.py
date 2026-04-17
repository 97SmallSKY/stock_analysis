# tests/test_db_crud.py
"""
数据库CRUD测试
用真实PG验证插入/查询/更新/删除
每个测试结束后回滚，不污染数据
"""

import pytest
import psycopg2
from psycopg2.extras import RealDictCursor

from tests.conftest import DB_CONFIG


@pytest.fixture(scope="function")
def tx_cursor():
    """事务cursor，测试后自动回滚"""
    conn = psycopg2.connect(**DB_CONFIG)
    conn.autocommit = False
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    yield cursor
    conn.rollback()
    cursor.close()
    conn.close()


class TestStockInfoCRUD:
    """股票基础信息表CRUD"""

    def test_insert_stock(self, tx_cursor):
        tx_cursor.execute("""
            INSERT INTO stock_info (ts_code, symbol, name, industry, market, is_active)
            VALUES ('000001.SZ', '000001', '平安银行', '银行', '主板', TRUE)
            RETURNING ts_code, name
        """)
        result = tx_cursor.fetchone()
        assert result["ts_code"] == "000001.SZ"
        assert result["name"] == "平安银行"

    def test_insert_duplicate_stock_fails(self, tx_cursor):
        tx_cursor.execute("""
            INSERT INTO stock_info (ts_code, symbol, name, industry, market, is_active)
            VALUES ('888888.SZ', '888888', '测试股票A', '测试', '主板', TRUE)
        """)
        with pytest.raises(psycopg2.errors.UniqueViolation):
            tx_cursor.execute("""
                INSERT INTO stock_info (ts_code, symbol, name, industry, market, is_active)
                VALUES ('888888.SZ', '888888', '测试股票B', '测试', '主板', TRUE)
            """)

    def test_update_stock(self, tx_cursor):
        tx_cursor.execute("""
            INSERT INTO stock_info (ts_code, symbol, name, industry, market, is_active)
            VALUES ('777777.SZ', '777777', '更新测试', '测试', '主板', TRUE)
        """)
        tx_cursor.execute("""
            UPDATE stock_info SET name = '已更新', industry = '已更新行业'
            WHERE ts_code = '777777.SZ' RETURNING name, industry
        """)
        result = tx_cursor.fetchone()
        assert result["name"] == "已更新"
        assert result["industry"] == "已更新行业"

    def test_delete_stock(self, tx_cursor):
        tx_cursor.execute("""
            INSERT INTO stock_info (ts_code, symbol, name, industry, market, is_active)
            VALUES ('666666.SZ', '666666', '删除测试', '测试', '主板', TRUE)
        """)
        tx_cursor.execute("DELETE FROM stock_info WHERE ts_code = '666666.SZ'")
        tx_cursor.execute("SELECT COUNT(*) as cnt FROM stock_info WHERE ts_code = '666666.SZ'")
        assert tx_cursor.fetchone()["cnt"] == 0


# ================================================================
# 2. lhb_stock_daily + lhb_seats 关联测试
# ================================================================

class TestLHBCrud:
    """龙虎榜数据CRUD"""

    def _insert_test_stock(self, cursor):
        """插入测试股票(前置依赖)"""
        cursor.execute("""
            INSERT INTO stock_info (ts_code, symbol, name, industry, market, is_active)
            VALUES ('999001.SZ', '999001', 'LHB测试股', '测试', '主板', TRUE)
            ON CONFLICT (ts_code) DO NOTHING
        """)

    def test_insert_lhb_daily(self, tx_cursor):
        self._insert_test_stock(tx_cursor)
        tx_cursor.execute("""
            INSERT INTO lhb_stock_daily (
                ts_code, trade_date, name, close_price, pct_change,
                l_buy, l_sell, l_net, reason, score
            ) VALUES (
                '999001.SZ', '2024-01-15', 'LHB测试股', 25.50, 0.0987,
                5000.00, 2000.00, 3000.00, '日涨幅偏离值达7%', 85.50
            ) RETURNING id, score
        """)
        result = tx_cursor.fetchone()
        assert result["id"] is not None
        assert float(result["score"]) == 85.50

    def test_lhb_unique_constraint(self, tx_cursor):
        """同一天同一股票不能重复插入"""
        self._insert_test_stock(tx_cursor)
        tx_cursor.execute("""
            INSERT INTO lhb_stock_daily (ts_code, trade_date, name, close_price, pct_change, reason)
            VALUES ('999001.SZ', '2024-02-01', 'LHB测试股', 20.00, 0.05, '测试')
        """)
        with pytest.raises(psycopg2.errors.UniqueViolation):
            tx_cursor.execute("""
                INSERT INTO lhb_stock_daily (ts_code, trade_date, name, close_price, pct_change, reason)
                VALUES ('999001.SZ', '2024-02-01', 'LHB测试股', 21.00, 0.06, '测试重复')
            """)

    def test_insert_lhb_seats(self, tx_cursor):
        """插入席位并验证外键关联"""
        self._insert_test_stock(tx_cursor)
        tx_cursor.execute("""
            INSERT INTO lhb_stock_daily (ts_code, trade_date, name, close_price, pct_change, reason)
            VALUES ('999001.SZ', '2024-03-01', 'LHB测试股', 30.00, 0.10, '测试席位')
            RETURNING id
        """)
        daily_id = tx_cursor.fetchone()["id"]

        tx_cursor.execute("""
            INSERT INTO lhb_seats (
                stock_daily_id, ts_code, trade_date, broker_name,
                buy_amount, sell_amount, net_amount, direction, rank
            ) VALUES (
                %s, '999001.SZ', '2024-03-01', '华泰证券深圳益田路',
                3000.00, 500.00, 2500.00, 'BUY', 1
            ) RETURNING id, broker_name
        """, (daily_id,))
        result = tx_cursor.fetchone()
        assert result["broker_name"] == "华泰证券深圳益田路"

    def test_lhb_tn_price(self, tx_cursor):
        """T+N价格追踪"""
        self._insert_test_stock(tx_cursor)
        tx_cursor.execute("""
            INSERT INTO lhb_stock_daily (ts_code, trade_date, name, close_price, pct_change, reason)
            VALUES ('999001.SZ', '2024-04-01', 'LHB测试股', 28.00, 0.08, '测试TN')
            RETURNING id
        """)
        daily_id = tx_cursor.fetchone()["id"]

        # 插入 T+1, T+3, T+5
        for n, pnl in [(1, 0.0312), (3, 0.0520), (5, -0.0180)]:
            tx_cursor.execute("""
                INSERT INTO lhb_tn_price (
                    stock_daily_id, ts_code, trade_date, n_day,
                    close_price, pnl_pct, calc_status
                ) VALUES (%s, '999001.SZ', '2024-04-01', %s, %s, %s, 'DONE')
            """, (daily_id, n, 28.00 * (1 + pnl), pnl))

        tx_cursor.execute("""
            SELECT n_day, pnl_pct FROM lhb_tn_price 
            WHERE stock_daily_id = %s ORDER BY n_day
        """, (daily_id,))
        results = tx_cursor.fetchall()
        assert len(results) == 3
        assert float(results[0]["pnl_pct"]) == pytest.approx(0.0312, abs=0.0001)


# ================================================================
# 3. 机器人 + 交易 完整流程
# ================================================================

class TestRobotTradingFlow:
    """机器人交易完整流程测试"""

    def _setup_robot(self, cursor):
        """创建测试策略和机器人"""
        cursor.execute("""
            INSERT INTO strategies (name, category, entry_rules, exit_rules, risk_rules, params)
            VALUES ('测试策略', 'LHB', '[]'::jsonb, '[]'::jsonb, '[]'::jsonb, '{}'::jsonb)
            RETURNING id
        """)
        strategy_id = cursor.fetchone()["id"]

        cursor.execute("""
            INSERT INTO robots (
                name, strategy_id, initial_capital, current_capital,
                cash, total_assets, status
            ) VALUES (
                '测试机器人', %s, 1000000.00, 1000000.00,
                1000000.00, 1000000.00, 'RUNNING'
            ) RETURNING id
        """, (strategy_id,))
        return cursor.fetchone()["id"]

    def _setup_stock(self, cursor):
        cursor.execute("""
            INSERT INTO stock_info (ts_code, symbol, name, industry, market, is_active)
            VALUES ('999002.SZ', '999002', '交易测试股', '测试', '主板', TRUE)
            ON CONFLICT (ts_code) DO NOTHING
        """)

    def test_full_buy_sell_flow(self, tx_cursor):
        """完整的买入→卖出流程"""
        self._setup_stock(tx_cursor)
        robot_id = self._setup_robot(tx_cursor)

        # 买入
        tx_cursor.execute("""
            INSERT INTO robot_trades (
                robot_id, ts_code, name, direction, price, shares, amount,
                commission, slippage, phase, signal_score, trade_time, trade_date
            ) VALUES (
                %s, '999002.SZ', '交易测试股', 'BUY', 25.00, 1000, 25000.00,
                7.50, 25.00, 'PHASE_A', 82.5, '2024-01-15 09:35:00', '2024-01-15'
            ) RETURNING id
        """, (robot_id,))
        buy_id = tx_cursor.fetchone()["id"]
        assert buy_id is not None

        # 记录资金流水 - 买入冻结
        tx_cursor.execute("""
            INSERT INTO cash_ledger (
                robot_id, trade_id, ledger_type, direction, amount,
                balance_before, balance_after, biz_date
            ) VALUES (
                %s, %s, 'BUY_FREEZE', 'OUT', 25032.50,
                1000000.00, 974967.50, '2024-01-15'
            )
        """, (robot_id, buy_id))

        # 卖出(3天后)
        tx_cursor.execute("""
            INSERT INTO robot_trades (
                robot_id, ts_code, name, direction, price, shares, amount,
                commission, slippage, stamp_tax, pnl_amount, pnl_pct,
                hold_days, cost_price, paired_trade_id,
                phase, trade_time, trade_date
            ) VALUES (
                %s, '999002.SZ', '交易测试股', 'SELL', 27.50, 1000, 27500.00,
                8.25, 27.50, 27.50, 2436.25, 0.0974,
                3, 25.00, %s,
                'TAKE_PROFIT', '2024-01-18 14:30:00', '2024-01-18'
            ) RETURNING id, pnl_amount
        """, (robot_id, buy_id))
        sell_result = tx_cursor.fetchone()
        assert float(sell_result["pnl_amount"]) == pytest.approx(2436.25, abs=0.01)

        # 验证交易记录
        tx_cursor.execute("""
            SELECT COUNT(*) as cnt FROM robot_trades WHERE robot_id = %s
        """, (robot_id,))
        assert tx_cursor.fetchone()["cnt"] == 2

    def test_daily_nav_snapshot(self, tx_cursor):
        """每日净值快照"""
        robot_id = self._setup_robot(tx_cursor)

        tx_cursor.execute("""
            INSERT INTO daily_nav (
                robot_id, trade_date, net_value, total_assets, cash,
                position_value, position_count, pnl_daily, pnl_daily_pct,
                pnl_total, pnl_total_pct, drawdown, max_drawdown, max_net_value
            ) VALUES (
                %s, '2024-01-15', 1.000000, 1000000.00, 1000000.00,
                0, 0, 0, 0, 0, 0, 0, 0, 1.000000
            )
        """, (robot_id,))

        tx_cursor.execute("""
            INSERT INTO daily_nav (
                robot_id, trade_date, net_value, total_assets, cash,
                position_value, position_count, pnl_daily, pnl_daily_pct,
                pnl_total, pnl_total_pct, drawdown, max_drawdown, max_net_value
            ) VALUES (
                %s, '2024-01-16', 1.025000, 1025000.00, 975000.00,
                50000.00, 2, 25000.00, 0.025, 25000.00, 0.025, 0, 0, 1.025000
            )
        """, (robot_id,))

        tx_cursor.execute("""
            SELECT trade_date, net_value, pnl_daily_pct FROM daily_nav 
            WHERE robot_id = %s ORDER BY trade_date
        """, (robot_id,))
        rows = tx_cursor.fetchall()
        assert len(rows) == 2
        assert float(rows[1]["net_value"]) == pytest.approx(1.025, abs=0.001)

    def test_daily_nav_unique(self, tx_cursor):
        """同一天不能有两条净值"""
        robot_id = self._setup_robot(tx_cursor)

        tx_cursor.execute("""
            INSERT INTO daily_nav (robot_id, trade_date, net_value, total_assets, cash)
            VALUES (%s, '2024-06-01', 1.0, 1000000, 1000000)
        """, (robot_id,))

        with pytest.raises(psycopg2.errors.UniqueViolation):
            tx_cursor.execute("""
                INSERT INTO daily_nav (robot_id, trade_date, net_value, total_assets, cash)
                VALUES (%s, '2024-06-01', 1.1, 1100000, 1100000)
            """, (robot_id,))


# ================================================================
# 4. 模拟交易
# ================================================================

class TestSimTrades:
    """模拟交易测试"""

    def _setup(self, cursor):
        cursor.execute("""
            INSERT INTO stock_info (ts_code, symbol, name, industry, market, is_active)
            VALUES ('999003.SZ', '999003', '模拟测试股', '测试', '主板', TRUE)
            ON CONFLICT (ts_code) DO NOTHING
        """)

        cursor.execute("""
            INSERT INTO robots (name, initial_capital, current_capital, cash, total_assets, status)
            VALUES ('模拟测试机器人', 1000000, 1000000, 1000000, 1000000, 'RUNNING')
            RETURNING id
        """)
        return cursor.fetchone()["id"]

    def test_sim_trade_lifecycle(self, tx_cursor):
        """模拟交易完整生命周期: 开仓→快照→平仓"""
        robot_id = self._setup(tx_cursor)

        # 开仓
        tx_cursor.execute("""
            INSERT INTO sim_trades (
                robot_id, ts_code, name, entry_date, entry_price,
                entry_score, shares, target_price, stop_loss_price,
                max_hold_days, status
            ) VALUES (
                %s, '999003.SZ', '模拟测试股', '2024-01-15', 20.00,
                78.5, 500, 24.00, 18.00, 10, 'OPEN'
            ) RETURNING id
        """, (robot_id,))
        sim_id = tx_cursor.fetchone()["id"]

        # 快照 Day1
        tx_cursor.execute("""
            INSERT INTO sim_trade_snapshots (
                sim_trade_id, robot_id, snapshot_date,
                current_price, unrealized_pnl, unrealized_pnl_pct, hold_days
            ) VALUES (%s, %s, '2024-01-16', 20.80, 400.00, 0.04, 1)
        """, (sim_id, robot_id))

        # 快照 Day2
        tx_cursor.execute("""
            INSERT INTO sim_trade_snapshots (
                sim_trade_id, robot_id, snapshot_date,
                current_price, unrealized_pnl, unrealized_pnl_pct, hold_days
            ) VALUES (%s, %s, '2024-01-17', 21.50, 750.00, 0.075, 2)
        """, (sim_id, robot_id))

        # 平仓(止盈)
        tx_cursor.execute("""
            UPDATE sim_trades SET
                status = 'TAKE_PROFIT',
                exit_date = '2024-01-18',
                exit_price = 24.10,
                exit_reason = '触发止盈: 最高价24.10 >= 目标价24.00',
                hold_days = 3,
                realized_pnl = 2050.00,
                realized_pnl_pct = 0.205
            WHERE id = %s RETURNING status, realized_pnl_pct
        """, (sim_id,))
        result = tx_cursor.fetchone()
        assert result["status"] == "TAKE_PROFIT"
        assert float(result["realized_pnl_pct"]) == pytest.approx(0.205, abs=0.001)

        # 验证快照数量
        tx_cursor.execute("""
            SELECT COUNT(*) as cnt FROM sim_trade_snapshots WHERE sim_trade_id = %s
        """, (sim_id,))
        assert tx_cursor.fetchone()["cnt"] == 2

    def test_sim_trade_stop_loss(self, tx_cursor):
        """模拟交易止损"""
        robot_id = self._setup(tx_cursor)

        tx_cursor.execute("""
            INSERT INTO sim_trades (
                robot_id, ts_code, name, entry_date, entry_price,
                shares, target_price, stop_loss_price, max_hold_days, status
            ) VALUES (
                %s, '999003.SZ', '模拟测试股', '2024-02-01', 30.00,
                300, 36.00, 27.00, 5, 'OPEN'
            ) RETURNING id
        """, (robot_id,))
        sim_id = tx_cursor.fetchone()["id"]

        tx_cursor.execute("""
            UPDATE sim_trades SET
                status = 'STOP_LOSS',
                exit_date = '2024-02-03',
                exit_price = 27.00,
                exit_reason = '触发止损: 最低价26.80 <= 止损价27.00',
                hold_days = 2,
                realized_pnl = -900.00,
                realized_pnl_pct = -0.10
            WHERE id = %s RETURNING status
        """, (sim_id,))
        assert tx_cursor.fetchone()["status"] == "STOP_LOSS"


# ================================================================
# 5. 回测任务
# ================================================================

class TestBacktest:
    """回测任务CRUD"""

    def test_backtest_lifecycle(self, tx_cursor):
        """回测任务: 创建→运行→完成→写结果"""
        # 创建策略
        tx_cursor.execute("""
            INSERT INTO strategies (name, category, entry_rules, exit_rules, risk_rules, params)
            VALUES ('回测策略', 'LHB', '[]'::jsonb, '[]'::jsonb, '[]'::jsonb, '{}'::jsonb)
            RETURNING id
        """)
        strategy_id = tx_cursor.fetchone()["id"]

        # 创建回测任务
        tx_cursor.execute("""
            INSERT INTO backtest_tasks (
                strategy_id, name, start_date, end_date,
                initial_capital, status, progress
            ) VALUES (
                %s, '龙虎榜高分策略回测', '2023-01-01', '2023-12-31',
                1000000.00, 'PENDING', 0
            ) RETURNING id
        """, (strategy_id,))
        task_id = tx_cursor.fetchone()["id"]

        # 更新为运行中
        tx_cursor.execute("""
            UPDATE backtest_tasks SET status = 'RUNNING', progress = 50, started_at = NOW()
            WHERE id = %s
        """, (task_id,))

        # 完成并写入结果
        tx_cursor.execute("""
            UPDATE backtest_tasks SET status = 'COMPLETED', progress = 100, finished_at = NOW()
            WHERE id = %s
        """, (task_id,))

        tx_cursor.execute("""
            INSERT INTO backtest_results (
                task_id, total_return, annual_return, max_drawdown,
                sharpe_ratio, win_rate, trade_count,
                win_count, lose_count, avg_hold_days,
                equity_curve, monthly_returns
            ) VALUES (
                %s, 0.3520, 0.3520, -0.0850,
                2.15, 0.6200, 48,
                30, 18, 4.5,
                '[{"date":"2023-01-02","nav":1.00},{"date":"2023-12-29","nav":1.352}]'::jsonb,
                '{"2023-01":0.05,"2023-02":0.03,"2023-03":-0.02}'::jsonb
            ) RETURNING total_return, sharpe_ratio
        """, (task_id,))
        result = tx_cursor.fetchone()
        assert float(result["total_return"]) == pytest.approx(0.352, abs=0.001)
        assert float(result["sharpe_ratio"]) == pytest.approx(2.15, abs=0.01)


# ================================================================
# 6. 市场数据
# ================================================================

class TestMarketData:
    """市场数据CRUD"""

    def test_market_sentiment(self, tx_cursor):
        tx_cursor.execute("""
            INSERT INTO market_sentiment (
                trade_date, limit_up_count, limit_down_count,
                up_down_ratio, avg_pct_change,
                sentiment_score, sentiment_level
            ) VALUES (
                '2024-01-15', 65, 8, 2.0, 0.0125,
                72.50, 'HOT'
            ) RETURNING sentiment_score
        """)
        assert float(tx_cursor.fetchone()["sentiment_score"]) == 72.50

    def test_market_sentiment_unique(self, tx_cursor):
        tx_cursor.execute("""
            INSERT INTO market_sentiment (trade_date, sentiment_score, sentiment_level)
            VALUES ('2024-06-01', 50, 'NEUTRAL')
        """)
        with pytest.raises(psycopg2.errors.UniqueViolation):
            tx_cursor.execute("""
                INSERT INTO market_sentiment (trade_date, sentiment_score, sentiment_level)
                VALUES ('2024-06-01', 60, 'WARM')
            """)

    def test_market_snapshots(self, tx_cursor):
        tx_cursor.execute("""
            INSERT INTO market_snapshots (
                trade_date, snapshot_type,
                sh_index, sh_pct_change, sz_index, sz_pct_change,
                up_count, down_count, limit_up_count, limit_down_count,
                total_amount, north_net_amount
            ) VALUES (
                '2024-01-15', 'CLOSE',
                3050.12, 0.0125, 10200.55, 0.0098,
                3200, 1600, 65, 8,
                10500.00, 85.30
            ) RETURNING sh_index
        """)
        assert float(tx_cursor.fetchone()["sh_index"]) == pytest.approx(3050.12)


# ================================================================
# 7. 审计日志和事件
# ================================================================

class TestLogsAndEvents:
    """日志和事件测试"""

    def test_audit_log(self, tx_cursor):
        tx_cursor.execute("""
            INSERT INTO audit_logs (
                action, entity_type, entity_id, operator,
                detail, old_value, new_value
            ) VALUES (
                'CREATE', 'robot', 1, 'SYSTEM',
                '{"msg":"创建机器人"}'::jsonb,
                NULL,
                '{"name":"新机器人","capital":1000000}'::jsonb
            ) RETURNING id
        """)
        assert tx_cursor.fetchone()["id"] is not None

    def test_trade_event(self, tx_cursor):
        tx_cursor.execute("""
            INSERT INTO robots (name, initial_capital, current_capital, cash, total_assets, status)
            VALUES ('事件测试机器人', 1000000, 1000000, 1000000, 1000000, 'RUNNING')
            RETURNING id
        """)
        robot_id = tx_cursor.fetchone()["id"]

        tx_cursor.execute("""
            INSERT INTO trade_events (
                robot_id, event_type, event_level,
                ts_code, title, message,
                trigger_type, trade_date
            ) VALUES (
                %s, 'STOP_LOSS', 'WARN',
                '999001.SZ', '止损触发: 999001.SZ', '当前价跌破止损线',
                'RISK', '2024-01-15'
            ) RETURNING id, event_type
        """, (robot_id,))
        result = tx_cursor.fetchone()
        assert result["event_type"] == "STOP_LOSS"

    def test_collector_error_log(self, tx_cursor):
        tx_cursor.execute("""
            INSERT INTO collector_error_log (
                adapter_name, category, api_name,
                error_type, error_message, retry_count
            ) VALUES (
                'tushare', 'DAILY_KLINE', 'pro.daily',
                'TIMEOUT', 'Connection timeout after 30s', 3
            ) RETURNING id
        """)
        assert tx_cursor.fetchone()["id"] is not None
