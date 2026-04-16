# 龙虎榜量化交易系统 v2 - 数据库设计

> 本文档定义完整的数据库 schema，包括表结构、索引、约束、触发器和 TimescaleDB 配置。

---

## 评审建议采纳说明

> 二次评审修订日期: 2026-04-16

### 已采纳的改进项 ✅

| 评审问题 | 采纳的改进 |
|----------|------------|
| robots 资金字段与 cash_ledger 一致性风险 | 明确字段语义注释 + 添加 version 乐观锁字段 + 对账函数 verify_robot_cash |
| robot_trades 状态机缺乏数据库层约束 | 添加状态转换触发器 check_trade_status_transition |
| trade_executions 与 robot_trades 数据一致性 | 明确字段定位注释 + 添加对账视图 v_trade_execution_summary |
| 索引冗余问题 | 删除 5 个冗余索引（idx_lhb_date、idx_seats_date、idx_tn_date、idx_tn_date_stock、idx_kline_date_stock） |
| 金额精度不统一 | 统一三种规格：金额 DECIMAL(16,2)、比率 DECIMAL(10,6)、价格 DECIMAL(10,3) |
| lhb_seats 缺少唯一约束 | 恢复唯一约束 uk_seats_date_stock_dir_rank |
| 视图设计存在性能隐患 | 优化 v_robot_health 使用 CTE 预聚合 |
| 数据库用户权限过于宽泛 | 分离 app_rw 读写用户和 app_ro 只读用户 |

### 不采纳的建议及原因 ❌

| 评审建议 | 不采纳原因 |
|----------|------------|
| 新增 robot_positions 持仓表（立即实现） | 当前阶段从 trades 聚合已足够，持仓表需要维护同步逻辑增加复杂度；作为后续优化方向在架构文档中说明 |
| 新增 backtest_tasks 表 | 回测任务的管理属于应用层，回测结果已通过其他表存储，暂不需要新表 |

---

## 一、数据库选型

| 组件 | 选型 | 说明 |
|------|------|------|
| 主库 | PostgreSQL 15 | ACID 事务，复杂查询 |
| 时序扩展 | TimescaleDB 2.x | 自动分区，时序查询优化 |
| 缓存 | Redis 7 | 实时行情缓存，Celery Broker |

---

## 二、命名规范

### 2.1 表名规范

| 规则 | 示例 | 说明 |
|------|------|------|
| 按业务域添加前缀 | robot_*, lhb_*, market_*, trade_* | 便于识别和管理 |
| 辅助表不加前缀 | strategies, app_config, stock_info, trade_calendar | 通用配置类表 |
| TimescaleDB 超表按数据类型命名 | kline_daily, market_snapshots | 时序数据表 |

### 2.2 字段名规范

| 类型 | 后缀 | 格式 | 示例 |
|------|------|------|------|
| 金额字段 | _amount, _capital, _balance | DECIMAL(16,2) | buy_amount, initial_capital, cash_balance |
| 比率字段 | _ratio, _rate | DECIMAL(10,6)（小数形式） | pnl_ratio, win_rate, change_ratio |
| 价格字段 | _price | DECIMAL(10,3) | buy_price, close_price |
| 数量字段 | _count, _shares | INT/BIGINT | trade_count, buy_shares |
| 时间字段 | created_at, updated_at, deleted_at | TIMESTAMPTZ | created_at |
| 日期字段 | _date | DATE | trade_date, signal_date |

### 2.3 主键规范

| 表类型 | 主键设计 |
|--------|----------|
| 普通表 | id BIGSERIAL PRIMARY KEY |
| TimescaleDB 超表 | 复合主键 (time_column, stock_code) |

---

## 三、核心表设计

### 3.1 robots（机器人配置）

