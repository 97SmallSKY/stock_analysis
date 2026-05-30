-- 回测重构 DB 迁移 (2026-05-08)
-- 在 backtest_results / backtest_runs 新增字段，新建 backtest_param_sweeps 表

-- backtest_results 新增
ALTER TABLE backtest_results ADD COLUMN IF NOT EXISTS run_id VARCHAR(32);
ALTER TABLE backtest_results ADD COLUMN IF NOT EXISTS signal_meta JSONB;
ALTER TABLE backtest_results ADD COLUMN IF NOT EXISTS exit_component TEXT;
ALTER TABLE backtest_results ADD COLUMN IF NOT EXISTS benchmark_return NUMERIC;

-- backtest_runs 新增
ALTER TABLE backtest_runs ADD COLUMN IF NOT EXISTS config_json TEXT;
ALTER TABLE backtest_runs ADD COLUMN IF NOT EXISTS benchmark_code VARCHAR(16);
ALTER TABLE backtest_runs ADD COLUMN IF NOT EXISTS universe TEXT;

-- 新表：参数敏感度扫描
CREATE TABLE IF NOT EXISTS backtest_param_sweeps (
    sweep_id    TEXT PRIMARY KEY,
    run_id      TEXT,
    param_x     TEXT,
    param_y     TEXT,
    x_value     NUMERIC,
    y_value     NUMERIC,
    avg_return  NUMERIC,
    win_rate    NUMERIC,
    trade_count INT,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 索引
CREATE INDEX IF NOT EXISTS idx_bt_results_run_id ON backtest_results(run_id);
CREATE INDEX IF NOT EXISTS idx_sweeps_run ON backtest_param_sweeps(run_id);
