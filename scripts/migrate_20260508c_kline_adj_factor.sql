-- Migration: kline_daily 加复权相关列
-- Date: 2026-05-08
-- 手动 apply:
--   ssh jd
--   cd ~/stock_analysis
--   PGPASSWORD=$(grep POSTGRES_PASSWORD .env | cut -d= -f2) \
--     psql -h 127.0.0.1 -U lhb_admin -d lhb_quant \
--     -f scripts/migrate_20260508c_kline_adj_factor.sql

BEGIN;

ALTER TABLE kline_daily
    ADD COLUMN IF NOT EXISTS adj_factor NUMERIC,
    ADD COLUMN IF NOT EXISTS close_qfq  NUMERIC,
    ADD COLUMN IF NOT EXISTS close_hfq  NUMERIC;

COMMENT ON COLUMN kline_daily.adj_factor IS
    'Cumulative adjustment factor. close_hfq = close * adj_factor; close_qfq = close * adj_factor / latest_adj_factor_of_this_ts_code';
COMMENT ON COLUMN kline_daily.close_qfq IS 'Forward-adjusted close (前复权收盘价)';
COMMENT ON COLUMN kline_daily.close_hfq IS 'Backward-adjusted close (后复权收盘价)';

CREATE INDEX IF NOT EXISTS idx_kline_daily_tscode_date
    ON kline_daily(ts_code, trade_date DESC);

COMMIT;

-- 验证:
--   SELECT column_name FROM information_schema.columns
--    WHERE table_name='kline_daily' AND column_name IN ('adj_factor','close_qfq','close_hfq');
