-- ============================================================
-- 龙虎榜量化系统 数据库初始化脚本
-- 数据库: PostgreSQL 15+
-- 编码: UTF-8
-- 约定: 百分比统一存小数形式 (5.23% → 0.0523)
--       金额统一 DECIMAL(16,2)
--       价格统一 DECIMAL(12,4)
-- ============================================================

-- 清理旧对象(开发环境用，生产环境请注释掉)
-- DROP SCHEMA public CASCADE;
-- CREATE SCHEMA public;

-- ============================================================
-- 0. 枚举类型定义
-- ============================================================

-- 机器人状态
DO $$ BEGIN
    CREATE TYPE robot_status AS ENUM ('IDLE', 'RUNNING', 'PAUSED', 'STOPPED', 'ERROR');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- 交易方向
DO $$ BEGIN
    CREATE TYPE trade_direction AS ENUM ('BUY', 'SELL');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- 交易阶段
DO $$ BEGIN
    CREATE TYPE trade_phase AS ENUM ('PHASE_A', 'PHASE_B', 'MANUAL', 'STOP_LOSS', 'TAKE_PROFIT', 'MONITOR');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- 交易执行状态
DO $$ BEGIN
    CREATE TYPE exec_status AS ENUM ('PENDING', 'FILLED', 'PARTIAL', 'REJECTED', 'CANCELLED');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- 资金流水类型
DO $$ BEGIN
    CREATE TYPE ledger_type AS ENUM ('DEPOSIT', 'WITHDRAW', 'BUY_FREEZE', 'SELL_RETURN', 'COMMISSION', 'SLIPPAGE', 'DIVIDEND');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- 模拟交易状态
DO $$ BEGIN
    CREATE TYPE sim_trade_status AS ENUM ('OPEN', 'CLOSED', 'STOP_LOSS', 'TAKE_PROFIT', 'EXPIRED');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- 回测状态
DO $$ BEGIN
    CREATE TYPE backtest_status AS ENUM ('PENDING', 'RUNNING', 'COMPLETED', 'FAILED');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- 龙虎榜席位方向
DO $$ BEGIN
    CREATE TYPE seat_direction AS ENUM ('BUY', 'SELL');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- 熔断器状态
DO $$ BEGIN
    CREATE TYPE circuit_state AS ENUM ('CLOSED', 'OPEN', 'HALF_OPEN');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- 审计操作类型
DO $$ BEGIN
    CREATE TYPE audit_action AS ENUM ('CREATE', 'UPDATE', 'DELETE', 'START', 'STOP', 'PAUSE', 'RESUME', 'EXECUTE', 'ERROR');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- 数据类别
DO $$ BEGIN
    CREATE TYPE data_category AS ENUM (
        'DAILY_KLINE', 'LHB_DETAIL', 'LHB_BROKER', 'INSTITUTION',
        'REALTIME_QUOTE', 'MARKET_SENTIMENT', 'FINANCIAL_DATA'
    );
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;


-- ============================================================
-- ============================================================
-- 第一部分: 基础配置表 (9张)
-- ============================================================
-- ============================================================


-- ============================================================
-- 1.1 股票基础信息
-- ============================================================
CREATE TABLE IF NOT EXISTS stock_info (
    ts_code             VARCHAR(16)     PRIMARY KEY,                     -- 000001.SZ
    symbol              VARCHAR(10)     NOT NULL,                        -- 000001
    name                VARCHAR(32)     NOT NULL,                        -- 平安银行
    area                VARCHAR(16),                                     -- 深圳
    industry            VARCHAR(32),                                     -- 银行
    market              VARCHAR(16),                                     -- 主板/创业板/科创板/北交所
    list_date           DATE,                                            -- 上市日期
    delist_date         DATE,                                            -- 退市日期
    is_hs               VARCHAR(4)      DEFAULT 'N',                     -- 沪深港通标的 N/H/S
    exchange            VARCHAR(8),                                      -- SSE/SZSE/BSE
    curr_type           VARCHAR(8)      DEFAULT 'CNY',
    is_active           BOOLEAN         DEFAULT TRUE,
    created_at          TIMESTAMP       DEFAULT NOW(),
    updated_at          TIMESTAMP       DEFAULT NOW()
);

COMMENT ON TABLE stock_info IS '股票基础信息表';
COMMENT ON COLUMN stock_info.ts_code IS '股票代码，如 000001.SZ';
COMMENT ON COLUMN stock_info.is_hs IS '沪深港通标的: N-否 H-沪股通 S-深股通';

CREATE INDEX IF NOT EXISTS idx_stock_info_industry ON stock_info(industry);
CREATE INDEX IF NOT EXISTS idx_stock_info_market ON stock_info(market);
CREATE INDEX IF NOT EXISTS idx_stock_info_name ON stock_info(name);


-- ============================================================
-- 1.2 交易日历
-- ============================================================
CREATE TABLE IF NOT EXISTS trade_calendar (
    id                  BIGSERIAL       PRIMARY KEY,
    exchange            VARCHAR(8)      NOT NULL DEFAULT 'SSE',          -- SSE/SZSE
    cal_date            DATE            NOT NULL,                        -- 日历日期
    is_open             BOOLEAN         NOT NULL DEFAULT FALSE,          -- 是否交易日
    pretrade_date       DATE,                                            -- 上一交易日
    created_at          TIMESTAMP       DEFAULT NOW(),

    CONSTRAINT uk_trade_calendar UNIQUE(exchange, cal_date)
);

COMMENT ON TABLE trade_calendar IS '交易日历';

CREATE INDEX IF NOT EXISTS idx_calendar_date ON trade_calendar(cal_date);
CREATE INDEX IF NOT EXISTS idx_calendar_open ON trade_calendar(exchange, cal_date) WHERE is_open = TRUE;


-- ============================================================
-- 1.3 应用配置
-- ============================================================
CREATE TABLE IF NOT EXISTS app_config (
    id                  BIGSERIAL       PRIMARY KEY,
    config_key          VARCHAR(128)    NOT NULL UNIQUE,                 -- 配置键
    config_value        TEXT,                                            -- 配置值
    value_type          VARCHAR(16)     DEFAULT 'STRING',                -- STRING/INT/FLOAT/BOOL/JSON
    category            VARCHAR(64)     DEFAULT 'SYSTEM',                -- 分类: SYSTEM/TRADING/DATA/DISPLAY
    description         TEXT,                                            -- 配置说明
    is_active           BOOLEAN         DEFAULT TRUE,
    created_at          TIMESTAMP       DEFAULT NOW(),
    updated_at          TIMESTAMP       DEFAULT NOW()
);

COMMENT ON TABLE app_config IS '应用全局配置表';

-- 插入默认配置
INSERT INTO app_config (config_key, config_value, value_type, category, description) VALUES
    ('trading.commission_rate', '0.0003', 'FLOAT', 'TRADING', '佣金费率(万三)'),
    ('trading.stamp_tax_rate', '0.001', 'FLOAT', 'TRADING', '印花税费率(千一，仅卖出)'),
    ('trading.slippage_rate', '0.001', 'FLOAT', 'TRADING', '滑点估算费率'),
    ('trading.min_commission', '5', 'FLOAT', 'TRADING', '最低佣金(元)'),
    ('trading.phase_a_time', '18:50', 'STRING', 'TRADING', 'Phase A 执行时间'),
    ('trading.phase_b_time', '09:35', 'STRING', 'TRADING', 'Phase B 执行时间'),
    ('scoring.tech_weight', '0.40', 'FLOAT', 'TRADING', '技术面评分权重'),
    ('scoring.fund_weight', '0.35', 'FLOAT', 'TRADING', '资金面评分权重'),
    ('scoring.hist_weight', '0.25', 'FLOAT', 'TRADING', '历史胜率评分权重'),
    ('data.sync_time', '17:00', 'STRING', 'DATA', '每日数据同步时间'),
    ('data.retry_count', '3', 'INT', 'DATA', '数据拉取重试次数'),
    ('circuit_breaker.failure_threshold', '5', 'INT', 'DATA', '熔断器失败阈值'),
    ('circuit_breaker.reset_timeout', '300', 'INT', 'DATA', '熔断器重置超时(秒)')
ON CONFLICT (config_key) DO NOTHING;