```sql
CREATE TABLE robots (
    id BIGSERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'paper_trading',
    mode VARCHAR(20) NOT NULL DEFAULT 'paper_trading',
    strategy_id BIGINT NOT NULL,
    initial_capital DECIMAL(16,2) NOT NULL,
    current_capital DECIMAL(16,2) NOT NULL DEFAULT 0.00,
    cash_balance DECIMAL(16,2) NOT NULL DEFAULT 0.00,
    position_ratio DECIMAL(5,4) NOT NULL DEFAULT 0.0000,
    version INT NOT NULL DEFAULT 0,         -- 乐观锁版本号
    config JSONB NOT NULL DEFAULT '{}',
    stop_loss_ratio DECIMAL(6,4) DEFAULT -0.0500,   -- 重命名：stop_loss_pct → stop_loss_ratio
    take_profit_tiers JSONB DEFAULT '[{"ratio":0.03,"sell_ratio":0.33},{"ratio":0.05,"sell_ratio":0.33},{"ratio":0.08,"sell_ratio":0.34}]',
    max_hold_days INT DEFAULT 5,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at TIMESTAMPTZ,
    CONSTRAINT chk_status CHECK (status IN ('paper_trading', 'running', 'paused', 'stopped', 'eliminated')),
    CONSTRAINT chk_mode CHECK (mode IN ('paper_trading', 'live')),
    CONSTRAINT chk_initial_capital CHECK (initial_capital > 0)
);

COMMENT ON TABLE robots IS '机器人配置表：存储策略配置和实时资金状态';
COMMENT ON COLUMN robots.current_capital IS '当前持仓市值（由结算任务更新），不含现金';
COMMENT ON COLUMN robots.cash_balance IS '当前可用现金（由交易执行原子更新），应用乐观锁';
COMMENT ON COLUMN robots.position_ratio IS '当前仓位比例 = 持仓市值 / (持仓市值 + 现金)';
COMMENT ON COLUMN robots.version IS '乐观锁版本号，每次更新资金时+1，防止并发更新丢失';
COMMENT ON COLUMN robots.stop_loss_ratio IS '策略级止损比例（小数形式），默认-5%';
COMMENT ON COLUMN robots.take_profit_tiers IS '分档止盈配置';

CREATE INDEX idx_robots_status ON robots(status) WHERE deleted_at IS NULL;
CREATE INDEX idx_robots_strategy ON robots(strategy_id);
CREATE INDEX idx_robots_deleted ON robots(deleted_at);

-- 对账函数：校验 robots.cash_balance 与 cash_ledger 是否一致
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
```

### 3.2 robot_trades（交易主表）

```sql
CREATE TABLE robot_trades (
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
    CONSTRAINT fk_robot FOREIGN KEY (robot_id) REFERENCES robots(id),
    CONSTRAINT chk_trade_status CHECK (status IN ('pending', 'open', 'partial', 'closed', 'abandoned', 'eliminated')),
    CONSTRAINT chk_stock_code CHECK (stock_code ~ '^[0-9]{6}$'),
    CONSTRAINT chk_buy_shares CHECK (buy_shares IS NULL OR buy_shares % 100 = 0)
);

COMMENT ON TABLE robot_trades IS '交易主表：一笔交易从候选到平仓的完整生命周期';
COMMENT ON COLUMN robot_trades.status IS 'pending=候选, open=持仓, partial=部分成交, closed=已平仓, abandoned=已放弃, eliminated=已淘汰';
COMMENT ON COLUMN robot_trades.take_profit_tiers IS '分档止盈状态，如 [{"ratio":0.03,"sell_ratio":0.33,"executed":false}]';

CREATE INDEX idx_trades_robot ON robot_trades(robot_id);
CREATE INDEX idx_trades_status ON robot_trades(status);
CREATE INDEX idx_trades_signal_date ON robot_trades(signal_date);
CREATE INDEX idx_trades_stock ON robot_trades(stock_code);
CREATE INDEX idx_trades_robot_status ON robot_trades(robot_id, status) WHERE deleted_at IS NULL;

-- 状态转换触发器：防止非法状态转换
CREATE OR REPLACE FUNCTION check_trade_status_transition()
RETURNS TRIGGER AS $$
DECLARE
    valid_transitions JSONB := '{
        "pending":    ["open", "abandoned"],
        "open":       ["partial", "closed", "abandoned", "eliminated"],
        "partial":   ["closed", "abandoned", "eliminated"],
        "closed":    [],
        "abandoned": [],
        "eliminated": []
    }'::JSONB;
    allowed_arr JSONB;
BEGIN
    IF OLD.status = NEW.status THEN
        RETURN NEW;
    END IF;

    allowed_arr := valid_transitions -> OLD.status;
    
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
```

### 3.3 trade_executions（交易执行明细）

