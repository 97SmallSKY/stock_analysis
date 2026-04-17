
-- ============================================================
-- ============================================================
-- 第五部分: 视图
-- ============================================================
-- ============================================================


-- ============================================================
-- 5.1 机器人持仓视图(实时计算)
-- ============================================================
CREATE OR REPLACE VIEW v_robot_positions AS
WITH buy_trades AS (
    -- 找到所有未完全卖出的买入记录
    SELECT
        bt.robot_id,
        bt.ts_code,
        bt.name,
        bt.shares AS buy_shares,
        bt.price AS cost_price,
        bt.amount AS cost_amount,
        bt.signal_score,
        bt.trade_date AS open_date,
        bt.id AS buy_trade_id,
        bt.lhb_daily_id,
        COALESCE(
            (SELECT SUM(st.shares)
             FROM robot_trades st
             WHERE st.paired_trade_id = bt.id AND st.direction = 'SELL'),
            0
        ) AS sold_shares
    FROM robot_trades bt
    WHERE bt.direction = 'BUY'
),
open_positions AS (
    SELECT *,
        (buy_shares - sold_shares) AS remaining_shares
    FROM buy_trades
    WHERE (buy_shares - sold_shares) > 0
)
SELECT
    op.robot_id,
    op.ts_code,
    op.name,
    op.remaining_shares AS shares,
    op.cost_price,
    op.cost_amount,
    op.signal_score,
    op.open_date,
    op.buy_trade_id,
    op.lhb_daily_id,
    -- 持仓天数(需要在应用层用当前交易日计算)
    (CURRENT_DATE - op.open_date) AS hold_calendar_days,
    -- 注意: current_price 需在应用层通过行情API填充
    r.status AS robot_status
FROM open_positions op
JOIN robots r ON r.id = op.robot_id;
COMMENT ON VIEW v_robot_positions IS '机器人当前持仓视图，基于买卖配对计算剩余股数。current_price需应用层填充';


-- ============================================================
-- 5.2 机器人概览视图
-- ============================================================
CREATE OR REPLACE VIEW v_robot_overview AS
SELECT
    r.id,
    r.name,
    r.status,
    r.initial_capital,
    r.current_capital,
    r.cash,
    r.total_assets,
    r.total_pnl,
    r.total_pnl_pct,
    r.total_trades,
    r.win_count,
    r.lose_count,
    CASE
        WHEN r.total_trades > 0
        THEN ROUND(r.win_count::DECIMAL / r.total_trades, 4)
        ELSE 0
    END AS win_rate,
    r.max_drawdown,
    r.ahs_score,

    -- 策略信息
    s.name AS strategy_name,
    s.category AS strategy_category,

    -- 持仓统计
    (SELECT COUNT(*) FROM v_robot_positions vp WHERE vp.robot_id = r.id) AS position_count,
    r.max_positions,

    -- 最近净值
    (SELECT net_value FROM daily_nav dn WHERE dn.robot_id = r.id ORDER BY trade_date DESC LIMIT 1) AS latest_nav,
    (SELECT pnl_daily_pct FROM daily_nav dn WHERE dn.robot_id = r.id ORDER BY trade_date DESC LIMIT 1) AS latest_daily_pnl_pct,

    -- 最近交易
    r.last_trade_date,
    r.started_at,
    r.created_at,
    r.updated_at

FROM robots r
LEFT JOIN strategies s ON s.id = r.strategy_id;

COMMENT ON VIEW v_robot_overview IS '机器人概览视图，汇总机器人状态/持仓/净值/策略等关键信息';


-- ============================================================
-- 5.3 龙虎榜信号视图(决策辅助)
-- ============================================================
CREATE OR REPLACE VIEW v_lhb_signals AS
SELECT
    lsd.id,
    lsd.ts_code,
    lsd.trade_date,
    lsd.name,
    lsd.close_price,
    lsd.pct_change,
    lsd.turnover_rate,
    lsd.amount,
    lsd.l_buy,
    lsd.l_sell,
    lsd.l_net,
    lsd.reason,
    lsd.reason_type,
    lsd.industry,
    lsd.market_cap,
    lsd.float_market_cap,

    -- 评分
    lsd.score,
    lsd.score_tech,
    lsd.score_fund,
    lsd.score_hist,
    lsd.score_detail,

    -- 席位汇总
    (SELECT COUNT(*) FROM lhb_seats ls
     WHERE ls.stock_daily_id = lsd.id AND ls.direction = 'BUY') AS buy_seat_count,
    (SELECT COUNT(*) FROM lhb_seats ls
     WHERE ls.stock_daily_id = lsd.id AND ls.direction = 'SELL') AS sell_seat_count,
    (SELECT COUNT(*) FROM lhb_seats ls
     WHERE ls.stock_daily_id = lsd.id AND ls.is_institution = TRUE) AS inst_seat_count,

    -- 机构净买入
    (SELECT COALESCE(SUM(ls.net_amount), 0) FROM lhb_seats ls
     WHERE ls.stock_daily_id = lsd.id AND ls.is_institution = TRUE) AS inst_net_amount,

    -- T+N收益(关联查询)
    tn1.pnl_pct AS pnl_1d,
    tn2.pnl_pct AS pnl_2d,
    tn3.pnl_pct AS pnl_3d,
    tn5.pnl_pct AS pnl_5d,
    tn10.pnl_pct AS pnl_10d,

    -- 历史统计
    agg.lhb_count AS hist_lhb_count,
    agg.win_rate_1d AS hist_win_rate_1d,
    agg.avg_pnl_1d AS hist_avg_pnl_1d,

    -- 市场情绪
    ms.sentiment_score AS market_sentiment,
    ms.sentiment_level AS market_level

FROM lhb_stock_daily lsd
LEFT JOIN lhb_tn_price tn1  ON tn1.stock_daily_id = lsd.id AND tn1.n_day = 1
LEFT JOIN lhb_tn_price tn2  ON tn2.stock_daily_id = lsd.id AND tn2.n_day = 2
LEFT JOIN lhb_tn_price tn3  ON tn3.stock_daily_id = lsd.id AND tn3.n_day = 3
LEFT JOIN lhb_tn_price tn5  ON tn5.stock_daily_id = lsd.id AND tn5.n_day = 5
LEFT JOIN lhb_tn_price tn10 ON tn10.stock_daily_id = lsd.id AND tn10.n_day = 10
LEFT JOIN lhb_agg_stats agg ON agg.stat_type = 'STOCK' AND agg.stat_key = lsd.ts_code AND agg.stat_period = '90D'
LEFT JOIN market_sentiment ms ON ms.trade_date = lsd.trade_date;

COMMENT ON VIEW v_lhb_signals IS '龙虎榜信号综合视图，包含评分/席位/T+N收益/历史统计/市场情绪';


-- ============================================================
-- 5.4 营业部画像视图
-- ============================================================
CREATE OR REPLACE VIEW v_broker_profile AS
SELECT
    agg.stat_key AS broker_name,
    agg.lhb_count,
    agg.buy_count,
    agg.sell_count,
    agg.total_buy_amount,
    agg.total_sell_amount,
    agg.total_net_amount,
    agg.win_rate_1d,
    agg.win_rate_3d,
    agg.win_rate_5d,
    agg.avg_pnl_1d,
    agg.avg_pnl_3d,
    agg.avg_pnl_5d,
    agg.max_pnl,
    agg.min_pnl,
    agg.top_industries,
    agg.top_stocks,
    agg.style_tag,
    agg.active_rank,
    agg.stat_period,
    agg.updated_at,

    -- 最近操作(最近5条)
    (SELECT jsonb_agg(recent ORDER BY recent_date DESC)
     FROM (
         SELECT jsonb_build_object(
             'ts_code', ls.ts_code,
             'trade_date', ls.trade_date,
             'direction', ls.direction,
             'net_amount', ls.net_amount
         ) AS recent,
         ls.trade_date AS recent_date
         FROM lhb_seats ls
         WHERE ls.broker_name = agg.stat_key
         ORDER BY ls.trade_date DESC
         LIMIT 5
     ) sub
    ) AS recent_trades

FROM lhb_agg_stats agg
WHERE agg.stat_type = 'BROKER';

COMMENT ON VIEW v_broker_profile IS '营业部画像视图，展示营业部的历史战绩和操作风格';


-- ============================================================
-- 5.5 每日盈亏分析视图
-- ============================================================
CREATE OR REPLACE VIEW v_daily_pnl_analysis AS
SELECT
    lsd.trade_date,
    COUNT(*) AS stock_count,

    -- 评分分布
    ROUND(AVG(lsd.score), 2) AS avg_score,
    ROUND(MIN(lsd.score), 2) AS min_score,
    ROUND(MAX(lsd.score), 2) AS max_score,
    COUNT(*) FILTER (WHERE lsd.score >= 80) AS high_score_count,
    COUNT(*) FILTER (WHERE lsd.score >= 60 AND lsd.score < 80) AS mid_score_count,
    COUNT(*) FILTER (WHERE lsd.score < 60) AS low_score_count,

    -- T+1收益
    ROUND(AVG(tn1.pnl_pct), 4) AS avg_pnl_1d,
    COUNT(*) FILTER (WHERE tn1.pnl_pct > 0) AS win_count_1d,
    COUNT(*) FILTER (WHERE tn1.pnl_pct <= 0) AS lose_count_1d,
    CASE
        WHEN COUNT(*) FILTER (WHERE tn1.pnl_pct IS NOT NULL) > 0
        THEN ROUND(
            COUNT(*) FILTER (WHERE tn1.pnl_pct > 0)::DECIMAL /
            COUNT(*) FILTER (WHERE tn1.pnl_pct IS NOT NULL), 4
        )
        ELSE NULL
    END AS win_rate_1d,

    -- T+3收益
    ROUND(AVG(tn3.pnl_pct), 4) AS avg_pnl_3d,
    CASE
        WHEN COUNT(*) FILTER (WHERE tn3.pnl_pct IS NOT NULL) > 0
        THEN ROUND(
            COUNT(*) FILTER (WHERE tn3.pnl_pct > 0)::DECIMAL /
            COUNT(*) FILTER (WHERE tn3.pnl_pct IS NOT NULL), 4
        )
        ELSE NULL
    END AS win_rate_3d,

    -- T+5收益
    ROUND(AVG(tn5.pnl_pct), 4) AS avg_pnl_5d,

    -- 龙虎榜资金
    ROUND(SUM(lsd.l_net), 2) AS total_net,
    ROUND(AVG(lsd.l_net), 2) AS avg_net,

    -- 市场情绪
    ms.sentiment_score,
    ms.sentiment_level

FROM lhb_stock_daily lsd
LEFT JOIN lhb_tn_price tn1 ON tn1.stock_daily_id = lsd.id AND tn1.n_day = 1
LEFT JOIN lhb_tn_price tn3 ON tn3.stock_daily_id = lsd.id AND tn3.n_day = 3
LEFT JOIN lhb_tn_price tn5 ON tn5.stock_daily_id = lsd.id AND tn5.n_day = 5
LEFT JOIN market_sentiment ms ON ms.trade_date = lsd.trade_date
GROUP BY lsd.trade_date, ms.sentiment_score, ms.sentiment_level
ORDER BY lsd.trade_date DESC;

COMMENT ON VIEW v_daily_pnl_analysis IS '每日龙虎榜盈亏分析视图，按日聚合评分/胜率/收益/资金';


-- ============================================================
-- ============================================================
-- 第六部分: 常用函数
-- ============================================================
-- ============================================================


-- ============================================================
-- 6.1 计算交易成本
-- ============================================================
CREATE OR REPLACE FUNCTION fn_calc_trade_cost(
    p_direction     trade_direction,
    p_amount        DECIMAL(16,2),
    p_commission_rate DECIMAL(8,6) DEFAULT 0.000300,
    p_slippage_rate DECIMAL(8,6) DEFAULT 0.001000,
    p_stamp_tax_rate DECIMAL(8,6) DEFAULT 0.001000,
    p_min_commission DECIMAL(10,2) DEFAULT 5.00
)
RETURNS TABLE (
    commission      DECIMAL(10,2),
    slippage        DECIMAL(10,2),
    stamp_tax       DECIMAL(10,2),
    total_cost      DECIMAL(10,2)
)
LANGUAGE plpgsql AS $$
DECLARE
    v_commission    DECIMAL(10,2);
    v_slippage      DECIMAL(10,2);
    v_stamp_tax     DECIMAL(10,2);
BEGIN
    -- 佣金(双向收取，有最低5元)
    v_commission := GREATEST(p_amount * p_commission_rate, p_min_commission);

    -- 滑点(双向)
    v_slippage := p_amount * p_slippage_rate;

    -- 印花税(仅卖出)
    IF p_direction = 'SELL' THEN
        v_stamp_tax := p_amount * p_stamp_tax_rate;
    ELSE
        v_stamp_tax := 0;
    END IF;

    RETURN QUERY SELECT
        v_commission,
        v_slippage,
        v_stamp_tax,
        (v_commission + v_slippage + v_stamp_tax);
