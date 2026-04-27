-- STO-88: 回测任务持久化(append-only audit log)
-- 动机:现有 backtest.py 用 in-memory TaskStore,服务重启丢任务历史。
-- backtest_tasks/results 表 schema 复杂(FK strategies + BIGSERIAL),
-- 不适合当前轻量 uuid hex task_id,另建一张轻量表做持久化。

CREATE TABLE IF NOT EXISTS backtest_runs (
    task_id        VARCHAR(32)  PRIMARY KEY,
    strategy_name  VARCHAR(64)  NOT NULL,
    status         VARCHAR(16)  NOT NULL,              -- PENDING/RUNNING/SUCCESS/FAILED
    config         JSONB        NOT NULL DEFAULT '{}'::jsonb,
    result_json    JSONB        DEFAULT NULL,
    error_message  TEXT         DEFAULT NULL,
    created_at     TIMESTAMP    NOT NULL DEFAULT NOW(),
    finished_at    TIMESTAMP    DEFAULT NULL
);

COMMENT ON TABLE backtest_runs IS 'STO-88 回测任务 append-only 持久化';

CREATE INDEX IF NOT EXISTS idx_backtest_runs_created
    ON backtest_runs (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_backtest_runs_strategy_created
    ON backtest_runs (strategy_name, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_backtest_runs_status
    ON backtest_runs (status);
