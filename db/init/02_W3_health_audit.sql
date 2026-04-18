-- ============================================================
-- W3 数据网关 II: HealthChecker + 审计
-- ============================================================

-- ------------------------------------------------------------
-- 适配器健康轮询历史
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS adapter_health (
    id              BIGSERIAL    PRIMARY KEY,
    adapter_name    VARCHAR(32)  NOT NULL,                              -- tushare/akshare/sina/crawler
    is_healthy      BOOLEAN      NOT NULL,                              -- health_check() 返回值
    latency_ms      INTEGER,                                            -- 探测耗时
    error_msg       TEXT,                                               -- 失败原因(可空)
    cb_state        VARCHAR(16),                                        -- CLOSED/OPEN/HALF_OPEN
    cb_failures     INTEGER,                                            -- 当前 CB 内累计失败
    checked_at      TIMESTAMP    DEFAULT NOW()
);

COMMENT ON TABLE  adapter_health IS '适配器健康检查历史(每 5 分钟一行)';
COMMENT ON COLUMN adapter_health.cb_state IS 'CircuitBreaker 状态';

CREATE INDEX IF NOT EXISTS idx_adapter_health_name_time
    ON adapter_health(adapter_name, checked_at DESC);
CREATE INDEX IF NOT EXISTS idx_adapter_health_time
    ON adapter_health(checked_at DESC);


-- ------------------------------------------------------------
-- Router 每次 fetch 的审计日志
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS data_fetch_log (
    id              BIGSERIAL    PRIMARY KEY,
    category        VARCHAR(32)  NOT NULL,                              -- DAILY_KLINE / LHB_DETAIL ...
    method_name     VARCHAR(64)  NOT NULL,                              -- get_daily_kline ...
    adapter_name    VARCHAR(32)  NOT NULL,                              -- 实际命中的 adapter
    latency_ms      INTEGER      NOT NULL,                              -- 耗时
    ok              BOOLEAN      NOT NULL,                              -- 是否拿到非空 DataFrame
    rows            INTEGER,                                            -- 返回行数(成功时)
    error_msg       TEXT,                                               -- 失败原因(可空)
    fetched_at      TIMESTAMP    DEFAULT NOW()
);

COMMENT ON TABLE data_fetch_log IS 'Router 每次 fetch 的审计';

CREATE INDEX IF NOT EXISTS idx_fetch_log_time         ON data_fetch_log(fetched_at DESC);
CREATE INDEX IF NOT EXISTS idx_fetch_log_adapter_time ON data_fetch_log(adapter_name, fetched_at DESC);
CREATE INDEX IF NOT EXISTS idx_fetch_log_category     ON data_fetch_log(category, fetched_at DESC);
