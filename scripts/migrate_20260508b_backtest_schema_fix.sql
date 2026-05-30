-- 回测重构 DB 迁移补丁 (2026-05-08 第二轮)
-- 修复 backtest_results / backtest_runs 缺失字段导致 storage.write 静默失败问题
--
-- 部署方式（CLAUDE.md 规范：deploy.sh 不跑 migration，必须 Ops 手动 apply）:
--   ssh jd "psql -h 117.72.184.8 -U <user> -d lhb_quant -f scripts/migrate_20260508b_backtest_schema_fix.sql"
--
-- 与 migrate_20260508_backtest_refactor.sql 互斥追加，可重复执行（IF NOT EXISTS）

-- backtest_results 补充列（代码 engine._save_results / pages/backtest.py::_save_section 写入）
ALTER TABLE backtest_results ADD COLUMN IF NOT EXISTS run_type      VARCHAR(32);
ALTER TABLE backtest_results ADD COLUMN IF NOT EXISTS strategy_name VARCHAR(64);
ALTER TABLE backtest_results ADD COLUMN IF NOT EXISTS created_at    TIMESTAMP;

-- backtest_runs 补充列
ALTER TABLE backtest_runs ADD COLUMN IF NOT EXISTS run_id      VARCHAR(32);
ALTER TABLE backtest_runs ADD COLUMN IF NOT EXISTS start_date  VARCHAR(16);
ALTER TABLE backtest_runs ADD COLUMN IF NOT EXISTS end_date    VARCHAR(16);
ALTER TABLE backtest_runs ADD COLUMN IF NOT EXISTS trade_count INTEGER;

-- 索引
CREATE INDEX IF NOT EXISTS idx_bt_runs_run_id        ON backtest_runs(run_id);
CREATE INDEX IF NOT EXISTS idx_bt_runs_strategy_name ON backtest_runs(strategy_name);
CREATE INDEX IF NOT EXISTS idx_bt_results_strategy   ON backtest_results(strategy_name);