END;
$$;

COMMENT ON FUNCTION fn_calc_trade_cost IS '计算交易成本(佣金+滑点+印花税)';


-- ============================================================
-- 6.2 计算机器人净值
-- ============================================================
CREATE OR REPLACE FUNCTION fn_calc_robot_nav(
    p_robot_id      BIGINT,
    p_trade_date    DATE
)
RETURNS DECIMAL(12,6)
LANGUAGE plpgsql AS $$
DECLARE
    v_initial_capital   DECIMAL(16,2);
    v_total_assets      DECIMAL(16,2);
    v_cash              DECIMAL(16,2);
    v_nav               DECIMAL(12,6);
BEGIN
    SELECT initial_capital, cash
    INTO v_initial_capital, v_cash
    FROM robots WHERE id = p_robot_id;

    IF v_initial_capital IS NULL OR v_initial_capital = 0 THEN
        RETURN 1.000000;
    END IF;

    -- 总资产 = 现金 + 持仓市值(需应用层传入或从行情API获取)
    -- 这里简化为从 daily_nav 获取，如果没有则返回 cash/initial
    SELECT total_assets INTO v_total_assets
    FROM daily_nav
    WHERE robot_id = p_robot_id AND trade_date = p_trade_date;

    IF v_total_assets IS NOT NULL THEN
        v_nav := v_total_assets / v_initial_capital;
    ELSE
        v_nav := v_cash / v_initial_capital;
    END IF;

    RETURN ROUND(v_nav, 6);
END;
$$;

COMMENT ON FUNCTION fn_calc_robot_nav IS '计算机器人指定日期的单位净值';


-- ============================================================
-- 6.3 获取下一个交易日
-- ============================================================
CREATE OR REPLACE FUNCTION fn_next_trade_date(
    p_date      DATE,
    p_n         INT DEFAULT 1,
    p_exchange  VARCHAR(8) DEFAULT 'SSE'
)
RETURNS DATE
LANGUAGE plpgsql AS $$
DECLARE
    v_result    DATE;
    v_count     INT := 0;
BEGIN
    IF p_n = 0 THEN
        -- 返回当日或当日之后最近的交易日
        SELECT cal_date INTO v_result
        FROM trade_calendar
        WHERE exchange = p_exchange
          AND cal_date >= p_date
          AND is_open = TRUE
        ORDER BY cal_date ASC
        LIMIT 1;
        RETURN v_result;
    END IF;

    IF p_n > 0 THEN
        -- 未来第N个交易日
        SELECT cal_date INTO v_result
        FROM trade_calendar
        WHERE exchange = p_exchange
          AND cal_date > p_date
          AND is_open = TRUE
        ORDER BY cal_date ASC
        LIMIT 1 OFFSET (p_n - 1);
    ELSE
        -- 过去第N个交易日
        SELECT cal_date INTO v_result
        FROM trade_calendar
        WHERE exchange = p_exchange
          AND cal_date < p_date
          AND is_open = TRUE
        ORDER BY cal_date DESC
        LIMIT 1 OFFSET (ABS(p_n) - 1);
    END IF;

    RETURN v_result;
END;
$$;

COMMENT ON FUNCTION fn_next_trade_date IS '获取相对于指定日期的第N个交易日，N>0未来，N<0过去，N=0当日或最近交易日';


-- ============================================================
-- 6.4 判断是否为交易日
-- ============================================================
CREATE OR REPLACE FUNCTION fn_is_trade_date(
    p_date      DATE,
    p_exchange  VARCHAR(8) DEFAULT 'SSE'
)
RETURNS BOOLEAN
LANGUAGE plpgsql AS $$
DECLARE
    v_is_open BOOLEAN;
BEGIN
    SELECT is_open INTO v_is_open
    FROM trade_calendar
    WHERE exchange = p_exchange AND cal_date = p_date;

    RETURN COALESCE(v_is_open, FALSE);
END;
$$;

COMMENT ON FUNCTION fn_is_trade_date IS '判断指定日期是否为交易日';


-- ============================================================
-- 6.5 计算区间交易日数量
-- ============================================================
CREATE OR REPLACE FUNCTION fn_trade_days_between(
    p_start_date    DATE,
    p_end_date      DATE,
    p_exchange      VARCHAR(8) DEFAULT 'SSE'
)
RETURNS INT
LANGUAGE plpgsql AS $$
DECLARE
    v_count INT;
BEGIN
    SELECT COUNT(*) INTO v_count
    FROM trade_calendar
    WHERE exchange = p_exchange
      AND cal_date > p_start_date
      AND cal_date <= p_end_date
      AND is_open = TRUE;

    RETURN v_count;
END;
$$;

COMMENT ON FUNCTION fn_trade_days_between IS '计算两个日期之间的交易日数量(不含起始日，含结束日)';


-- ============================================================
-- 6.6 更新机器人统计(触发器辅助函数)
-- ============================================================
CREATE OR REPLACE FUNCTION fn_update_robot_stats()
RETURNS TRIGGER
LANGUAGE plpgsql AS $$
BEGIN
    -- 仅在卖出(平仓)时更新统计
    IF NEW.direction = 'SELL' AND NEW.pnl_amount IS NOT NULL THEN
        UPDATE robots SET
            total_trades = total_trades + 1,
            win_count = win_count + CASE WHEN NEW.pnl_amount > 0 THEN 1 ELSE 0 END,
            lose_count = lose_count + CASE WHEN NEW.pnl_amount <= 0 THEN 1 ELSE 0 END,
            total_pnl = total_pnl + NEW.pnl_amount,
            total_pnl_pct = CASE
                WHEN initial_capital > 0
                THEN (total_pnl + NEW.pnl_amount) / initial_capital
                ELSE 0
            END,
            last_trade_date = NEW.trade_date,
            updated_at = NOW()
        WHERE id = NEW.robot_id;
    ELSIF NEW.direction = 'BUY' THEN
        UPDATE robots SET
            last_trade_date = NEW.trade_date,
            updated_at = NOW()
        WHERE id = NEW.robot_id;
    END IF;

    RETURN NEW;
END;
$$;

COMMENT ON FUNCTION fn_update_robot_stats IS '交易记录插入后自动更新机器人统计字段';


-- ============================================================
-- ============================================================
-- 第七部分: 触发器
-- ============================================================
-- ============================================================


-- ============================================================
-- 7.1 交易记录插入后更新机器人统计
-- ============================================================
DROP TRIGGER IF EXISTS trg_robot_trades_stats ON robot_trades;
CREATE TRIGGER trg_robot_trades_stats
    AFTER INSERT ON robot_trades
    FOR EACH ROW
    EXECUTE FUNCTION fn_update_robot_stats();


-- ============================================================
-- 7.2 通用 updated_at 自动更新触发器
-- ============================================================
CREATE OR REPLACE FUNCTION fn_auto_update_timestamp()
RETURNS TRIGGER
LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;

-- 为需要 updated_at 的表添加触发器
DO $$
DECLARE
    tbl TEXT;
BEGIN
    FOR tbl IN
        SELECT unnest(ARRAY[
            'stock_info',
            'app_config',
            'strategies',
            'strategy_params',
            'datasource_config',
            'robots',
            'sim_trades',
            'lhb_stock_daily',
            'lhb_tn_price',
            'lhb_agg_stats',
            'lhb_institution_stat',
            'market_sentiment'
        ])
    LOOP
        EXECUTE format(
            'DROP TRIGGER IF EXISTS trg_%s_updated_at ON %I;
             CREATE TRIGGER trg_%s_updated_at
                 BEFORE UPDATE ON %I
                 FOR EACH ROW
                 EXECUTE FUNCTION fn_auto_update_timestamp();',
            tbl, tbl, tbl, tbl
        );
    END LOOP;
END;
$$;


-- ============================================================
-- 7.3 资金流水余额校验触发器
-- ============================================================
CREATE OR REPLACE FUNCTION fn_validate_cash_ledger()
RETURNS TRIGGER
LANGUAGE plpgsql AS $$
DECLARE
    v_current_cash DECIMAL(16,2);
BEGIN
    -- 获取机器人当前现金
    SELECT cash INTO v_current_cash FROM robots WHERE id = NEW.robot_id;

    -- 出金方向校验: 余额不能为负
    IF NEW.direction = 'OUT' AND (v_current_cash - NEW.amount) < 0 THEN
        RAISE EXCEPTION '资金不足: 当前现金 %, 出金 %, 机器人ID %',
            v_current_cash, NEW.amount, NEW.robot_id;
    END IF;

    -- 自动填充 balance_before
    IF NEW.balance_before IS NULL THEN
        NEW.balance_before := v_current_cash;
    END IF;

    -- 自动填充 balance_after
    IF NEW.balance_after IS NULL THEN
        IF NEW.direction = 'IN' THEN
            NEW.balance_after := v_current_cash + NEW.amount;
        ELSE
            NEW.balance_after := v_current_cash - NEW.amount;
        END IF;
    END IF;

    -- 同步更新机器人现金
    UPDATE robots SET
        cash = NEW.balance_after,
        updated_at = NOW()
    WHERE id = NEW.robot_id;

    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_cash_ledger_validate ON cash_ledger;
CREATE TRIGGER trg_cash_ledger_validate
    BEFORE INSERT ON cash_ledger
    FOR EACH ROW
    EXECUTE FUNCTION fn_validate_cash_ledger();


-- ============================================================
-- ============================================================
-- 第八部分: 分区表(大数据量优化)
-- ============================================================
-- ============================================================


-- ============================================================
-- 8.1 K线数据按年分区
-- ============================================================

-- 注意: 如果数据量不大(单表<5000万行)，可不启用分区
-- 以下为启用分区的示例模板，需要将 kline_daily 改为分区表

-- 如需启用，先删除原表并重建为分区表:
/*
DROP TABLE IF EXISTS kline_daily CASCADE;
CREATE TABLE kline_daily (
    id                  BIGSERIAL,
    ts_code             VARCHAR(16)     NOT NULL,
    trade_date          DATE            NOT NULL,
    open                DECIMAL(12,4),
    high                DECIMAL(12,4),
    low                 DECIMAL(12,4),
    close               DECIMAL(12,4)   NOT NULL,
    pre_close           DECIMAL(12,4),
    pct_change          DECIMAL(8,4),
    vol                 DECIMAL(18,2),
    amount              DECIMAL(18,2),
    turnover_rate       DECIMAL(8,4),
    turnover_rate_f     DECIMAL(8,4),
    volume_ratio        DECIMAL(8,4),
    pe_ttm              DECIMAL(12,4),
    pb                  DECIMAL(12,4),
    total_mv            DECIMAL(18,2),
    circ_mv             DECIMAL(18,2),
    is_st               BOOLEAN         DEFAULT FALSE,
    is_limit_up         BOOLEAN         DEFAULT FALSE,
    is_limit_down       BOOLEAN         DEFAULT FALSE,
    created_at          TIMESTAMP       DEFAULT NOW(),

    CONSTRAINT pk_kline_daily PRIMARY KEY (id, trade_date),
    CONSTRAINT uk_kline_daily UNIQUE (ts_code, trade_date)
) PARTITION BY RANGE (trade_date);

-- 按年创建分区
CREATE TABLE kline_daily_2020 PARTITION OF kline_daily FOR VALUES FROM ('2020-01-01') TO ('2021-01-01');
CREATE TABLE kline_daily_2021 PARTITION OF kline_daily FOR VALUES FROM ('2021-01-01') TO ('2022-01-01');
CREATE TABLE kline_daily_2022 PARTITION OF kline_daily FOR VALUES FROM ('2022-01-01') TO ('2023-01-01');
CREATE TABLE kline_daily_2023 PARTITION OF kline_daily FOR VALUES FROM ('2023-01-01') TO ('2024-01-01');
CREATE TABLE kline_daily_2024 PARTITION OF kline_daily FOR VALUES FROM ('2024-01-01') TO ('2025-01-01');
CREATE TABLE kline_daily_2025 PARTITION OF kline_daily FOR VALUES FROM ('2025-01-01') TO ('2026-01-01');
CREATE TABLE kline_daily_default PARTITION OF kline_daily DEFAULT;
*/


-- ============================================================
-- 8.2 龙虎榜数据按年分区 (模板)
-- ============================================================
/*
-- 同理，lhb_stock_daily 可按年分区
-- 仅当数据量较大时启用
*/


-- ============================================================
-- ============================================================
-- 第九部分: 初始化数据
-- ============================================================
-- ============================================================


