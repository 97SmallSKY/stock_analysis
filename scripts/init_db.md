# 龙虎榜量化交易系统 v2 - 数据库设计

> 基于数据库设计评审优化版本
> 评审日期: 2026-04-16

## 一、数据库选型

### 1.1 PostgreSQL 15 + TimescaleDB

- **PostgreSQL**: 业务数据、审计日志、配置表
- **TimescaleDB**: 时序数据（K线、盘中快照），利用自动分区和压缩

### 1.2 表分层

```
┌─────────────────────────────────────────────────────────────┐
│                    TimescaleDB Hypertable                    │
│  ┌────────────────────┐  ┌────────────────────────────┐  │
│  │   kline_daily      │  │    market_snapshots         │  │
│  │   (日K线)          │  │    (盘中快照 10秒级)         │  │
│  └────────────────────┘  └────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│                    PostgreSQL 普通表                         │
│                                                             │
│  ┌───────────┐  ┌───────────┐  ┌────────────────────────┐│
│  │ 策略层    │  │ 机器人层  │  │ 龙虎榜数据层            ││
│  │ strategies│  │ robots    │  │ lhb_stock_daily        ││
│  │ strategy_ │  │ robot_     │  │ lhb_seats              ││
│  │ params    │  │ trades    │  │ lhb_tn_prices (纵表)   ││
│  └───────────┘  │ trade_    │  │ lhb_agg_stats          ││
│                 │ executions│  └────────────────────────┘│
│                 │ cash_     │                              │
│                 │ ledger    │  ┌────────────────────────┐│
│                 │ daily_nav  │  │ 市场情绪层             ││
│                 │ ahs_history│  │ market_sentiment        ││
│                 └───────────┘  └────────────────────────┘│
│  ┌─────────────────────────────────────────────────────┐  │
│  │ 事件与审计层                                         │  │
│  │ trade_events | audit_log | selection_log | app_config│  │
│  └─────────────────────────────────────────────────────┘  │
│  ┌─────────────────────────────────────────────────────┐  │
│  │ 基础数据层                                           │  │
│  │ trade_calendar | stock_info                          │  │
│  └─────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

---

## 二、完整 DDL

```sql
-- ============================================================
-- 龙虎榜量化交易系统 v2 - 数据库初始化脚本
-- PostgreSQL 15 + TimescaleDB
-- 优化版本 (基于数据库设计评审)
-- ============================================================

-- ============================================================
-- 0. 扩展
-- ============================================================
CREATE EXTENSION IF NOT EXISTS timescaledb;

-- ============================================================
-- 一、基础数据层 (新增)
-- ============================================================

