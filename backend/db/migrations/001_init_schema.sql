-- ============================================================
-- 龙虎榜量化交易系统 v2 - 数据库初始化脚本
-- 文件: 001_init_schema.sql
-- 说明: 创建所有核心表结构（严格按照 DATABASE.md 定义）
-- ============================================================

-- 注意: TimescaleDB 扩展是可选的，如果未安装将使用普通 PostgreSQL 表
-- 如果已安装 TimescaleDB，可以手动执行: CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;

-- ============================================================
-- 交易核心表
-- ============================================================

-- 3.1 robots (机器人配置)
CREATE TABLE IF NOT EXISTS robots (
    id BIGSERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'paper_trading',
    mode VARCHAR(20) NOT NULL DEFAULT 'paper_trading',
    strategy_id BIGINT NOT NULL,
    initial_capital DECIMAL(16,2) NOT NULL,
    current_capital DECIMAL(16,2) NOT NULL DEFAULT 0.00,
    cash_balance DECIMAL(16,2) NOT NULL DEFAULT 0.00,
    position_ratio DECIMAL(5,4) NOT NULL DEFAULT 0.0000,
    version INT NOT NULL DEFAULT 0,
    config JSONB NOT NULL DEFAULT '{}',
    stop_loss_ratio DECIMAL(6,4) DEFAULT -0.0500,
    take_profit_tiers JSONB DEFAULT '[{"ratio":0.03,"sell_ratio":0.33},{"ratio":0.05,"sell_ratio":0.33},{"ratio":0.08,"sell_ratio":0.34}]',
    max_hold_days INT DEFAULT 5,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at TIMESTAMPTZ,
    CONSTRAINT chk_robots_status CHECK (status IN ('paper_trading', 'running', 'paused', 'stopped', 'eliminated')),
    CONSTRAINT chk_robots_mode CHECK (mode IN ('paper_trading', 'live')),
    CONSTRAINT chk_robots_initial_capital CHECK (initial_capital > 0)
);

COMMENT ON TABLE robots IS '机器人配置表：存储策略配置和实时资金状态';
COMMENT ON COLUMN robots.current_capital IS '当前持仓市值（由结算任务更新），不含现金';
COMMENT ON COLUMN robots.cash_balance IS '当前可用现金（由交易执行原子更新），应用乐观锁';
COMMENT ON COLUMN robots.position_ratio IS '当前仓位比例 = 持仓市值 / (持仓市值 + 现金)';
COMMENT ON COLUMN robots.version IS '乐观锁版本号，每次更新资金时+1，防止并发更新丢失';
COMMENT ON COLUMN robots.stop_loss_ratio IS '策略级止损比例（小数形式），默认-5%';
COMMENT ON COLUMN robots.take_profit_tiers IS '分档止盈配置';

-- 3.2 robot_trades (交易主表)
CREATE TABLE IF NOT EXISTS robot_trades (
    id BIGSERIAL PRIMARY KEY,
    robot_id BIGINT NOT NULL,
    stock_code VARCHAR(6) NOT NULL,
    stock_name VARCHAR(50),
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    signal_date DATE NOT NULL,
    signal_score DECIMAL(5,2),
    signal_rank INT,
    signal_reasons TEXT[],
    buy_date DATE,
    buy_price DECIMAL(10,3),
    buy_shares INT,
    buy_amount DECIMAL(16,2),
    buy_slippage DECIMAL(6,4) DEFAULT 0.0000,
    sell_date DATE,
    sell_price DECIMAL(10,3),
    sell_shares INT,
    sell_amount DECIMAL(16,2),
    sell_reason VARCHAR(50),
    realized_pnl DECIMAL(16,2),
    realized_pnl_ratio DECIMAL(8,4),
    hold_days INT,
    stop_loss_price DECIMAL(10,3),
    take_profit_tiers JSONB,
    max_price DECIMAL(10,3),
    min_price DECIMAL(10,3),
    max_drawdown DECIMAL(8,4),
    max_profit DECIMAL(8,4),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at TIMESTAMPTZ,
    CONSTRAINT chk_robot_trades_status CHECK (status IN ('pending', 'open', 'partial', 'closed', 'abandoned', 'eliminated')),
    CONSTRAINT chk_robot_trades_stock_code CHECK (stock_code ~ '^[0-9]{6}$'),
    CONSTRAINT chk_robot_trades_buy_shares CHECK (buy_shares IS NULL OR buy_shares % 100 = 0)
);