-- ============================================================
-- 9.1 默认策略
-- ============================================================
INSERT INTO strategies (name, description, category, entry_rules, exit_rules, risk_rules, params) VALUES
(
    '龙虎榜高分策略',
    '基于龙虎榜综合评分的选股策略，选择评分>=75的标的，结合市场情绪和资金面综合判断',
    'LHB',
    '[
        {"type": "score_above", "field": "score", "value": 75, "desc": "综合评分>=75"},
        {"type": "net_buy_positive", "field": "l_net", "value": 0, "desc": "龙虎榜净买入>0"},
        {"type": "turnover_range", "field": "turnover_rate", "min": 0.03, "max": 0.25, "desc": "换手率3%-25%"},
        {"type": "market_cap_range", "field": "float_market_cap", "min": 200000, "max": 10000000, "desc": "流通市值20亿-1000亿"}
    ]'::jsonb,
    '[
        {"type": "take_profit", "value": 0.15, "desc": "止盈15%"},
        {"type": "stop_loss", "value": -0.07, "desc": "止损7%"},
        {"type": "max_hold_days", "value": 10, "desc": "最大持仓10天"},
        {"type": "trailing_stop", "value": 0.05, "desc": "从最高点回撤5%止盈"}
    ]'::jsonb,
    '[
        {"type": "max_positions", "value": 5, "desc": "最大持仓5只"},
        {"type": "single_position_pct", "value": 0.20, "desc": "单票仓位<=20%"},
        {"type": "daily_loss_limit", "value": -0.05, "desc": "单日亏损熔断5%"},
        {"type": "sentiment_filter", "min_level": "COLD", "desc": "极寒行情不开仓"}
    ]'::jsonb,
    '{
        "lookback_days": 20,
        "score_weights": {"tech": 0.40, "fund": 0.35, "hist": 0.25},
        "prefer_industries": [],
        "exclude_st": true,
        "exclude_new_stock_days": 60
    }'::jsonb
),
(
    '机构席位跟踪策略',
    '跟踪机构专用席位净买入标的，适合中线持有',
    'LHB',
    '[
        {"type": "inst_net_positive", "field": "inst_net_amount", "value": 500, "desc": "机构净买入>=500万"},
        {"type": "inst_count", "field": "inst_buy_count", "value": 2, "desc": ">=2家机构买入"},
        {"type": "score_above", "field": "score", "value": 65, "desc": "综合评分>=65"}
    ]'::jsonb,
    '[
        {"type": "take_profit", "value": 0.20, "desc": "止盈20%"},
        {"type": "stop_loss", "value": -0.08, "desc": "止损8%"},
        {"type": "max_hold_days", "value": 20, "desc": "最大持仓20天"}
    ]'::jsonb,
    '[
        {"type": "max_positions", "value": 3, "desc": "最大持仓3只"},
        {"type": "single_position_pct", "value": 0.30, "desc": "单票仓位<=30%"},
        {"type": "daily_loss_limit", "value": -0.05, "desc": "单日亏损熔断5%"}
    ]'::jsonb,
    '{
        "lookback_days": 30,
        "score_weights": {"tech": 0.30, "fund": 0.45, "hist": 0.25},
        "prefer_industries": ["半导体", "新能源", "医药生物"],
        "exclude_st": true,
        "exclude_new_stock_days": 90,
        "min_market_cap": 500000
    }'::jsonb
),
(
    '游资打板策略',
    '跟踪知名游资席位涨停板标的，短线快进快出',
    'LHB',
    '[
        {"type": "is_limit_up", "value": true, "desc": "当日涨停"},
        {"type": "hot_money_seat", "value": true, "desc": "知名游资席位买入"},
        {"type": "score_above", "field": "score", "value": 70, "desc": "综合评分>=70"},
        {"type": "turnover_range", "field": "turnover_rate", "min": 0.05, "max": 0.30, "desc": "换手率5%-30%"},
        {"type": "market_cap_range", "field": "float_market_cap", "min": 100000, "max": 5000000, "desc": "流通市值10亿-500亿"}
    ]'::jsonb,
    '[
        {"type": "take_profit", "value": 0.10, "desc": "止盈10%"},
        {"type": "stop_loss", "value": -0.05, "desc": "止损5%"},
        {"type": "max_hold_days", "value": 3, "desc": "最大持仓3天"},
        {"type": "open_limit_sell", "value": true, "desc": "次日未封板则卖出"}
    ]'::jsonb,
    '[
        {"type": "max_positions", "value": 3, "desc": "最大持仓3只"},
        {"type": "single_position_pct", "value": 0.25, "desc": "单票仓位<=25%"},
        {"type": "daily_loss_limit", "value": -0.06, "desc": "单日亏损熔断6%"},
        {"type": "sentiment_filter", "min_level": "NEUTRAL", "desc": "情绪低于中性不开仓"}
    ]'::jsonb,
    '{
        "lookback_days": 10,
        "score_weights": {"tech": 0.45, "fund": 0.35, "hist": 0.20},
        "prefer_industries": [],
        "exclude_st": true,
        "exclude_new_stock_days": 30,
        "hot_money_list": ["赵老哥", "章盟主", "炒股养家", "小鳄鱼"]
    }'::jsonb
)
ON CONFLICT DO NOTHING;


-- ============================================================
-- 9.2 默认机器人
-- ============================================================
INSERT INTO robots (name, description, strategy_id, initial_capital, current_capital, cash, total_assets, status, max_positions, per_position_pct, risk_config)
SELECT
    '默认模拟机器人',
    '基于龙虎榜高分策略的模拟交易机器人，初始资金100万',
    s.id,
    1000000.00,
    1000000.00,
    1000000.00,
    1000000.00,
    'IDLE',
    5,
    0.20,
    '{
        "stop_loss_pct": -0.07,
        "take_profit_pct": 0.15,
        "daily_loss_limit": -0.05,
        "trailing_stop_pct": 0.05,
        "max_hold_days": 10,
        "score_threshold": 75,
        "max_single_loss": -0.10,
        "circuit_break_daily_pct": -0.08
    }'::jsonb
FROM strategies s
WHERE s.name = '龙虎榜高分策略'
LIMIT 1
ON CONFLICT DO NOTHING;


-- ============================================================
-- 9.3 交易日历初始化说明
-- ============================================================
-- 交易日历数据需通过数据同步任务从 TuShare/AKShare 拉取
-- 以下为手动插入示例模板(实际需完整数据)
/*
INSERT INTO trade_calendar (exchange, cal_date, is_open, pretrade_date) VALUES
    ('SSE', '2025-01-01', FALSE, '2024-12-31'),
    ('SSE', '2025-01-02', TRUE, '2024-12-31'),
    ('SSE', '2025-01-03', TRUE, '2025-01-02'),
    ...
*/


-- ============================================================
-- ============================================================
-- 第十部分: 权限与安全
-- ============================================================
-- ============================================================


-- ============================================================
-- 10.1 创建应用用户(生产环境)
-- ============================================================
/*
-- 只读用户(用于报表/分析)
CREATE ROLE lhb_readonly WITH LOGIN PASSWORD 'your_readonly_password';
GRANT CONNECT ON DATABASE lhb_quant TO lhb_readonly;
GRANT USAGE ON SCHEMA public TO lhb_readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO lhb_readonly;
GRANT SELECT ON ALL SEQUENCES IN SCHEMA public TO lhb_readonly;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO lhb_readonly;

-- 应用用户(用于后端服务)
CREATE ROLE lhb_app WITH LOGIN PASSWORD 'your_app_password';
GRANT CONNECT ON DATABASE lhb_quant TO lhb_app;
GRANT USAGE ON SCHEMA public TO lhb_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO lhb_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO lhb_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO lhb_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO lhb_app;

-- 管理员用户(用于DDL变更)
CREATE ROLE lhb_admin WITH LOGIN PASSWORD 'your_admin_password' CREATEDB;
GRANT ALL PRIVILEGES ON DATABASE lhb_quant TO lhb_admin;
GRANT ALL PRIVILEGES ON SCHEMA public TO lhb_admin;
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO lhb_admin;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO lhb_admin;
GRANT ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA public TO lhb_admin;
*/


-- ============================================================
-- 10.2 行级安全策略(可选，多租户时启用)
-- ============================================================
/*
-- 如果未来支持多用户各自管理自己的机器人
ALTER TABLE robots ENABLE ROW LEVEL SECURITY;

CREATE POLICY robot_user_policy ON robots
    USING (owner_id = current_setting('app.current_user_id')::BIGINT);
*/


-- ============================================================
-- ============================================================
-- 第十一部分: 维护用存储过程
-- ============================================================
-- ============================================================


-- ============================================================
-- 11.1 T+N价格批量计算
-- ============================================================
CREATE OR REPLACE PROCEDURE sp_calc_tn_prices(
    p_trade_date    DATE DEFAULT NULL
)
LANGUAGE plpgsql AS $$
DECLARE
    v_rec           RECORD;
    v_n             INT;
    v_target_date   DATE;
    v_close         DECIMAL(12,4);
    v_high          DECIMAL(12,4);
    v_low           DECIMAL(12,4);
    v_base_close    DECIMAL(12,4);
    v_count         INT := 0;
BEGIN
    -- 遍历需要计算的龙虎榜记录
    FOR v_rec IN
        SELECT
            lsd.id AS stock_daily_id,
            lsd.ts_code,
            lsd.trade_date,
            lsd.close_price AS base_close
        FROM lhb_stock_daily lsd
        WHERE (p_trade_date IS NULL OR lsd.trade_date = p_trade_date)
          AND lsd.close_price IS NOT NULL
          AND lsd.close_price > 0
    LOOP
        v_base_close := v_rec.base_close;

        -- 对每个N值计算
        FOREACH v_n IN ARRAY ARRAY[1, 2, 3, 5, 10]
        LOOP
            -- 获取T+N交易日
            v_target_date := fn_next_trade_date(v_rec.trade_date, v_n);

            IF v_target_date IS NULL THEN
                CONTINUE;
            END IF;

            -- 获取T+N日收盘价
            SELECT close INTO v_close
            FROM kline_daily
            WHERE ts_code = v_rec.ts_code AND trade_date = v_target_date;

            -- 获取 T 到 T+N 区间极值
            SELECT MAX(high), MIN(low) INTO v_high, v_low
            FROM kline_daily
            WHERE ts_code = v_rec.ts_code
              AND trade_date > v_rec.trade_date
              AND trade_date <= v_target_date;

            IF v_close IS NOT NULL THEN
                INSERT INTO lhb_tn_price (
                    stock_daily_id, ts_code, trade_date, n_day,
                    target_date, close_price, pnl_pct,
                    high_price, low_price, high_pct, low_pct,
                    is_limit_up, is_limit_down, calc_status
                ) VALUES (
                    v_rec.stock_daily_id,
                    v_rec.ts_code,
                    v_rec.trade_date,
                    v_n,
                    v_target_date,
                    v_close,
                    ROUND((v_close - v_base_close) / v_base_close, 4),
                    v_high,
                    v_low,
                    CASE WHEN v_high IS NOT NULL THEN ROUND((v_high - v_base_close) / v_base_close, 4) ELSE NULL END,
                    CASE WHEN v_low IS NOT NULL THEN ROUND((v_low - v_base_close) / v_base_close, 4) ELSE NULL END,
                    EXISTS(SELECT 1 FROM kline_daily WHERE ts_code = v_rec.ts_code AND trade_date = v_target_date AND is_limit_up = TRUE),
                    EXISTS(SELECT 1 FROM kline_daily WHERE ts_code = v_rec.ts_code AND trade_date = v_target_date AND is_limit_down = TRUE),
                    'DONE'
                )
                ON CONFLICT (stock_daily_id, n_day) DO UPDATE SET
                    target_date = EXCLUDED.target_date,
                    close_price = EXCLUDED.close_price,
                    pnl_pct = EXCLUDED.pnl_pct,
                    high_price = EXCLUDED.high_price,
                    low_price = EXCLUDED.low_price,
                    high_pct = EXCLUDED.high_pct,
                    low_pct = EXCLUDED.low_pct,
                    is_limit_up = EXCLUDED.is_limit_up,
                    is_limit_down = EXCLUDED.is_limit_down,
                    calc_status = EXCLUDED.calc_status,
                    updated_at = NOW();

                v_count := v_count + 1;
            END IF;
        END LOOP;
    END LOOP;

    RAISE NOTICE 'T+N价格计算完成，共处理 % 条记录', v_count;
END;
$$;

COMMENT ON PROCEDURE sp_calc_tn_prices IS '批量计算龙虎榜T+N日价格，可指定日期或全量计算';


-- ============================================================
-- 11.2 龙虎榜聚合统计刷新
-- ============================================================
CREATE OR REPLACE PROCEDURE sp_refresh_lhb_agg_stats(
    p_stat_type     VARCHAR(16) DEFAULT 'ALL',     -- STOCK/BROKER/INDUSTRY/ALL
    p_stat_period   VARCHAR(16) DEFAULT 'ALL'      -- 7D/30D/90D/ALL
)
LANGUAGE plpgsql AS $$
DECLARE
    v_periods       VARCHAR(16)[];
    v_period        VARCHAR(16);
    v_cutoff_date   DATE;
    v_count         INT := 0;