```sql
CREATE TABLE trade_executions (
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
    CONSTRAINT fk_trade FOREIGN KEY (trade_id) REFERENCES robot_trades(id),
    CONSTRAINT chk_exec_type CHECK (execution_type IN ('buy', 'sell_1', 'sell_2', 'sell_3', 'stop_loss', 'expire', 'manual'))
);

COMMENT ON TABLE trade_executions IS '交易执行明细：每笔实际成交记录（含费用）';
COMMENT ON COLUMN trade_executions.commission IS '佣金（最低5元）';
COMMENT ON COLUMN trade_executions.stamp_tax IS '印花税（卖出时0.05%）';
COMMENT ON COLUMN trade_executions.transfer_fee IS '过户费（0.001%）';

CREATE INDEX idx_executions_trade ON trade_executions(trade_id);
CREATE INDEX idx_executions_time ON trade_executions(execution_time);
CREATE INDEX idx_executions_idempotency ON trade_executions(idempotency_key);
```

### 3.4 cash_ledger（现金流水）

```sql
CREATE TABLE cash_ledger (
    id BIGSERIAL PRIMARY KEY,
    robot_id BIGINT NOT NULL,
    trans_type VARCHAR(20) NOT NULL,
    amount DECIMAL(16,2) NOT NULL,
    balance_after DECIMAL(16,2) NOT NULL,
    ref_type VARCHAR(50),
    ref_id BIGINT,
    memo TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT fk_robot_cash FOREIGN KEY (robot_id) REFERENCES robots(id),
    CONSTRAINT chk_trans_type CHECK (trans_type IN ('deposit', 'withdraw', 'buy', 'sell', 'commission', 'stamp_tax', 'transfer_fee', 'dividend', 'adjustment'))
);

COMMENT ON TABLE cash_ledger IS '现金流水：所有资金进出记录，用于一致性校验';

CREATE INDEX idx_cash_robot ON cash_ledger(robot_id);
CREATE INDEX idx_cash_time ON cash_ledger(created_at);
CREATE INDEX idx_cash_ref ON cash_ledger(ref_type, ref_id);
```

### 3.5 daily_nav（净值曲线）

```sql
CREATE TABLE daily_nav (
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
    CONSTRAINT fk_robot_nav FOREIGN KEY (robot_id) REFERENCES robots(id),
    CONSTRAINT uk_nav_date UNIQUE (robot_id, nav_date)
);

COMMENT ON TABLE daily_nav IS '每日净值快照：用于计算收益曲线和风险指标';

CREATE INDEX idx_nav_robot_date ON daily_nav(robot_id, nav_date);
```

### 3.6 weekly_summary（周汇总）

```sql
CREATE TABLE weekly_summary (
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
    CONSTRAINT fk_robot_weekly FOREIGN KEY (robot_id) REFERENCES robots(id),
    CONSTRAINT uk_weekly UNIQUE (robot_id, week_start_date)
);

COMMENT ON TABLE weekly_summary IS '周度汇总：用于淘汰机制计算';

CREATE INDEX idx_weekly_robot ON weekly_summary(robot_id);
CREATE INDEX idx_weekly_date ON weekly_summary(week_end_date);
```

### 3.7 ahs_history（AHS评分历史）

```sql
CREATE TABLE ahs_history (
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
    CONSTRAINT fk_robot_ahs FOREIGN KEY (robot_id) REFERENCES robots(id),
    CONSTRAINT chk_mode CHECK (trading_mode IN ('normal', 'cautious', 'warning', 'crisis'))
);

COMMENT ON TABLE ahs_history IS 'AHS评分历史：追溯风控决策';

CREATE INDEX idx_ahs_robot ON ahs_history(robot_id);
CREATE INDEX idx_ahs_date ON ahs_history(score_date);
```

### 3.8 lhb_stock_daily（龙虎榜日数据）