COMMENT ON TABLE robot_trades IS '交易主表：一笔交易从候选到平仓的完整生命周期';
COMMENT ON COLUMN robot_trades.status IS 'pending=候选, open=持仓, partial=部分成交, closed=已平仓, abandoned=已放弃, eliminated=已淘汰';
COMMENT ON COLUMN robot_trades.take_profit_tiers IS '分档止盈状态，如 [{"ratio":0.03,"sell_ratio":0.33,"executed":false}]';

-- 3.3 trade_executions (交易执行明细)
CREATE TABLE IF NOT EXISTS trade_executions (
    id BIGSERIAL PRIMARY KEY,
    trade_id BIGINT NOT NULL,
    execution_type VARCHAR(20) NOT NULL,
    execution_time TIMESTAMPTZ NOT NULL,
    execution_price DECIMAL(10,3) NOT NULL,
    execution_shares INT NOT NULL,
    execution_amount DECIMAL(16,2) NOT NULL,
    slippage DECIMAL(6,4) DEFAULT 0.0000,
    commission DECIMAL(10,2) DEFAULT 0.00,
    stamp_tax DECIMAL(10,2) DEFAULT 0.00,
    transfer_fee DECIMAL(10,2) DEFAULT 0.00,
    total_cost DECIMAL(16,2) DEFAULT 0.00,
    idempotency_key VARCHAR(100) UNIQUE NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_trade_executions_exec_type CHECK (execution_type IN ('buy', 'sell_1', 'sell_2', 'sell_3', 'stop_loss', 'expire', 'manual'))
);

COMMENT ON TABLE trade_executions IS '交易执行明细：每笔实际成交记录（含费用）';
COMMENT ON COLUMN trade_executions.commission IS '佣金（最低5元）';
COMMENT ON COLUMN trade_executions.stamp_tax IS '印花税（卖出时0.05%）';
COMMENT ON COLUMN trade_executions.transfer_fee IS '过户费（0.001%）';

-- 3.4 cash_ledger (现金流水)
CREATE TABLE IF NOT EXISTS cash_ledger (
    id BIGSERIAL PRIMARY KEY,
    robot_id BIGINT NOT NULL,
    trans_type VARCHAR(20) NOT NULL,
    amount DECIMAL(16,2) NOT NULL,
    balance_after DECIMAL(16,2) NOT NULL,
    ref_type VARCHAR(50),
    ref_id BIGINT,
    memo TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_cash_ledger_trans_type CHECK (trans_type IN ('deposit', 'withdraw', 'buy', 'sell', 'commission', 'stamp_tax', 'transfer_fee', 'dividend', 'adjustment'))
);

COMMENT ON TABLE cash_ledger IS '现金流水：所有资金进出记录，用于一致性校验';

-- 3.5 daily_nav (净值曲线)
CREATE TABLE IF NOT EXISTS daily_nav (
    id BIGSERIAL PRIMARY KEY,
    robot_id BIGINT NOT NULL,
    nav_date DATE NOT NULL,
    open_nav DECIMAL(16,2) NOT NULL,
    close_nav DECIMAL(16,2) NOT NULL,
    daily_pnl DECIMAL(16,2) NOT NULL,
    daily_pnl_ratio DECIMAL(10,6) NOT NULL,
    position_value DECIMAL(16,2) NOT NULL,
    cash_balance DECIMAL(16,2) NOT NULL,
    position_ratio DECIMAL(10,6) NOT NULL,
    max_drawdown DECIMAL(10,6),
    sharpe_ratio DECIMAL(10,6),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uk_daily_nav_robot_date UNIQUE (robot_id, nav_date)
);