BEGIN
    -- 确定要计算的周期
    IF p_stat_period = 'ALL' THEN
        v_periods := ARRAY['7D', '30D', '90D', 'ALL'];
    ELSE
        v_periods := ARRAY[p_stat_period];
    END IF;

    FOREACH v_period IN ARRAY v_periods
    LOOP
        -- 计算截止日期
        v_cutoff_date := CASE v_period
            WHEN '7D'  THEN CURRENT_DATE - INTERVAL '7 days'
            WHEN '30D' THEN CURRENT_DATE - INTERVAL '30 days'
            WHEN '90D' THEN CURRENT_DATE - INTERVAL '90 days'
            ELSE '2000-01-01'::DATE
        END;

        -- 按股票聚合
        IF p_stat_type IN ('STOCK', 'ALL') THEN
            INSERT INTO lhb_agg_stats (
                stat_type, stat_key, stat_period,
                lhb_count, buy_count, sell_count,
                total_buy_amount, total_sell_amount, total_net_amount,
                avg_score, win_rate_1d, win_rate_3d, win_rate_5d,
                avg_pnl_1d, avg_pnl_3d, avg_pnl_5d,
                max_pnl, min_pnl, updated_at
            )
            SELECT
                'STOCK',
                lsd.ts_code,
                v_period,
                COUNT(*),
                COUNT(*) FILTER (WHERE lsd.l_net > 0),
                COUNT(*) FILTER (WHERE lsd.l_net <= 0),
                COALESCE(SUM(lsd.l_buy), 0),
                COALESCE(SUM(lsd.l_sell), 0),
                COALESCE(SUM(lsd.l_net), 0),
                ROUND(AVG(lsd.score), 2),
                ROUND(COUNT(*) FILTER (WHERE tn1.pnl_pct > 0)::DECIMAL /
                      NULLIF(COUNT(*) FILTER (WHERE tn1.pnl_pct IS NOT NULL), 0), 4),
                ROUND(COUNT(*) FILTER (WHERE tn3.pnl_pct > 0)::DECIMAL /
                      NULLIF(COUNT(*) FILTER (WHERE tn3.pnl_pct IS NOT NULL), 0), 4),
                ROUND(COUNT(*) FILTER (WHERE tn5.pnl_pct > 0)::DECIMAL /
                      NULLIF(COUNT(*) FILTER (WHERE tn5.pnl_pct IS NOT NULL), 0), 4),
                ROUND(AVG(tn1.pnl_pct), 4),
                ROUND(AVG(tn3.pnl_pct), 4),
                ROUND(AVG(tn5.pnl_pct), 4),
                MAX(tn1.pnl_pct),
                MIN(tn1.pnl_pct),
                NOW()
            FROM lhb_stock_daily lsd
            LEFT JOIN lhb_tn_price tn1 ON tn1.stock_daily_id = lsd.id AND tn1.n_day = 1
            LEFT JOIN lhb_tn_price tn3 ON tn3.stock_daily_id = lsd.id AND tn3.n_day = 3
            LEFT JOIN lhb_tn_price tn5 ON tn5.stock_daily_id = lsd.id AND tn5.n_day = 5
            WHERE lsd.trade_date >= v_cutoff_date
            GROUP BY lsd.ts_code
            ON CONFLICT (stat_type, stat_key, stat_period) DO UPDATE SET
                lhb_count = EXCLUDED.lhb_count,
                buy_count = EXCLUDED.buy_count,
                sell_count = EXCLUDED.sell_count,
                total_buy_amount = EXCLUDED.total_buy_amount,
                total_sell_amount = EXCLUDED.total_sell_amount,
                total_net_amount = EXCLUDED.total_net_amount,
                avg_score = EXCLUDED.avg_score,
                win_rate_1d = EXCLUDED.win_rate_1d,
                win_rate_3d = EXCLUDED.win_rate_3d,
                win_rate_5d = EXCLUDED.win_rate_5d,
                avg_pnl_1d = EXCLUDED.avg_pnl_1d,
                avg_pnl_3d = EXCLUDED.avg_pnl_3d,
                avg_pnl_5d = EXCLUDED.avg_pnl_5d,
                max_pnl = EXCLUDED.max_pnl,
                min_pnl = EXCLUDED.min_pnl,
                updated_at = NOW();

            GET DIAGNOSTICS v_count = ROW_COUNT;
            RAISE NOTICE '按股票聚合(%): 更新 % 条', v_period, v_count;
        END IF;

        -- 按营业部聚合
        IF p_stat_type IN ('BROKER', 'ALL') THEN
            INSERT INTO lhb_agg_stats (
                stat_type, stat_key, stat_period,
                lhb_count, buy_count, sell_count,
                total_buy_amount, total_sell_amount, total_net_amount,
                avg_score, win_rate_1d, win_rate_3d, win_rate_5d,
                avg_pnl_1d, avg_pnl_3d, avg_pnl_5d,
                max_pnl, min_pnl,
                top_industries, top_stocks, style_tag,
                updated_at
            )
            SELECT
                'BROKER',
                ls.broker_name,
                v_period,
                COUNT(DISTINCT (ls.ts_code, ls.trade_date)),
                COUNT(*) FILTER (WHERE ls.direction = 'BUY'),
                COUNT(*) FILTER (WHERE ls.direction = 'SELL'),
                COALESCE(SUM(ls.buy_amount), 0),
                COALESCE(SUM(ls.sell_amount), 0),
                COALESCE(SUM(ls.net_amount), 0),
                ROUND(AVG(lsd.score), 2),
                -- T+1胜率: 仅计算买入方向
                ROUND(
                    COUNT(*) FILTER (WHERE ls.direction = 'BUY' AND tn1.pnl_pct > 0)::DECIMAL /
                    NULLIF(COUNT(*) FILTER (WHERE ls.direction = 'BUY' AND tn1.pnl_pct IS NOT NULL), 0),
                    4
                ),
                ROUND(
                    COUNT(*) FILTER (WHERE ls.direction = 'BUY' AND tn3.pnl_pct > 0)::DECIMAL /
                    NULLIF(COUNT(*) FILTER (WHERE ls.direction = 'BUY' AND tn3.pnl_pct IS NOT NULL), 0),
                    4
                ),
                ROUND(
                    COUNT(*) FILTER (WHERE ls.direction = 'BUY' AND tn5.pnl_pct > 0)::DECIMAL /
                    NULLIF(COUNT(*) FILTER (WHERE ls.direction = 'BUY' AND tn5.pnl_pct IS NOT NULL), 0),
                    4
                ),
                ROUND(AVG(CASE WHEN ls.direction = 'BUY' THEN tn1.pnl_pct END), 4),
                ROUND(AVG(CASE WHEN ls.direction = 'BUY' THEN tn3.pnl_pct END), 4),
                ROUND(AVG(CASE WHEN ls.direction = 'BUY' THEN tn5.pnl_pct END), 4),
                MAX(CASE WHEN ls.direction = 'BUY' THEN tn1.pnl_pct END),
                MIN(CASE WHEN ls.direction = 'BUY' THEN tn1.pnl_pct END),
                -- Top行业(子查询)
                (
                    SELECT jsonb_agg(jsonb_build_object('industry', sub.industry, 'count', sub.cnt) ORDER BY sub.cnt DESC)
                    FROM (
                        SELECT lsd2.industry, COUNT(*) AS cnt
                        FROM lhb_seats ls2
                        JOIN lhb_stock_daily lsd2 ON lsd2.id = ls2.stock_daily_id
                        WHERE ls2.broker_name = ls.broker_name
                          AND lsd2.trade_date >= v_cutoff_date
                          AND lsd2.industry IS NOT NULL
                        GROUP BY lsd2.industry
                        ORDER BY cnt DESC
                        LIMIT 5
                    ) sub
                ),
                -- Top标的(子查询)
                (
                    SELECT jsonb_agg(jsonb_build_object('ts_code', sub.ts_code, 'name', sub.name, 'count', sub.cnt) ORDER BY sub.cnt DESC)
                    FROM (
                        SELECT ls2.ts_code, MAX(lsd2.name) AS name, COUNT(*) AS cnt
                        FROM lhb_seats ls2
                        JOIN lhb_stock_daily lsd2 ON lsd2.id = ls2.stock_daily_id
                        WHERE ls2.broker_name = ls.broker_name
                          AND lsd2.trade_date >= v_cutoff_date
                        GROUP BY ls2.ts_code
                        ORDER BY cnt DESC
                        LIMIT 10
                    ) sub
                ),
                -- 风格标签
                CASE
                    WHEN AVG(CASE WHEN ls.direction = 'BUY' THEN lsd.pct_change END) > 0.08 THEN '打板型'
                    WHEN AVG(CASE WHEN ls.direction = 'BUY' THEN lsd.pct_change END) < 0.02 THEN '低吸型'
                    WHEN ls.is_institution THEN '机构型'
                    ELSE '综合型'
                END,
                NOW()
            FROM lhb_seats ls
            JOIN lhb_stock_daily lsd ON lsd.id = ls.stock_daily_id
            LEFT JOIN lhb_tn_price tn1 ON tn1.stock_daily_id = lsd.id AND tn1.n_day = 1
            LEFT JOIN lhb_tn_price tn3 ON tn3.stock_daily_id = lsd.id AND tn3.n_day = 3
            LEFT JOIN lhb_tn_price tn5 ON tn5.stock_daily_id = lsd.id AND tn5.n_day = 5
            WHERE lsd.trade_date >= v_cutoff_date
            GROUP BY ls.broker_name, ls.is_institution
            ON CONFLICT (stat_type, stat_key, stat_period) DO UPDATE SET
                lhb_count = EXCLUDED.lhb_count,
                buy_count = EXCLUDED.buy_count,
                sell_count = EXCLUDED.sell_count,
                total_buy_amount = EXCLUDED.total_buy_amount,
                total_sell_amount = EXCLUDED.total_sell_amount,
                total_net_amount = EXCLUDED.total_net_amount,
                avg_score = EXCLUDED.avg_score,
                win_rate_1d = EXCLUDED.win_rate_1d,
                win_rate_3d = EXCLUDED.win_rate_3d,
                win_rate_5d = EXCLUDED.win_rate_5d,
                avg_pnl_1d = EXCLUDED.avg_pnl_1d,
                avg_pnl_3d = EXCLUDED.avg_pnl_3d,
                avg_pnl_5d = EXCLUDED.avg_pnl_5d,
                max_pnl = EXCLUDED.max_pnl,
                min_pnl = EXCLUDED.min_pnl,
                top_industries = EXCLUDED.top_industries,
                top_stocks = EXCLUDED.top_stocks,
                style_tag = EXCLUDED.style_tag,
                updated_at = NOW();

            GET DIAGNOSTICS v_count = ROW_COUNT;
            RAISE NOTICE '按营业部聚合(%): 更新 % 条', v_period, v_count;
        END IF;

        -- 按行业聚合
        IF p_stat_type IN ('INDUSTRY', 'ALL') THEN
            INSERT INTO lhb_agg_stats (
                stat_type, stat_key, stat_period,
                lhb_count,
                total_buy_amount, total_sell_amount, total_net_amount,
                avg_score,
                win_rate_1d, avg_pnl_1d,
                win_rate_3d, avg_pnl_3d,
                updated_at
            )
            SELECT
                'INDUSTRY',
                COALESCE(lsd.industry, '未知'),
                v_period,
                COUNT(*),
                COALESCE(SUM(lsd.l_buy), 0),
                COALESCE(SUM(lsd.l_sell), 0),
                COALESCE(SUM(lsd.l_net), 0),
                ROUND(AVG(lsd.score), 2),
                ROUND(COUNT(*) FILTER (WHERE tn1.pnl_pct > 0)::DECIMAL /
                      NULLIF(COUNT(*) FILTER (WHERE tn1.pnl_pct IS NOT NULL), 0), 4),
                ROUND(AVG(tn1.pnl_pct), 4),
                ROUND(COUNT(*) FILTER (WHERE tn3.pnl_pct > 0)::DECIMAL /
                      NULLIF(COUNT(*) FILTER (WHERE tn3.pnl_pct IS NOT NULL), 0), 4),
                ROUND(AVG(tn3.pnl_pct), 4),
                NOW()
            FROM lhb_stock_daily lsd
            LEFT JOIN lhb_tn_price tn1 ON tn1.stock_daily_id = lsd.id AND tn1.n_day = 1
            LEFT JOIN lhb_tn_price tn3 ON tn3.stock_daily_id = lsd.id AND tn3.n_day = 3
            WHERE lsd.trade_date >= v_cutoff_date
            GROUP BY COALESCE(lsd.industry, '未知')
            ON CONFLICT (stat_type, stat_key, stat_period) DO UPDATE SET
                lhb_count = EXCLUDED.lhb_count,
                total_buy_amount = EXCLUDED.total_buy_amount,
                total_sell_amount = EXCLUDED.total_sell_amount,
                total_net_amount = EXCLUDED.total_net_amount,
                avg_score = EXCLUDED.avg_score,
                win_rate_1d = EXCLUDED.win_rate_1d,
                avg_pnl_1d = EXCLUDED.avg_pnl_1d,
                win_rate_3d = EXCLUDED.win_rate_3d,
                avg_pnl_3d = EXCLUDED.avg_pnl_3d,
                updated_at = NOW();

            GET DIAGNOSTICS v_count = ROW_COUNT;
            RAISE NOTICE '按行业聚合(%): 更新 % 条', v_period, v_count;
        END IF;

    END LOOP;

    -- 更新营业部活跃度排名
    IF p_stat_type IN ('BROKER', 'ALL') THEN
        WITH ranked AS (
            SELECT
                id,
                ROW_NUMBER() OVER (
                    PARTITION BY stat_period
                    ORDER BY lhb_count DESC, total_net_amount DESC
                ) AS rk
            FROM lhb_agg_stats
            WHERE stat_type = 'BROKER'
        )
        UPDATE lhb_agg_stats a
        SET active_rank = r.rk
        FROM ranked r
        WHERE a.id = r.id;

        RAISE NOTICE '营业部活跃度排名更新完成';
    END IF;

    RAISE NOTICE '聚合统计刷新全部完成';
