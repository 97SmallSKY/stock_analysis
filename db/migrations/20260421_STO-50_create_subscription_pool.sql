-- ============================================================
-- STO-50: 补建 subscription_pool 表 + 清理存储过程
-- ============================================================
-- 背景:
--   `db/init/03_W8_market_snapshots.sql` 定义了 subscription_pool 表,但
--   线上 DB `\dt` 显示**该表未被创建**(只有 market_snapshots)。推测是
--   init 文件分批合并时,subscription_pool 段加入晚于首次 init,jd 未
--   replay。
--
-- 现象:
--   `journalctl -u stock_analysis_api` 每 10 分钟刷一次:
--     WARNING [backend.core.runtime] [runtime] cleanup failed:
--     relation "subscription_pool" does not exist
--
-- 影响:
--   - 业务无破(cleanup 异常被 catch,scheduler 继续跑)
--   - 日志噪音(WARN 每 10min 一次,掩盖真·异常)
--   - 订阅池功能失效(LHB_RECENT / ZT_TODAY / MANUAL 三类永远空)
--
-- 修复:本一次性 migration 补齐表 + 索引 + 存储过程,与 init 文件完全一致。
--
-- 执行语义:idempotent(IF NOT EXISTS / OR REPLACE,可重复)
-- 部署步骤:
--   psql -h <host> -U <user> -d lhb_quant -f db/migrations/20260421_STO-50_create_subscription_pool.sql
--
-- 验证:
--   SELECT tablename FROM pg_tables WHERE tablename='subscription_pool';   -- 应返回 1 行
--   SELECT proname FROM pg_proc WHERE proname='sp_snapshot_cleanup_subs';  -- 应返回 1 行
--   部署后观察 30 分钟 `journalctl -u stock_analysis_api | grep cleanup failed` 应无新增
-- ============================================================

-- 1) 订阅池主表(定义与 db/init/03_W8_market_snapshots.sql 完全一致)
CREATE TABLE IF NOT EXISTS subscription_pool (
    ts_code         VARCHAR(12)     NOT NULL,
    reason          VARCHAR(32)     NOT NULL,
    added_at        TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    expires_at      TIMESTAMPTZ,                                     -- NULL=常驻, 到期自动清理
    meta            JSONB,                                           -- 任意标注
    PRIMARY KEY (ts_code, reason)
);

COMMENT ON TABLE  subscription_pool IS '盘中快照订阅池';
COMMENT ON COLUMN subscription_pool.reason IS 'LHB_RECENT / ZT_TODAY / MANUAL';
COMMENT ON COLUMN subscription_pool.expires_at IS '过期时间, NULL=常驻; sp_snapshot_cleanup_subs 清理';

-- 2) 按 expires_at 过滤的索引(cleanup 每 10 分钟扫)
CREATE INDEX IF NOT EXISTS idx_subpool_expires
    ON subscription_pool (expires_at);

-- 3) 清理过期订阅的存储过程(scheduler 每 10 分钟调)
CREATE OR REPLACE PROCEDURE sp_snapshot_cleanup_subs()
LANGUAGE plpgsql AS $$
BEGIN
    DELETE FROM subscription_pool
    WHERE expires_at IS NOT NULL
      AND expires_at < NOW();
END;
$$;

COMMENT ON PROCEDURE sp_snapshot_cleanup_subs IS 'W8: 清理到期订阅 (建议 10m 调一次)';
