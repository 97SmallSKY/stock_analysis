-- ============================================================
-- 龙虎榜量化交易系统 v2 - 索引和外键约束
-- 文件: 002_init_indexes.sql
-- 说明: 创建所有索引和约束（根据 DATABASE.md，删除冗余索引）
-- ============================================================

-- ============================================================
-- robots 表索引
-- ============================================================
CREATE INDEX IF NOT EXISTS idx_robots_status ON robots(status) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_robots_strategy ON robots(strategy_id);
CREATE INDEX IF NOT EXISTS idx_robots_deleted ON robots(deleted_at);

-- ============================================================
-- robot_trades 表索引
-- ============================================================
CREATE INDEX IF NOT EXISTS idx_trades_robot ON robot_trades(robot_id);
CREATE INDEX IF NOT EXISTS idx_trades_status ON robot_trades(status);
CREATE INDEX IF NOT EXISTS idx_trades_signal_date ON robot_trades(signal_date);
CREATE INDEX IF NOT EXISTS idx_trades_stock ON robot_trades(stock_code);
CREATE INDEX IF NOT EXISTS idx_trades_robot_status ON robot_trades(robot_id, status) WHERE deleted_at IS NULL;

-- ============================================================
-- trade_executions 表索引
-- ============================================================
CREATE INDEX IF NOT EXISTS idx_executions_trade ON trade_executions(trade_id);
CREATE INDEX IF NOT EXISTS idx_executions_time ON trade_executions(execution_time);
CREATE INDEX IF NOT EXISTS idx_executions_idempotency ON trade_executions(idempotency_key);

-- ============================================================
-- cash_ledger 表索引
-- ============================================================
CREATE INDEX IF NOT EXISTS idx_cash_robot ON cash_ledger(robot_id);
CREATE INDEX IF NOT EXISTS idx_cash_time ON cash_ledger(created_at);
CREATE INDEX IF NOT EXISTS idx_cash_ref ON cash_ledger(ref_type, ref_id);

-- ============================================================
-- daily_nav 表索引
-- ============================================================
CREATE INDEX IF NOT EXISTS idx_nav_robot_date ON daily_nav(robot_id, nav_date);

-- ============================================================
-- weekly_summary 表索引
-- ============================================================
CREATE INDEX IF NOT EXISTS idx_weekly_robot ON weekly_summary(robot_id);
CREATE INDEX IF NOT EXISTS idx_weekly_date ON weekly_summary(week_end_date);

-- ============================================================
-- ahs_history 表索引
-- ============================================================
CREATE INDEX IF NOT EXISTS idx_ahs_robot ON ahs_history(robot_id);
CREATE INDEX IF NOT EXISTS idx_ahs_date ON ahs_history(score_date);

-- ============================================================
-- lhb_stock_daily 表索引（注意：删除冗余索引 idx_lhb_date）
-- ============================================================
CREATE INDEX IF NOT EXISTS idx_lhb_stock ON lhb_stock_daily(stock_code);
CREATE INDEX IF NOT EXISTS idx_lhb_net_buy ON lhb_stock_daily(net_buy_amount DESC);
-- 冗余索引已删除：idx_lhb_date（被 uk_lhb_stock_daily_date_code 覆盖）

-- ============================================================
-- lhb_seats 表索引（注意：删除冗余索引 idx_seats_date）
-- ============================================================
CREATE INDEX IF NOT EXISTS idx_seats_stock ON lhb_seats(stock_code);
CREATE INDEX IF NOT EXISTS idx_seats_name ON lhb_seats(seat_name);
-- 冗余索引已删除：idx_seats_date（被 uk_lhb_seats_date_stock_dir_rank 覆盖）

-- ============================================================
-- lhb_tn_prices 表索引（注意：删除所有冗余索引）
-- ============================================================
-- 冗余索引已删除：idx_tn_date、idx_tn_date_stock（被 uk_lhb_tn_prices_date_code_n 覆盖）

-- ============================================================
-- lhb_agg_stats 表索引
-- ============================================================
CREATE INDEX IF NOT EXISTS idx_agg_stock ON lhb_agg_stats(stock_code);
CREATE INDEX IF NOT EXISTS idx_agg_date ON lhb_agg_stats(stat_date);

-- ============================================================
-- lhb_institution_stats 表索引
-- ============================================================
CREATE INDEX IF NOT EXISTS idx_inst_seat ON lhb_institution_stats(seat_name);
CREATE INDEX IF NOT EXISTS idx_inst_date ON lhb_institution_stats(stat_date);

-- ============================================================
-- kline_daily 表索引（TimescaleDB 超表）
-- ============================================================
CREATE INDEX IF NOT EXISTS idx_kline_stock ON kline_daily(stock_code);
-- 冗余索引已删除：idx_kline_date_stock（被主键覆盖）

-- ============================================================
-- market_snapshots 表索引（TimescaleDB 超表）
-- ============================================================
CREATE INDEX IF NOT EXISTS idx_snapshot_stock ON market_snapshots(stock_code);
CREATE INDEX IF NOT EXISTS idx_snapshot_time_stock ON market_snapshots(snapshot_time, stock_code);

-- ============================================================
-- market_sentiment 表索引
-- ============================================================
CREATE INDEX IF NOT EXISTS idx_sentiment_date ON market_sentiment(trade_date);

-- ============================================================
-- strategies 表索引
-- ============================================================
CREATE INDEX IF NOT EXISTS idx_strategies_active ON strategies(is_active) WHERE deleted_at IS NULL;

