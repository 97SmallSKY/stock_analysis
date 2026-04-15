-- ============================================================
-- stock_analysis 数据库初始化脚本
-- 用于创建数据库表结构和初始配置
-- ============================================================
-- 使用方法：
--   docker exec -i stock_analysis-db psql -U stock_user -d stock_analysis < init_schema.sql
-- 或在 psql 中执行：
--   \i init_schema.sql
-- ============================================================

-- 备注：由于项目使用 SQLAlchemy 自动建表，以下仅为参考结构定义
-- 实际表结构由 pandas DataFrame.to_sql() 在首次写入时自动创建

-- 以下是项目中已知的表结构（从 database.md 文档整理）

-- ============================================================
-- 1. lhb_top10 — 龙虎榜净买入 Top10
-- ============================================================
CREATE TABLE IF NOT EXISTS lhb_top10 (
    序号 BIGINT,
    代码 TEXT,
    名称 TEXT,
    上榜日 DATE,
    解读 TEXT,
    收盘价 DOUBLE PRECISION,
    涨跌幅 DOUBLE PRECISION,
    龙虎榜净买额 DOUBLE PRECISION,
    龙虎榜买入额 DOUBLE PRECISION,
    龙虎榜卖出额 DOUBLE PRECISION,
    龙虎榜成交额 DOUBLE PRECISION,
    市场总成交额 DOUBLE PRECISION,
    净买额占总成交比 DOUBLE PRECISION,
    成交额占总成交比 DOUBLE PRECISION,
    换手率 DOUBLE PRECISION,
    流通市值 DOUBLE PRECISION,
    上榜原因 TEXT,
    上榜后1日 DOUBLE PRECISION,
    上榜后2日 DOUBLE PRECISION,
    上榜后5日 DOUBLE PRECISION,
    上榜后10日 DOUBLE PRECISION,
    排名 BIGINT,
    trade_date TEXT
);

-- ============================================================
-- 2. lhb_top10_prices — T+N 价格与收益
-- ============================================================
CREATE TABLE IF NOT EXISTS lhb_top10_prices (
    代码 TEXT,
    trade_date TEXT,
    T1买入价 DOUBLE PRECISION,
    T1最高价 DOUBLE PRECISION,
    T1最低价 DOUBLE PRECISION,
    T1收盘价 DOUBLE PRECISION,
    T1收盘收益 DOUBLE PRECISION,
    T1最高收益 DOUBLE PRECISION,
    T2最高价 DOUBLE PRECISION,
    T2最低价 DOUBLE PRECISION,
    T2收盘价 DOUBLE PRECISION,
    T2收盘收益 DOUBLE PRECISION,
    T2最高收益 DOUBLE PRECISION,
    T5最高价 DOUBLE PRECISION,
    T5最低价 DOUBLE PRECISION,
    T5收盘价 DOUBLE PRECISION,
    T5收盘收益 DOUBLE PRECISION,
    T5最高收益 DOUBLE PRECISION,
    T10最高价 DOUBLE PRECISION,
    T10最低价 DOUBLE PRECISION,
    T10收盘价 DOUBLE PRECISION,
    T10收盘收益 DOUBLE PRECISION,
    T10最高收益 DOUBLE PRECISION
);

-- ============================================================
-- 3. lhb_detail — 龙虎榜明细
-- ============================================================
CREATE TABLE IF NOT EXISTS lhb_detail (
    trade_date TEXT,
    代码 TEXT,
    名称 TEXT,
    收盘价 DOUBLE PRECISION,
    涨跌幅 DOUBLE PRECISION,
    成交额 DOUBLE PRECISION,
    换手率 DOUBLE PRECISION,
    上榜原因 TEXT,
    解读 TEXT,
    龙虎榜净买额 DOUBLE PRECISION,
    龙虎榜买入额 DOUBLE PRECISION,
    龙虎榜卖出额 DOUBLE PRECISION
);

-- ============================================================
-- 4. lhb_seat_detail — 席位明细
-- ============================================================
CREATE TABLE IF NOT EXISTS lhb_seat_detail (
    trade_date TEXT,
    代码 TEXT,
    名称 TEXT,
    营业部名称 TEXT,
    买1 TEXT,
    买2 TEXT,
    买3 TEXT,
    买4 TEXT,
    买5 TEXT,
    买额1 DOUBLE PRECISION,
    买额2 DOUBLE PRECISION,
    买额3 DOUBLE PRECISION,
    买额4 DOUBLE PRECISION,
    买额5 DOUBLE PRECISION,
    卖1 TEXT,
    卖2 TEXT,
    卖3 TEXT,
    卖4 TEXT,
    卖5 TEXT,
    卖额1 DOUBLE PRECISION,
    卖额2 DOUBLE PRECISION,
    卖额3 DOUBLE PRECISION,
    卖额4 DOUBLE PRECISION,
    卖额5 DOUBLE PRECISION,
    解读 TEXT
);