-- ============================================================
-- 1.4 策略配置
-- ============================================================
CREATE TABLE IF NOT EXISTS strategies (
    id                  BIGSERIAL       PRIMARY KEY,
    name                VARCHAR(64)     NOT NULL,
    description         TEXT,
    category            VARCHAR(32)     DEFAULT 'LHB',                   -- LHB/MOMENTUM/CUSTOM

    -- 规则定义 (JSONB)
    entry_rules         JSONB           NOT NULL DEFAULT '[]'::jsonb,    -- 入场规则
    exit_rules          JSONB           NOT NULL DEFAULT '[]'::jsonb,    -- 出场规则
    risk_rules          JSONB           NOT NULL DEFAULT '[]'::jsonb,    -- 风控规则
    params              JSONB           NOT NULL DEFAULT '{}'::jsonb,    -- 默认参数

    -- 元信息
    version             INT             DEFAULT 1,
    is_active           BOOLEAN         DEFAULT TRUE,
    author              VARCHAR(32),
    tags                VARCHAR(128)[],                                   -- 标签数组
    created_at          TIMESTAMP       DEFAULT NOW(),
    updated_at          TIMESTAMP       DEFAULT NOW(),

    -- JSONB 基础校验
    CONSTRAINT chk_entry_rules CHECK (jsonb_typeof(entry_rules) = 'array'),
    CONSTRAINT chk_exit_rules CHECK (jsonb_typeof(exit_rules) = 'array'),
    CONSTRAINT chk_risk_rules CHECK (jsonb_typeof(risk_rules) = 'array'),
    CONSTRAINT chk_params CHECK (jsonb_typeof(params) = 'object')
);

COMMENT ON TABLE strategies IS '策略配置表';
COMMENT ON COLUMN strategies.entry_rules IS '入场规则JSON数组，如 [{"type":"score_above","value":70}]';
COMMENT ON COLUMN strategies.params IS '策略默认参数，如 {"lookback_days":20,"max_hold_days":5}';

CREATE INDEX IF NOT EXISTS idx_strategies_active ON strategies(is_active, category);


-- ============================================================
-- 1.5 策略参数 (运行时参数覆盖)
-- ============================================================
CREATE TABLE IF NOT EXISTS strategy_params (
    id                  BIGSERIAL       PRIMARY KEY,
    strategy_id         BIGINT          NOT NULL REFERENCES strategies(id) ON DELETE CASCADE,
    param_key           VARCHAR(64)     NOT NULL,                        -- 参数名
    param_value         TEXT            NOT NULL,                        -- 参数值
    value_type          VARCHAR(16)     DEFAULT 'STRING',                -- STRING/INT/FLOAT/BOOL/JSON
    description         TEXT,
    is_active           BOOLEAN         DEFAULT TRUE,
    updated_at          TIMESTAMP       DEFAULT NOW(),

    CONSTRAINT uk_strategy_param UNIQUE(strategy_id, param_key)
);

COMMENT ON TABLE strategy_params IS '策略运行时参数覆盖表，优先级高于 strategies.params 中的默认值';


-- ============================================================
-- 1.6 数据源配置
-- ============================================================
CREATE TABLE IF NOT EXISTS datasource_config (
    id                  BIGSERIAL       PRIMARY KEY,
    category            data_category   NOT NULL,                        -- 数据类别
    adapter_name        VARCHAR(32)     NOT NULL,                        -- tushare/akshare/sina/crawler
    priority            SMALLINT        NOT NULL DEFAULT 1,              -- 优先级(越小越高)
    is_enabled          BOOLEAN         DEFAULT TRUE,
    config_json         JSONB           DEFAULT '{}'::jsonb,             -- 连接配置(token等)
    rate_limit          INT             DEFAULT 60,                      -- 每分钟请求上限
    timeout_seconds     INT             DEFAULT 30,                      -- 超时时间(秒)
    created_at          TIMESTAMP       DEFAULT NOW(),
    updated_at          TIMESTAMP       DEFAULT NOW(),

    CONSTRAINT uk_datasource UNIQUE(category, adapter_name)
);

COMMENT ON TABLE datasource_config IS '数据源配置与优先级路由';

-- 默认数据源配置
INSERT INTO datasource_config (category, adapter_name, priority) VALUES
    ('DAILY_KLINE', 'tushare', 1),
    ('DAILY_KLINE', 'akshare', 2),
    ('DAILY_KLINE', 'sina', 3),
    ('LHB_DETAIL', 'akshare', 1),
    ('LHB_DETAIL', 'sina', 2),
    ('LHB_DETAIL', 'crawler', 3),
    ('LHB_BROKER', 'akshare', 1),
    ('LHB_BROKER', 'crawler', 2),
    ('REALTIME_QUOTE', 'akshare', 1),
    ('REALTIME_QUOTE', 'sina', 2),
    ('MARKET_SENTIMENT', 'akshare', 1),
    ('MARKET_SENTIMENT', 'sina', 2),
    ('FINANCIAL_DATA', 'tushare', 1),
    ('FINANCIAL_DATA', 'akshare', 2)
ON CONFLICT (category, adapter_name) DO NOTHING;


-- ============================================================
-- 1.7 数据源熔断日志
-- ============================================================
CREATE TABLE IF NOT EXISTS datasource_circuit_log (
    id                  BIGSERIAL       PRIMARY KEY,
    config_id           BIGINT          NOT NULL REFERENCES datasource_config(id),
    state               circuit_state   NOT NULL DEFAULT 'CLOSED',
    failure_count       INT             DEFAULT 0,
    success_count       INT             DEFAULT 0,
    last_failure_time   TIMESTAMP,
    last_success_time   TIMESTAMP,
    opened_at           TIMESTAMP,                                       -- 熔断打开时间
    half_opened_at      TIMESTAMP,                                       -- 半开时间
    closed_at           TIMESTAMP,                                       -- 恢复关闭时间
    error_message       TEXT,
    created_at          TIMESTAMP       DEFAULT NOW()
);

COMMENT ON TABLE datasource_circuit_log IS '数据源熔断器状态变更日志';

CREATE INDEX IF NOT EXISTS idx_circuit_log_config ON datasource_circuit_log(config_id, created_at DESC);


-- ============================================================
-- 1.8 采集器错误日志
-- ============================================================
CREATE TABLE IF NOT EXISTS collector_error_log (
    id                  BIGSERIAL       PRIMARY KEY,
    adapter_name        VARCHAR(32)     NOT NULL,                        -- 适配器名称
    category            data_category,                                   -- 数据类别
    api_name            VARCHAR(128),                                    -- 调用的API名称
    request_params      JSONB,                                           -- 请求参数
    error_type          VARCHAR(64),                                     -- 错误类型: TIMEOUT/RATE_LIMIT/AUTH/PARSE/NETWORK
    error_message       TEXT,
    error_stack         TEXT,                                            -- 异常堆栈
    retry_count         SMALLINT        DEFAULT 0,                       -- 已重试次数
    is_resolved         BOOLEAN         DEFAULT FALSE,                   -- 是否已解决
    resolved_at         TIMESTAMP,
    created_at          TIMESTAMP       DEFAULT NOW()
);

COMMENT ON TABLE collector_error_log IS '数据采集器错误日志，用于排障和监控';