END;
$$;

COMMENT ON PROCEDURE sp_refresh_lhb_agg_stats IS '刷新龙虎榜聚合统计，支持按类型(STOCK/BROKER/INDUSTRY)和周期(7D/30D/90D/ALL)刷新';


-- ============================================================
-- 11.3 每日结算(机器人净值快照)
-- ============================================================
CREATE OR REPLACE PROCEDURE sp_daily_settlement(
    p_trade_date    DATE DEFAULT CURRENT_DATE,
    p_robot_id      BIGINT DEFAULT NULL              -- NULL表示所有运行中的机器人
)
LANGUAGE plpgsql AS $$
DECLARE
    v_robot         RECORD;
    v_position_val  DECIMAL(16,2);
    v_total_assets  DECIMAL(16,2);
    v_nav           DECIMAL(12,6);
    v_prev_nav      DECIMAL(12,6);
    v_prev_assets   DECIMAL(16,2);
    v_max_nav       DECIMAL(12,6);
    v_pnl_daily     DECIMAL(16,2);
    v_pnl_daily_pct DECIMAL(8,4);
    v_pnl_total     DECIMAL(16,2);
    v_pnl_total_pct DECIMAL(8,4);
    v_drawdown      DECIMAL(8,4);
    v_max_drawdown  DECIMAL(8,4);
    v_pos_count     INT;
    v_buy_amt       DECIMAL(16,2);
    v_sell_amt      DECIMAL(16,2);
    v_trade_count   INT;
    v_benchmark_pct DECIMAL(8,4);
    v_count         INT := 0;
BEGIN
    -- 检查是否交易日
    IF NOT fn_is_trade_date(p_trade_date) THEN
        RAISE NOTICE '% 非交易日，跳过结算', p_trade_date;
        RETURN;
    END IF;

    -- 遍历需要结算的机器人
    FOR v_robot IN
        SELECT * FROM robots
        WHERE status IN ('RUNNING', 'PAUSED')
          AND (p_robot_id IS NULL OR id = p_robot_id)
    LOOP
        -- 1. 计算持仓市值
        --    注意: 实际应用中 current_price 需从行情API获取
        --    这里从 kline_daily 获取当日收盘价
        SELECT
            COALESCE(SUM(vp.shares * COALESCE(kd.close, vp.cost_price)), 0),
            COUNT(*)
        INTO v_position_val, v_pos_count
        FROM v_robot_positions vp
        LEFT JOIN kline_daily kd ON kd.ts_code = vp.ts_code AND kd.trade_date = p_trade_date
        WHERE vp.robot_id = v_robot.id;

        -- 2. 计算总资产
        v_total_assets := v_robot.cash + v_position_val;

        -- 3. 计算单位净值
        IF v_robot.initial_capital > 0 THEN
            v_nav := ROUND(v_total_assets / v_robot.initial_capital, 6);
        ELSE
            v_nav := 1.000000;
        END IF;

        -- 4. 获取前一日净值
        SELECT net_value, total_assets, max_net_value
        INTO v_prev_nav, v_prev_assets, v_max_nav
        FROM daily_nav
        WHERE robot_id = v_robot.id AND trade_date < p_trade_date
        ORDER BY trade_date DESC
        LIMIT 1;

        -- 首次结算默认值
        v_prev_nav := COALESCE(v_prev_nav, 1.000000);
        v_prev_assets := COALESCE(v_prev_assets, v_robot.initial_capital);
        v_max_nav := COALESCE(v_max_nav, 1.000000);

        -- 5. 计算当日盈亏
        v_pnl_daily := v_total_assets - v_prev_assets;
        v_pnl_daily_pct := CASE
            WHEN v_prev_assets > 0 THEN ROUND(v_pnl_daily / v_prev_assets, 4)
            ELSE 0
        END;

        -- 6. 计算累计盈亏
        v_pnl_total := v_total_assets - v_robot.initial_capital;
        v_pnl_total_pct := CASE
            WHEN v_robot.initial_capital > 0 THEN ROUND(v_pnl_total / v_robot.initial_capital, 4)
            ELSE 0
        END;

        -- 7. 更新最高净值
        IF v_nav > v_max_nav THEN
            v_max_nav := v_nav;
        END IF;

        -- 8. 计算回撤
        v_drawdown := CASE
            WHEN v_max_nav > 0 THEN ROUND((v_nav - v_max_nav) / v_max_nav, 4)
            ELSE 0
        END;

        -- 9. 历史最大回撤
        SELECT LEAST(
            COALESCE(MIN(drawdown), 0),
            v_drawdown
        ) INTO v_max_drawdown
        FROM daily_nav
        WHERE robot_id = v_robot.id;

        -- 10. 当日交易统计
        SELECT
            COALESCE(COUNT(*), 0),
            COALESCE(SUM(CASE WHEN direction = 'BUY' THEN amount ELSE 0 END), 0),
            COALESCE(SUM(CASE WHEN direction = 'SELL' THEN amount ELSE 0 END), 0)
        INTO v_trade_count, v_buy_amt, v_sell_amt
        FROM robot_trades
        WHERE robot_id = v_robot.id AND trade_date = p_trade_date;

        -- 11. 获取基准收益(沪深300)
        SELECT pct_change INTO v_benchmark_pct
        FROM kline_daily
        WHERE ts_code = '000300.SH' AND trade_date = p_trade_date;

        -- 12. 写入净值快照
        INSERT INTO daily_nav (
            robot_id, trade_date,
            net_value, cumulative_nav,
            total_assets, cash, frozen_amount, position_value, position_count,
            pnl_daily, pnl_daily_pct,
            pnl_total, pnl_total_pct,
            drawdown, max_drawdown, max_net_value,
            trade_count, buy_amount, sell_amount,
            benchmark_pct, excess_return
        ) VALUES (
            v_robot.id, p_trade_date,
            v_nav, v_nav,  -- cumulative_nav 暂等于 nav(无分红场景)
            v_total_assets, v_robot.cash, v_robot.frozen_amount, v_position_val, v_pos_count,
            v_pnl_daily, v_pnl_daily_pct,
            v_pnl_total, v_pnl_total_pct,
            v_drawdown, v_max_drawdown, v_max_nav,
            v_trade_count, v_buy_amt, v_sell_amt,
            v_benchmark_pct,
            CASE WHEN v_benchmark_pct IS NOT NULL THEN v_pnl_daily_pct - v_benchmark_pct ELSE NULL END
        )
        ON CONFLICT (robot_id, trade_date) DO UPDATE SET
            net_value = EXCLUDED.net_value,
            cumulative_nav = EXCLUDED.cumulative_nav,
            total_assets = EXCLUDED.total_assets,
            cash = EXCLUDED.cash,
            frozen_amount = EXCLUDED.frozen_amount,
            position_value = EXCLUDED.position_value,
            position_count = EXCLUDED.position_count,
            pnl_daily = EXCLUDED.pnl_daily,
            pnl_daily_pct = EXCLUDED.pnl_daily_pct,
            pnl_total = EXCLUDED.pnl_total,
            pnl_total_pct = EXCLUDED.pnl_total_pct,
            drawdown = EXCLUDED.drawdown,
            max_drawdown = EXCLUDED.max_drawdown,
            max_net_value = EXCLUDED.max_net_value,
            trade_count = EXCLUDED.trade_count,
            buy_amount = EXCLUDED.buy_amount,
            sell_amount = EXCLUDED.sell_amount,
            benchmark_pct = EXCLUDED.benchmark_pct,
            excess_return = EXCLUDED.excess_return;

        -- 13. 同步更新机器人主表
        UPDATE robots SET
            current_capital = v_total_assets,
            total_assets = v_total_assets,
            total_pnl = v_pnl_total,
            total_pnl_pct = v_pnl_total_pct,
            max_drawdown = v_max_drawdown,
            max_drawdown_amount = CASE
                WHEN v_robot.initial_capital > 0
                THEN ROUND(v_max_drawdown * v_robot.initial_capital, 2)
                ELSE 0
            END,
            updated_at = NOW()
        WHERE id = v_robot.id;

        v_count := v_count + 1;
        RAISE NOTICE '机器人[%] % 结算完成: 净值=%, 日收益=%%, 总收益=%%',
            v_robot.id, v_robot.name, v_nav,
            ROUND(v_pnl_daily_pct * 100, 2),
            ROUND(v_pnl_total_pct * 100, 2);

    END LOOP;

    RAISE NOTICE '每日结算完成，共处理 % 个机器人', v_count;
END;
$$;

COMMENT ON PROCEDURE sp_daily_settlement IS '每日收盘后结算，计算机器人净值/盈亏/回撤并生成快照';


-- ============================================================
-- 11.4 数据清理(历史数据归档)
-- ============================================================
CREATE OR REPLACE PROCEDURE sp_cleanup_old_data(
    p_keep_days     INT DEFAULT 365,                  -- 保留天数
    p_dry_run       BOOLEAN DEFAULT TRUE              -- 试运行(不实际删除)
)
LANGUAGE plpgsql AS $$
DECLARE
    v_cutoff_date   DATE;
    v_count         BIGINT;
BEGIN
    v_cutoff_date := CURRENT_DATE - (p_keep_days || ' days')::INTERVAL;

    RAISE NOTICE '清理截止日期: %, 模式: %',
        v_cutoff_date,
        CASE WHEN p_dry_run THEN '试运行(DRY RUN)' ELSE '正式执行' END;

    -- 1. 采集器错误日志(保留较短时间)
    SELECT COUNT(*) INTO v_count
    FROM collector_error_log WHERE created_at < v_cutoff_date - INTERVAL '30 days';
    RAISE NOTICE '  collector_error_log: 将清理 % 条', v_count;
    IF NOT p_dry_run THEN
        DELETE FROM collector_error_log WHERE created_at < v_cutoff_date - INTERVAL '30 days';
    END IF;

    -- 2. 数据源熔断日志
    SELECT COUNT(*) INTO v_count
    FROM datasource_circuit_log WHERE created_at < v_cutoff_date - INTERVAL '30 days';
    RAISE NOTICE '  datasource_circuit_log: 将清理 % 条', v_count;
    IF NOT p_dry_run THEN
        DELETE FROM datasource_circuit_log WHERE created_at < v_cutoff_date - INTERVAL '30 days';
    END IF;

    -- 3. 审计日志(保留较长)
    SELECT COUNT(*) INTO v_count
    FROM audit_logs WHERE created_at < v_cutoff_date;
    RAISE NOTICE '  audit_logs: 将清理 % 条', v_count;
    IF NOT p_dry_run THEN
        DELETE FROM audit_logs WHERE created_at < v_cutoff_date;
    END IF;

    -- 4. 交易事件(已读+已处理的)
    SELECT COUNT(*) INTO v_count
    FROM trade_events
    WHERE created_at < v_cutoff_date
      AND is_read = TRUE
      AND is_handled = TRUE;
    RAISE NOTICE '  trade_events(已处理): 将清理 % 条', v_count;
    IF NOT p_dry_run THEN
        DELETE FROM trade_events
        WHERE created_at < v_cutoff_date
          AND is_read = TRUE
          AND is_handled = TRUE;
    END IF;

    -- 5. 模拟交易快照(已关闭的模拟交易)
    SELECT COUNT(*) INTO v_count
    FROM sim_trade_snapshots sts
    JOIN sim_trades st ON st.id = sts.sim_trade_id
    WHERE st.status != 'OPEN'
      AND sts.snapshot_date < v_cutoff_date - INTERVAL '90 days';
    RAISE NOTICE '  sim_trade_snapshots(已关闭): 将清理 % 条', v_count;
    IF NOT p_dry_run THEN
        DELETE FROM sim_trade_snapshots
        WHERE sim_trade_id IN (
            SELECT id FROM sim_trades WHERE status != 'OPEN'
        )
        AND snapshot_date < v_cutoff_date - INTERVAL '90 days';
    END IF;

    -- 6. 市场快照(只保留 CLOSE 类型，删除盘中快照)
    SELECT COUNT(*) INTO v_count
    FROM market_snapshots
    WHERE snapshot_type = 'REALTIME'
      AND trade_date < v_cutoff_date - INTERVAL '7 days';
    RAISE NOTICE '  market_snapshots(REALTIME): 将清理 % 条', v_count;
    IF NOT p_dry_run THEN
        DELETE FROM market_snapshots
        WHERE snapshot_type = 'REALTIME'
          AND trade_date < v_cutoff_date - INTERVAL '7 days';
    END IF;

    RAISE NOTICE '数据清理%完成',
        CASE WHEN p_dry_run THEN '(试运行)' ELSE '' END;