COMMENT ON TABLE daily_nav IS '每日净值快照：用于计算收益曲线和风险指标';

-- 3.6 weekly_summary (周汇总)
CREATE TABLE IF NOT EXISTS weekly_summary (
    id BIGSERIAL PRIMARY KEY,
    robot_id BIGINT NOT NULL,
    week_start_date DATE NOT NULL,
    week_end_date DATE NOT NULL,
    start_nav DECIMAL(16,2) NOT NULL,
    end_nav DECIMAL(16,2) NOT NULL,
    weekly_pnl DECIMAL(16,2) NOT NULL,
    weekly_pnl_ratio DECIMAL(10,6) NOT NULL,
    trade_count INT DEFAULT 0,
    win_count INT DEFAULT 0,
    lose_count INT DEFAULT 0,
    max_drawdown DECIMAL(8,4),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uk_weekly_summary_robot_week UNIQUE (robot_id, week_start_date)
);

COMMENT ON TABLE weekly_summary IS '周度汇总：用于淘汰机制计算';

-- 3.7 ahs_history (AHS评分历史)
CREATE TABLE IF NOT EXISTS ahs_history (
    id BIGSERIAL PRIMARY KEY,
    robot_id BIGINT NOT NULL,
    score_date DATE NOT NULL,
    ahs_score DECIMAL(5,2) NOT NULL,
    consecutive_losses INT DEFAULT 0,
    weekly_pnl_ratio DECIMAL(10,6),
    win_rate DECIMAL(10,6),
    profit_loss_ratio DECIMAL(10,6),
    loss_penalty DECIMAL(5,2),
    winrate_penalty DECIMAL(5,2),
    plr_penalty DECIMAL(5,2),
    trading_mode VARCHAR(20) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_ahs_history_mode CHECK (trading_mode IN ('normal', 'cautious', 'warning', 'crisis'))
);

COMMENT ON TABLE ahs_history IS 'AHS评分历史：追溯风控决策';

-- ============================================================
-- 市场数据表
-- ============================================================

-- 3.8 lhb_stock_daily (龙虎榜日数据)
CREATE TABLE IF NOT EXISTS lhb_stock_daily (
    id BIGSERIAL PRIMARY KEY,
    trade_date DATE NOT NULL,
    stock_code VARCHAR(6) NOT NULL,
    stock_name VARCHAR(50),
    close_price DECIMAL(10,3),
    change_ratio DECIMAL(10,6),
    turnover_rate DECIMAL(10,6),
    total_buy_amount DECIMAL(16,2),
    total_sell_amount DECIMAL(16,2),
    net_buy_amount DECIMAL(16,2),
    net_buy_ratio DECIMAL(10,6),
    buy_seats INT DEFAULT 0,
    sell_seats INT DEFAULT 0,
    institution_buy_amount DECIMAL(16,2) DEFAULT 0.00,
    institution_sell_amount DECIMAL(16,2) DEFAULT 0.00,
    institution_net_buy DECIMAL(16,2) DEFAULT 0.00,
    reason VARCHAR(200),
    board_type VARCHAR(20),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uk_lhb_stock_daily_date_code UNIQUE (trade_date, stock_code),
    CONSTRAINT chk_lhb_stock_daily_stock_code CHECK (stock_code ~ '^[0-9]{6}$')
);

COMMENT ON TABLE lhb_stock_daily IS '龙虎榜每日股票汇总数据';

