-- ══════════════════════════════════════════════════════════════════════════════
-- 数据库迁移脚本：修复三大问题
-- 执行时间：2026-04-14
-- 说明：
--   1. 问题3：添加 sim_selection_log.lianban_score 字段
--   2. 问题2：创建 sim_robots 表 + sim_trades/sim_summary 添加 robot_id
-- ══════════════════════════════════════════════════════════════════════════════

-- ── 问题3：sim_selection_log 添加缺失字段 ─────────────────────────────────
ALTER TABLE sim_selection_log 
ADD COLUMN IF NOT EXISTS lianban_score FLOAT DEFAULT 0.0;

-- 验证
DO $$ 
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name='sim_selection_log' AND column_name='lianban_score'
    ) THEN
        RAISE NOTICE '✅ sim_selection_log.lianban_score 字段已添加';
    ELSE
        RAISE EXCEPTION '❌ 字段添加失败';
    END IF;
END $$;

-- ── 问题2：多机器人完全隔离架构 ─────────────────────────────────────────

-- 创建机器人账户表
CREATE TABLE IF NOT EXISTS sim_robots (
    robot_id       VARCHAR(32) PRIMARY KEY,
    robot_name     VARCHAR(64) NOT NULL,
    strategy_key   VARCHAR(32) NOT NULL,  -- FK → sim_strategy
    initial_capital FLOAT NOT NULL DEFAULT 100000.0,
    status         VARCHAR(16) DEFAULT 'active',  -- active / paused / archived
    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    description    TEXT
);

-- 插入默认机器人（兼容历史数据）
INSERT INTO sim_robots (robot_id, robot_name, strategy_key, initial_capital, status, description)
VALUES ('default', '默认策略机器人', 'default', 100000.0, 'active', '默认主板龙虎榜策略')
ON CONFLICT (robot_id) DO NOTHING;

-- sim_trades 添加 robot_id 字段
ALTER TABLE sim_trades 
ADD COLUMN IF NOT EXISTS robot_id VARCHAR(32) DEFAULT 'default';

-- 为历史数据设置默认值
UPDATE sim_trades SET robot_id = 'default' WHERE robot_id IS NULL;

-- sim_summary 添加 robot_id 字段
ALTER TABLE sim_summary 
ADD COLUMN IF NOT EXISTS robot_id VARCHAR(32) DEFAULT 'default';

-- 为历史数据设置默认值
UPDATE sim_summary SET robot_id = 'default' WHERE robot_id IS NULL;

-- 添加索引提升查询性能
CREATE INDEX IF NOT EXISTS idx_sim_trades_robot ON sim_trades(robot_id);
CREATE INDEX IF NOT EXISTS idx_sim_summary_robot ON sim_summary(robot_id);

-- 验证
DO $$ 
BEGIN
    IF EXISTS (SELECT 1 FROM sim_robots WHERE robot_id='default') THEN
        RAISE NOTICE '✅ 默认机器人已创建';
    ELSE
        RAISE EXCEPTION '❌ 默认机器人创建失败';
    END IF;
    
    IF EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name='sim_trades' AND column_name='robot_id'
    ) THEN
        RAISE NOTICE '✅ sim_trades.robot_id 字段已添加';
    ELSE
        RAISE EXCEPTION '❌ sim_trades.robot_id 字段添加失败';
    END IF;
END $$;

-- ══════════════════════════════════════════════════════════════════════════════
-- 完成提示
-- ══════════════════════════════════════════════════════════════════════════════
-- 执行后请：
-- 1. 重启 Phase A / Phase B 机器人
-- 2. 验证 sim_selection_log 新记录是否正常写入
-- 3. 测试新建机器人账户（通过前端 UI 或 API）