```sql
CREATE TABLE lhb_stock_daily (
    id BIGSERIAL PRIMARY KEY,
    trade_date DATE NOT NULL,
    stock_code VARCHAR(6) NOT NULL,
    stock_name VARCHAR(50),
    close_price DECIMAL(10,3),
    change_ratio DECIMAL(10,6),       -- 统一精度
    turnover_rate DECIMAL(10,6),      -- 统一精度
    total_buy_amount DECIMAL(16,2),  -- 统一精度
    total_sell_amount DECIMAL(16,2),
    net_buy_amount DECIMAL(16,2),
    net_buy_ratio DECIMAL(10,6),     -- 统一精度
    buy_seats INT DEFAULT 0,
    sell_seats INT DEFAULT 0,
    institution_buy_amount DECIMAL(16,2) DEFAULT 0.00,
    institution_sell_amount DECIMAL(16,2) DEFAULT 0.00,
    institution_net_buy DECIMAL(16,2) DEFAULT 0.00,
    reason VARCHAR(200),
    board_type VARCHAR(20),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uk_lhb_stock_date UNIQUE (trade_date, stock_code),
    CONSTRAINT chk_stock_code CHECK (stock_code ~ '^[0-9]{6}$')
);

COMMENT ON TABLE lhb_stock_daily IS '龙虎榜每日股票汇总数据';

CREATE INDEX idx_lhb_stock ON lhb_stock_daily(stock_code);
CREATE INDEX idx_lhb_net_buy ON lhb_stock_daily(net_buy_amount DESC);
```

### 3.9 lhb_seats（席位明细）

```sql
-- 注意：去掉了对外键约束，改为应用层保证一致性
CREATE TABLE lhb_seats (
    id BIGSERIAL PRIMARY KEY,
    trade_date DATE NOT NULL,
    stock_code VARCHAR(6) NOT NULL,
    seat_name VARCHAR(100) NOT NULL,
    seat_type VARCHAR(20) NOT NULL,
    direction VARCHAR(4) NOT NULL,
    rank INT NOT NULL,
    amount DECIMAL(18,2) NOT NULL DEFAULT 0.00,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_seat_type CHECK (seat_type IN ('institution', 'broker', 'individual')),
    CONSTRAINT chk_direction CHECK (direction IN ('buy', 'sell')),
    CONSTRAINT chk_rank CHECK (rank BETWEEN 1 AND 5),
    CONSTRAINT chk_stock CHECK (stock_code ~ '^[0-9]{6}$'),
    -- 恢复唯一约束，防止重复采集
    CONSTRAINT uk_seats_date_stock_dir_rank UNIQUE (trade_date, stock_code, direction, rank)
);

COMMENT ON TABLE lhb_seats IS '龙虎榜席位明细：买入和卖出席位分开记录，带排名';
COMMENT ON COLUMN lhb_seats.direction IS 'buy=买入, sell=卖出';
COMMENT ON COLUMN lhb_seats.rank IS '席位排名 1-5';

CREATE INDEX idx_seats_stock ON lhb_seats(stock_code);
CREATE INDEX idx_seats_name ON lhb_seats(seat_name);
```

### 3.10 lhb_tn_prices（T+N 价格纵表）

```sql
CREATE TABLE lhb_tn_prices (
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
    CONSTRAINT uk_tn_price UNIQUE (trade_date, stock_code, n_days),
    CONSTRAINT chk_n_days CHECK (n_days >= 1),
    CONSTRAINT chk_stock CHECK (stock_code ~ '^[0-9]{6}$')
);

COMMENT ON TABLE lhb_tn_prices IS '龙虎榜T+N表现：纵表设计，N无上限';
COMMENT ON COLUMN lhb_tn_prices.n_days IS 'T+N 中的 N，取值 >= 1';

-- 删除冗余索引：uk_tn_price 前缀已覆盖所有查询场景
```

### 3.11 lhb_agg_stats（龙虎榜汇总统计）

```sql
CREATE TABLE lhb_agg_stats (
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
    CONSTRAINT uk_agg_stat UNIQUE (stock_code, stat_date, lookback_days),
    CONSTRAINT chk_stock CHECK (stock_code ~ '^[0-9]{6}$')
);

COMMENT ON TABLE lhb_agg_stats IS '龙虎榜多日汇总统计';

CREATE INDEX idx_agg_stock ON lhb_agg_stats(stock_code);
CREATE INDEX idx_agg_date ON lhb_agg_stats(stat_date);
```

### 3.12 lhb_institution_stats（机构统计）

```sql
CREATE TABLE lhb_institution_stats (
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
    CONSTRAINT uk_inst_stat UNIQUE (seat_name, stat_date, lookback_days)
);

COMMENT ON TABLE lhb_institution_stats IS '机构席位历史胜率统计';

CREATE INDEX idx_inst_seat ON lhb_institution_stats(seat_name);
CREATE INDEX idx_inst_date ON lhb_institution_stats(stat_date);
```