-- 3.9 lhb_seats (席位明细)
CREATE TABLE IF NOT EXISTS lhb_seats (
    id BIGSERIAL PRIMARY KEY,
    trade_date DATE NOT NULL,
    stock_code VARCHAR(6) NOT NULL,
    seat_name VARCHAR(100) NOT NULL,
    seat_type VARCHAR(20) NOT NULL,
    direction VARCHAR(4) NOT NULL,
    rank INT NOT NULL,
    amount DECIMAL(18,2) NOT NULL DEFAULT 0.00,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_lhb_seats_seat_type CHECK (seat_type IN ('institution', 'broker', 'individual')),
    CONSTRAINT chk_lhb_seats_direction CHECK (direction IN ('buy', 'sell')),
    CONSTRAINT chk_lhb_seats_rank CHECK (rank BETWEEN 1 AND 5),
    CONSTRAINT chk_lhb_seats_stock CHECK (stock_code ~ '^[0-9]{6}$'),
    CONSTRAINT uk_lhb_seats_date_stock_dir_rank UNIQUE (trade_date, stock_code, direction, rank)
);

COMMENT ON TABLE lhb_seats IS '龙虎榜席位明细：买入和卖出席位分开记录，带排名';
COMMENT ON COLUMN lhb_seats.direction IS 'buy=买入, sell=卖出';
COMMENT ON COLUMN lhb_seats.rank IS '席位排名 1-5';

-- 3.10 lhb_tn_prices (T+N 价格纵表)
CREATE TABLE IF NOT EXISTS lhb_tn_prices (
    id BIGSERIAL PRIMARY KEY,
    trade_date DATE NOT NULL,
    stock_code VARCHAR(6) NOT NULL,
    n_days INT NOT NULL,
    close_price DECIMAL(10,3),
    high_price DECIMAL(10,3),
    low_price DECIMAL(10,3),
    high_ret DECIMAL(10,4),
    close_ret DECIMAL(10,4),
    low_ret DECIMAL(10,4),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uk_lhb_tn_prices_date_code_n UNIQUE (trade_date, stock_code, n_days),
    CONSTRAINT chk_lhb_tn_prices_n_days CHECK (n_days >= 1),
    CONSTRAINT chk_lhb_tn_prices_stock CHECK (stock_code ~ '^[0-9]{6}$')
);

COMMENT ON TABLE lhb_tn_prices IS '龙虎榜T+N表现：纵表设计，N无上限';
COMMENT ON COLUMN lhb_tn_prices.n_days IS 'T+N 中的 N，取值 >= 1';

-- 3.11 lhb_agg_stats (龙虎榜汇总统计)
CREATE TABLE IF NOT EXISTS lhb_agg_stats (
    id BIGSERIAL PRIMARY KEY,
    stock_code VARCHAR(6) NOT NULL,
    stat_date DATE NOT NULL,
    lookback_days INT NOT NULL,
    total_list_count INT DEFAULT 0,
    total_net_buy DECIMAL(16,2) DEFAULT 0.00,
    avg_net_buy DECIMAL(16,2) DEFAULT 0.00,
    win_rate DECIMAL(10,6) DEFAULT 0.000000,
    avg_high_ret DECIMAL(10,6) DEFAULT 0.000000,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uk_lhb_agg_stats_code_date_days UNIQUE (stock_code, stat_date, lookback_days),
    CONSTRAINT chk_lhb_agg_stats_stock CHECK (stock_code ~ '^[0-9]{6}$')
);

COMMENT ON TABLE lhb_agg_stats IS '龙虎榜多日汇总统计';

-- 3.12 lhb_institution_stats (机构统计)
CREATE TABLE IF NOT EXISTS lhb_institution_stats (
    id BIGSERIAL PRIMARY KEY,
    seat_name VARCHAR(100) NOT NULL,
    stat_date DATE NOT NULL,
    lookback_days INT NOT NULL,
    total_buy_amount DECIMAL(16,2) DEFAULT 0.00,
    total_sell_amount DECIMAL(16,2) DEFAULT 0.00,
    trade_count INT DEFAULT 0,
    win_count INT DEFAULT 0,
    win_rate DECIMAL(10,6) DEFAULT 0.000000,
    avg_pnl_ratio DECIMAL(10,6) DEFAULT 0.000000,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uk_lhb_inst_stats_seat_date_days UNIQUE (seat_name, stat_date, lookback_days)
);

