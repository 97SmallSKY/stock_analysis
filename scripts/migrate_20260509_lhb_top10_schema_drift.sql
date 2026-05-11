-- Migration: 修复 lhb_top10 schema drift
-- Date: 2026-05-09
--
-- 背景:modules/data_fetcher.py:fetch_lhb_netbuy_by_date 写入 22 列宽 schema,
-- 但线上 lhb_top10 只有 12 列,to_sql 抛 UndefinedColumn 被静默吞掉,
-- 导致 daily_sync.sh step1 长期默认失败,5-08 起 lhb_top10 无新数据。
--
-- 同时 step11_refresh_lhb_returns 报 column "上榜后1日" does not exist,
-- 也是因为缺这几列。
--
-- 手动 apply:
--   ssh jd "cd ~/stock_analysis && PGPASSWORD=$(grep POSTGRES_PASSWORD .env | cut -d= -f2) \
--     psql -h 127.0.0.1 -U lhb_admin -d lhb_quant -f scripts/migrate_20260509_lhb_top10_schema_drift.sql"

BEGIN;

ALTER TABLE lhb_top10
    ADD COLUMN IF NOT EXISTS "序号"               BIGINT,
    ADD COLUMN IF NOT EXISTS "上榜日"             DATE,
    ADD COLUMN IF NOT EXISTS "解读"               TEXT,
    ADD COLUMN IF NOT EXISTS "龙虎榜成交额"       DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS "市场总成交额"       DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS "净买额占总成交比"   DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS "成交额占总成交比"   DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS "流通市值"           DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS "上榜后1日"          DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS "上榜后2日"          DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS "上榜后5日"          DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS "上榜后10日"         DOUBLE PRECISION;

COMMIT;

-- 验证:
--   SELECT column_name, data_type FROM information_schema.columns
--    WHERE table_name='lhb_top10' ORDER BY ordinal_position;
--   预期 23 列(原 12 + 新 11)。
