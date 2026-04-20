-- ============================================================
-- STO-31 Phase 1:数据源状态字段 + 北交所(.BJ) 止损标记
-- ============================================================
-- 背景:北交所 920xxx 股票在 akshare/sina/crawler 三家数据源都无法拉取,
-- 每次 daily ingest 都产生 ~190 条 ERROR,淹没真正 bug。
--
-- 本次迁移:
-- 1. `stock_info` 表新增 `data_source_status` 字段,枚举 ok / bse_unavailable / delisted
-- 2. 把所有 .BJ 结尾的股票(= 北交所)标记为 bse_unavailable
-- 3. 不动 is_active(保留给真正退市用)
--
-- 执行语义:idempotent(可重复 apply 不出错)
-- 回滚:ALTER TABLE stock_info DROP COLUMN data_source_status;
-- ============================================================

-- 1) 新增字段(IF NOT EXISTS,幂等)
ALTER TABLE stock_info
    ADD COLUMN IF NOT EXISTS data_source_status VARCHAR(20) DEFAULT 'ok';

COMMENT ON COLUMN stock_info.data_source_status IS
    'STO-31: 数据源可用性。ok=正常, bse_unavailable=北交所数据源不支持, delisted=退市(预留)';

-- 2) 建索引(ingest 查询会按此过滤)
CREATE INDEX IF NOT EXISTS idx_stock_info_data_source_status
    ON stock_info(data_source_status);

-- 3) 回填:所有 920xxx 北交所股票 → bse_unavailable
--    说明:DB 实际数据里北交所股票 ts_code 后缀为 .SH(可能是 akshare
--    导入时后缀规则不同),但 symbol 一定以 920 开头。所以按 symbol
--    前缀过滤更可靠,不依赖后缀。
--    幂等:只更新当前状态为 'ok' 的记录,避免覆盖已人工标记的
UPDATE stock_info
    SET data_source_status = 'bse_unavailable',
        updated_at = NOW()
    WHERE symbol LIKE '920%'
      AND data_source_status = 'ok';

-- 4) 统计提示(执行时 psql 会打印影响行数)
--    预期:~307 行(当前北交所 920xxx 股票数)