COMMENT ON TABLE lhb_institution_stats IS '机构席位历史胜率统计';

-- 3.13 kline_daily (日K线，TimescaleDB)
CREATE TABLE IF NOT EXISTS kline_daily (
    trade_date DATE NOT NULL,
    stock_code VARCHAR(6) NOT NULL,
    open_price DECIMAL(10,3),
    high_price DECIMAL(10,3),
    low_price DECIMAL(10,3),
    close_price DECIMAL(10,3),
    volume BIGINT,
    amount DECIMAL(16,2),
    turnover_rate DECIMAL(10,6),
    change_ratio DECIMAL(10,6),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (trade_date, stock_code),
    CONSTRAINT chk_kline_daily_stock CHECK (stock_code ~ '^[0-9]{6}$'),
    CONSTRAINT chk_kline_daily_price CHECK (
        high_price >= low_price AND
        high_price >= open_price AND
        high_price >= close_price AND
        low_price <= open_price AND
        low_price <= close_price
    )
);

COMMENT ON TABLE kline_daily IS '日K线数据（TimescaleDB hypertable，无MA冗余字段）';

-- 注意: TimescaleDB 超表创建已禁用，如需启用请手动执行:
-- SELECT create_hypertable('kline_daily', 'trade_date', if_not_exists => TRUE);

-- 3.14 market_snapshots (盘中快照，TimescaleDB)
CREATE TABLE IF NOT EXISTS market_snapshots (
    snapshot_time TIMESTAMPTZ NOT NULL,
    stock_code VARCHAR(6) NOT NULL,
    current_price DECIMAL(10,3),
    bid_price1 DECIMAL(10,3),
    ask_price1 DECIMAL(10,3),
    bid_volume1 BIGINT,
    ask_volume1 BIGINT,
    volume BIGINT,
    amount DECIMAL(16,2),
    high_price DECIMAL(10,3),
    low_price DECIMAL(10,3),
    open_price DECIMAL(10,3),
    prev_close DECIMAL(10,3),
    change_ratio DECIMAL(10,6),
    turnover_rate DECIMAL(10,6),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (snapshot_time, stock_code),
    CONSTRAINT chk_market_snapshots_stock CHECK (stock_code ~ '^[0-9]{6}$')
);

COMMENT ON TABLE market_snapshots IS '盘中实时快照（TimescaleDB hypertable，每10秒采集）';

-- 注意: TimescaleDB 超表创建已禁用，如需启用请手动执行:
-- SELECT create_hypertable('market_snapshots', 'snapshot_time', if_not_exists => TRUE);