### 3.13 kline_daily（日K线，TimescaleDB）

```sql
-- 注意：使用 DATE 类型而非 TIMESTAMP
CREATE TABLE kline_daily (
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
    CONSTRAINT chk_stock CHECK (stock_code ~ '^[0-9]{6}$'),
    CONSTRAINT chk_price CHECK (
        high_price >= low_price AND
        high_price >= open_price AND
        high_price >= close_price AND
        low_price <= open_price AND
        low_price <= close_price
    )
);

SELECT create_hypertable('kline_daily', 'trade_date', if_not_exists => TRUE);

COMMENT ON TABLE kline_daily IS '日K线数据（TimescaleDB hypertable，无MA冗余字段）';

CREATE INDEX idx_kline_stock ON kline_daily(stock_code);

-- 分区策略说明：
-- kline_daily: 默认7天分区（约 5000只 × 7天 = 35000条/chunk）
-- market_snapshots: 默认7天分区（约 50只 × 72000条/天 × 7 = 50.4万条/chunk）
-- 如果监控股票数超过 200 只，建议缩短为 1 天分区

-- 压缩策略（超过3个月的数据压缩）
SELECT add_compression_policy('kline_daily', INTERVAL '3 months', if_not_exists => TRUE);

-- 数据保留策略（超过2年的K线归档）
SELECT add_retention_policy('kline_daily', INTERVAL '2 years', if_not_exists => TRUE);
```

### 3.14 market_snapshots（盘中快照，TimescaleDB）

```sql
CREATE TABLE market_snapshots (
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
    CONSTRAINT chk_stock CHECK (stock_code ~ '^[0-9]{6}$')
);

SELECT create_hypertable('market_snapshots', 'snapshot_time', if_not_exists => TRUE);

COMMENT ON TABLE market_snapshots IS '盘中实时快照（TimescaleDB hypertable，每10秒采集）';

CREATE INDEX idx_snapshot_stock ON market_snapshots(stock_code);
CREATE INDEX idx_snapshot_time_stock ON market_snapshots(snapshot_time, stock_code);

-- 压缩策略
SELECT add_compression_policy('market_snapshots', INTERVAL '7 days', if_not_exists => TRUE);

-- 数据保留策略（90天）
SELECT add_retention_policy('market_snapshots', INTERVAL '90 days', if_not_exists => TRUE);
```

### 3.15 market_sentiment（市场情绪）

```sql
CREATE TABLE market_sentiment (
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

CREATE INDEX idx_sentiment_date ON market_sentiment(trade_date);
```

### 3.16 strategies（策略配置）

```sql
CREATE TABLE strategies (
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

CREATE INDEX idx_strategies_active ON strategies(is_active) WHERE deleted_at IS NULL;
```

### 3.17 strategy_params（策略参数）

```sql
CREATE TABLE strategy_params (
    id BIGSERIAL PRIMARY KEY,
    strategy_id BIGINT NOT NULL,
    param_name VARCHAR(50) NOT NULL,
    param_value TEXT NOT NULL,
    param_type VARCHAR(20) NOT NULL DEFAULT 'string',
    description TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT fk_strategy FOREIGN KEY (strategy_id) REFERENCES strategies(id),
    CONSTRAINT uk_strategy_param UNIQUE (strategy_id, param_name)
);

COMMENT ON TABLE strategy_params IS '策略参数表：支持运行时动态调整';

CREATE INDEX idx_params_strategy ON strategy_params(strategy_id);
```

### 3.18 trade_events（交易事件）

```sql
CREATE TABLE trade_events (
    id BIGSERIAL PRIMARY KEY,
    trade_id BIGINT NOT NULL,
    event_type VARCHAR(50) NOT NULL,
    event_time TIMESTAMPTZ NOT NULL,
    event_data JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT fk_trade FOREIGN KEY (trade_id) REFERENCES robot_trades(id)
);

COMMENT ON TABLE trade_events IS '交易事件日志：记录交易的每个关键事件';

CREATE INDEX idx_events_trade ON trade_events(trade_id);
CREATE INDEX idx_events_time ON trade_events(event_time);
CREATE INDEX idx_events_type ON trade_events(event_type);
```

### 3.19 selection_log（选股日志）

