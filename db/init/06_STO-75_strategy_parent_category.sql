-- STO-75: strategies 表加 parent_category 大类列(拆自 STO-48 设计文档 §3.2)
-- 两级分类:parent_category(3 大类) + category(原字段,子类 LHB_HOT_MONEY 等)
-- 10 大类里聚焦 3 类:EVENT_DRIVEN / TREND_FOLLOW / MULTI_FACTOR(详见 docs/design/strategy-taxonomy.md)

ALTER TABLE strategies
  ADD COLUMN IF NOT EXISTS parent_category VARCHAR(32) DEFAULT NULL;

-- 部分索引(只索引非 NULL 值,小表开销忽略):按大类 + 活跃过滤,覆盖前端导航
CREATE INDEX IF NOT EXISTS idx_strategies_parent_active
  ON strategies(parent_category, is_active)
  WHERE parent_category IS NOT NULL;

-- 回填:现有 category='LHB' 归「事件驱动」大类(现有 lhb_hot_money / lhb_institution 都是)
UPDATE strategies
  SET parent_category = 'EVENT_DRIVEN'
  WHERE category = 'LHB' AND parent_category IS NULL;

-- 注释
COMMENT ON COLUMN strategies.parent_category IS
  '大类(STO-75):EVENT_DRIVEN/TREND_FOLLOW/MULTI_FACTOR;NULL=未归类。详见 docs/design/strategy-taxonomy.md';