-- ============================================================
-- strategy_params 表索引
-- ============================================================
CREATE INDEX IF NOT EXISTS idx_params_strategy ON strategy_params(strategy_id);

-- ============================================================
-- trade_events 表索引
-- ============================================================
CREATE INDEX IF NOT EXISTS idx_events_trade ON trade_events(trade_id);
CREATE INDEX IF NOT EXISTS idx_events_time ON trade_events(event_time);
CREATE INDEX IF NOT EXISTS idx_events_type ON trade_events(event_type);

-- ============================================================
-- selection_log 表索引
-- ============================================================
CREATE INDEX IF NOT EXISTS idx_selection_robot ON selection_log(robot_id);
CREATE INDEX IF NOT EXISTS idx_selection_date ON selection_log(signal_date);
CREATE INDEX IF NOT EXISTS idx_selection_robot_date ON selection_log(robot_id, signal_date);

-- ============================================================
-- audit_log 表索引
-- ============================================================
CREATE INDEX IF NOT EXISTS idx_audit_user ON audit_log(user_id);
CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_log(action);
CREATE INDEX IF NOT EXISTS idx_audit_resource ON audit_log(resource_type, resource_id);
CREATE INDEX IF NOT EXISTS idx_audit_time ON audit_log(created_at);

-- ============================================================
-- trade_calendar 表索引
-- ============================================================
CREATE INDEX IF NOT EXISTS idx_calendar_open ON trade_calendar(is_open) WHERE is_open = TRUE;

-- ============================================================
-- stock_info 表索引
-- ============================================================
CREATE INDEX IF NOT EXISTS idx_stock_active ON stock_info(is_active);
CREATE INDEX IF NOT EXISTS idx_stock_industry ON stock_info(industry);

-- ============================================================
-- collector_error_log 表索引
-- ============================================================
CREATE INDEX IF NOT EXISTS idx_collector_error_name ON collector_error_log(collector_name);
CREATE INDEX IF NOT EXISTS idx_collector_error_date ON collector_error_log(trade_date);
CREATE INDEX IF NOT EXISTS idx_collector_error_resolved ON collector_error_log(resolved) WHERE resolved = FALSE;

-- ============================================================
-- 外键约束（延迟添加以提高导入性能）
-- ============================================================

-- 注意: 外键约束已在表创建时定义，此处仅作为备份
-- 如果需要重新添加外键，请先删除旧约束再添加新约束

-- robots 表外键
-- ALTER TABLE robots ADD CONSTRAINT fk_robots_strategy FOREIGN KEY (strategy_id) REFERENCES strategies(id);

-- robot_trades 表外键
-- ALTER TABLE robot_trades ADD CONSTRAINT fk_robot_trades_robot FOREIGN KEY (robot_id) REFERENCES robots(id);

-- trade_executions 表外键
-- ALTER TABLE trade_executions ADD CONSTRAINT fk_trade_executions_trade FOREIGN KEY (trade_id) REFERENCES robot_trades(id);

-- cash_ledger 表外键
-- ALTER TABLE cash_ledger ADD CONSTRAINT fk_cash_ledger_robot FOREIGN KEY (robot_id) REFERENCES robots(id);

-- daily_nav 表外键
-- ALTER TABLE daily_nav ADD CONSTRAINT fk_daily_nav_robot FOREIGN KEY (robot_id) REFERENCES robots(id);

-- weekly_summary 表外键
-- ALTER TABLE weekly_summary ADD CONSTRAINT fk_weekly_summary_robot FOREIGN KEY (robot_id) REFERENCES robots(id);

-- ahs_history 表外键
-- ALTER TABLE ahs_history ADD CONSTRAINT fk_ahs_history_robot FOREIGN KEY (robot_id) REFERENCES robots(id);

-- strategy_params 表外键
-- ALTER TABLE strategy_params ADD CONSTRAINT fk_strategy_params_strategy FOREIGN KEY (strategy_id) REFERENCES strategies(id);

-- trade_events 表外键
-- ALTER TABLE trade_events ADD CONSTRAINT fk_trade_events_trade FOREIGN KEY (trade_id) REFERENCES robot_trades(id);

-- selection_log 表外键
-- ALTER TABLE selection_log ADD CONSTRAINT fk_selection_log_robot FOREIGN KEY (robot_id) REFERENCES robots(id);

-- ============================================================
-- TimescaleDB 压缩策略（如果已安装 TimescaleDB 可手动执行）
-- ============================================================

-- 注意: 以下语句需要 TimescaleDB 扩展，如未安装请忽略

-- kline_daily: 超过3个月的数据压缩
-- SELECT add_compression_policy('kline_daily', INTERVAL '3 months', if_not_exists => TRUE);

-- market_snapshots: 超过7天的数据压缩
-- SELECT add_compression_policy('market_snapshots', INTERVAL '7 days', if_not_exists => TRUE);

-- ============================================================
-- TimescaleDB 数据保留策略（如果已安装 TimescaleDB 可手动执行）
-- ============================================================

-- kline_daily: 超过2年的数据归档
-- SELECT add_retention_policy('kline_daily', INTERVAL '2 years', if_not_exists => TRUE);

-- market_snapshots: 超过90天的数据删除
-- SELECT add_retention_policy('market_snapshots', INTERVAL '90 days', if_not_exists => TRUE);