CREATE INDEX IF NOT EXISTS idx_collector_err_adapter ON collector_error_log(adapter_name, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_collector_err_type ON collector_error_log(error_type, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_collector_err_unresolved ON collector_error_log(is_resolved, created_at DESC) WHERE is_resolved = FALSE;


-- ============================================================
-- 1.9 审计日志
-- ============================================================
CREATE TABLE IF NOT EXISTS audit_logs (
    id                  BIGSERIAL       PRIMARY KEY,
    action              audit_action    NOT NULL,
    entity_type         VARCHAR(32)     NOT NULL,                        -- robot/strategy/trade/datasource
    entity_id           BIGINT,                                          -- 关联实体ID
    operator            VARCHAR(32)     DEFAULT 'SYSTEM',                -- 操作者
    detail              JSONB,                                           -- 操作详情
    old_value           JSONB,                                           -- 变更前值
    new_value           JSONB,                                           -- 变更后值
    ip_address          VARCHAR(48),
    created_at          TIMESTAMP       DEFAULT NOW()
);

COMMENT ON TABLE audit_logs IS '系统审计日志';

CREATE INDEX IF NOT EXISTS idx_audit_entity ON audit_logs(entity_type, entity_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_logs(action, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_time ON audit_logs(created_at DESC);


-- ============================================================
-- ============================================================
-- 第二部分: 市场数据表 (8张)
-- ============================================================
-- ============================================================


-- ============================================================
-- 2.1 日K线数据
-- ============================================================
CREATE TABLE IF NOT EXISTS kline_daily (
    id                  BIGSERIAL       PRIMARY KEY,
    ts_code             VARCHAR(16)     NOT NULL REFERENCES stock_info(ts_code),
    trade_date          DATE            NOT NULL,
    open                DECIMAL(12,4),
    high                DECIMAL(12,4),
    low                 DECIMAL(12,4),
    close               DECIMAL(12,4)   NOT NULL,
    pre_close           DECIMAL(12,4),                                   -- 昨收价
    pct_change          DECIMAL(8,4),                                    -- 涨跌幅(小数形式 0.0523=5.23%)
    vol                 DECIMAL(18,2),                                   -- 成交量(手)
    amount              DECIMAL(18,2),                                   -- 成交额(千元)
    turnover_rate       DECIMAL(8,4),                                    -- 换手率(小数形式)
    turnover_rate_f     DECIMAL(8,4),                                    -- 自由流通换手率
    volume_ratio        DECIMAL(8,4),                                    -- 量比
    pe_ttm              DECIMAL(12,4),                                   -- 市盈率TTM
    pb                  DECIMAL(12,4),                                   -- 市净率
    total_mv            DECIMAL(18,2),                                   -- 总市值(万元)
    circ_mv             DECIMAL(18,2),                                   -- 流通市值(万元)
    is_st               BOOLEAN         DEFAULT FALSE,                   -- 是否ST
    is_limit_up         BOOLEAN         DEFAULT FALSE,                   -- 是否涨停
    is_limit_down       BOOLEAN         DEFAULT FALSE,                   -- 是否跌停
    created_at          TIMESTAMP       DEFAULT NOW(),

    CONSTRAINT uk_kline_daily UNIQUE(ts_code, trade_date)
);

COMMENT ON TABLE kline_daily IS '股票日K线数据';
COMMENT ON COLUMN kline_daily.pct_change IS '涨跌幅，小数形式，0.0523表示5.23%';
COMMENT ON COLUMN kline_daily.total_mv IS '总市值，单位万元';

CREATE INDEX IF NOT EXISTS idx_kline_code_date ON kline_daily(ts_code, trade_date DESC);
CREATE INDEX IF NOT EXISTS idx_kline_date ON kline_daily(trade_date DESC);
CREATE INDEX IF NOT EXISTS idx_kline_date_pct ON kline_daily(trade_date DESC, pct_change DESC);
CREATE INDEX IF NOT EXISTS idx_kline_limit_up ON kline_daily(trade_date, is_limit_up) WHERE is_limit_up = TRUE;


-- ============================================================
-- 2.2 龙虎榜股票汇总
-- ============================================================
CREATE TABLE IF NOT EXISTS lhb_stock_daily (
    id                  BIGSERIAL       PRIMARY KEY,
    ts_code             VARCHAR(16)     NOT NULL REFERENCES stock_info(ts_code),
    trade_date          DATE            NOT NULL,
    name                VARCHAR(32),                                     -- 股票名称(冗余方便查询)
    close_price         DECIMAL(12,4),
    pct_change          DECIMAL(8,4),                                    -- 涨跌幅(小数形式)
    turnover_rate       DECIMAL(8,4),
    amount              DECIMAL(16,2),                                   -- 成交额(万元)

    -- 龙虎榜资金
    l_buy               DECIMAL(16,2)   DEFAULT 0,                       -- 龙虎榜买入额(万)
    l_sell              DECIMAL(16,2)   DEFAULT 0,                       -- 龙虎榜卖出额(万)
    l_net               DECIMAL(16,2)   DEFAULT 0,                       -- 龙虎榜净买入(万)

    -- 上榜信息
    reason              VARCHAR(256),                                    -- 上榜原因
    reason_type         VARCHAR(64),                                     -- 原因分类: 涨幅偏离/换手率/连续三日

    -- 冗余的股票属性(避免频繁JOIN)
    industry            VARCHAR(32),
    market_cap          DECIMAL(18,2),                                   -- 总市值(万元)
    float_market_cap    DECIMAL(18,2),                                   -- 流通市值(万元)

    -- 评分
    score               DECIMAL(6,2)    DEFAULT 0,                       -- 综合评分(0-100)
    score_tech          DECIMAL(6,2)    DEFAULT 0,                       -- 技术面评分
    score_fund          DECIMAL(6,2)    DEFAULT 0,                       -- 资金面评分
    score_hist          DECIMAL(6,2)    DEFAULT 0,                       -- 历史胜率评分
    score_detail        JSONB           DEFAULT '{}'::jsonb,             -- 评分明细

    created_at          TIMESTAMP       DEFAULT NOW(),
    updated_at          TIMESTAMP       DEFAULT NOW(),

    CONSTRAINT uk_lhb_stock_daily UNIQUE(ts_code, trade_date)
);

COMMENT ON TABLE lhb_stock_daily IS '龙虎榜每日股票汇总';
COMMENT ON COLUMN lhb_stock_daily.score IS '综合评分 0-100，技术面*0.4 + 资金面*0.35 + 历史*0.25';
COMMENT ON COLUMN lhb_stock_daily.reason_type IS '上榜原因分类: PRICE_DEV(涨跌幅偏离)/TURNOVER(换手率)/CONSECUTIVE(连续三日)/AMPLITUDE(振幅)';

CREATE INDEX IF NOT EXISTS idx_lhb_daily_date_score ON lhb_stock_daily(trade_date DESC, score DESC);
CREATE INDEX IF NOT EXISTS idx_lhb_daily_code_date ON lhb_stock_daily(ts_code, trade_date DESC);
CREATE INDEX IF NOT EXISTS idx_lhb_daily_reason_type ON lhb_stock_daily(reason_type, trade_date DESC);
CREATE INDEX IF NOT EXISTS idx_lhb_daily_industry ON lhb_stock_daily(industry, trade_date DESC);


-- ============================================================
-- 2.3 龙虎榜席位明细
-- ============================================================
CREATE TABLE IF NOT EXISTS lhb_seats (
    id                  BIGSERIAL       PRIMARY KEY,
    stock_daily_id      BIGINT          NOT NULL REFERENCES lhb_stock_daily(id) ON DELETE CASCADE,
    ts_code             VARCHAR(16)     NOT NULL,                        -- 冗余方便查询
    trade_date          DATE            NOT NULL,                        -- 冗余方便查询

    -- 营业部信息
    broker_name         VARCHAR(128)    NOT NULL,                        -- 营业部名称
    broker_type         VARCHAR(16)     DEFAULT 'UNKNOWN',               -- INSTITUTION(机构)/HOT_MONEY(游资)/UNKNOWN
    is_institution      BOOLEAN         DEFAULT FALSE,                   -- 是否机构专用席位

    -- 交易数据
    buy_amount          DECIMAL(16,2)   DEFAULT 0,                       -- 买入额(万)
    sell_amount         DECIMAL(16,2)   DEFAULT 0,                       -- 卖出额(万)
    net_amount          DECIMAL(16,2)   DEFAULT 0,                       -- 净买入(万)
    buy_pct             DECIMAL(8,4),                                    -- 买入占总成交比
    sell_pct            DECIMAL(8,4),                                    -- 卖出占总成交比
    direction           seat_direction  NOT NULL,                        -- BUY/SELL
    rank                SMALLINT,                                        -- 排名 1-5

    created_at          TIMESTAMP       DEFAULT NOW(),

    CONSTRAINT uk_lhb_seats UNIQUE(ts_code, trade_date, broker_name, direction)
);

COMMENT ON TABLE lhb_seats IS '龙虎榜营业部席位明细';
COMMENT ON COLUMN lhb_seats.broker_type IS '席位类型: INSTITUTION-机构专用 HOT_MONEY-知名游资 UNKNOWN-未分类';

CREATE INDEX IF NOT EXISTS idx_seats_broker_date ON lhb_seats(broker_name, trade_date DESC);
CREATE INDEX IF NOT EXISTS idx_seats_date_net ON lhb_seats(trade_date DESC, net_amount DESC);
CREATE INDEX IF NOT EXISTS idx_seats_daily_id ON lhb_seats(stock_daily_id);
CREATE INDEX IF NOT EXISTS idx_seats_institution ON lhb_seats(trade_date, is_institution) WHERE is_institution = TRUE;


-- ============================================================
-- 2.4 龙虎榜T+N价格追踪
-- ============================================================
CREATE TABLE IF NOT EXISTS lhb_tn_price (
    id                  BIGSERIAL       PRIMARY KEY,
    stock_daily_id      BIGINT          NOT NULL REFERENCES lhb_stock_daily(id) ON DELETE CASCADE,
    ts_code             VARCHAR(16)     NOT NULL,
    trade_date          DATE            NOT NULL,                        -- 上榜日期(基准日)
    n_day               SMALLINT        NOT NULL,                        -- T+N 的 N (1,2,3,5,10)

    -- T+N日价格
    target_date         DATE,                                            -- T+N 对应的实际交易日
    close_price         DECIMAL(12,4),                                   -- T+N 收盘价
    pnl_pct             DECIMAL(8,4),                                    -- 相对上榜日涨跌幅(小数)

    -- 区间极值
    high_price          DECIMAL(12,4),                                   -- T到T+N最高价
    low_price           DECIMAL(12,4),                                   -- T到T+N最低价
    high_pct            DECIMAL(8,4),                                    -- 区间最大涨幅(小数)
    low_pct             DECIMAL(8,4),                                    -- 区间最大跌幅(小数)

    -- 标记
    is_limit_up         BOOLEAN         DEFAULT FALSE,                   -- T+N是否涨停
    is_limit_down       BOOLEAN         DEFAULT FALSE,                   -- T+N是否跌停

    -- 计算状态
    calc_status         VARCHAR(16)     DEFAULT 'PENDING',               -- PENDING/DONE/SKIP(停牌等)
    created_at          TIMESTAMP       DEFAULT NOW(),
    updated_at          TIMESTAMP       DEFAULT NOW(),

    CONSTRAINT uk_lhb_tn_price UNIQUE(stock_daily_id, n_day)
);

COMMENT ON TABLE lhb_tn_price IS '龙虎榜T+N日价格追踪，用于盈亏回测分析';
COMMENT ON COLUMN lhb_tn_price.pnl_pct IS '相对上榜日收盘价的涨跌幅，小数形式';

CREATE INDEX IF NOT EXISTS idx_tn_daily_n ON lhb_tn_price(stock_daily_id, n_day);
CREATE INDEX IF NOT EXISTS idx_tn_code_date ON lhb_tn_price(ts_code, trade_date DESC, n_day);
CREATE INDEX IF NOT EXISTS idx_tn_calc_status ON lhb_tn_price(calc_status) WHERE calc_status = 'PENDING';


-- ============================================================
-- 2.5 龙虎榜聚合统计
-- ============================================================
CREATE TABLE IF NOT EXISTS lhb_agg_stats (
    id                  BIGSERIAL       PRIMARY KEY,

    -- 统计维度
    stat_type           VARCHAR(16)     NOT NULL,                        -- STOCK(按股票)/BROKER(按营业部)/INDUSTRY(按行业)
    stat_key            VARCHAR(128)    NOT NULL,                        -- ts_code 或 broker_name 或 industry
    stat_period         VARCHAR(16)     NOT NULL,                        -- 7D/30D/90D/ALL

    -- 基础统计
    lhb_count           INT             DEFAULT 0,                       -- 上榜次数
    buy_count           INT             DEFAULT 0,                       -- 作为买方次数
    sell_count          INT             DEFAULT 0,                       -- 作为卖方次数
    total_buy_amount    DECIMAL(18,2)   DEFAULT 0,                       -- 总买入额
    total_sell_amount   DECIMAL(18,2)   DEFAULT 0,                       -- 总卖出额
    total_net_amount    DECIMAL(18,2)   DEFAULT 0,                       -- 总净额

    -- 收益统计
    avg_score           DECIMAL(6,2),
    win_rate_1d         DECIMAL(8,4),                                    -- T+1胜率(小数)
    win_rate_3d         DECIMAL(8,4),
    win_rate_5d         DECIMAL(8,4),
    avg_pnl_1d          DECIMAL(8,4),                                    -- T+1平均收益(小数)
    avg_pnl_3d          DECIMAL(8,4),
    avg_pnl_5d          DECIMAL(8,4),
    max_pnl             DECIMAL(8,4),                                    -- 最大单次收益
    min_pnl             DECIMAL(8,4),                                    -- 最大单次亏损

    -- 偏好分析(仅stat_type=BROKER时有意义)
    top_industries      JSONB           DEFAULT '[]'::jsonb,             -- 偏好行业 [{"industry":"半导体","count":15}]
    top_stocks          JSONB           DEFAULT '[]'::jsonb,             -- 偏好标的 [{"ts_code":"000001.SZ","count":8}]
    style_tag           VARCHAR(64),                                     -- 游资风格标签: 打板/低吸/趋势/机构
    active_rank         INT,                                             -- 活跃度排名

    updated_at          TIMESTAMP       DEFAULT NOW(),

    CONSTRAINT uk_lhb_agg_stats UNIQUE(stat_type, stat_key, stat_period)
);

COMMENT ON TABLE lhb_agg_stats IS '龙虎榜聚合统计表，按股票/营业部/行业多维度汇总';
COMMENT ON COLUMN lhb_agg_stats.stat_type IS 'STOCK-按股票聚合 BROKER-按营业部聚合 INDUSTRY-按行业聚合';
COMMENT ON COLUMN lhb_agg_stats.stat_period IS '统计周期: 7D/30D/90D/ALL';

CREATE INDEX IF NOT EXISTS idx_agg_stats_type_period ON lhb_agg_stats(stat_type, stat_period);
CREATE INDEX IF NOT EXISTS idx_agg_stats_key ON lhb_agg_stats(stat_key, stat_period);
CREATE INDEX IF NOT EXISTS idx_agg_stats_winrate ON lhb_agg_stats(stat_type, stat_period, win_rate_1d DESC);


-- ============================================================
-- 2.6 机构统计
-- ============================================================
CREATE TABLE IF NOT EXISTS lhb_institution_stat (
    id                  BIGSERIAL       PRIMARY KEY,
    ts_code             VARCHAR(16)     NOT NULL REFERENCES stock_info(ts_code),
    trade_date          DATE            NOT NULL,
    name                VARCHAR(32),

    -- 机构买卖汇总
    inst_buy_count      SMALLINT        DEFAULT 0,                       -- 机构买入席位数
    inst_sell_count     SMALLINT        DEFAULT 0,                       -- 机构卖出席位数
    inst_buy_amount     DECIMAL(16,2)   DEFAULT 0,                       -- 机构买入总额(万)
    inst_sell_amount    DECIMAL(16,2)   DEFAULT 0,                       -- 机构卖出总额(万)
    inst_net_amount     DECIMAL(16,2)   DEFAULT 0,                       -- 机构净买入(万)
    inst_buy_pct        DECIMAL(8,4),                                    -- 机构买入占比(小数)

    -- 关联龙虎榜
    stock_daily_id      BIGINT          REFERENCES lhb_stock_daily(id),

    -- 后续追踪
    pnl_1d              DECIMAL(8,4),
    pnl_3d              DECIMAL(8,4),
    pnl_5d              DECIMAL(8,4),

    created_at          TIMESTAMP       DEFAULT NOW(),
    updated_at          TIMESTAMP       DEFAULT NOW(),

    CONSTRAINT uk_inst_stat UNIQUE(ts_code, trade_date)
);

COMMENT ON TABLE lhb_institution_stat IS '龙虎榜机构席位统计，汇总每日每只股票的机构买卖情况';

CREATE INDEX IF NOT EXISTS idx_inst_stat_date ON lhb_institution_stat(trade_date DESC);
CREATE INDEX IF NOT EXISTS idx_inst_stat_net ON lhb_institution_stat(trade_date DESC, inst_net_amount DESC);
CREATE INDEX IF NOT EXISTS idx_inst_stat_code ON lhb_institution_stat(ts_code, trade_date DESC);


-- ============================================================
-- 2.7 市场快照
-- ============================================================
CREATE TABLE IF NOT EXISTS market_snapshots (
    id                  BIGSERIAL       PRIMARY KEY,
    trade_date          DATE            NOT NULL,
    snapshot_time       TIMESTAMP,                                       -- 快照时间(盘中可多次)
    snapshot_type       VARCHAR(16)     DEFAULT 'CLOSE',                 -- OPEN/MIDDAY/CLOSE/REALTIME

    -- 四大指数
    sh_index            DECIMAL(12,4),                                   -- 上证指数
    sh_pct_change       DECIMAL(8,4),
    sz_index            DECIMAL(12,4),                                   -- 深证成指
    sz_pct_change       DECIMAL(8,4),
    cyb_index           DECIMAL(12,4),                                   -- 创业板指
    cyb_pct_change      DECIMAL(8,4),
    kcb_index           DECIMAL(12,4),                                   -- 科创50
    kcb_pct_change      DECIMAL(8,4),

    -- 市场宽度
    up_count            INT             DEFAULT 0,                       -- 上涨家数
    down_count          INT             DEFAULT 0,                       -- 下跌家数
    flat_count          INT             DEFAULT 0,                       -- 平盘家数
    limit_up_count      INT             DEFAULT 0,                       -- 涨停数
    limit_down_count    INT             DEFAULT 0,                       -- 跌停数

    -- 成交数据
    total_volume        DECIMAL(18,2),                                   -- 两市总成交量(亿股)
    total_amount        DECIMAL(18,2),                                   -- 两市总成交额(亿元)

    -- 资金流向
    north_net_amount    DECIMAL(16,2),                                   -- 北向资金净流入(亿元)
    north_buy_amount    DECIMAL(16,2),                                   -- 北向买入额
    north_sell_amount   DECIMAL(16,2),                                   -- 北向卖出额
    margin_balance      DECIMAL(18,2),                                   -- 两融余额(亿元)
    margin_buy_amount   DECIMAL(16,2),                                   -- 融资买入额(亿元)

    created_at          TIMESTAMP       DEFAULT NOW(),

    CONSTRAINT uk_market_snapshot UNIQUE(trade_date, snapshot_type)
);

COMMENT ON TABLE market_snapshots IS '市场快照，记录四大指数和市场宽度数据';
COMMENT ON COLUMN market_snapshots.snapshot_type IS 'OPEN-开盘 MIDDAY-午盘 CLOSE-收盘 REALTIME-实时';

CREATE INDEX IF NOT EXISTS idx_market_snap_date ON market_snapshots(trade_date DESC);
CREATE INDEX IF NOT EXISTS idx_market_snap_type ON market_snapshots(snapshot_type, trade_date DESC);


-- ============================================================
-- 2.8 市场情绪指标
-- ============================================================
CREATE TABLE IF NOT EXISTS market_sentiment (
    id                  BIGSERIAL       PRIMARY KEY,
    trade_date          DATE            NOT NULL,

    -- 涨跌停分析
    limit_up_count      INT             DEFAULT 0,                       -- 涨停数(不含ST)
    limit_down_count    INT             DEFAULT 0,                       -- 跌停数(不含ST)
    st_limit_up         INT             DEFAULT 0,                       -- ST涨停数
    st_limit_down       INT             DEFAULT 0,                       -- ST跌停数
    first_limit_up      INT             DEFAULT 0,                       -- 首板数量
    cont_limit_up       INT             DEFAULT 0,                       -- 连板数量(2板+)
    max_cont_board      SMALLINT        DEFAULT 0,                       -- 最高连板数
    open_limit_up_pct   DECIMAL(8,4),                                    -- 涨停开板率(小数)

    -- 连板梯队
    board_2             INT             DEFAULT 0,                       -- 2连板数量
    board_3             INT             DEFAULT 0,                       -- 3连板
    board_4             INT             DEFAULT 0,                       -- 4连板
    board_5_plus        INT             DEFAULT 0,                       -- 5板及以上

    -- 情绪指标
    up_down_ratio       DECIMAL(8,4),                                    -- 涨跌比(上涨数/下跌数)
    avg_pct_change      DECIMAL(8,4),                                    -- 全市场平均涨幅(小数)
    median_pct_change   DECIMAL(8,4),                                    -- 中位数涨幅
    strong_stock_count  INT             DEFAULT 0,                       -- 强势股数量(涨幅>5%)
    weak_stock_count    INT             DEFAULT 0,                       -- 弱势股数量(跌幅>5%)

    -- 龙虎榜统计
    lhb_stock_count     INT             DEFAULT 0,                       -- 当日龙虎榜上榜股票数
    lhb_total_net       DECIMAL(16,2),                                   -- 龙虎榜总净买入(万)
    lhb_avg_score       DECIMAL(6,2),                                    -- 龙虎榜平均评分

    -- 综合情绪评分
    sentiment_score     DECIMAL(6,2),                                    -- 综合情绪(0-100)
    sentiment_level     VARCHAR(16),                                     -- ICE_COLD/COLD/NEUTRAL/WARM/HOT/EXTREME
    sentiment_change    DECIMAL(6,2),                                    -- 相比昨日变化

    created_at          TIMESTAMP       DEFAULT NOW(),
    updated_at          TIMESTAMP       DEFAULT NOW(),

    CONSTRAINT uk_market_sentiment UNIQUE(trade_date)
);

COMMENT ON TABLE market_sentiment IS '市场情绪指标，每日一条';
COMMENT ON COLUMN market_sentiment.sentiment_level IS '情绪级别: ICE_COLD(极寒)/COLD(冷)/NEUTRAL(中性)/WARM(温)/HOT(热)/EXTREME(极热)';

CREATE INDEX IF NOT EXISTS idx_sentiment_date ON market_sentiment(trade_date DESC);
CREATE INDEX IF NOT EXISTS idx_sentiment_level ON market_sentiment(sentiment_level, trade_date DESC);


-- ============================================================
-- ============================================================
-- 第三部分: 交易相关表 (7张)
-- ============================================================
-- ============================================================


-- ============================================================
-- 3.1 交易机器人
-- ============================================================
CREATE TABLE IF NOT EXISTS robots (
    id                  BIGSERIAL       PRIMARY KEY,
    name                VARCHAR(64)     NOT NULL,
    description         TEXT,
    strategy_id         BIGINT          REFERENCES strategies(id) ON DELETE SET NULL,

    -- 资金
    initial_capital     DECIMAL(16,2)   NOT NULL DEFAULT 1000000.00,
    current_capital     DECIMAL(16,2)   NOT NULL DEFAULT 1000000.00,     -- 总资产
    cash                DECIMAL(16,2)   NOT NULL DEFAULT 1000000.00,     -- 可用现金
    total_assets        DECIMAL(16,2)   NOT NULL DEFAULT 1000000.00,     -- 总资产(cash+持仓市值)
    frozen_amount       DECIMAL(16,2)   DEFAULT 0,                       -- 冻结资金

    -- 累计统计(冗余字段，避免频繁聚合)
    total_pnl           DECIMAL(16,2)   DEFAULT 0,                       -- 累计盈亏金额
    total_pnl_pct       DECIMAL(8,4)    DEFAULT 0,                       -- 累计收益率(小数)
    total_trades        INT             DEFAULT 0,                       -- 总交易笔数
    win_count           INT             DEFAULT 0,                       -- 盈利笔数
    lose_count          INT             DEFAULT 0,                       -- 亏损笔数
    max_drawdown        DECIMAL(8,4)    DEFAULT 0,                       -- 历史最大回撤(小数)
    max_drawdown_amount DECIMAL(16,2)   DEFAULT 0,                       -- 最大回撤金额

    -- 状态与配置
    status              robot_status    DEFAULT 'IDLE',
    max_positions       SMALLINT        DEFAULT 5,                       -- 最大持仓数
    per_position_pct    DECIMAL(4,2)    DEFAULT 0.20,                    -- 单票仓位上限(小数)

    -- 风控配置
    risk_config         JSONB           DEFAULT '{
        "stop_loss_pct": -0.07,
        "take_profit_pct": 0.15,
        "daily_loss_limit": -0.05,
        "trailing_stop_pct": null,
        "max_hold_days": 10,
        "score_threshold": 70,
        "max_single_loss": -0.10,
        "circuit_break_daily_pct": -0.08
    }'::jsonb,

    -- 账户健康度评分
    ahs_score           DECIMAL(6,2)    DEFAULT 50.00,                   -- Account Health Score (0-100)
    ahs_detail          JSONB           DEFAULT '{}'::jsonb,             -- AHS 各维度明细

    -- 运行信息
    last_trade_date     DATE,                                            -- 最后交易日
    last_signal_date    DATE,                                            -- 最后信号日
    started_at          TIMESTAMP,                                       -- 启动时间
    stopped_at          TIMESTAMP,                                       -- 停止时间
    error_message       TEXT,                                            -- 最近错误信息

    created_at          TIMESTAMP       DEFAULT NOW(),
    updated_at          TIMESTAMP       DEFAULT NOW(),

    -- 风控JSONB基础校验
    CONSTRAINT chk_risk_config CHECK (
        risk_config ? 'stop_loss_pct' AND
        risk_config ? 'take_profit_pct'
    ),
    CONSTRAINT chk_capital CHECK (cash >= 0 AND initial_capital > 0)
);

COMMENT ON TABLE robots IS '模拟交易机器人';
COMMENT ON COLUMN robots.ahs_score IS '账户健康度评分 0-100，综合资金利用率/胜率/回撤等';
COMMENT ON COLUMN robots.per_position_pct IS '单票仓位上限，小数形式，0.20表示20%';

CREATE INDEX IF NOT EXISTS idx_robots_status ON robots(status);
CREATE INDEX IF NOT EXISTS idx_robots_strategy ON robots(strategy_id);


-- ============================================================
-- 3.2 交易记录
-- ============================================================
CREATE TABLE IF NOT EXISTS robot_trades (
    id                  BIGSERIAL       PRIMARY KEY,
    robot_id            BIGINT          NOT NULL REFERENCES robots(id) ON DELETE CASCADE,
    ts_code             VARCHAR(16)     NOT NULL REFERENCES stock_info(ts_code),
    name                VARCHAR(32),                                     -- 股票名称(冗余)

    -- 交易信息
    direction           trade_direction NOT NULL,                        -- BUY/SELL
    price               DECIMAL(12,4)   NOT NULL,                        -- 委托价格
    shares              INT             NOT NULL,                        -- 委托股数
    amount              DECIMAL(16,2)   NOT NULL,                        -- 委托金额

    -- 成本(摩擦成本)
    commission          DECIMAL(10,2)   DEFAULT 0,                       -- 手续费
    slippage            DECIMAL(10,2)   DEFAULT 0,                       -- 滑点成本
    stamp_tax           DECIMAL(10,2)   DEFAULT 0,                       -- 印花税(仅卖出)
    total_cost          DECIMAL(10,2)   DEFAULT 0,                       -- 总交易成本

    -- 盈亏(仅卖出时计算)
    pnl_amount          DECIMAL(16,2),                                   -- 本笔盈亏金额
    pnl_pct             DECIMAL(8,4),                                    -- 本笔盈亏比例(小数)
    hold_days           INT,                                             -- 持仓天数(卖出时填)
    cost_price          DECIMAL(12,4),                                   -- 持仓成本价(卖出时填)

    -- 信号与阶段
    phase               trade_phase     NOT NULL,                        -- PHASE_A/PHASE_B/MANUAL/STOP_LOSS/TAKE_PROFIT/MONITOR
    signal_score        DECIMAL(6,2),                                    -- 触发时的评分
    signal_detail       JSONB,                                           -- 信号明细
    lhb_daily_id        BIGINT          REFERENCES lhb_stock_daily(id),  -- 关联龙虎榜记录
    sim_trade_id        BIGINT,                                          -- 关联模拟交易(后续添加FK)

    -- 关联
    paired_trade_id     BIGINT,                                          -- 配对交易ID(买→卖关联)
    reason              VARCHAR(256),                                    -- 交易原因描述

    trade_time          TIMESTAMP       NOT NULL,                        -- 交易时间
    trade_date          DATE            NOT NULL,                        -- 交易日期
    created_at          TIMESTAMP       DEFAULT NOW(),

    CONSTRAINT chk_shares CHECK (shares > 0),
    CONSTRAINT chk_price CHECK (price > 0)
);

COMMENT ON TABLE robot_trades IS '机器人交易记录';
COMMENT ON COLUMN robot_trades.paired_trade_id IS '配对交易ID，卖出时指向对应的买入记录ID';
COMMENT ON COLUMN robot_trades.phase IS '交易阶段: PHASE_A-盘后选股 PHASE_B-盘中执行 MANUAL-手动 STOP_LOSS-止损 TAKE_PROFIT-止盈 MONITOR-监控触发';

CREATE INDEX IF NOT EXISTS idx_trades_robot_time ON robot_trades(robot_id, trade_time DESC);
CREATE INDEX IF NOT EXISTS idx_trades_robot_date ON robot_trades(robot_id, trade_date DESC);
CREATE INDEX IF NOT EXISTS idx_trades_code_time ON robot_trades(ts_code, trade_time DESC);
CREATE INDEX IF NOT EXISTS idx_trades_phase ON robot_trades(phase, trade_date DESC);
CREATE INDEX IF NOT EXISTS idx_trades_direction ON robot_trades(robot_id, direction, trade_date DESC);
CREATE INDEX IF NOT EXISTS idx_trades_lhb ON robot_trades(lhb_daily_id) WHERE lhb_daily_id IS NOT NULL;


-- ============================================================
-- 3.3 交易执行记录
-- ============================================================
CREATE TABLE IF NOT EXISTS trade_executions (
    id                  BIGSERIAL       PRIMARY KEY,
    trade_id            BIGINT          NOT NULL REFERENCES robot_trades(id) ON DELETE CASCADE,
    robot_id            BIGINT          NOT NULL REFERENCES robots(id),

    -- 执行信息
    exec_price          DECIMAL(12,4)   NOT NULL,                        -- 实际成交价
    exec_shares         INT             NOT NULL,                        -- 实际成交股数
    exec_amount         DECIMAL(16,2)   NOT NULL,                        -- 实际成交金额

    -- 成本明细
    commission          DECIMAL(10,2)   DEFAULT 0,
    slippage            DECIMAL(10,2)   DEFAULT 0,
    stamp_tax           DECIMAL(10,2)   DEFAULT 0,

    -- 状态
    status              exec_status     NOT NULL DEFAULT 'PENDING',      -- PENDING/FILLED/PARTIAL/REJECTED/CANCELLED
    reject_reason       VARCHAR(256),                                    -- 拒绝/取消原因
    error_message       TEXT,                                            -- 执行失败错误信息

    -- 市场快照(执行时刻)
    market_price        DECIMAL(12,4),                                   -- 执行时市场价
    bid_price           DECIMAL(12,4),                                   -- 买一价
    ask_price           DECIMAL(12,4),                                   -- 卖一价

    executed_at         TIMESTAMP,                                       -- 实际执行时间
    created_at          TIMESTAMP       DEFAULT NOW()
);

COMMENT ON TABLE trade_executions IS '交易执行明细，一笔 trade 可能有多次部分成交';

CREATE INDEX IF NOT EXISTS idx_exec_trade ON trade_executions(trade_id);
CREATE INDEX IF NOT EXISTS idx_exec_robot ON trade_executions(robot_id, executed_at DESC);
CREATE INDEX IF NOT EXISTS idx_exec_status ON trade_executions(status) WHERE status != 'FILLED';


-- ============================================================
-- 3.4 资金流水
-- ============================================================
CREATE TABLE IF NOT EXISTS cash_ledger (
    id                  BIGSERIAL       PRIMARY KEY,
    robot_id            BIGINT          NOT NULL REFERENCES robots(id) ON DELETE CASCADE,
    trade_id            BIGINT          REFERENCES robot_trades(id),     -- 关联交易(可空，如入金)

    -- 流水信息
    ledger_type         ledger_type     NOT NULL,                        -- DEPOSIT/WITHDRAW/BUY_FREEZE/SELL_RETURN/COMMISSION/SLIPPAGE/DIVIDEND
    direction           VARCHAR(4)      NOT NULL,                        -- IN(入金)/OUT(出金)
    amount              DECIMAL(16,2)   NOT NULL,                        -- 金额(正数)
    balance_before      DECIMAL(16,2)   NOT NULL,                        -- 变动前余额
    balance_after       DECIMAL(16,2)   NOT NULL,                        -- 变动后余额

    -- 关联信息
    ts_code             VARCHAR(16),                                     -- 关联股票(交易流水时)
    description         VARCHAR(256),                                    -- 流水描述

    -- 日期
    biz_date            DATE            NOT NULL,                        -- 业务日期
    created_at          TIMESTAMP       DEFAULT NOW(),

    CONSTRAINT chk_amount CHECK (amount >= 0),
    CONSTRAINT chk_direction CHECK (direction IN ('IN', 'OUT'))
);

COMMENT ON TABLE cash_ledger IS '资金流水账本，记录每一笔资金变动';
COMMENT ON COLUMN cash_ledger.ledger_type IS '流水类型: DEPOSIT-入金 WITHDRAW-出金 BUY_FREEZE-买入冻结 SELL_RETURN-卖出回款 COMMISSION-佣金 SLIPPAGE-滑点 DIVIDEND-分红';

CREATE INDEX IF NOT EXISTS idx_ledger_robot_date ON cash_ledger(robot_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_ledger_robot_biz ON cash_ledger(robot_id, biz_date DESC);
CREATE INDEX IF NOT EXISTS idx_ledger_type ON cash_ledger(robot_id, ledger_type, biz_date DESC);
CREATE INDEX IF NOT EXISTS idx_ledger_trade ON cash_ledger(trade_id) WHERE trade_id IS NOT NULL;


-- ============================================================
-- 3.5 每日净值快照
-- ============================================================
CREATE TABLE IF NOT EXISTS daily_nav (
    id                  BIGSERIAL       PRIMARY KEY,
    robot_id            BIGINT          NOT NULL REFERENCES robots(id) ON DELETE CASCADE,
    trade_date          DATE            NOT NULL,

    -- 净值
    net_value           DECIMAL(12,6)   NOT NULL,                        -- 单位净值(初始1.000000)
    cumulative_nav      DECIMAL(12,6),                                   -- 累计净值(含分红)

    -- 资产明细
    total_assets        DECIMAL(16,2)   NOT NULL,                        -- 总资产
    cash                DECIMAL(16,2)   NOT NULL,                        -- 现金
    frozen_amount       DECIMAL(16,2)   DEFAULT 0,                       -- 冻结资金
    position_value      DECIMAL(16,2)   DEFAULT 0,                       -- 持仓市值
    position_count      SMALLINT        DEFAULT 0,                       -- 持仓股票数

    -- 当日盈亏
    pnl_daily           DECIMAL(16,2)   DEFAULT 0,                       -- 当日盈亏金额
    pnl_daily_pct       DECIMAL(8,4)    DEFAULT 0,                       -- 当日收益率(小数)

    -- 累计盈亏
    pnl_total           DECIMAL(16,2)   DEFAULT 0,                       -- 累计盈亏金额
    pnl_total_pct       DECIMAL(8,4)    DEFAULT 0,                       -- 累计收益率(小数)

    -- 回撤
    drawdown            DECIMAL(8,4)    DEFAULT 0,                       -- 当前回撤(小数)
    max_drawdown        DECIMAL(8,4)    DEFAULT 0,                       -- 历史最大回撤(小数)
    max_net_value       DECIMAL(12,6),                                   -- 历史最高净值

    -- 当日交易统计
    trade_count         SMALLINT        DEFAULT 0,                       -- 当日交易笔数
    buy_amount          DECIMAL(16,2)   DEFAULT 0,                       -- 当日买入额
    sell_amount         DECIMAL(16,2)   DEFAULT 0,                       -- 当日卖出额

    -- 基准对比
    benchmark_pct       DECIMAL(8,4),                                    -- 基准(沪深300)当日涨跌
    excess_return       DECIMAL(8,4),                                    -- 超额收益(小数)

    created_at          TIMESTAMP       DEFAULT NOW(),

    CONSTRAINT uk_daily_nav UNIQUE(robot_id, trade_date)
);

COMMENT ON TABLE daily_nav IS '机器人每日净值快照，每个交易日收盘后生成一条';

CREATE INDEX IF NOT EXISTS idx_nav_robot_date ON daily_nav(robot_id, trade_date DESC);
CREATE INDEX IF NOT EXISTS idx_nav_date ON daily_nav(trade_date DESC);


-- ============================================================
-- 3.6 模拟交易(观察仓)
-- ============================================================
CREATE TABLE IF NOT EXISTS sim_trades (
    id                  BIGSERIAL       PRIMARY KEY,
    robot_id            BIGINT          NOT NULL REFERENCES robots(id) ON DELETE CASCADE,
    ts_code             VARCHAR(16)     NOT NULL REFERENCES stock_info(ts_code),
    name                VARCHAR(32),

    -- 入场信息
    entry_date          DATE            NOT NULL,
    entry_price         DECIMAL(12,4)   NOT NULL,
    entry_score         DECIMAL(6,2),                                    -- 入场时评分
    entry_reason        VARCHAR(256),                                    -- 入场原因
    shares              INT             NOT NULL DEFAULT 0,              -- 模拟股数

    -- 目标与止损
    target_price        DECIMAL(12,4),                                   -- 目标价
    stop_loss_price     DECIMAL(12,4),                                   -- 止损价
    max_hold_days       INT             DEFAULT 10,                      -- 最大持仓天数

    -- 出场信息
    exit_date           DATE,
    exit_price          DECIMAL(12,4),
    exit_reason         VARCHAR(256),                                    -- 出场原因

    -- 盈亏
    realized_pnl        DECIMAL(16,2),                                   -- 已实现盈亏
    realized_pnl_pct    DECIMAL(8,4),                                    -- 已实现收益率(小数)
    max_profit_pct      DECIMAL(8,4),                                    -- 持仓期间最大浮盈(小数)
    max_loss_pct        DECIMAL(8,4),                                    -- 持仓期间最大浮亏(小数)
    hold_days           INT,                                             -- 实际持仓天数

    -- 关联
    lhb_daily_id        BIGINT          REFERENCES lhb_stock_daily(id),  -- 关联龙虎榜记录
    converted_trade_id  BIGINT,                                          -- 转为真实交易后的trade_id

    -- 状态
    status              sim_trade_status DEFAULT 'OPEN',                 -- OPEN/CLOSED/STOP_LOSS/TAKE_PROFIT/EXPIRED

    created_at          TIMESTAMP       DEFAULT NOW(),
    updated_at          TIMESTAMP       DEFAULT NOW(),

    CONSTRAINT chk_sim_entry CHECK (entry_price > 0)
);

COMMENT ON TABLE sim_trades IS '模拟交易(观察仓)，用于追踪未实际入场的信号表现';
COMMENT ON COLUMN sim_trades.converted_trade_id IS '如果模拟交易确认入场，关联到 robot_trades.id';

CREATE INDEX IF NOT EXISTS idx_sim_robot_status ON sim_trades(robot_id, status);
CREATE INDEX IF NOT EXISTS idx_sim_robot_date ON sim_trades(robot_id, entry_date DESC);
CREATE INDEX IF NOT EXISTS idx_sim_code ON sim_trades(ts_code, entry_date DESC);
CREATE INDEX IF NOT EXISTS idx_sim_open ON sim_trades(status, updated_at DESC) WHERE status = 'OPEN';
CREATE INDEX IF NOT EXISTS idx_sim_lhb ON sim_trades(lhb_daily_id) WHERE lhb_daily_id IS NOT NULL;

-- 添加robot_trades的sim_trade_id外键(延迟添加，因为两表互相引用)
ALTER TABLE robot_trades
    ADD CONSTRAINT fk_trades_sim FOREIGN KEY (sim_trade_id) REFERENCES sim_trades(id) ON DELETE SET NULL;


-- ============================================================
-- 3.7 模拟交易快照
-- ============================================================
CREATE TABLE IF NOT EXISTS sim_trade_snapshots (
    id                  BIGSERIAL       PRIMARY KEY,
    sim_trade_id        BIGINT          NOT NULL REFERENCES sim_trades(id) ON DELETE CASCADE,
    robot_id            BIGINT          NOT NULL REFERENCES robots(id),
    snapshot_date       DATE            NOT NULL,

    -- 价格快照
    current_price       DECIMAL(12,4)   NOT NULL,
    open_price          DECIMAL(12,4),
    high_price          DECIMAL(12,4),
    low_price           DECIMAL(12,4),
    pct_change          DECIMAL(8,4),                                    -- 当日涨跌幅(小数)

    -- 浮动盈亏
    unrealized_pnl      DECIMAL(16,2),                                   -- 浮动盈亏金额
    unrealized_pnl_pct  DECIMAL(8,4),                                    -- 浮动盈亏比例(小数)

    -- 区间极值(从入场至今)
    max_price_since     DECIMAL(12,4),                                   -- 入场以来最高价
    min_price_since     DECIMAL(12,4),                                   -- 入场以来最低价
    max_pnl_pct         DECIMAL(8,4),                                    -- 入场以来最大浮盈(小数)
    min_pnl_pct         DECIMAL(8,4),                                    -- 入场以来最大浮亏(小数)

    -- 持仓信息
    hold_days           INT             NOT NULL DEFAULT 0,              -- 已持仓天数
    distance_to_target  DECIMAL(8,4),                                    -- 距目标价比例(小数)
    distance_to_stop    DECIMAL(8,4),                                    -- 距止损价比例(小数)

    -- 市场环境(快照时刻)
    market_sentiment    DECIMAL(6,2),                                    -- 当日市场情绪评分

    created_at          TIMESTAMP       DEFAULT NOW(),

    CONSTRAINT uk_sim_snapshot UNIQUE(sim_trade_id, snapshot_date)
);

COMMENT ON TABLE sim_trade_snapshots IS '模拟交易每日快照，追踪观察仓每日表现';

CREATE INDEX IF NOT EXISTS idx_sim_snap_trade ON sim_trade_snapshots(sim_trade_id, snapshot_date DESC);
CREATE INDEX IF NOT EXISTS idx_sim_snap_robot ON sim_trade_snapshots(robot_id, snapshot_date DESC);
CREATE INDEX IF NOT EXISTS idx_sim_snap_date ON sim_trade_snapshots(snapshot_date DESC);


-- ============================================================
-- ============================================================
-- 第四部分: 事件与日志表 (3张)
-- ============================================================
-- ============================================================


-- ============================================================
-- 4.1 交易事件
-- ============================================================
CREATE TABLE IF NOT EXISTS trade_events (
    id                  BIGSERIAL       PRIMARY KEY,
    robot_id            BIGINT          NOT NULL REFERENCES robots(id) ON DELETE CASCADE,

    -- 事件信息
    event_type          VARCHAR(32)     NOT NULL,                        -- 事件类型
    event_level         VARCHAR(8)      DEFAULT 'INFO',                  -- DEBUG/INFO/WARN/ERROR/CRITICAL

    -- 关联实体
    ts_code             VARCHAR(16),
    trade_id            BIGINT          REFERENCES robot_trades(id),
    sim_trade_id        BIGINT          REFERENCES sim_trades(id),

    -- 事件内容
    title               VARCHAR(256)    NOT NULL,                        -- 事件标题
    message             TEXT,                                            -- 事件详情
    detail              JSONB           DEFAULT '{}'::jsonb,             -- 结构化详情

    -- 触发条件
    trigger_type        VARCHAR(32),                                     -- SIGNAL/RISK/SCHEDULE/MANUAL/SYSTEM
    trigger_value       JSONB,                                           -- 触发条件值

    -- 状态
    is_read             BOOLEAN         DEFAULT FALSE,
    is_handled          BOOLEAN         DEFAULT FALSE,
    handled_at          TIMESTAMP,
    handler_note        TEXT,                                            -- 处理备注

    trade_date          DATE,
    created_at          TIMESTAMP       DEFAULT NOW()
);

COMMENT ON TABLE trade_events IS '交易事件流，记录机器人运行中的所有重要事件';
COMMENT ON COLUMN trade_events.event_type IS '事件类型: SIGNAL_GENERATED(信号生成)/ORDER_PLACED(下单)/ORDER_FILLED(成交)/STOP_LOSS(止损触发)/TAKE_PROFIT(止盈触发)/RISK_ALERT(风控告警)/CIRCUIT_BREAK(熔断触发)/DAILY_SETTLE(日终结算)/POSITION_EXPIRE(持仓到期)/AHS_UPDATE(健康度更新)';

CREATE INDEX IF NOT EXISTS idx_event_robot_date ON trade_events(robot_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_event_type ON trade_events(event_type, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_event_level ON trade_events(event_level, created_at DESC) WHERE event_level IN ('WARN', 'ERROR', 'CRITICAL');
CREATE INDEX IF NOT EXISTS idx_event_unread ON trade_events(robot_id, is_read) WHERE is_read = FALSE;
CREATE INDEX IF NOT EXISTS idx_event_trade_date ON trade_events(trade_date DESC);


-- ============================================================
-- 4.2 选股日志
-- ============================================================
CREATE TABLE IF NOT EXISTS selection_logs (
    id                  BIGSERIAL       PRIMARY KEY,
    robot_id            BIGINT          REFERENCES robots(id) ON DELETE SET NULL,
    strategy_id         BIGINT          REFERENCES strategies(id) ON DELETE SET NULL,

    -- 选股日期与阶段
    trade_date          DATE            NOT NULL,
    phase               trade_phase     NOT NULL,                        -- PHASE_A/PHASE_B

    -- 选股池
    candidate_count     INT             DEFAULT 0,                       -- 候选股数量
    candidates          JSONB           DEFAULT '[]'::jsonb,             -- 候选列表 [{"ts_code":"xxx","score":85}]

    -- 筛选结果
    selected_count      INT             DEFAULT 0,                       -- 最终选出数量
    selected_stocks     JSONB           DEFAULT '[]'::jsonb,             -- 选中列表(含评分明细)

    -- 筛选过程
    filter_steps        JSONB           DEFAULT '[]'::jsonb,             -- 筛选步骤记录
    -- 示例: [
    --   {"step":"score_filter","threshold":70,"before":45,"after":12},
    --   {"step":"position_check","max":5,"current":3,"available":2,"after":2},
    --   {"step":"risk_check","rejected":["000001.SZ"],"reason":"daily_loss_limit","after":1}
    -- ]

    -- 关联龙虎榜
    lhb_daily_ids       BIGINT[],                                        -- 关联的龙虎榜记录ID数组

    -- 市场环境
    market_sentiment    DECIMAL(6,2),                                    -- 选股时市场情绪
    market_snapshot     JSONB,                                           -- 选股时市场快照

    -- 执行结果
    exec_status         VARCHAR(16)     DEFAULT 'PENDING',               -- PENDING/EXECUTED/SKIPPED/FAILED
    exec_message        TEXT,

    created_at          TIMESTAMP       DEFAULT NOW()
);

COMMENT ON TABLE selection_logs IS '选股日志，记录每次选股的完整过程和筛选逻辑';

CREATE INDEX IF NOT EXISTS idx_selection_robot_date ON selection_logs(robot_id, trade_date DESC);
CREATE INDEX IF NOT EXISTS idx_selection_date_phase ON selection_logs(trade_date DESC, phase);
CREATE INDEX IF NOT EXISTS idx_selection_strategy ON selection_logs(strategy_id, trade_date DESC);


-- ============================================================
-- 4.3 回测任务与结果
-- ============================================================
CREATE TABLE IF NOT EXISTS backtest_tasks (
    id                  BIGSERIAL       PRIMARY KEY,
    strategy_id         BIGINT          NOT NULL REFERENCES strategies(id),
    name                VARCHAR(128),
    description         TEXT,

    -- 回测区间
    start_date          DATE            NOT NULL,
    end_date            DATE            NOT NULL,

    -- 回测参数
    initial_capital     DECIMAL(16,2)   DEFAULT 1000000.00,
    params_override     JSONB           DEFAULT '{}'::jsonb,             -- 参数覆盖
    benchmark_code      VARCHAR(16)     DEFAULT '000300.SH',             -- 基准指数
    commission_rate     DECIMAL(8,6)    DEFAULT 0.000300,                -- 佣金费率
    slippage_rate       DECIMAL(8,6)    DEFAULT 0.001000,                -- 滑点费率
    stamp_tax_rate      DECIMAL(8,6)    DEFAULT 0.001000,                -- 印花税率

    -- 状态
    status              backtest_status DEFAULT 'PENDING',
    progress            SMALLINT        DEFAULT 0,                       -- 0-100
    error_message       TEXT,

    -- 时间
    started_at          TIMESTAMP,
    finished_at         TIMESTAMP,
    elapsed_seconds     INT,                                             -- 耗时(秒)

    created_at          TIMESTAMP       DEFAULT NOW()
);

COMMENT ON TABLE backtest_tasks IS '回测任务';

CREATE INDEX IF NOT EXISTS idx_bt_task_strategy ON backtest_tasks(strategy_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_bt_task_status ON backtest_tasks(status);


-- ============================================================
-- 4.4 回测结果
-- ============================================================
CREATE TABLE IF NOT EXISTS backtest_results (
    id                  BIGSERIAL       PRIMARY KEY,
    task_id             BIGINT          NOT NULL REFERENCES backtest_tasks(id) ON DELETE CASCADE,

    -- 收益指标
    total_return        DECIMAL(10,4),                                   -- 总收益率(小数)
    annual_return       DECIMAL(10,4),                                   -- 年化收益率(小数)
    benchmark_return    DECIMAL(10,4),                                   -- 基准收益率(小数)
    excess_return       DECIMAL(10,4),                                   -- 超额收益(小数)

    -- 风险指标
    max_drawdown        DECIMAL(8,4),                                    -- 最大回撤(小数)
    max_drawdown_days   INT,                                             -- 最大回撤持续天数
    volatility          DECIMAL(8,4),                                    -- 年化波动率(小数)
    downside_volatility DECIMAL(8,4),                                    -- 下行波动率

    -- 风险调整收益
    sharpe_ratio        DECIMAL(8,4),                                    -- 夏普比率
    sortino_ratio       DECIMAL(8,4),                                    -- 索提诺比率
    calmar_ratio        DECIMAL(8,4),                                    -- 卡玛比率
    information_ratio   DECIMAL(8,4),                                    -- 信息比率

    -- 交易统计
    trade_count         INT             DEFAULT 0,                       -- 总交易次数
    win_count           INT             DEFAULT 0,                       -- 盈利次数
    lose_count          INT             DEFAULT 0,                       -- 亏损次数
    win_rate            DECIMAL(8,4),                                    -- 胜率(小数)
    avg_win             DECIMAL(8,4),                                    -- 平均盈利(小数)
    avg_loss            DECIMAL(8,4),                                    -- 平均亏损(小数)
    profit_loss_ratio   DECIMAL(8,4),                                    -- 盈亏比
    max_consecutive_win INT             DEFAULT 0,                       -- 最大连胜
    max_consecutive_lose INT            DEFAULT 0,                       -- 最大连亏
    avg_hold_days       DECIMAL(6,2),                                    -- 平均持仓天数

    -- 资金使用
    avg_position_pct    DECIMAL(8,4),                                    -- 平均仓位(小数)
    max_position_pct    DECIMAL(8,4),                                    -- 最大仓位(小数)
    turnover_rate       DECIMAL(8,4),                                    -- 换手率

    -- 曲线数据(JSON)
    equity_curve        JSONB           DEFAULT '[]'::jsonb,             -- 净值曲线 [{"date":"2024-01-02","nav":1.05}]
    drawdown_curve      JSONB           DEFAULT '[]'::jsonb,             -- 回撤曲线
    monthly_returns     JSONB           DEFAULT '{}'::jsonb,             -- 月度收益 {"2024-01":0.05,"2024-02":-0.02}

    -- 交易明细
    trade_log           JSONB           DEFAULT '[]'::jsonb,             -- 交易日志(精简版)

    created_at          TIMESTAMP       DEFAULT NOW(),

    CONSTRAINT uk_bt_result UNIQUE(task_id)
);

COMMENT ON TABLE backtest_results IS '回测结果，一个task对应一条result';

CREATE INDEX IF NOT EXISTS idx_bt_result_task ON backtest_results(task_id);