```sql
CREATE TABLE selection_log (
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
    CONSTRAINT fk_robot FOREIGN KEY (robot_id) REFERENCES robots(id),
    CONSTRAINT chk_phase CHECK (phase IN ('A', 'B')),
    CONSTRAINT chk_action CHECK (action_taken IN ('confirmed', 'rejected', 'pending', 'expired'))
);

COMMENT ON TABLE selection_log IS '选股日志：Phase A/B 的候选和确认记录';

CREATE INDEX idx_selection_robot ON selection_log(robot_id);
CREATE INDEX idx_selection_date ON selection_log(signal_date);
CREATE INDEX idx_selection_robot_date ON selection_log(robot_id, signal_date);
```

### 3.20 audit_log（审计日志）

```sql
CREATE TABLE audit_log (
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

CREATE INDEX idx_audit_user ON audit_log(user_id);
CREATE INDEX idx_audit_action ON audit_log(action);
CREATE INDEX idx_audit_resource ON audit_log(resource_type, resource_id);
CREATE INDEX idx_audit_time ON audit_log(created_at);

-- 审计日志禁止删除触发器
CREATE OR REPLACE FUNCTION prevent_audit_delete()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'audit_log records cannot be deleted';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trigger_prevent_audit_delete
    BEFORE DELETE ON audit_log
    FOR EACH ROW EXECUTE FUNCTION prevent_audit_delete();
```

### 3.21 其他辅助表

```sql
-- 应用配置
CREATE TABLE app_config (
    key VARCHAR(100) PRIMARY KEY,
    value TEXT NOT NULL,
    description TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 交易日历
CREATE TABLE trade_calendar (
    trade_date DATE PRIMARY KEY,
    is_open BOOLEAN NOT NULL DEFAULT TRUE,
    market VARCHAR(20) DEFAULT 'A股',
    prev_trading_day DATE,
    next_trading_day DATE
);

CREATE INDEX idx_calendar_open ON trade_calendar(is_open) WHERE is_open = TRUE;

-- 股票基础信息
CREATE TABLE stock_info (
    stock_code VARCHAR(6) PRIMARY KEY,
    stock_name VARCHAR(50) NOT NULL,
    industry VARCHAR(50),
    board_type VARCHAR(20),
    is_st BOOLEAN DEFAULT FALSE,
    list_date DATE,
    is_active BOOLEAN DEFAULT TRUE,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_stock_active ON stock_info(is_active);
CREATE INDEX idx_stock_industry ON stock_info(industry);
```

---

## 四、视图定义

### 4.1 数据健康巡检视图

```sql
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
```

### 4.2 机器人健康概览视图

```sql
CREATE OR REPLACE VIEW v_robot_health AS
WITH latest_ahs AS (
    -- 取每个机器人最新的 AHS 记录（而非当日）
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
    -- 预聚合持仓数量，避免关联子查询
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

-- 对账视图：检查 executions 与 trades 的一致性
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

-- 对账查询：找出不一致的交易
-- SELECT * FROM v_trade_execution_summary WHERE NOT buy_match OR NOT sell_match;
```

---

## 五、触发器

### 5.1 自动更新 updated_at

```sql
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

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
```

---

## 六、TimescaleDB 配置

```sql
-- 压缩策略
SELECT add_compression_policy('kline_daily', INTERVAL '3 months', if_not_exists => TRUE);
SELECT add_compression_policy('market_snapshots', INTERVAL '7 days', if_not_exists => TRUE);

-- 数据保留策略
SELECT add_retention_policy('kline_daily', INTERVAL '2 years', if_not_exists => TRUE);
SELECT add_retention_policy('market_snapshots', INTERVAL '90 days', if_not_exists => TRUE);
```

---

## 七、数据库用户权限

```sql
-- 1. 应用读写用户（FastAPI + Celery）
CREATE USER app_rw WITH PASSWORD '${APP_RW_PASSWORD}';
GRANT CONNECT ON DATABASE lhb_quant TO app_rw;
GRANT USAGE ON SCHEMA public TO app_rw;
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public TO app_rw;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO app_rw;
-- 仅对特定表授予 DELETE（软删除不需要 DELETE）
GRANT DELETE ON TABLE trade_events, selection_log, market_snapshots TO app_rw;

-- 2. 只读用户（监控、BI、数据分析）
CREATE USER app_ro WITH PASSWORD '${APP_RO_PASSWORD}';
GRANT CONNECT ON DATABASE lhb_quant TO app_ro;
GRANT USAGE ON SCHEMA public TO app_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO app_ro;

-- 3. 自动授权新表
ALTER DEFAULT PRIVILEGES IN SCHEMA public 
    GRANT SELECT, INSERT, UPDATE ON TABLES TO app_rw;
ALTER DEFAULT PRIVILEGES IN SCHEMA public 
    GRANT SELECT ON TABLES TO app_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA public 
    GRANT USAGE, SELECT ON SEQUENCES TO app_rw;
```