-- 3.15 market_sentiment (市场情绪)
CREATE TABLE IF NOT EXISTS market_sentiment (
    id BIGSERIAL PRIMARY KEY,
    trade_date DATE NOT NULL UNIQUE,
    limit_up_count INT DEFAULT 0,
    limit_down_count INT DEFAULT 0,
    rise_count INT DEFAULT 0,
    fall_count INT DEFAULT 0,
    flat_count INT DEFAULT 0,
    total_amount DECIMAL(20,2) DEFAULT 0.00,
    limit_up_ratio DECIMAL(10,6) DEFAULT 0.000000,
    limit_down_ratio DECIMAL(10,6) DEFAULT 0.000000,
    avg_turnover DECIMAL(10,6) DEFAULT 0.000000,
    sentiment_score DECIMAL(5,2) DEFAULT 50.00,
    market_stage VARCHAR(20) DEFAULT 'normal',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE market_sentiment IS '市场情绪指标：涨跌停数量、成交额、情绪评分';
COMMENT ON COLUMN market_sentiment.market_stage IS '市场阶段：hot/normal/cold/crisis';

-- ============================================================
-- 配置表
-- ============================================================

-- 3.16 strategies (策略配置)
CREATE TABLE IF NOT EXISTS strategies (
    id BIGSERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL UNIQUE,
    type VARCHAR(50) NOT NULL,
    version VARCHAR(20) NOT NULL DEFAULT '1.0.0',
    description TEXT,
    config JSONB NOT NULL DEFAULT '{}',
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at TIMESTAMPTZ
);

COMMENT ON TABLE strategies IS '策略配置表';

-- 3.17 strategy_params (策略参数)
CREATE TABLE IF NOT EXISTS strategy_params (
    id BIGSERIAL PRIMARY KEY,
    strategy_id BIGINT NOT NULL,
    param_name VARCHAR(50) NOT NULL,
    param_value TEXT NOT NULL,
    param_type VARCHAR(20) NOT NULL DEFAULT 'string',
    description TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uk_strategy_params_strategy_param UNIQUE (strategy_id, param_name)
);

COMMENT ON TABLE strategy_params IS '策略参数表：支持运行时动态调整';

-- 3.18 trade_events (交易事件)
CREATE TABLE IF NOT EXISTS trade_events (
    id BIGSERIAL PRIMARY KEY,
    trade_id BIGINT NOT NULL,
    event_type VARCHAR(50) NOT NULL,
    event_time TIMESTAMPTZ NOT NULL,
    event_data JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE trade_events IS '交易事件日志：记录交易的每个关键事件';

-- 3.19 selection_log (选股日志)
CREATE TABLE IF NOT EXISTS selection_log (
    id BIGSERIAL PRIMARY KEY,
    robot_id BIGINT NOT NULL,
    signal_date DATE NOT NULL,
    stock_code VARCHAR(6) NOT NULL,
    phase VARCHAR(10) NOT NULL,
    final_score DECIMAL(5,2),
    rank INT,
    factor_scores JSONB,
    reasons TEXT[],
    action_taken VARCHAR(20),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_selection_log_phase CHECK (phase IN ('A', 'B')),
    CONSTRAINT chk_selection_log_action CHECK (action_taken IN ('confirmed', 'rejected', 'pending', 'expired'))
);

COMMENT ON TABLE selection_log IS '选股日志：Phase A/B 的候选和确认记录';

-- 3.20 audit_log (审计日志)
CREATE TABLE IF NOT EXISTS audit_log (
    id BIGSERIAL PRIMARY KEY,
    user_id VARCHAR(100) DEFAULT 'system',
    action VARCHAR(100) NOT NULL,
    resource_type VARCHAR(50) NOT NULL,
    resource_id BIGINT,
    old_value JSONB,
    new_value JSONB,
    ip_address VARCHAR(45),
    user_agent TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE audit_log IS '审计日志：记录所有关键操作的变更历史';

-- 3.21 辅助表
CREATE TABLE IF NOT EXISTS app_config (
    key VARCHAR(100) PRIMARY KEY,
    value TEXT NOT NULL,
    description TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE app_config IS '应用配置表';

CREATE TABLE IF NOT EXISTS trade_calendar (
    trade_date DATE PRIMARY KEY,
    is_open BOOLEAN NOT NULL DEFAULT TRUE,
    market VARCHAR(20) DEFAULT 'A股',
    prev_trading_day DATE,
    next_trading_day DATE
);

COMMENT ON TABLE trade_calendar IS '交易日历表';

CREATE TABLE IF NOT EXISTS stock_info (
    stock_code VARCHAR(6) PRIMARY KEY,
    stock_name VARCHAR(50) NOT NULL,
    industry VARCHAR(50),
    board_type VARCHAR(20),
    is_st BOOLEAN DEFAULT FALSE,
    list_date DATE,
    is_active BOOLEAN DEFAULT TRUE,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE stock_info IS '股票基础信息表';

-- 3.22 collector_error_log (采集错误日志)
CREATE TABLE IF NOT EXISTS collector_error_log (
    id BIGSERIAL PRIMARY KEY,
    collector_name VARCHAR(100) NOT NULL,
    error_type VARCHAR(50) NOT NULL,
    error_message TEXT NOT NULL,
    trade_date DATE,
    retry_count INT DEFAULT 0,
    resolved BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE collector_error_log IS '数据采集错误日志表';