-- 交易日历表 (量化系统必备)
CREATE TABLE IF NOT EXISTS trade_calendar (
    id                  SERIAL PRIMARY KEY,
    cal_date            DATE            NOT NULL UNIQUE,
    is_trading_day      BOOLEAN         NOT NULL DEFAULT FALSE,
    prev_trading_day    DATE,
    next_trading_day    DATE,
    week_of_year        SMALLINT,
    month               SMALLINT,
    quarter             SMALLINT,
    year                SMALLINT,
    note                TEXT,
    created_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_cal_trading ON trade_calendar (cal_date) WHERE is_trading_day;
CREATE INDEX IF NOT EXISTS idx_cal_next_trade ON trade_calendar (next_trading_day) WHERE is_trading_day;

-- 股票基础信息表
CREATE TABLE IF NOT EXISTS stock_info (
    id                  SERIAL PRIMARY KEY,
    stock_code          CHAR(6)         NOT NULL UNIQUE,
    stock_name          TEXT            NOT NULL,
    market              CHAR(2)         NOT NULL CHECK (market IN ('SH','SZ','BJ')),
    board               TEXT,                           -- 主板/创业板/科创板/北交所
    industry_l1         TEXT,                           -- 一级行业
    industry_l2         TEXT,                           -- 二级行业
    list_date           DATE,
    delist_date         DATE,
    is_st               BOOLEAN         NOT NULL DEFAULT FALSE,
    is_suspended        BOOLEAN         NOT NULL DEFAULT FALSE,
    updated_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_stock_name ON stock_info (stock_name);
CREATE INDEX IF NOT EXISTS idx_stock_industry ON stock_info (industry_l1, industry_l2);

-- ============================================================
-- 二、时序行情层 (TimescaleDB Hypertable)
-- ============================================================

-- 日K线（全市场，TimescaleDB 自动分区）
CREATE TABLE IF NOT EXISTS kline_daily (
    time            DATE            NOT NULL,
    stock_code      CHAR(6)         NOT NULL,
    stock_name      TEXT,                           -- 冗余存储，便于快速查询
    open            NUMERIC(10,3)   NOT NULL,
    high            NUMERIC(10,3)   NOT NULL,
    low             NUMERIC(10,3)   NOT NULL,
    close           NUMERIC(10,3)   NOT NULL,
    volume          BIGINT          NOT NULL,
    turnover        NUMERIC(16,2),
    adjust_flag     CHAR(3)         NOT NULL DEFAULT 'raw'
                    CHECK (adjust_flag IN ('raw','qfq','hfq')),
    source          TEXT            NOT NULL DEFAULT 'tushare',
    UNIQUE (time, stock_code, adjust_flag)
);
SELECT create_hypertable('kline_daily', 'time', if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS idx_kline_code_time ON kline_daily (stock_code, time DESC);
CREATE INDEX IF NOT EXISTS idx_kline_adj_code_time ON kline_daily (adjust_flag, stock_code, time DESC);

-- 盘中快照（10秒级，TimescaleDB 自动分区）
-- 注意：此表数据量极大，不使用自增ID，直接用时间戳+股票代码
CREATE TABLE IF NOT EXISTS market_snapshots (
    time            TIMESTAMPTZ     NOT NULL,
    stock_code      CHAR(6)         NOT NULL,
    price           NUMERIC(10,3)   NOT NULL,
    open            NUMERIC(10,3),
    high            NUMERIC(10,3),
    low             NUMERIC(10,3),
    volume          BIGINT,
    turnover        NUMERIC(16,2),
    bid1            NUMERIC(10,3),
    ask1            NUMERIC(10,3),
    source          TEXT            NOT NULL DEFAULT 'akshare',
    UNIQUE (time, stock_code)
);
SELECT create_hypertable('market_snapshots', 'time', if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS idx_snap_code_time ON market_snapshots (stock_code, time DESC);

-- 自动压缩（7天后压缩快照）
SELECT add_compression_policy('market_snapshots', INTERVAL '7 days', if_not_exists => TRUE);
-- 自动保留（90天后删除快照）
SELECT add_retention_policy('market_snapshots', INTERVAL '90 days', if_not_exists => TRUE);

-- ============================================================
-- 三、龙虎榜数据层
-- ============================================================

-- 龙虎榜日数据
CREATE TABLE IF NOT EXISTS lhb_stock_daily (
    id              BIGSERIAL PRIMARY KEY,            -- 改为 BIGSERIAL
    trade_date      DATE            NOT NULL,
    stock_code      CHAR(6)         NOT NULL,
    stock_name      TEXT            NOT NULL,
    close_price     NUMERIC(10,3)   NOT NULL,
    change_pct      NUMERIC(8,4),
    net_buy         BIGINT          NOT NULL,
    total_buy       BIGINT,
    total_sell      BIGINT,
    lhb_turnover    BIGINT,
    market_turnover BIGINT,
    net_buy_ratio   NUMERIC(8,6),
    turnover_ratio  NUMERIC(8,6),
    turnover_rate   NUMERIC(8,6),
    float_cap       BIGINT,
    list_reason     TEXT,
    rank            INT,
    source          TEXT            NOT NULL DEFAULT 'akshare',
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    UNIQUE (trade_date, stock_code)
);
-- UNIQUE 约束已覆盖 (trade_date, stock_code)，无需单独索引
CREATE INDEX IF NOT EXISTS idx_lhb_daily_code ON lhb_stock_daily (stock_code);

-- 席位明细
CREATE TABLE IF NOT EXISTS lhb_seats (
    id              BIGSERIAL PRIMARY KEY,
    trade_date      DATE            NOT NULL,
    stock_code      CHAR(6)         NOT NULL,
    direction       CHAR(4)         NOT NULL CHECK (direction IN ('buy','sell')),
    rank            SMALLINT        NOT NULL CHECK (rank BETWEEN 1 AND 5),
    seat_name       TEXT            NOT NULL,
    amount          BIGINT          NOT NULL,
    note            TEXT,
    source          TEXT            NOT NULL DEFAULT 'akshare',
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    UNIQUE (trade_date, stock_code, direction, rank),
    CONSTRAINT chk_seat_amount_positive CHECK (amount > 0)
);
CREATE INDEX IF NOT EXISTS idx_seats_date_code ON lhb_seats (trade_date, stock_code);
CREATE INDEX IF NOT EXISTS idx_seats_name ON lhb_seats (seat_name);

-- T+N 价格 (优化：改为纵表设计，支持任意 T+N)
CREATE TABLE IF NOT EXISTS lhb_tn_prices (
    id              BIGSERIAL PRIMARY KEY,
    trade_date      DATE            NOT NULL,
    stock_code      CHAR(6)         NOT NULL,
    n_days          SMALLINT        NOT NULL,              -- 1, 2, 3, 5, 10, 20...
    buy_price       NUMERIC(10,3),                       -- T+N 日开盘买入价
    close_price     NUMERIC(10,3),
    high_price      NUMERIC(10,3),
    low_price       NUMERIC(10,3),
    close_ret       NUMERIC(8,6),                        -- 收盘涨幅
    high_ret        NUMERIC(8,6),                        -- 最高涨幅
    low_ret         NUMERIC(8,6),                        -- 最低涨幅（最大回撤参考）
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    UNIQUE (trade_date, stock_code, n_days)
);
CREATE INDEX IF NOT EXISTS idx_tn_date_code ON lhb_tn_prices (trade_date, stock_code);
CREATE INDEX IF NOT EXISTS idx_tn_ndays ON lhb_tn_prices (n_days);

-- T+N 宽表物化视图（可选，用于快速查询）
-- CREATE MATERIALIZED VIEW IF NOT EXISTS lhb_tn_prices_wide AS
-- SELECT trade_date, stock_code,
--     MAX(CASE WHEN n_days=1 THEN close_ret END) AS t1_close_ret,
--     MAX(CASE WHEN n_days=1 THEN high_ret END) AS t1_high_ret,
--     MAX(CASE WHEN n_days=2 THEN close_ret END) AS t2_close_ret,
--     MAX(CASE WHEN n_days=2 THEN high_ret END) AS t2_high_ret,
--     MAX(CASE WHEN n_days=5 THEN close_ret END) AS t5_close_ret,
--     MAX(CASE WHEN n_days=5 THEN high_ret END) AS t5_high_ret,
--     MAX(CASE WHEN n_days=10 THEN close_ret END) AS t10_close_ret,
--     MAX(CASE WHEN n_days=10 THEN high_ret END) AS t10_high_ret
-- FROM lhb_tn_prices GROUP BY trade_date, stock_code;

-- 汇总统计
CREATE TABLE IF NOT EXISTS lhb_agg_stats (
    id              BIGSERIAL PRIMARY KEY,
    stat_date       DATE            NOT NULL,
    stat_days       SMALLINT        NOT NULL,
    stock_code      CHAR(6)         NOT NULL,
    stock_name      TEXT            NOT NULL,
    list_count      INT             NOT NULL DEFAULT 0,
    total_buy       NUMERIC(16,2)   NOT NULL DEFAULT 0,
    total_sell      NUMERIC(16,2)   NOT NULL DEFAULT 0,
    net_amount      NUMERIC(16,2)   NOT NULL DEFAULT 0,
    buy_seats       INT             DEFAULT 0,
    sell_seats      INT             DEFAULT 0,
    source          TEXT            NOT NULL DEFAULT 'akshare',
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    UNIQUE (stat_date, stat_days, stock_code)
);
CREATE INDEX IF NOT EXISTS idx_agg_date ON lhb_agg_stats (stat_date, stat_days);

-- ============================================================
-- 四、市场情绪层
-- ============================================================

CREATE TABLE IF NOT EXISTS market_sentiment (
    id              BIGSERIAL PRIMARY KEY,
    trade_date      DATE            NOT NULL UNIQUE,
    limit_up        INT             NOT NULL,
    limit_down      INT             NOT NULL,
    up_down_ratio   NUMERIC(8,4),
    continuous_limit INT,
    index_close     NUMERIC(10,3),
    index_ma5       NUMERIC(10,3),
    index_ma20      NUMERIC(10,3),
    index_change    NUMERIC(8,6),
    index_trend     TEXT            CHECK (index_trend IN ('up','down','sideways')),
    advance_ratio   NUMERIC(8,4),
    vol_ratio       NUMERIC(8,4),
    northbound_net  NUMERIC(12,2),
    temperature     NUMERIC(5,2),                       -- 市场温度指数 0-100
    trade_signal    TEXT,
    signal_reason   TEXT,
    position_coef   NUMERIC(5,3),                      -- 仓位系数 0-1
    source          TEXT            NOT NULL DEFAULT 'akshare',
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_sentiment_date ON market_sentiment (trade_date);

-- ============================================================
-- 五、策略层
-- ============================================================

CREATE TABLE IF NOT EXISTS strategies (
    id              SERIAL PRIMARY KEY,
    name            TEXT            NOT NULL UNIQUE,
    description     TEXT,
    version         TEXT            NOT NULL DEFAULT '1.0.0',
    is_active       BOOLEAN         NOT NULL DEFAULT TRUE,
    deleted_at      TIMESTAMPTZ,                         -- 软删除
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS strategy_params (
    id              SERIAL PRIMARY KEY,
    strategy_name   TEXT            NOT NULL REFERENCES strategies(name) ON DELETE CASCADE,
    param_key       TEXT            NOT NULL,
    param_value     TEXT            NOT NULL,
    param_type      TEXT            NOT NULL DEFAULT 'float'
                    CHECK (param_type IN ('float','int','bool','str','json')),
    description     TEXT,
    updated_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    UNIQUE (strategy_name, param_key)
);

-- ============================================================
-- 六、机器人层 (优化：拆分为交易主表 + 执行明细表)
-- ============================================================

CREATE TABLE IF NOT EXISTS robots (
    id              SERIAL PRIMARY KEY,
    name            TEXT            NOT NULL,
    strategy_name   TEXT            NOT NULL REFERENCES strategies(name),
    status          TEXT            NOT NULL DEFAULT 'running'
                    CHECK (status IN ('running','paused','ended','eliminated')),
    initial_capital NUMERIC(14,2)   NOT NULL DEFAULT 100000,
    current_cash    NUMERIC(14,2)   NOT NULL,
    max_positions   INT             NOT NULL DEFAULT 3,
    max_hold_days   INT             NOT NULL DEFAULT 7,

    -- 止损/止盈参数
    stop_loss_pct   NUMERIC(6,4)    NOT NULL DEFAULT -0.05,  -- 止损阈值，负数
    sell1_pct       NUMERIC(6,4)    NOT NULL DEFAULT 0.03,  -- 第一档止盈
    sell2_pct       NUMERIC(6,4)    NOT NULL DEFAULT 0.05,  -- 第二档止盈
    sell3_pct       NUMERIC(6,4)    NOT NULL DEFAULT 0.08,  -- 第三档止盈
    sell1_ratio     NUMERIC(5,4)    NOT NULL DEFAULT 0.333, -- 第一档卖出比例
    sell2_ratio     NUMERIC(5,4)    NOT NULL DEFAULT 0.333, -- 第二档卖出比例

    -- 交易费用参数
    commission_rate NUMERIC(8,6)    NOT NULL DEFAULT 0.0003,  -- 万3
    commission_min  NUMERIC(8,2)    NOT NULL DEFAULT 5.00,      -- 最低5元
    stamp_tax_rate  NUMERIC(8,6)    NOT NULL DEFAULT 0.0005,  -- 万5(卖出)
    transfer_rate   NUMERIC(8,6)    NOT NULL DEFAULT 0.00001, -- 沪市过户费
    slippage_buy    NUMERIC(8,6)    NOT NULL DEFAULT 0.001,   -- 买入滑点0.1%
    slippage_sell   NUMERIC(8,6)    NOT NULL DEFAULT 0.001,   -- 卖出滑点0.1%

    batch_buy       BOOLEAN         NOT NULL DEFAULT FALSE,
    parent_robot_id INT             REFERENCES robots(id),
    elimination_score NUMERIC(8,4),                         -- 淘汰评分
    note            TEXT,
    deleted_at      TIMESTAMPTZ,                             -- 软删除
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    ended_at        TIMESTAMPTZ,
    
    CONSTRAINT chk_initial_capital_positive CHECK (initial_capital > 0)
);
CREATE INDEX IF NOT EXISTS idx_robots_status ON robots (status);
CREATE INDEX IF NOT EXISTS idx_robots_strategy ON robots (strategy_name);
CREATE INDEX IF NOT EXISTS idx_robots_active ON robots (strategy_name) WHERE status = 'running';

-- 交易主表（核心状态 + 评分）
CREATE TABLE IF NOT EXISTS robot_trades (
    id              BIGSERIAL PRIMARY KEY,
    robot_id        INT             NOT NULL REFERENCES robots(id),
    signal_date     DATE            NOT NULL,
    buy_date        DATE,
    sell_date       DATE,
    stock_code      CHAR(6)         NOT NULL,
    stock_name      TEXT            NOT NULL,
    status          TEXT            NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending','open','partial','closed','abandoned')),
    hold_days       INT,
    lianban_count   INT             NOT NULL DEFAULT 0,

    -- 评分（选股时确定，不变）
    signal_score    NUMERIC(6,2),
    tech_score      NUMERIC(6,2),
    fund_score      NUMERIC(6,2),
    hist_score      NUMERIC(6,2),
    t2_win_rate     NUMERIC(6,4),
    seat_tags       JSONB,
    list_price      NUMERIC(10,3),
    list_reason     TEXT,
    llm_reason      TEXT,

    -- 汇总盈亏（由触发器或应用层计算）
    total_buy_cost  NUMERIC(14,4)   DEFAULT 0,
    total_sell_income NUMERIC(14,4) DEFAULT 0,
    realized_profit NUMERIC(14,4)   DEFAULT 0,
    profit_pct      NUMERIC(8,6),
    
    deleted_at      TIMESTAMPTZ,                             -- 软删除
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);
-- 部分索引：只索引活跃交易
CREATE INDEX IF NOT EXISTS idx_rt_open_trades ON robot_trades (robot_id, stock_code) WHERE status IN ('pending', 'open', 'partial');
CREATE INDEX IF NOT EXISTS idx_rt_robot_date ON robot_trades (robot_id, signal_date);
-- JSON 索引
CREATE INDEX IF NOT EXISTS idx_rt_seat_tags ON robot_trades USING GIN (seat_tags);

-- 交易执行明细（每次买卖操作都是一条记录）
CREATE TABLE IF NOT EXISTS trade_executions (
    id              BIGSERIAL PRIMARY KEY,
    trade_id        INT             NOT NULL REFERENCES robot_trades(id),
    robot_id        INT             NOT NULL REFERENCES robots(id),
    exec_date       DATE            NOT NULL,
    exec_type       TEXT            NOT NULL 
                    CHECK (exec_type IN ('buy','sell1','sell2','sell3','stop_loss','expire','manual')),
    price           NUMERIC(10,3)   NOT NULL,
    shares          INT             NOT NULL,
    amount          NUMERIC(14,2)   NOT NULL,
    commission      NUMERIC(10,4)   NOT NULL DEFAULT 0,
    stamp_tax       NUMERIC(10,4)   NOT NULL DEFAULT 0,
    transfer_fee    NUMERIC(10,4)   NOT NULL DEFAULT 0,
    slippage        NUMERIC(10,4)   NOT NULL DEFAULT 0,
    total_cost      NUMERIC(14,4)   NOT NULL,
    net_amount      NUMERIC(14,4)   NOT NULL,              -- 买入为负，卖出为正
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    
    CONSTRAINT chk_exec_shares_lot CHECK (shares % 100 = 0)
);
CREATE INDEX IF NOT EXISTS idx_exec_trade ON trade_executions (trade_id);
CREATE INDEX IF NOT EXISTS idx_exec_robot_date ON trade_executions (robot_id, exec_date);

-- 现金流水
CREATE TABLE IF NOT EXISTS cash_ledger (
    id              BIGSERIAL PRIMARY KEY,
    robot_id        INT             NOT NULL REFERENCES robots(id),
    trade_date      DATE            NOT NULL,
    event_type      TEXT            NOT NULL
                    CHECK (event_type IN ('deposit','buy_cost','sell_income','dividend','fee','adjust')),
    amount          NUMERIC(14,4)   NOT NULL,
    balance         NUMERIC(14,4)   NOT NULL,
    trade_id        INT             REFERENCES robot_trades(id),
    note            TEXT,
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_cash_robot_date ON cash_ledger (robot_id, trade_date);
CREATE INDEX IF NOT EXISTS idx_cash_trade ON cash_ledger (trade_id);

-- 每日净值
CREATE TABLE IF NOT EXISTS daily_nav (
    id              BIGSERIAL PRIMARY KEY,
    robot_id        INT             NOT NULL REFERENCES robots(id),
    nav_date        DATE            NOT NULL,
    cash            NUMERIC(14,4)   NOT NULL,
    position_mv     NUMERIC(14,4)   NOT NULL,
    total_nav       NUMERIC(14,4)   NOT NULL,
    nav_ratio       NUMERIC(10,6)   NOT NULL,
    drawdown        NUMERIC(10,6),
    max_nav         NUMERIC(14,4),
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    UNIQUE (robot_id, nav_date)
);
CREATE INDEX IF NOT EXISTS idx_nav_robot_date ON daily_nav (robot_id, nav_date);

-- AHS 历史
CREATE TABLE IF NOT EXISTS ahs_history (
    id              BIGSERIAL PRIMARY KEY,
    robot_id        INT             NOT NULL REFERENCES robots(id),
    calc_date       DATE            NOT NULL,
    ahs_score       SMALLINT        NOT NULL CHECK (ahs_score BETWEEN 0 AND 100),
    win_rate        NUMERIC(6,4),
    avg_pnl         NUMERIC(8,6),
    consecutive_loss SMALLINT,
    position_mult   NUMERIC(5,3),
    details         JSONB,
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    UNIQUE (robot_id, calc_date)
);
CREATE INDEX IF NOT EXISTS idx_ahs_robot_date ON ahs_history (robot_id, calc_date);

-- 周汇总
CREATE TABLE IF NOT EXISTS weekly_summary (
    id              BIGSERIAL PRIMARY KEY,
    robot_id        INT             NOT NULL REFERENCES robots(id),
    week_tag        TEXT            NOT NULL,
    total_trades    INT             NOT NULL DEFAULT 0,
    win_trades      INT             NOT NULL DEFAULT 0,
    lose_trades     INT             NOT NULL DEFAULT 0,
    win_rate        NUMERIC(6,4),
    total_profit    NUMERIC(14,4),
    avg_profit_pct  NUMERIC(8,6),
    max_profit_pct  NUMERIC(8,6),
    max_loss_pct    NUMERIC(8,6),
    sharpe_ratio    NUMERIC(8,4),
    max_drawdown    NUMERIC(8,6),
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    UNIQUE (robot_id, week_tag)
);

-- ============================================================
-- 七、事件与审计层
-- ============================================================

CREATE TABLE IF NOT EXISTS trade_events (
    id              BIGSERIAL PRIMARY KEY,
    trade_id        INT             NOT NULL REFERENCES robot_trades(id),
    robot_id        INT             NOT NULL REFERENCES robots(id),
    event_type      TEXT            NOT NULL
                    CHECK (event_type IN ('phase_a','phase_b','sell1','sell2','sell3','stop_loss','monitor','expire','manual','abandon')),
    event_time      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    price           NUMERIC(10,3),
    shares          INT,
    amount          NUMERIC(14,4),
    description     TEXT,
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_events_trade ON trade_events (trade_id);
CREATE INDEX IF NOT EXISTS idx_events_robot ON trade_events (robot_id);
CREATE INDEX IF NOT EXISTS idx_events_time ON trade_events (event_time);

-- 审计日志（防删除）
CREATE TABLE IF NOT EXISTS audit_log (
    id              BIGSERIAL PRIMARY KEY,
    robot_id        INT             REFERENCES robots(id),
    trade_id        INT             REFERENCES robot_trades(id),
    event_type      TEXT            NOT NULL,
    event_data      JSONB,
    ip_address      INET,
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_audit_robot ON audit_log (robot_id);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_log (created_at);

-- 防删除触发器
CREATE OR REPLACE FUNCTION prevent_audit_delete()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'audit_log 禁止删除';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_audit_no_delete ON audit_log;
CREATE TRIGGER trg_audit_no_delete
    BEFORE DELETE ON audit_log
    FOR EACH ROW EXECUTE FUNCTION prevent_audit_delete();

-- 选股决策日志
CREATE TABLE IF NOT EXISTS selection_log (
    id              BIGSERIAL PRIMARY KEY,
    robot_id        INT             NOT NULL REFERENCES robots(id),
    signal_date     DATE            NOT NULL,
    stock_code      CHAR(6)         NOT NULL,
    stock_name      TEXT,
    signal_score    NUMERIC(6,2),
    tech_score      NUMERIC(6,2),
    fund_score      NUMERIC(6,2),
    hist_score      NUMERIC(6,2),
    decision        TEXT            NOT NULL
                    CHECK (decision IN ('selected','rejected','filtered')),
    reject_reason   TEXT,
    llm_reason      TEXT,
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_sel_robot_date ON selection_log (robot_id, signal_date);
CREATE INDEX IF NOT EXISTS idx_sel_selected ON selection_log (robot_id, signal_date) WHERE decision = 'selected';

-- ============================================================
-- 八、应用配置
-- ============================================================

CREATE TABLE IF NOT EXISTS app_config (
    key             TEXT            PRIMARY KEY,
    value           TEXT            NOT NULL,
    value_type      TEXT            NOT NULL DEFAULT 'str'
                    CHECK (value_type IN ('str','int','float','bool','json')),
    description     TEXT,
    updated_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

-- ============================================================
-- 九、通用触发器：updated_at 自动更新
-- ============================================================

CREATE OR REPLACE FUNCTION update_timestamp()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- 为所有有 updated_at 的表创建触发器
DO $$
DECLARE
    tbl TEXT;
BEGIN
    FOREACH tbl IN ARRAY ARRAY[
        'trade_calendar', 'stock_info', 'kline_daily', 
        'lhb_stock_daily', 'lhb_seats', 'lhb_tn_prices', 'lhb_agg_stats',
        'market_sentiment', 'strategies', 'strategy_params',
        'robots', 'robot_trades', 'daily_nav', 'ahs_history', 'weekly_summary',
        'trade_events', 'selection_log', 'app_config'
    ]
    LOOP
        EXECUTE format(
            'DROP TRIGGER IF EXISTS trg_%s_updated_at ON %I;
             CREATE TRIGGER trg_%s_updated_at 
             BEFORE UPDATE ON %I 
             FOR EACH ROW EXECUTE FUNCTION update_timestamp()',
            tbl, tbl, tbl, tbl
        );
    END LOOP;
END $$;

-- ============================================================
-- 十、初始数据
-- ============================================================

INSERT INTO strategies (name, description, version) VALUES
    ('lhb_multifactor', '龙虎榜多因子策略（技术40+资金35+历史25）', '1.0.0')
ON CONFLICT (name) DO NOTHING;

INSERT INTO strategy_params (strategy_name, param_key, param_value, param_type, description) VALUES
    ('lhb_multifactor', 'min_net_buy',    '50000000', 'int',   '最低净买入（元）'),
    ('lhb_multifactor', 'min_tech_score', '50',       'int',   '最低技术面得分'),
    ('lhb_multifactor', 'max_hold_days',  '3',        'int',   '最大持仓交易日'),
    ('lhb_multifactor', 'stop_loss_pct',  '-0.05',    'float', '止损阈值'),
    ('lhb_multifactor', 'sell1_pct',      '0.03',     'float', '第一档止盈'),
    ('lhb_multifactor', 'sell2_pct',      '0.05',     'float', '第二档止盈'),
    ('lhb_multifactor', 'sell3_pct',      '0.08',     'float', '第三档止盈（清仓）'),
    ('lhb_multifactor', 'sell1_ratio',    '0.333',    'float', '第一档卖出比例'),
    ('lhb_multifactor', 'sell2_ratio',    '0.333',    'float', '第二档卖出比例'),
    ('lhb_multifactor', 'top_n',          '3',        'int',   '每日候选数量')
ON CONFLICT (strategy_name, param_key) DO NOTHING;

INSERT INTO app_config (key, value, value_type, description) VALUES
    ('SKIP_CIRCUIT_BREAKER',  'false', 'bool',  '跳过熔断检查，仅调试用'),
    ('MONITOR_INTERVAL_SEC',  '10',    'int',   '盘中行情轮询间隔（秒）'),
    ('SNAP_INTERVAL_MIN',     '5',     'int',   '行情快照存储间隔（分钟）'),
    ('ELIMINATION_ENABLED',   'true',  'bool',  '是否启用自动淘汰'),
    ('ELIMINATION_DAY',       '5',     'int',   '每周几执行淘汰（5=周五）'),
    ('FEISHU_WEBHOOK_URL',    '',      'str',   '飞书通知 Webhook'),
    ('LLM_ENABLED',           'true',  'bool',  'LLM 选股开关'),
    ('LLM_FALLBACK_RULE',     'true',  'bool',  'LLM 失败时启用规则降级')
ON CONFLICT (key) DO NOTHING;

-- 默认机器人
INSERT INTO robots (name, strategy_name, current_cash, note) VALUES
    ('Alpha-1', 'lhb_multifactor', 100000, '默认机器人，龙虎榜多因子策略')
ON CONFLICT DO NOTHING;

-- ============================================================
-- 十一、表注释
-- ============================================================

DO $$
DECLARE
    tbl_comment TEXT;
    col_comment TEXT;
BEGIN
    -- 表注释
    EXECUTE 'COMMENT ON TABLE trade_calendar IS ''交易日历表''';
    EXECUTE 'COMMENT ON TABLE stock_info IS ''股票基础信息表''';
    EXECUTE 'COMMENT ON TABLE kline_daily IS ''日K线行情（TimescaleDB 超表，按月分区）''';
    EXECUTE 'COMMENT ON TABLE market_snapshots IS ''盘中行情快照（10秒级，按天分区，90天保留）''';
    EXECUTE 'COMMENT ON TABLE lhb_stock_daily IS ''龙虎榜每日个股数据''';
    EXECUTE 'COMMENT ON TABLE lhb_seats IS ''龙虎榜买卖席位明细（每只股票买卖各Top5）''';
    EXECUTE 'COMMENT ON TABLE lhb_tn_prices IS ''龙虎榜上榜股票T+N日价格追踪（纵表设计）''';
    EXECUTE 'COMMENT ON TABLE lhb_agg_stats IS ''龙虎榜多日汇总统计（3日/5日/10日等）''';
    EXECUTE 'COMMENT ON TABLE market_sentiment IS ''市场情绪指标（每日一条）''';
    EXECUTE 'COMMENT ON TABLE strategies IS ''策略定义表''';
    EXECUTE 'COMMENT ON TABLE strategy_params IS ''策略参数表（KV结构）''';
    EXECUTE 'COMMENT ON TABLE robots IS ''交易机器人实例''';
    EXECUTE 'COMMENT ON TABLE robot_trades IS ''机器人交易记录（信号+汇总盈亏）''';
    EXECUTE 'COMMENT ON TABLE trade_executions IS ''交易执行明细（每次买卖操作）''';
    EXECUTE 'COMMENT ON TABLE cash_ledger IS ''机器人现金流水账''';
    EXECUTE 'COMMENT ON TABLE daily_nav IS ''机器人每日净值''';
    EXECUTE 'COMMENT ON TABLE ahs_history IS ''机器人自适应健康评分历史''';
    EXECUTE 'COMMENT ON TABLE weekly_summary IS ''机器人周度绩效汇总''';
    EXECUTE 'COMMENT ON TABLE trade_events IS ''交易事件流（买卖信号、监控、止损等）''';
    EXECUTE 'COMMENT ON TABLE audit_log IS ''审计日志（禁止删除）''';
    EXECUTE 'COMMENT ON TABLE selection_log IS ''选股决策日志（入选/淘汰原因）''';
    EXECUTE 'COMMENT ON TABLE app_config IS ''应用级配置（KV结构）''';

    -- 关键列注释
    EXECUTE 'COMMENT ON COLUMN robots.stop_loss_pct IS ''止损阈值，负数，如 -0.05 表示亏5%止损''';
    EXECUTE 'COMMENT ON COLUMN robots.sell1_ratio IS ''第一档止盈卖出比例，如 0.333 表示卖1/3''';
    EXECUTE 'COMMENT ON COLUMN robots.elimination_score IS ''淘汰评分，低于阈值的机器人将被淘汰''';
    EXECUTE 'COMMENT ON COLUMN robots.slippage_buy IS ''买入滑点比例，如 0.001 表示0.1%''';
    EXECUTE 'COMMENT ON COLUMN market_sentiment.temperature IS ''市场温度指数 0-100，越高越热''';
    EXECUTE 'COMMENT ON COLUMN market_sentiment.position_coef IS ''仓位系数 0-1，情绪越冷系数越低''';
    EXECUTE 'COMMENT ON COLUMN lhb_stock_daily.net_buy_ratio IS ''净买入占比 = net_buy / market_turnover''';
    EXECUTE 'COMMENT ON COLUMN ahs_history.ahs_score IS ''自适应健康评分 0-100''';
    EXECUTE 'COMMENT ON COLUMN kline_daily.adjust_flag IS ''复权类型：raw=不复权, qfq=前复权, hfq=后复权''';
    EXECUTE 'COMMENT ON COLUMN trade_executions.exec_type IS ''执行类型：buy=买入, sell1~3=分档卖出, stop_loss=止损, expire=到期, manual=手动''';
END $$;

-- ============================================================
-- 完成
-- ============================================================
DO $$
BEGIN
    RAISE NOTICE 'Database initialization completed successfully!';
    RAISE NOTICE 'Optimized version with:';
    RAISE NOTICE '  - T+N prices: Vertical table design';
    RAISE NOTICE '  - Trades: Split into main table + executions';
    RAISE NOTICE '  - High-frequency tables: BIGSERIAL';
    RAISE NOTICE '  - Business constraints added';
    RAISE NOTICE '  - Auto-update triggers';
    RAISE NOTICE '  - Soft delete support';
    RAISE NOTICE '  - Table comments';
END $$;
