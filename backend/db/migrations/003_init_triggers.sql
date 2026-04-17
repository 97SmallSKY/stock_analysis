-- ============================================================
-- 龙虎榜量化交易系统 v2 - 触发器和函数
-- 文件: 003_init_triggers.sql
-- 说明: 创建所有触发器和辅助函数（严格按照 DATABASE.md 定义）
-- ============================================================

-- ============================================================
-- 1. 自动更新 updated_at 触发器
-- ============================================================

CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- 应用到所有需要 updated_at 的表
CREATE TRIGGER trigger_robots_updated
    BEFORE UPDATE ON robots
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

CREATE TRIGGER trigger_trades_updated
    BEFORE UPDATE ON robot_trades
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

CREATE TRIGGER trigger_lhb_updated
    BEFORE UPDATE ON lhb_stock_daily
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

CREATE TRIGGER trigger_strategies_updated
    BEFORE UPDATE ON strategies
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

CREATE TRIGGER trigger_stock_info_updated
    BEFORE UPDATE ON stock_info
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

CREATE TRIGGER trigger_app_config_updated
    BEFORE UPDATE ON app_config
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- ============================================================
-- 2. 交易状态转换检查触发器
-- ============================================================

CREATE OR REPLACE FUNCTION check_trade_status_transition()
RETURNS TRIGGER AS $$
DECLARE
    valid_transitions JSONB := '{
        "pending":    ["open", "abandoned"],
        "open":       ["partial", "closed", "abandoned", "eliminated"],
        "partial":    ["closed", "abandoned", "eliminated"],
        "closed":     [],
        "abandoned":  [],
        "eliminated": []
    }'::JSONB;
    allowed_arr JSONB;
BEGIN
    -- 如果状态未改变，直接返回
    IF OLD.status = NEW.status THEN
        RETURN NEW;
    END IF;

    -- 获取允许的目标状态
    allowed_arr := valid_transitions -> OLD.status;
    
    -- 检查是否允许转换
    IF allowed_arr IS NULL OR NOT (allowed_arr ? NEW.status) THEN
        RAISE EXCEPTION '非法状态转换: % → %（交易ID: %）', 
            OLD.status, NEW.status, OLD.id;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trigger_trade_status_check
    BEFORE UPDATE OF status ON robot_trades
    FOR EACH ROW EXECUTE FUNCTION check_trade_status_transition();

-- ============================================================
-- 3. 审计日志禁止删除触发器
-- ============================================================

CREATE OR REPLACE FUNCTION prevent_audit_delete()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'audit_log records cannot be deleted';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trigger_prevent_audit_delete
    BEFORE DELETE ON audit_log
    FOR EACH ROW EXECUTE FUNCTION prevent_audit_delete();

-- ============================================================
-- 4. robots 资金对账函数
-- ============================================================

CREATE OR REPLACE FUNCTION verify_robot_cash(p_robot_id BIGINT)
RETURNS TABLE(robot_cash DECIMAL, ledger_cash DECIMAL, is_match BOOLEAN) AS $$
BEGIN
    RETURN QUERY
    SELECT 
        r.cash_balance,
        COALESCE(cl.balance_after, 0.00),
        ABS(r.cash_balance - COALESCE(cl.balance_after, 0.00)) < 0.01
    FROM robots r
    LEFT JOIN LATERAL (
        SELECT balance_after 
        FROM cash_ledger 
        WHERE robot_id = p_robot_id 
        ORDER BY created_at DESC LIMIT 1
    ) cl ON TRUE
    WHERE r.id = p_robot_id;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION verify_robot_cash IS '校验 robots.cash_balance 与 cash_ledger 是否一致';

-- ============================================================
-- 5. 视图定义
-- ============================================================

-- 5.1 数据健康巡检视图
CREATE OR REPLACE VIEW v_data_health AS
SELECT 
    'lhb_stock_daily' AS table_name,
    trade_date,
    COUNT(*) AS record_count,
    COUNT(DISTINCT stock_code) AS stock_count,
    SUM(CASE WHEN net_buy_amount IS NULL THEN 1 ELSE 0 END) AS null_count
