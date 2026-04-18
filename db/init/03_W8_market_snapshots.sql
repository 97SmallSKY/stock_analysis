-- ============================================================
-- W8 实时 Ⅰ: 盘中 10s 快照 + 订阅池
-- ============================================================
--
-- 设计要点:
--   1. market_snapshots 按 ts 做 TimescaleDB hypertable (chunk 1 day)
--      保留策略由 sp_cleanup_old_data 处理 (REALTIME 保留 7 天)
--   2. 插入用 execute_values (每 10s ~3000 行 / 300 股票).
--   3. snapshot_type 支持 REALTIME (盘中) / CLOSE (收盘存档).
--      与既有 sp_cleanup_old_data 兼容.
--   4. subscription_pool 保存订阅码来源, 便于审计为什么跟踪这只股.
--
-- ------------------------------------------------------------
-- market_snapshots
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS market_snapshots (
    id              BIGSERIAL,
    ts_code         VARCHAR(12)     NOT NULL,
    ts              TIMESTAMPTZ     NOT NULL,                        -- 快照时间戳
    trade_date      DATE            NOT NULL,                        -- ts::date, 用于分区查询/清理
    snapshot_type   VARCHAR(16)     NOT NULL DEFAULT 'REALTIME',     -- REALTIME | CLOSE
    price           NUMERIC(12,4),
    pct_change      NUMERIC(10,4),                                   -- %
    change          NUMERIC(12,4),
    volume          BIGINT,                                          -- 累计成交量(手)
    amount          NUMERIC(20,4),                                   -- 累计成交额(元)
    open            NUMERIC(12,4),
    high            NUMERIC(12,4),
    low             NUMERIC(12,4),
    prev_close      NUMERIC(12,4),
    bid1            NUMERIC(12,4),
    bid1_vol        BIGINT,
    ask1            NUMERIC(12,4),
    ask1_vol        BIGINT,
    source          VARCHAR(16),                                     -- crawler/sina/akshare
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    PRIMARY KEY (ts, ts_code)                                        -- hypertable 必须含时间列
);

COMMENT ON TABLE  market_snapshots IS '盘中行情快照, 10s 粒度';
COMMENT ON COLUMN market_snapshots.snapshot_type IS 'REALTIME(盘中 10s), CLOSE(收盘归档, 永久保留)';
COMMENT ON COLUMN market_snapshots.trade_date  IS '= ts::date, 便于清理/日级聚合查询';

CREATE INDEX IF NOT EXISTS idx_market_snapshots_code_ts
    ON market_snapshots (ts_code, ts DESC);
CREATE INDEX IF NOT EXISTS idx_market_snapshots_date_code
    ON market_snapshots (trade_date, ts_code);

-- 转为 hypertable (chunk = 1 day). 若已经是 hypertable 会 no-op.
-- 未装 TimescaleDB 时静默忽略 (extension 不存在 -> 异常, 用 DO 捕获).
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'timescaledb') THEN
        PERFORM create_hypertable(
            'market_snapshots', 'ts',
            chunk_time_interval => INTERVAL '1 day',
            if_not_exists => TRUE,
            migrate_data => TRUE
        );
    END IF;
END $$;


-- ------------------------------------------------------------
-- subscription_pool
-- ------------------------------------------------------------
-- 盘中订阅池: 哪些 ts_code 需要定时抓快照.
--   reason: LHB_RECENT (近 T-20 上过龙虎榜) / ZT_TODAY (今日涨停池) / MANUAL
--   添加和移除都走 ingest/scheduler, 前端只读.
CREATE TABLE IF NOT EXISTS subscription_pool (
    ts_code         VARCHAR(12)     NOT NULL,
    reason          VARCHAR(32)     NOT NULL,
    added_at        TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    expires_at      TIMESTAMPTZ,                                     -- NULL=常驻, 到期自动清理
    meta            JSONB,                                           -- 任意标注
    PRIMARY KEY (ts_code, reason)
);

COMMENT ON TABLE  subscription_pool IS '盘中快照订阅池';
COMMENT ON COLUMN subscription_pool.reason IS 'LHB_RECENT / ZT_TODAY / MANUAL';
COMMENT ON COLUMN subscription_pool.expires_at IS '过期时间, NULL=常驻; sp_snapshot_cleanup_subs 清理';

CREATE INDEX IF NOT EXISTS idx_subpool_expires
    ON subscription_pool (expires_at);


-- ------------------------------------------------------------
-- 订阅池维护辅助 SQL
-- ------------------------------------------------------------
-- 清理已过期订阅 (ingest/scheduler 每 10 分钟调用)
CREATE OR REPLACE PROCEDURE sp_snapshot_cleanup_subs()
LANGUAGE plpgsql AS $$
BEGIN
    DELETE FROM subscription_pool
    WHERE expires_at IS NOT NULL
      AND expires_at < NOW();
END;
$$;

COMMENT ON PROCEDURE sp_snapshot_cleanup_subs IS 'W8: 清理到期订阅 (建议 10m 调一次)';