END;
$$;

COMMENT ON PROCEDURE sp_cleanup_old_data IS '清理历史数据，支持试运行模式。p_keep_days控制保留天数';


-- ============================================================
-- 11.5 AHS(账户健康度)计算
-- ============================================================
CREATE OR REPLACE PROCEDURE sp_calc_ahs_score(
    p_robot_id      BIGINT DEFAULT NULL
)
LANGUAGE plpgsql AS $$
DECLARE
    v_robot         RECORD;
    v_ahs           DECIMAL(6,2);
    v_detail        JSONB;
    -- 各维度分数(0-100)
    v_capital_util  DECIMAL(6,2);    -- 资金利用率得分
    v_win_rate_s    DECIMAL(6,2);    -- 胜率得分
    v_drawdown_s    DECIMAL(6,2);    -- 回撤得分
    v_consistency_s DECIMAL(6,2);    -- 稳定性得分
    v_risk_s        DECIMAL(6,2);    -- 风控得分
    -- 中间变量
    v_util_pct      DECIMAL(8,4);
    v_win_rate      DECIMAL(8,4);
    v_recent_pnl    DECIMAL(8,4)[];
    v_std_dev       DECIMAL(8,4);
    v_count         INT := 0;
BEGIN
    FOR v_robot IN
        SELECT * FROM robots
        WHERE (p_robot_id IS NULL OR id = p_robot_id)
          AND status IN ('RUNNING', 'PAUSED')
    LOOP
        -- 1. 资金利用率 (仓位太低或太高都不好)
        --    最优区间: 40%-80%
        v_util_pct := CASE
            WHEN v_robot.total_assets > 0
            THEN 1.0 - (v_robot.cash / v_robot.total_assets)
            ELSE 0
        END;
        v_capital_util := CASE
            WHEN v_util_pct BETWEEN 0.40 AND 0.80 THEN 100
            WHEN v_util_pct BETWEEN 0.20 AND 0.40 THEN 60 + (v_util_pct - 0.20) / 0.20 * 40
            WHEN v_util_pct BETWEEN 0.80 AND 0.95 THEN 100 - (v_util_pct - 0.80) / 0.15 * 30
            WHEN v_util_pct > 0.95 THEN 40  -- 满仓风险
            WHEN v_util_pct < 0.20 THEN 40  -- 空仓效率低
            ELSE 50
        END;

        -- 2. 胜率得分
        v_win_rate := CASE
            WHEN v_robot.total_trades > 0
            THEN v_robot.win_count::DECIMAL / v_robot.total_trades
            ELSE 0.5  -- 无交易默认50%
        END;
        v_win_rate_s := CASE
            WHEN v_win_rate >= 0.60 THEN 100
            WHEN v_win_rate >= 0.50 THEN 70 + (v_win_rate - 0.50) / 0.10 * 30
            WHEN v_win_rate >= 0.40 THEN 40 + (v_win_rate - 0.40) / 0.10 * 30
            WHEN v_win_rate >= 0.30 THEN 20 + (v_win_rate - 0.30) / 0.10 * 20
            ELSE 10
        END;

        -- 3. 回撤得分 (回撤越小分数越高)
        v_drawdown_s := CASE
            WHEN ABS(v_robot.max_drawdown) <= 0.05 THEN 100
            WHEN ABS(v_robot.max_drawdown) <= 0.10 THEN 80
            WHEN ABS(v_robot.max_drawdown) <= 0.15 THEN 60
            WHEN ABS(v_robot.max_drawdown) <= 0.20 THEN 40
            WHEN ABS(v_robot.max_drawdown) <= 0.30 THEN 20
            ELSE 5
        END;

        -- 4. 稳定性得分 (近10日收益波动率)
        SELECT ARRAY_AGG(pnl_daily_pct ORDER BY trade_date DESC)
        INTO v_recent_pnl
        FROM (
            SELECT pnl_daily_pct, trade_date
            FROM daily_nav
            WHERE robot_id = v_robot.id
            ORDER BY trade_date DESC
            LIMIT 10
        ) sub;

        IF v_recent_pnl IS NOT NULL AND array_length(v_recent_pnl, 1) >= 3 THEN
            -- 计算标准差
            SELECT STDDEV(val) INTO v_std_dev
            FROM unnest(v_recent_pnl) AS val;

            v_consistency_s := CASE
                WHEN v_std_dev IS NULL THEN 50
                WHEN v_std_dev <= 0.01 THEN 100     -- 日波动<1%
                WHEN v_std_dev <= 0.02 THEN 80
                WHEN v_std_dev <= 0.03 THEN 60
                WHEN v_std_dev <= 0.05 THEN 40
                ELSE 20
            END;
        ELSE
            v_consistency_s := 50;  -- 数据不足默认50
        END IF;

        -- 5. 风控得分 (综合判断)
        v_risk_s := 100;

        -- 5a. 检查是否触发过止损
        IF EXISTS (
            SELECT 1 FROM robot_trades
            WHERE robot_id = v_robot.id
              AND phase = 'STOP_LOSS'
              AND trade_date >= CURRENT_DATE - INTERVAL '30 days'
        ) THEN
            v_risk_s := v_risk_s - 15;  -- 近30日有止损扣分
        END IF;

        -- 5b. 检查单笔最大亏损
        DECLARE
            v_max_single_loss DECIMAL(8,4);
        BEGIN
            SELECT MIN(pnl_pct) INTO v_max_single_loss
            FROM robot_trades
            WHERE robot_id = v_robot.id
              AND direction = 'SELL'
              AND pnl_pct IS NOT NULL
              AND trade_date >= CURRENT_DATE - INTERVAL '30 days';

            IF v_max_single_loss IS NOT NULL AND v_max_single_loss < -0.10 THEN
                v_risk_s := v_risk_s - 20;  -- 单笔亏损超10%
            ELSIF v_max_single_loss IS NOT NULL AND v_max_single_loss < -0.07 THEN
                v_risk_s := v_risk_s - 10;  -- 单笔亏损超7%
            END IF;
        END;

        -- 5c. 检查持仓集中度
        DECLARE
            v_max_position_pct DECIMAL(8,4);
        BEGIN
            SELECT MAX(
                CASE WHEN v_robot.total_assets > 0
                THEN (vp.shares * COALESCE(kd.close, vp.cost_price)) / v_robot.total_assets
                ELSE 0 END
            ) INTO v_max_position_pct
            FROM v_robot_positions vp
            LEFT JOIN kline_daily kd ON kd.ts_code = vp.ts_code
                AND kd.trade_date = (SELECT MAX(trade_date) FROM kline_daily WHERE ts_code = vp.ts_code)
            WHERE vp.robot_id = v_robot.id;

            IF v_max_position_pct IS NOT NULL AND v_max_position_pct > 0.35 THEN
                v_risk_s := v_risk_s - 15;  -- 单票仓位超35%
            END IF;
        END;

        -- 5d. 连续亏损检查
        DECLARE
            v_consecutive_loss INT := 0;
        BEGIN
            SELECT COUNT(*) INTO v_consecutive_loss
            FROM (
                SELECT pnl_daily_pct,
                    ROW_NUMBER() OVER (ORDER BY trade_date DESC) AS rn
                FROM daily_nav
                WHERE robot_id = v_robot.id
                ORDER BY trade_date DESC
                LIMIT 10
            ) sub
            WHERE rn <= (
                -- 找到第一个非亏损日之前的连续亏损天数
                SELECT COALESCE(MIN(rn) - 1, 10)
                FROM (
                    SELECT pnl_daily_pct,
                        ROW_NUMBER() OVER (ORDER BY trade_date DESC) AS rn
                    FROM daily_nav
                    WHERE robot_id = v_robot.id
                    ORDER BY trade_date DESC
                    LIMIT 10
                ) inner_sub
                WHERE pnl_daily_pct >= 0
            );

            IF v_consecutive_loss >= 5 THEN
                v_risk_s := v_risk_s - 20;
            ELSIF v_consecutive_loss >= 3 THEN
                v_risk_s := v_risk_s - 10;
            END IF;
        END;

        -- 下限保护
        v_risk_s := GREATEST(v_risk_s, 0);

        -- 6. 综合AHS评分 (加权平均)
        --    权重: 资金利用率15% + 胜率25% + 回撤25% + 稳定性15% + 风控20%
        v_ahs := ROUND(
            v_capital_util * 0.15 +
            v_win_rate_s   * 0.25 +
            v_drawdown_s   * 0.25 +
            v_consistency_s * 0.15 +
            v_risk_s       * 0.20,
            2
        );

        -- 构建明细JSON
        v_detail := jsonb_build_object(
            'capital_utilization', jsonb_build_object(
                'score', v_capital_util,
                'weight', 0.15,
                'util_pct', ROUND(v_util_pct, 4),
                'desc', '资金利用率评分'
            ),
            'win_rate', jsonb_build_object(
                'score', v_win_rate_s,
                'weight', 0.25,
                'win_rate', ROUND(v_win_rate, 4),
                'total_trades', v_robot.total_trades,
                'desc', '胜率评分'
            ),
            'drawdown', jsonb_build_object(
                'score', v_drawdown_s,
                'weight', 0.25,
                'max_drawdown', v_robot.max_drawdown,
                'desc', '回撤控制评分'
            ),
            'consistency', jsonb_build_object(
                'score', v_consistency_s,
                'weight', 0.15,
                'std_dev', v_std_dev,
                'data_points', COALESCE(array_length(v_recent_pnl, 1), 0),
                'desc', '收益稳定性评分'
            ),
            'risk_control', jsonb_build_object(
                'score', v_risk_s,
                'weight', 0.20,
                'desc', '风控综合评分'
            ),
            'calculated_at', NOW()
        );

        -- 7. 更新机器人
        UPDATE robots SET
            ahs_score = v_ahs,
            ahs_detail = v_detail,
            updated_at = NOW()
        WHERE id = v_robot.id;

        v_count := v_count + 1;
        RAISE NOTICE '机器人[%] % AHS=% (资金:%/胜率:%/回撤:%/稳定:%/风控:%)',
            v_robot.id, v_robot.name, v_ahs,
            v_capital_util, v_win_rate_s, v_drawdown_s, v_consistency_s, v_risk_s;

    END LOOP;

    RAISE NOTICE 'AHS计算完成，共处理 % 个机器人', v_count;
END;
$$;

COMMENT ON PROCEDURE sp_calc_ahs_score IS '计算机器人账户健康度(AHS)评分，综合资金利用率/胜率/回撤/稳定性/风控五个维度';


-- ============================================================
-- ============================================================
-- 第十二部分: 定期维护任务SQL(配合Celery调度)
-- ============================================================
-- ============================================================


-- ============================================================
-- 12.1 过期模拟交易自动关闭
-- ============================================================
CREATE OR REPLACE PROCEDURE sp_close_expired_sim_trades()
LANGUAGE plpgsql AS $$
DECLARE
    v_rec       RECORD;
    v_close     DECIMAL(12,4);
    v_count     INT := 0;
BEGIN
    FOR v_rec IN
        SELECT
            st.*,
            fn_trade_days_between(st.entry_date, CURRENT_DATE) AS actual_hold_days
        FROM sim_trades st
        WHERE st.status = 'OPEN'
          AND fn_trade_days_between(st.entry_date, CURRENT_DATE) >= st.max_hold_days
    LOOP
        -- 获取最新收盘价
        SELECT close INTO v_close
        FROM kline_daily
        WHERE ts_code = v_rec.ts_code
        ORDER BY trade_date DESC
        LIMIT 1;

        -- 更新模拟交易
        UPDATE sim_trades SET
            status = 'EXPIRED',
            exit_date = CURRENT_DATE,
            exit_price = v_close,
            exit_reason = format('持仓达到最大天数限制(%s天)', v_rec.max_hold_days),
            hold_days = v_rec.actual_hold_days,
            realized_pnl = CASE
                WHEN v_close IS NOT NULL AND v_rec.entry_price > 0
                THEN (v_close - v_rec.entry_price) * v_rec.shares
                ELSE NULL
            END,
            realized_pnl_pct = CASE
                WHEN v_close IS NOT NULL AND v_rec.entry_price > 0
                THEN ROUND((v_close - v_rec.entry_price) / v_rec.entry_price, 4)
                ELSE NULL
            END,
            updated_at = NOW()
        WHERE id = v_rec.id;

        v_count := v_count + 1;
    END LOOP;

    RAISE NOTICE '过期模拟交易关闭完成，共处理 % 条', v_count;
END;
$$;

COMMENT ON PROCEDURE sp_close_expired_sim_trades IS '关闭超过最大持仓天数的模拟交易';


