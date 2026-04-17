-- ============================================================
-- 龙虎榜量化交易系统 v2 - 初始数据
-- 文件: 004_init_seed_data.sql
-- 说明: 插入基础数据（策略配置、应用配置、默认机器人）
-- ============================================================

-- ============================================================
-- 1. 默认策略
-- ============================================================
INSERT INTO strategies (name, type, version, description, config) VALUES
(
    'lhb_multifactor', 
    'lhb', 
    '1.0.0', 
    '龙虎榜多因子策略（技术面40% + 资金面35% + 历史面25%）',
    '{
        "weights": {"tech": 0.40, "fund": 0.35, "hist": 0.25},
        "min_score": 60,
        "top_n": 3
    }'::JSONB
)
ON CONFLICT (name) DO NOTHING;

-- ============================================================
-- 2. 默认策略参数
-- ============================================================
INSERT INTO strategy_params (strategy_id, param_name, param_value, param_type, description) VALUES
((SELECT id FROM strategies WHERE name = 'lhb_multifactor'), 'min_net_buy', '50000000', 'int', '最低净买入（元）'),
((SELECT id FROM strategies WHERE name = 'lhb_multifactor'), 'min_tech_score', '50', 'int', '最低技术面得分'),
((SELECT id FROM strategies WHERE name = 'lhb_multifactor'), 'max_hold_days', '5', 'int', '最大持仓交易日'),
((SELECT id FROM strategies WHERE name = 'lhb_multifactor'), 'stop_loss_ratio', '-0.05', 'float', '止损阈值'),
((SELECT id FROM strategies WHERE name = 'lhb_multifactor'), 'top_n', '3', 'int', '每日候选数量')
ON CONFLICT (strategy_id, param_name) DO NOTHING;

-- ============================================================
-- 3. 应用配置
-- ============================================================
INSERT INTO app_config (key, value, description) VALUES
('MONITOR_INTERVAL_SEC', '10', '盘中行情轮询间隔（秒）'),
('ELIMINATION_ENABLED', 'true', '是否启用自动淘汰'),
('ELIMINATION_DAY', '5', '每周几执行淘汰（5=周五）'),
('LLM_ENABLED', 'false', 'LLM 选股开关'),
('FEISHU_WEBHOOK_URL', '', '飞书通知 Webhook URL'),
('DATA_SYNC_RETRY_COUNT', '3', '数据同步失败重试次数'),
('SKIP_CIRCUIT_BREAKER', 'false', '跳过熔断检查（仅调试用）'),
('SNAP_INTERVAL_MIN', '5', '行情快照存储间隔（分钟）'),
('LLM_FALLBACK_RULE', 'true', 'LLM 失败时启用规则降级')
ON CONFLICT (key) DO NOTHING;

-- ============================================================
-- 4. 默认机器人
-- ============================================================
INSERT INTO robots (
    name, status, mode, strategy_id, 
    initial_capital, current_capital, cash_balance
) 
SELECT 
    'Alpha-1', 
    'paper_trading', 
    'paper_trading',
    (SELECT id FROM strategies WHERE name = 'lhb_multifactor'),
    100000.00, 
    0.00, 
    100000.00
WHERE NOT EXISTS (SELECT 1 FROM robots WHERE name = 'Alpha-1');

-- ============================================================
-- 5. 股票基础信息（核心股票）
-- ============================================================
-- 注意：实际使用时应通过 AKShare 接口批量导入完整的股票列表
-- 这里仅插入几个示例股票用于测试

INSERT INTO stock_info (stock_code, stock_name, industry, board_type, is_st, list_date, is_active) VALUES
('000001', '平安银行', '银行', '主板', FALSE, '1991-04-03', TRUE),
('000002', '万科A', '房地产', '主板', FALSE, '1991-01-29', TRUE),
('600000', '浦发银行', '银行', '主板', FALSE, '1999-11-10', TRUE),
('600519', '贵州茅台', '食品饮料', '主板', FALSE, '2001-08-27', TRUE),
('000858', '五粮液', '食品饮料', '主板', FALSE, '1998-04-27', TRUE)
ON CONFLICT (stock_code) DO UPDATE SET
    stock_name = EXCLUDED.stock_name,
    industry = EXCLUDED.industry,
    board_type = EXCLUDED.board_type,
    is_st = EXCLUDED.is_st,
    list_date = EXCLUDED.list_date,
    is_active = EXCLUDED.is_active,
    updated_at = NOW();

