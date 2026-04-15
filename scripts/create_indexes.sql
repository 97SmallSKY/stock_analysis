-- ============================================================
-- stock_analysis 数据库索引创建脚本
-- 用于优化查询性能
-- ============================================================
-- 使用方法：
--   docker exec -i stock_analysis-db psql -U stock_user -d stock_analysis < scripts/create_indexes.sql
-- ============================================================

-- lhb_top10 表索引
CREATE INDEX IF NOT EXISTS idx_lhb_top10_trade ON lhb_top10(trade_date);
CREATE INDEX IF NOT EXISTS idx_lhb_top10_code ON lhb_top10(代码);

-- lhb_detail 表索引
CREATE INDEX IF NOT EXISTS idx_lhb_detail_trade ON lhb_detail(trade_date);
CREATE INDEX IF NOT EXISTS idx_lhb_detail_code ON lhb_detail(代码);

-- lhb_seat_detail 表索引
CREATE INDEX IF NOT EXISTS idx_lhb_seat_trade ON lhb_seat_detail(trade_date);
CREATE INDEX IF NOT EXISTS idx_lhb_seat_code ON lhb_seat_detail(代码);

-- lhb_jigou 表索引
CREATE INDEX IF NOT EXISTS idx_lhb_jigou_fetch ON lhb_jigou(fetch_date);

-- lhb_ggtj 表索引
CREATE INDEX IF NOT EXISTS idx_lhb_ggtj_fetch ON lhb_ggtj(fetch_date);

-- lhb_top10_prices 表索引
CREATE INDEX IF NOT EXISTS idx_lhb_prices_code ON lhb_top10_prices(代码);
CREATE INDEX IF NOT EXISTS idx_lhb_prices_trade ON lhb_top10_prices(trade_date);

-- sim_trades 表索引
CREATE INDEX IF NOT EXISTS idx_sim_trades_status ON sim_trades(status);
CREATE INDEX IF NOT EXISTS idx_sim_trades_trade ON sim_trades(trade_date);

-- ============================================================
-- 索引创建完成
-- ============================================================