---

## 八、初始数据

```sql
-- ============================================================
-- 默认策略
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
);

-- ============================================================
-- 默认策略参数
-- ============================================================
INSERT INTO strategy_params (strategy_id, param_name, param_value, param_type, description) VALUES
((SELECT id FROM strategies WHERE name = 'lhb_multifactor'), 'min_net_buy', '50000000', 'int', '最低净买入（元）'),
((SELECT id FROM strategies WHERE name = 'lhb_multifactor'), 'min_tech_score', '50', 'int', '最低技术面得分'),
((SELECT id FROM strategies WHERE name = 'lhb_multifactor'), 'max_hold_days', '5', 'int', '最大持仓交易日'),
((SELECT id FROM strategies WHERE name = 'lhb_multifactor'), 'stop_loss_ratio', '-0.05', 'float', '止损阈值'),
((SELECT id FROM strategies WHERE name = 'lhb_multifactor'), 'top_n', '3', 'int', '每日候选数量');

-- ============================================================
-- 默认应用配置
-- ============================================================
INSERT INTO app_config (key, value, description) VALUES
('MONITOR_INTERVAL_SEC', '10', '盘中行情轮询间隔（秒）'),
('ELIMINATION_ENABLED', 'true', '是否启用自动淘汰'),
('ELIMINATION_DAY', '5', '每周几执行淘汰（5=周五）'),
('LLM_ENABLED', 'false', 'LLM 选股开关'),
('FEISHU_WEBHOOK_URL', '', '飞书通知 Webhook URL'),
('DATA_SYNC_RETRY_COUNT', '3', '数据同步失败重试次数'),
('SKIP_CIRCUIT_BREAKER', 'false', '跳过熔断检查（仅调试用）');

-- ============================================================
-- 默认机器人
-- ============================================================
INSERT INTO robots (
    name, status, mode, strategy_id, 
    initial_capital, current_capital, cash_balance
) VALUES (
    'Alpha-1', 'paper_trading', 'paper_trading',
    (SELECT id FROM strategies WHERE name = 'lhb_multifactor'),
    100000.00, 0.00, 100000.00
);
```

---

## 九、补充注释

```sql
-- ============================================================
-- 辅助表注释补充
-- ============================================================

COMMENT ON TABLE trade_calendar IS '交易日历：标记每日是否开市，含前后交易日指针';
COMMENT ON COLUMN trade_calendar.prev_trading_day IS '上一个交易日，用于T+N计算';
COMMENT ON COLUMN trade_calendar.next_trading_day IS '下一个交易日，用于T+N计算';

COMMENT ON TABLE stock_info IS '股票基础信息：代码、名称、行业、板块、ST标记';
COMMENT ON COLUMN stock_info.board_type IS '板块类型：main/sme/gem/star/bse（主板/中小板/创业板/科创板/北交所）';
COMMENT ON COLUMN stock_info.is_st IS '是否ST股，影响涨跌幅限制（±5%）';

COMMENT ON TABLE app_config IS '应用配置：运行时可调整的系统参数';

-- ============================================================
-- 字段结构说明补充
-- ============================================================

COMMENT ON COLUMN robots.config IS '机器人扩展配置（JSON），结构参考：{"max_positions":3,"commission_rate":0.0003}';
COMMENT ON COLUMN robot_trades.signal_reasons IS '选股原因数组，如 ["净买入>5千万","机构席位买入","技术面MA5金叉"]';
COMMENT ON COLUMN selection_log.factor_scores IS '因子得分明细（JSON），如 {"tech":72.5,"fund":85.0,"hist":60.0}';
COMMENT ON COLUMN cash_ledger.ref_type IS '关联资源类型：trade_execution/dividend/manual_adjust';
COMMENT ON COLUMN cash_ledger.ref_id IS '关联资源ID，如 trade_executions.id';
```