FROM lhb_stock_daily
GROUP BY trade_date
ORDER BY trade_date DESC
LIMIT 30;

COMMENT ON VIEW v_data_health IS '龙虎榜数据健康巡检：检查记录数和空值情况';

CREATE OR REPLACE VIEW v_kline_health AS
SELECT 
    stock_code,
    COUNT(*) AS days,
    MAX(trade_date) AS last_date,
    MIN(trade_date) AS first_date
FROM kline_daily
GROUP BY stock_code
HAVING COUNT(*) < 20
ORDER BY days;

COMMENT ON VIEW v_kline_health IS 'K线数据健康巡检：找出数据天数不足20天的股票';

-- 5.2 机器人健康概览视图
CREATE OR REPLACE VIEW v_robot_health AS
WITH latest_ahs AS (
    -- 取每个机器人最新的 AHS 记录
    SELECT DISTINCT ON (robot_id) 
        robot_id, ahs_score, consecutive_losses, trading_mode, score_date
    FROM ahs_history
    ORDER BY robot_id, score_date DESC
),
latest_weekly AS (
    -- 取每个机器人最新的周汇总
    SELECT DISTINCT ON (robot_id)
        robot_id, weekly_pnl_ratio, week_end_date
    FROM weekly_summary
    ORDER BY robot_id, week_end_date DESC
),
position_counts AS (
    -- 预聚合持仓数量
    SELECT robot_id, COUNT(*) AS open_positions
    FROM robot_trades
    WHERE status IN ('open', 'partial') AND deleted_at IS NULL
    GROUP BY robot_id
)
SELECT 
    r.id,
    r.name,
    r.status,
    r.initial_capital,
    r.current_capital,
    r.cash_balance,
    (r.current_capital + r.cash_balance) AS total_nav,
    r.position_ratio,
    COALESCE(a.ahs_score, 0) AS ahs_score,
    COALESCE(a.consecutive_losses, 0) AS consecutive_losses,
    COALESCE(a.trading_mode, 'normal') AS trading_mode,
    a.score_date AS ahs_date,
    COALESCE(w.weekly_pnl_ratio, 0) AS weekly_pnl_ratio,
    COALESCE(p.open_positions, 0) AS open_positions
FROM robots r
LEFT JOIN latest_ahs a ON a.robot_id = r.id
LEFT JOIN latest_weekly w ON w.robot_id = r.id
LEFT JOIN position_counts p ON p.robot_id = r.id
WHERE r.deleted_at IS NULL;

COMMENT ON VIEW v_robot_health IS '机器人健康概览：资金、AHS评分、持仓情况';

-- 5.3 交易执行对账视图
CREATE OR REPLACE VIEW v_trade_execution_summary AS
SELECT 
    t.id AS trade_id,
    t.robot_id,
    t.stock_code,
    t.status,
    t.buy_amount AS recorded_buy_amount,
    t.sell_amount AS recorded_sell_amount,
    COALESCE(SUM(CASE WHEN e.execution_type = 'buy' THEN e.execution_amount ELSE 0 END), 0) AS calc_buy_amount,
    COALESCE(SUM(CASE WHEN e.execution_type != 'buy' THEN e.execution_amount ELSE 0 END), 0) AS calc_sell_amount,
    ABS(COALESCE(t.buy_amount, 0) - COALESCE(SUM(CASE WHEN e.execution_type = 'buy' THEN e.execution_amount ELSE 0 END), 0)) < 0.01 AS buy_match,
    ABS(COALESCE(t.sell_amount, 0) - COALESCE(SUM(CASE WHEN e.execution_type != 'buy' THEN e.execution_amount ELSE 0 END), 0)) < 0.01 AS sell_match
FROM robot_trades t
LEFT JOIN trade_executions e ON e.trade_id = t.id
GROUP BY t.id, t.robot_id, t.stock_code, t.status, t.buy_amount, t.sell_amount;

COMMENT ON VIEW v_trade_execution_summary IS '交易执行对账：检查 trades 与 executions 的金额一致性';

-- 找出不一致的交易（示例查询）
-- SELECT * FROM v_trade_execution_summary WHERE NOT buy_match OR NOT sell_match;
