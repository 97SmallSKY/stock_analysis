-- STO-9 · 数据采集告警落盘
-- 用于熔断器触发 / 批任务失败 / 永久性采集错误的结构化告警记录
-- 告警消费方:先落表,后续可由 Ops 接 webhook / 邮件轮询

CREATE TABLE IF NOT EXISTS alert_log (
    id              BIGSERIAL PRIMARY KEY,
    created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    level           VARCHAR(16) NOT NULL,        -- INFO / WARN / ERROR / CRITICAL
    category        VARCHAR(32) NOT NULL,        -- NETWORK / API / BATCH_JOB / DATA_QUALITY
    source          VARCHAR(64) NOT NULL,        -- 触发者标识 (如 adapter 名 / job 名)
    message         TEXT NOT NULL,
    details         JSONB,                       -- 额外结构化上下文 (失败次数 / 日期 / error 类型)
    acknowledged    BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS idx_alert_log_created_at ON alert_log(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_alert_log_level ON alert_log(level);
CREATE INDEX IF NOT EXISTS idx_alert_log_ack_created ON alert_log(acknowledged, created_at DESC);

COMMENT ON TABLE alert_log IS 'STO-9 告警中心: 熔断/批任务失败/永久性错误的结构化落盘';