-- ============================================================
-- 12.2 模拟交易止损止盈检查
-- ============================================================
CREATE OR REPLACE PROCEDURE sp_check_sim_trade_triggers()
LANGUAGE plpgsql AS $$
DECLARE
    v_rec       RECORD;
    v_close     DECIMAL(12,4);
    v_high      DECIMAL(12,4);
    v_low       DECIMAL(12,4);
    v_pnl_pct   DECIMAL(8,4);
    v_new_status sim_trade_status;
    v_reason    VARCHAR(256);
    v_count     INT := 0;
BEGIN
    FOR v_rec IN
        SELECT st.*
        FROM sim_trades st
        WHERE st.status = 'OPEN'
    LOOP
        -- 获取最新价格
        SELECT close, high, low INTO v_close, v_high, v_low
        FROM kline_daily
        WHERE ts_code = v_rec.ts_code
        ORDER BY trade_date DESC
        LIMIT 1;

        IF v_close IS NULL THEN
            CONTINUE;
        END IF;

        v_pnl_pct := ROUND((v_close - v_rec.entry_price) / v_rec.entry_price, 4);
        v_new_status := NULL;
        v_reason := NULL;

        -- 止损检查(用最低价)
        IF v_rec.stop_loss_price IS NOT NULL AND v_low <= v_rec.stop_loss_price THEN
            v_new_status := 'STOP_LOSS';
            v_close := v_rec.stop_loss_price;  -- 以止损价计算
            v_reason := format('触发止损: 最低价 %s <= 止损价 %s', v_low, v_rec.stop_loss_price);
        -- 止盈检查(用最高价)
        ELSIF v_rec.target_price IS NOT NULL AND v_high >= v_rec.target_price THEN
            v_new_status := 'TAKE_PROFIT';
            v_close := v_rec.target_price;  -- 以目标价计算
            v_reason := format('触发止盈: 最高价 %s >= 目标价 %s', v_high, v_rec.target_price);
        END IF;

        IF v_new_status IS NOT NULL THEN
            v_pnl_pct := ROUND((v_close - v_rec.entry_price) / v_rec.entry_price, 4);

            UPDATE sim_trades SET
                status = v_new_status,
                exit_date = CURRENT_DATE,
                exit_price = v_close,
                exit_reason = v_reason,
                hold_days = fn_trade_days_between(v_rec.entry_date, CURRENT_DATE),
                realized_pnl = ROUND((v_close - v_rec.entry_price) * v_rec.shares, 2),
                realized_pnl_pct = v_pnl_pct,
                updated_at = NOW()
            WHERE id = v_rec.id;

            -- 写入交易事件
            INSERT INTO trade_events (
                robot_id, event_type, event_level,
                ts_code, sim_trade_id,
                title, message, detail,
                trigger_type, trigger_value,
                trade_date
            ) VALUES (
                v_rec.robot_id,
                CASE v_new_status
                    WHEN 'STOP_LOSS' THEN 'STOP_LOSS'
                    WHEN 'TAKE_PROFIT' THEN 'TAKE_PROFIT'
                END,
                CASE v_new_status
                    WHEN 'STOP_LOSS' THEN 'WARN'
                    WHEN 'TAKE_PROFIT' THEN 'INFO'
                END,
                v_rec.ts_code,
                v_rec.id,
                format('模拟交易%s: %s(%s)',
                    CASE v_new_status WHEN 'STOP_LOSS' THEN '止损' ELSE '止盈' END,
                    v_rec.name, v_rec.ts_code),
                v_reason,
                jsonb_build_object(
                    'entry_price', v_rec.entry_price,
                    'exit_price', v_close,
                    'pnl_pct', v_pnl_pct,
                    'hold_days', fn_trade_days_between(v_rec.entry_date, CURRENT_DATE),
                    'stop_loss_price', v_rec.stop_loss_price,
                    'target_price', v_rec.target_price
                ),
                'RISK',
                jsonb_build_object('trigger', v_new_status::TEXT, 'price', v_close),
                CURRENT_DATE
            );

            v_count := v_count + 1;
        ELSE
            -- 更新最大浮盈/浮亏
            UPDATE sim_trades SET
                max_profit_pct = GREATEST(COALESCE(max_profit_pct, 0), v_pnl_pct),
                max_loss_pct = LEAST(COALESCE(max_loss_pct, 0), v_pnl_pct),
                updated_at = NOW()
            WHERE id = v_rec.id;
        END IF;

    END LOOP;

    RAISE NOTICE '模拟交易止损止盈检查完成，触发 % 条', v_count;
END;
$$;

COMMENT ON PROCEDURE sp_check_sim_trade_triggers IS '检查所有OPEN状态模拟交易的止损止盈条件';


-- ============================================================
-- 12.3 模拟交易每日快照生成
-- ============================================================
CREATE OR REPLACE PROCEDURE sp_generate_sim_snapshots(
    p_trade_date    DATE DEFAULT CURRENT_DATE
)
LANGUAGE plpgsql AS $$
DECLARE
    v_rec       RECORD;
    v_close     DECIMAL(12,4);
    v_open      DECIMAL(12,4);
    v_high      DECIMAL(12,4);
    v_low       DECIMAL(12,4);
    v_pct       DECIMAL(8,4);
    v_pnl_pct   DECIMAL(8,4);
    v_max_price DECIMAL(12,4);
    v_min_price DECIMAL(12,4);
    v_sentiment DECIMAL(6,2);
    v_count     INT := 0;
BEGIN
    -- 获取当日市场情绪
    SELECT sentiment_score INTO v_sentiment
    FROM market_sentiment WHERE trade_date = p_trade_date;

    FOR v_rec IN
        SELECT st.* FROM sim_trades st WHERE st.status = 'OPEN'
    LOOP
        -- 获取当日K线
        SELECT open, high, low, close, pct_change
        INTO v_open, v_high, v_low, v_close, v_pct
        FROM kline_daily
        WHERE ts_code = v_rec.ts_code AND trade_date = p_trade_date;

        IF v_close IS NULL THEN
            CONTINUE;  -- 当日无数据(停牌等)
        END IF;

        v_pnl_pct := ROUND((v_close - v_rec.entry_price) / v_rec.entry_price, 4);

        -- 入场以来极值
        SELECT MAX(high), MIN(low)
        INTO v_max_price, v_min_price
        FROM kline_daily
        WHERE ts_code = v_rec.ts_code
          AND trade_date >= v_rec.entry_date
          AND trade_date <= p_trade_date;

        INSERT INTO sim_trade_snapshots (
            sim_trade_id, robot_id, snapshot_date,
            current_price, open_price, high_price, low_price, pct_change,
            unrealized_pnl, unrealized_pnl_pct,
            max_price_since, min_price_since,
            max_pnl_pct, min_pnl_pct,
            hold_days,
            distance_to_target, distance_to_stop,
            market_sentiment
        ) VALUES (
            v_rec.id, v_rec.robot_id, p_trade_date,
            v_close, v_open, v_high, v_low, v_pct,
            ROUND((v_close - v_rec.entry_price) * v_rec.shares, 2),
            v_pnl_pct,
            v_max_price, v_min_price,
            CASE WHEN v_max_price IS NOT NULL
                THEN ROUND((v_max_price - v_rec.entry_price) / v_rec.entry_price, 4)
                ELSE NULL END,
            CASE WHEN v_min_price IS NOT NULL
                THEN ROUND((v_min_price - v_rec.entry_price) / v_rec.entry_price, 4)
                ELSE NULL END,
            fn_trade_days_between(v_rec.entry_date, p_trade_date),
            CASE WHEN v_rec.target_price IS NOT NULL AND v_close > 0
                THEN ROUND((v_rec.target_price - v_close) / v_close, 4)
                ELSE NULL END,
            CASE WHEN v_rec.stop_loss_price IS NOT NULL AND v_close > 0
                THEN ROUND((v_rec.stop_loss_price - v_close) / v_close, 4)
                ELSE NULL END,
            v_sentiment
        )
        ON CONFLICT (sim_trade_id, snapshot_date) DO UPDATE SET
            current_price = EXCLUDED.current_price,
            open_price = EXCLUDED.open_price,
            high_price = EXCLUDED.high_price,
            low_price = EXCLUDED.low_price,
            pct_change = EXCLUDED.pct_change,
            unrealized_pnl = EXCLUDED.unrealized_pnl,
            unrealized_pnl_pct = EXCLUDED.unrealized_pnl_pct,
            max_price_since = EXCLUDED.max_price_since,
            min_price_since = EXCLUDED.min_price_since,
            max_pnl_pct = EXCLUDED.max_pnl_pct,
            min_pnl_pct = EXCLUDED.min_pnl_pct,
            hold_days = EXCLUDED.hold_days,
            distance_to_target = EXCLUDED.distance_to_target,
            distance_to_stop = EXCLUDED.distance_to_stop,
            market_sentiment = EXCLUDED.market_sentiment;

        v_count := v_count + 1;
    END LOOP;

    RAISE NOTICE '模拟交易快照生成完成，共 % 条', v_count;
END;
$$;

COMMENT ON PROCEDURE sp_generate_sim_snapshots IS '为所有OPEN状态的模拟交易生成每日快照';


-- ============================================================
-- ============================================================
-- 第十三部分: 完整性校验查询(运维用)
-- ============================================================
-- ============================================================


-- ============================================================
-- 13.1 数据完整性检查
-- ============================================================
CREATE OR REPLACE FUNCTION fn_data_integrity_check()
RETURNS TABLE (
    check_name      VARCHAR(64),
    check_result    VARCHAR(16),
    detail          TEXT
)
LANGUAGE plpgsql AS $$
BEGIN
    -- 1. 孤立的龙虎榜席位(无对应汇总)
    RETURN QUERY
    SELECT
        '孤立席位数据'::VARCHAR(64),
        CASE WHEN COUNT(*) = 0 THEN 'PASS' ELSE 'FAIL' END::VARCHAR(16),
        format('发现 %s 条无对应lhb_stock_daily的席位记录', COUNT(*))::TEXT
    FROM lhb_seats ls
    WHERE NOT EXISTS (
        SELECT 1 FROM lhb_stock_daily lsd WHERE lsd.id = ls.stock_daily_id
    );

    -- 2. 机器人资金不一致
    RETURN QUERY
    SELECT
        '机器人资金一致性'::VARCHAR(64),
        CASE WHEN COUNT(*) = 0 THEN 'PASS' ELSE 'WARN' END::VARCHAR(16),
        format('发现 %s 个机器人cash>total_assets', COUNT(*))::TEXT
    FROM robots
    WHERE cash > total_assets AND status = 'RUNNING';

    -- 3. 未计算的T+N价格
    RETURN QUERY
    SELECT
        'T+N价格计算完整性'::VARCHAR(64),
        CASE WHEN COUNT(*) = 0 THEN 'PASS' ELSE 'INFO' END::VARCHAR(16),
        format('发现 %s 条待计算的龙虎榜记录(10日前上榜)', COUNT(*))::TEXT
    FROM lhb_stock_daily lsd
    WHERE lsd.trade_date <= CURRENT_DATE - INTERVAL '10 days'
      AND NOT EXISTS (
          SELECT 1 FROM lhb_tn_price tn
          WHERE tn.stock_daily_id = lsd.id AND tn.n_day = 10 AND tn.calc_status = 'DONE'
      );

    -- 4. 交易记录与流水一致性
    RETURN QUERY
    SELECT
        '交易流水一致性'::VARCHAR(64),
        CASE WHEN COUNT(*) = 0 THEN 'PASS' ELSE 'WARN' END::VARCHAR(16),
        format('发现 %s 条交易无对应资金流水', COUNT(*))::TEXT
    FROM robot_trades rt
    WHERE NOT EXISTS (
        SELECT 1 FROM cash_ledger cl WHERE cl.trade_id = rt.id
    )
    AND rt.created_at < NOW() - INTERVAL '1 hour';  -- 排除刚创建的

    -- 5. 净值连续性
    RETURN QUERY
    SELECT
        '净值连续性'::VARCHAR(64),
        CASE WHEN COUNT(*) = 0 THEN 'PASS' ELSE 'WARN' END::VARCHAR(16),
        format('发现 %s 个机器人存在净值缺失(运行中)', COUNT(DISTINCT robot_id))::TEXT
    FROM (
        SELECT
            dn.robot_id,
            dn.trade_date,
            LEAD(dn.trade_date) OVER (PARTITION BY dn.robot_id ORDER BY dn.trade_date) AS next_date
        FROM daily_nav dn
        JOIN robots r ON r.id = dn.robot_id AND r.status = 'RUNNING'
    ) sub
    WHERE next_date IS NOT NULL
      AND fn_trade_days_between(sub.trade_date, sub.next_date) > 1;

    -- 6. stock_info 覆盖率
    RETURN QUERY
    SELECT
        'stock_info覆盖率'::VARCHAR(64),
        CASE WHEN COUNT(*) = 0 THEN 'PASS' ELSE 'WARN' END::VARCHAR(16),
        format('发现 %s 个龙虎榜股票不在stock_info中', COUNT(DISTINCT lsd.ts_code))::TEXT
    FROM lhb_stock_daily lsd
    WHERE NOT EXISTS (
        SELECT 1 FROM stock_info si WHERE si.ts_code = lsd.ts_code
    );

    RETURN;