-- ============================================================
-- 5. lhb_jigou — 机构汇总
-- ============================================================
CREATE TABLE IF NOT EXISTS lhb_jigou (
    trade_date TEXT,
    代码 TEXT,
    名称 TEXT,
    机构买入总额 DOUBLE PRECISION,
    机构卖出总额 DOUBLE PRECISION,
    机构净买额 DOUBLE PRECISION
);

-- ============================================================
-- 6. lhb_ggtj — 个股上榜统计
-- ============================================================
CREATE TABLE IF NOT EXISTS lhb_ggtj (
    trade_date TEXT,
    代码 TEXT,
    名称 TEXT,
    上榜次数 INTEGER,
    累计净买额 DOUBLE PRECISION,
    累计买入额 DOUBLE PRECISION,
    累计卖出额 DOUBLE PRECISION
);

-- ============================================================
-- 7. market_sentiment — 市场情绪
-- ============================================================
CREATE TABLE IF NOT EXISTS market_sentiment (
    trade_date TEXT PRIMARY KEY,
    涨停数 INTEGER,
    跌停数 INTEGER,
    跌停数 INTEGER,
    连板数 INTEGER,
    市场温度 DOUBLE PRECISION,
    备注 TEXT
);

-- ============================================================
-- 8. selected_stocks — 每日选股
-- ============================================================
CREATE TABLE IF NOT EXISTS selected_stocks (
    id SERIAL PRIMARY KEY,
    trade_date TEXT,
    code TEXT,
    name TEXT,
    score DOUBLE PRECISION,
    reason TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================
-- 9. sim_trades — 模拟交易记录
-- ============================================================
CREATE TABLE IF NOT EXISTS sim_trades (
    id SERIAL PRIMARY KEY,
    trade_date TEXT,
    code TEXT,
    name TEXT,
    signal_score DOUBLE PRECISION,
    t2_win_rate DOUBLE PRECISION,
    seat_tags TEXT,
    buy_price DOUBLE PRECISION,
    buy_amount DOUBLE PRECISION DEFAULT 100000,
    buy_shares INTEGER,
    list_price DOUBLE PRECISION,
    list_reason TEXT,
    sell_price DOUBLE PRECISION,
    sell_date TEXT,
    sell_type TEXT,
    profit DOUBLE PRECISION,
    profit_pct DOUBLE PRECISION,
    status TEXT DEFAULT 'open',
    week_tag TEXT,
    llm_reason TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    buy_source TEXT,
    reason_tech TEXT,
    reason_fund TEXT,
    reason_history TEXT,
    reason_seat TEXT
);

-- ============================================================
-- 10. sim_trade_events — 交易事件日志
-- ============================================================
CREATE TABLE IF NOT EXISTS sim_trade_events (
    id SERIAL PRIMARY KEY,
    trade_id INTEGER REFERENCES sim_trades(id),
    event_type TEXT,
    event_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    description TEXT,
    price DOUBLE PRECISION,
    shares INTEGER,
    amount DOUBLE PRECISION
);

-- ============================================================
-- 11. sim_audit_log — 审计日志
-- ============================================================
CREATE TABLE IF NOT EXISTS sim_audit_log (
    id SERIAL PRIMARY KEY,
    event_type TEXT,
    event_data JSONB,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================
-- 12. sim_selection_log — 选股日志
-- ============================================================
CREATE TABLE IF NOT EXISTS sim_selection_log (
    id SERIAL PRIMARY KEY,
    trade_date TEXT,
    code TEXT,
    name TEXT,
    score DOUBLE PRECISION,
    llm_reason TEXT,
    decision TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================
-- 索引创建（可选，提升查询性能）
-- ============================================================
CREATE INDEX IF NOT EXISTS idx_lhb_top10_trade_date ON lhb_top10(trade_date);
CREATE INDEX IF NOT EXISTS idx_lhb_top10_code ON lhb_top10(代码);
CREATE INDEX IF NOT EXISTS idx_lhb_top10_prices_code_date ON lhb_top10_prices(代码, trade_date);
CREATE INDEX IF NOT EXISTS idx_lhb_detail_trade_date ON lhb_detail(trade_date);
CREATE INDEX IF NOT EXISTS idx_sim_trades_status ON sim_trades(status);
CREATE INDEX IF NOT EXISTS idx_sim_trades_trade_date ON sim_trades(trade_date);
CREATE INDEX IF NOT EXISTS idx_sim_trade_events_trade_id ON sim_trade_events(trade_id);

-- ============================================================
-- 初始化完成
-- ============================================================
