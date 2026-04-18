-- ============================================================
-- W11 优化: 压缩 + 覆盖索引
-- ============================================================
--
-- 说明:
--   * TimescaleDB compression 需 >= 2.0. 未装则条件跳过.
--   * 关键索引用 CREATE INDEX IF NOT EXISTS 幂等; 生产请在低峰期跑.
--     (psql 下可改成 CREATE INDEX CONCURRENTLY, 脚本无事务包装即可)
--
--   压缩策略 (以 ts_code 段分区, orderby 时间倒序 — 典型时序查询都是
--   最近 N 天, 解压命中率高):
--       kline_daily        尚未 hypertable 化 (当前分区表), 跳过 compress
--       market_snapshots   已是 hypertable (W8-2), 开启压缩, 3 天前自动压
--

-- ------------------------------------------------------------
-- market_snapshots 压缩
-- ------------------------------------------------------------
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'timescaledb') THEN
        BEGIN
            EXECUTE $sql$
                ALTER TABLE market_snapshots SET (
                    timescaledb.compress,
                    timescaledb.compress_segmentby = 'ts_code',
                    timescaledb.compress_orderby = 'ts DESC'
                )
            $sql$;
        EXCEPTION WHEN others THEN
            -- 某些版本已设置时会抛 duplicate setting, 幂等吃掉
            RAISE NOTICE 'market_snapshots compression already configured: %', SQLERRM;
        END;

        -- 3 天前的 chunk 自动压缩 (新采样仍 insert 到最新 chunk)
        BEGIN
            PERFORM add_compression_policy('market_snapshots', INTERVAL '3 days', if_not_exists => TRUE);
        EXCEPTION WHEN others THEN
            RAISE NOTICE 'add_compression_policy skipped: %', SQLERRM;
        END;
    END IF;
END $$;


-- ------------------------------------------------------------
-- 覆盖索引 (关键查询)
-- ------------------------------------------------------------

-- /api/lhb 分页查询: WHERE trade_date BETWEEN ... AND net_amount / score / pct_change 过滤 + ORDER BY
--   lhb_stock_daily 已有主键 + 日期索引, 补一个 (trade_date, score DESC) 用于 Dashboard top_lhb
CREATE INDEX IF NOT EXISTS idx_lhb_daily_date_score
    ON lhb_stock_daily (trade_date, score DESC NULLS LAST);

CREATE INDEX IF NOT EXISTS idx_lhb_daily_date_net
    ON lhb_stock_daily (trade_date, net_amount DESC NULLS LAST);

-- /api/lhb/seats 按 (trade_date, ts_code, direction) 精确查
CREATE INDEX IF NOT EXISTS idx_lhb_seats_date_code_dir
    ON lhb_seats (trade_date, ts_code, direction);

-- market_sentiment 取最新一行
CREATE INDEX IF NOT EXISTS idx_market_sentiment_date_desc
    ON market_sentiment (trade_date DESC);

-- backtest_tasks 周报 top 10 (annual_return desc, finished_at desc)
CREATE INDEX IF NOT EXISTS idx_backtest_tasks_status_finished
    ON backtest_tasks (status, finished_at DESC)
    WHERE status = 'SUCCESS';

-- cash_ledger 按 robot_id + biz_date
CREATE INDEX IF NOT EXISTS idx_cash_ledger_robot_date
    ON cash_ledger (robot_id, biz_date DESC);

-- fetch_audit Router 审计查询 (adapter_name, ts)
CREATE INDEX IF NOT EXISTS idx_data_fetch_log_name_ts
    ON data_fetch_log (adapter_name, fetched_at DESC);

-- market_snapshots 价格时间查询 (已存在类似索引, 保底)
CREATE INDEX IF NOT EXISTS idx_market_snapshots_code_ts_desc
    ON market_snapshots (ts_code, ts DESC);