END;
$$;

COMMENT ON FUNCTION fn_data_integrity_check IS '数据完整性检查，返回多项检查结果';


-- ============================================================
-- 13.2 系统统计概览
-- ============================================================
CREATE OR REPLACE FUNCTION fn_system_stats()
RETURNS TABLE (
    metric_name     VARCHAR(64),
    metric_value    BIGINT,
    description     TEXT
)
LANGUAGE plpgsql AS $$
BEGIN
    RETURN QUERY
    SELECT 'stock_info_count'::VARCHAR(64), COUNT(*)::BIGINT, '股票信息总数'::TEXT FROM stock_info WHERE is_active = TRUE
    UNION ALL
    SELECT 'kline_daily_count', COUNT(*), '日K线总记录数' FROM kline_daily
    UNION ALL
    SELECT 'lhb_stock_daily_count', COUNT(*), '龙虎榜汇总记录数' FROM lhb_stock_daily
    UNION ALL
    SELECT 'lhb_seats_count', COUNT(*), '龙虎榜席位记录数' FROM lhb_seats
    UNION ALL
    SELECT 'lhb_tn_price_count', COUNT(*), 'T+N价格记录数' FROM lhb_tn_price
    UNION ALL
    SELECT 'robot_count', COUNT(*), '机器人总数' FROM robots
    UNION ALL
    SELECT 'robot_running_count', COUNT(*), '运行中机器人' FROM robots WHERE status = 'RUNNING'
    UNION ALL
    SELECT 'trade_total_count', COUNT(*), '交易记录总数' FROM robot_trades
    UNION ALL
    SELECT 'sim_trade_open_count', COUNT(*), '进行中模拟交易' FROM sim_trades WHERE status = 'OPEN'
    UNION ALL
    SELECT 'strategy_count', COUNT(*), '策略总数' FROM strategies WHERE is_active = TRUE
    UNION ALL
    SELECT 'backtest_count', COUNT(*), '回测任务总数' FROM backtest_tasks
    UNION ALL
    SELECT 'trade_calendar_days', COUNT(*), '交易日历天数' FROM trade_calendar WHERE is_open = TRUE
    UNION ALL
    SELECT 'latest_kline_date', EXTRACT(EPOCH FROM MAX(trade_date))::BIGINT, '最新K线日期(epoch)' FROM kline_daily
    UNION ALL
    SELECT 'latest_lhb_date', EXTRACT(EPOCH FROM MAX(trade_date))::BIGINT, '最新龙虎榜日期(epoch)' FROM lhb_stock_daily
    UNION ALL
    SELECT 'error_log_24h', COUNT(*), '24小时内采集错误数' FROM collector_error_log WHERE created
    UNION ALL
    SELECT 'error_log_24h', COUNT(*), '24小时内采集错误数' FROM collector_error_log WHERE created_at > NOW() - INTERVAL '24 hours'
    UNION ALL
    SELECT 'unread_events', COUNT(*), '未读交易事件数' FROM trade_events WHERE is_read = FALSE
    UNION ALL
    SELECT 'circuit_open_count', COUNT(*), '当前熔断中的数据源' FROM datasource_circuit_log
        WHERE state = 'OPEN' AND created_at = (
            SELECT MAX(created_at) FROM datasource_circuit_log dcl2 WHERE dcl2.config_id = datasource_circuit_log.config_id
        );

    RETURN;
END;
$$;

COMMENT ON FUNCTION fn_system_stats IS '系统关键指标统计概览，用于仪表盘和监控';


-- ============================================================
-- ============================================================
-- 第十四部分: 性能优化建议(注释说明)
-- ============================================================
-- ============================================================

/*
=================================================================
 性能优化检查清单
=================================================================

1. 定期VACUUM & ANALYZE
   ────────────────────
   -- 建议每日凌晨执行
   VACUUM ANALYZE kline_daily;
   VACUUM ANALYZE lhb_stock_daily;
   VACUUM ANALYZE lhb_seats;
   VACUUM ANALYZE lhb_tn_price;
   VACUUM ANALYZE robot_trades;
   VACUUM ANALYZE daily_nav;

2. 慢查询监控
   ────────────
   -- 启用 pg_stat_statements
   CREATE EXTENSION IF NOT EXISTS pg_stat_statements;

   -- 查看Top10慢查询
   SELECT query, calls, mean_exec_time, total_exec_time
   FROM pg_stat_statements
   ORDER BY mean_exec_time DESC
   LIMIT 10;

3. 索引使用率检查
   ────────────────
   -- 找出未使用的索引
   SELECT
       schemaname, tablename, indexname,
       idx_scan, idx_tup_read, idx_tup_fetch,
       pg_size_pretty(pg_relation_size(indexrelid)) AS index_size
   FROM pg_stat_user_indexes
   WHERE idx_scan = 0
   ORDER BY pg_relation_size(indexrelid) DESC;

   -- 找出缺少索引的表(顺序扫描过多)
   SELECT
       schemaname, relname,
       seq_scan, seq_tup_read,
       idx_scan, idx_tup_fetch,
       CASE WHEN seq_scan > 0
           THEN ROUND(seq_tup_read::DECIMAL / seq_scan)
           ELSE 0
       END AS avg_seq_tup
   FROM pg_stat_user_tables
   WHERE seq_scan > 100
   ORDER BY seq_tup_read DESC;

4. 表膨胀检查
   ──────────
   SELECT
       schemaname, tablename,
       pg_size_pretty(pg_total_relation_size(schemaname || '.' || tablename)) AS total_size,
       pg_size_pretty(pg_relation_size(schemaname || '.' || tablename)) AS table_size,
       pg_size_pretty(pg_indexes_size(schemaname || '.' || tablename)) AS index_size,
       n_live_tup, n_dead_tup,
       CASE WHEN n_live_tup > 0
           THEN ROUND(n_dead_tup::DECIMAL / n_live_tup * 100, 2)
           ELSE 0
       END AS dead_pct
   FROM pg_stat_user_tables
   ORDER BY pg_total_relation_size(schemaname || '.' || tablename) DESC;

5. 连接池配置建议
   ────────────────
   -- PostgreSQL max_connections: 100-200
   -- 应用层使用连接池(SQLAlchemy pool_size=20, max_overflow=10)
   -- 或使用 PgBouncer 作为中间件

6. 分区表启用条件
   ────────────────
   -- kline_daily > 5000万行时考虑按年分区
   -- lhb_stock_daily > 1000万行时考虑按年分区
   -- robot_trades > 500万行时考虑按月分区

=================================================================
*/


-- ============================================================
-- ============================================================
-- 第十五部分: 汇总校验
-- ============================================================
-- ============================================================

-- 打印所有创建的对象
DO $$
DECLARE
    v_tables    INT;
    v_views     INT;
    v_indexes   INT;
    v_functions INT;
    v_procedures INT;
    v_triggers  INT;
    v_types     INT;
BEGIN
    SELECT COUNT(*) INTO v_tables FROM information_schema.tables
        WHERE table_schema = 'public' AND table_type = 'BASE TABLE';
    SELECT COUNT(*) INTO v_views FROM information_schema.views
        WHERE table_schema = 'public';
    SELECT COUNT(*) INTO v_indexes FROM pg_indexes
        WHERE schemaname = 'public';
    SELECT COUNT(*) INTO v_functions FROM information_schema.routines
        WHERE routine_schema = 'public' AND routine_type = 'FUNCTION';
    SELECT COUNT(*) INTO v_procedures FROM information_schema.routines
        WHERE routine_schema = 'public' AND routine_type = 'PROCEDURE';
    SELECT COUNT(*) INTO v_triggers FROM information_schema.triggers
        WHERE trigger_schema = 'public';
    SELECT COUNT(*) INTO v_types FROM pg_type
        WHERE typnamespace = 'public'::regnamespace AND typtype = 'e';

    RAISE NOTICE '';
    RAISE NOTICE '╔══════════════════════════════════════════════════╗';
    RAISE NOTICE '║     龙虎榜量化系统 数据库初始化完成               ║';
    RAISE NOTICE '╠══════════════════════════════════════════════════╣';
    RAISE NOTICE '║  基础表:       % 张                              ║', LPAD(v_tables::TEXT, 3);
    RAISE NOTICE '║  视图:         % 个                              ║', LPAD(v_views::TEXT, 3);
    RAISE NOTICE '║  索引:         % 个                              ║', LPAD(v_indexes::TEXT, 3);
    RAISE NOTICE '║  函数:         % 个                              ║', LPAD(v_functions::TEXT, 3);
    RAISE NOTICE '║  存储过程:     % 个                              ║', LPAD(v_procedures::TEXT, 3);
    RAISE NOTICE '║  触发器:       % 个                              ║', LPAD(v_triggers::TEXT, 3);
    RAISE NOTICE '║  枚举类型:     % 个                              ║', LPAD(v_types::TEXT, 3);
    RAISE NOTICE '╠══════════════════════════════════════════════════╣';
    RAISE NOTICE '║  对象清单:                                        ║';
    RAISE NOTICE '║                                                    ║';
    RAISE NOTICE '║  表: stock_info, trade_calendar, app_config,       ║';
    RAISE NOTICE '║      strategies, strategy_params,                  ║';
    RAISE NOTICE '║      datasource_config, datasource_circuit_log,    ║';
    RAISE NOTICE '║      collector_error_log, audit_logs,              ║';
    RAISE NOTICE '║      kline_daily, lhb_stock_daily, lhb_seats,     ║';
    RAISE NOTICE '║      lhb_tn_price, lhb_agg_stats,                 ║';
    RAISE NOTICE '║      lhb_institution_stat, market_snapshots,       ║';
    RAISE NOTICE '║      market_sentiment, robots, robot_trades,       ║';
    RAISE NOTICE '║      trade_executions, cash_ledger, daily_nav,     ║';
    RAISE NOTICE '║      sim_trades, sim_trade_snapshots,              ║';
    RAISE NOTICE '║      trade_events, selection_logs,                 ║';
    RAISE NOTICE '║      backtest_tasks, backtest_results              ║';
    RAISE NOTICE '║                                                    ║';
    RAISE NOTICE '║  视图: v_robot_positions, v_robot_overview,        ║';
    RAISE NOTICE '║        v_lhb_signals, v_broker_profile,            ║';
    RAISE NOTICE '║        v_daily_pnl_analysis                        ║';
    RAISE NOTICE '║                                                    ║';
    RAISE NOTICE '║  函数: fn_calc_trade_cost, fn_calc_robot_nav,      ║';
    RAISE NOTICE '║        fn_next_trade_date, fn_is_trade_date,       ║';
    RAISE NOTICE '║        fn_trade_days_between,                      ║';
    RAISE NOTICE '║        fn_data_integrity_check, fn_system_stats    ║';
    RAISE NOTICE '║                                                    ║';
    RAISE NOTICE '║  存储过程: sp_calc_tn_prices,                      ║';
    RAISE NOTICE '║            sp_refresh_lhb_agg_stats,               ║';
    RAISE NOTICE '║            sp_daily_settlement,                    ║';
    RAISE NOTICE '║            sp_cleanup_old_data,                    ║';
    RAISE NOTICE '║            sp_calc_ahs_score,                      ║';
    RAISE NOTICE '║            sp_close_expired_sim_trades,            ║';
    RAISE NOTICE '║            sp_check_sim_trade_triggers,            ║';
    RAISE NOTICE '║            sp_generate_sim_snapshots               ║';
    RAISE NOTICE '╠══════════════════════════════════════════════════╣';
    RAISE NOTICE '║  Celery调度建议:                                   ║';
    RAISE NOTICE '║  ├─ 17:00 数据同步(K线/龙虎榜)                    ║';
    RAISE NOTICE '║  ├─ 17:30 sp_calc_tn_prices                       ║';
    RAISE NOTICE '║  ├─ 18:00 sp_refresh_lhb_agg_stats                ║';
    RAISE NOTICE '║  ├─ 18:50 Phase A 选股+信号                       ║';
    RAISE NOTICE '║  ├─ 09:35 Phase B 盘中执行                        ║';
    RAISE NOTICE '║  ├─ 10s   sp_check_sim_trade_triggers(盘中)        ║';
    RAISE NOTICE '║  ├─ 15:05 sp_daily_settlement                     ║';
    RAISE NOTICE '║  ├─ 15:10 sp_generate_sim_snapshots                ║';
    RAISE NOTICE '║  ├─ 15:15 sp_close_expired_sim_trades              ║';
    RAISE NOTICE '║  ├─ 20:00 sp_calc_ahs_score                       ║';
    RAISE NOTICE '║  └─ 02:00 sp_cleanup_old_data (每周一次)           ║';
    RAISE NOTICE '╚══════════════════════════════════════════════════╝';
END;
$$;