-- ============================================================
-- 6. 交易日历初始化（示例）
-- ============================================================
-- 注意：实际使用时应通过 AKShare tool_trade_date_hist_sina() 批量导入
-- 这里仅插入几个示例日期

INSERT INTO trade_calendar (trade_date, is_open, market, prev_trading_day, next_trading_day) VALUES
('2024-01-02', TRUE, 'A股', '2023-12-29', '2024-01-03'),
('2024-01-03', TRUE, 'A股', '2024-01-02', '2024-01-04'),
('2024-01-04', TRUE, 'A股', '2024-01-03', '2024-01-05'),
('2024-01-05', TRUE, 'A股', '2024-01-04', '2024-01-08'),
('2024-01-06', FALSE, 'A股', NULL, NULL),  -- 周末
('2024-01-07', FALSE, 'A股', NULL, NULL),  -- 周末
('2024-01-08', TRUE, 'A股', '2024-01-05', '2024-01-09')
ON CONFLICT (trade_date) DO NOTHING;

-- ============================================================
-- 7. 数据库用户权限设置
-- ============================================================
-- 注意：这部分需要超级用户权限执行，通常在部署时单独运行

-- -- 创建应用读写用户
-- DO $$
-- BEGIN
--     IF NOT EXISTS (SELECT FROM pg_user WHERE usename = 'app_rw') THEN
--         CREATE USER app_rw WITH PASSWORD 'change_this_password';
--     END IF;
-- END
-- $$;
-- 
-- GRANT CONNECT ON DATABASE stock_quant TO app_rw;
-- GRANT USAGE ON SCHEMA public TO app_rw;
-- GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public TO app_rw;
-- GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO app_rw;
-- GRANT DELETE ON TABLE trade_events, selection_log, market_snapshots TO app_rw;
-- 
-- -- 创建只读用户
-- DO $$
-- BEGIN
--     IF NOT EXISTS (SELECT FROM pg_user WHERE usename = 'app_ro') THEN
--         CREATE USER app_ro WITH PASSWORD 'change_this_password';
--     END IF;
-- END
-- $$;
-- 
-- GRANT CONNECT ON DATABASE stock_quant TO app_ro;
-- GRANT USAGE ON SCHEMA public TO app_ro;
-- GRANT SELECT ON ALL TABLES IN SCHEMA public TO app_ro;
-- 
-- -- 自动授权新表
-- ALTER DEFAULT PRIVILEGES IN SCHEMA public 
--     GRANT SELECT, INSERT, UPDATE ON TABLES TO app_rw;
-- ALTER DEFAULT PRIVILEGES IN SCHEMA public 
--     GRANT SELECT ON TABLES TO app_ro;
-- ALTER DEFAULT PRIVILEGES IN SCHEMA public 
--     GRANT USAGE, SELECT ON SEQUENCES TO app_rw;

-- ============================================================
-- 完成提示
-- ============================================================
DO $$
BEGIN
    RAISE NOTICE '============================================================';
    RAISE NOTICE '初始数据导入完成！';
    RAISE NOTICE '- 默认策略: lhb_multifactor';
    RAISE NOTICE '- 默认机器人: Alpha-1 (初始资金: 100,000.00)';
    RAISE NOTICE '- 核心股票: 5 只示例股票';
    RAISE NOTICE '- 交易日历: 2024-01 示例数据';
    RAISE NOTICE '============================================================';
    RAISE NOTICE '下一步：';
    RAISE NOTICE '1. 运行数据采集器批量导入完整的股票列表和交易日历';
    RAISE NOTICE '2. 执行首次龙虎榜数据采集';
    RAISE NOTICE '3. 启动定时调度器';
    RAISE NOTICE '============================================================';
END $$;
