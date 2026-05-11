-- Create T+N price calculation helper and procedure
-- Run: psql -h localhost -U lhb_admin -d lhb_quant -f create_tn_procedure.sql

-- First ensure fn_next_trade_date exists
CREATE OR REPLACE FUNCTION fn_next_trade_date(
    p_date      DATE,
    p_n         INT DEFAULT 1,
    p_exchange  VARCHAR(8) DEFAULT 'SSE'
)
RETURNS DATE
LANGUAGE plpgsql AS $$
DECLARE
    v_result    DATE;
BEGIN
    IF p_n = 0 THEN
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
        SELECT cal_date INTO v_result
        FROM trade_calendar
        WHERE exchange = p_exchange
          AND cal_date > p_date
          AND is_open = TRUE
        ORDER BY cal_date ASC
        LIMIT 1 OFFSET (p_n - 1);
    ELSE
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

-- Create the T+N price calculation procedure
CREATE OR REPLACE PROCEDURE sp_calc_tn_prices(
    p_trade_date DATE DEFAULT NULL
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
        FOREACH v_n IN ARRAY ARRAY[1, 2, 3, 5, 10]
        LOOP
            v_target_date := fn_next_trade_date(v_rec.trade_date, v_n);
            IF v_target_date IS NULL THEN
                CONTINUE;
            END IF;

            SELECT close INTO v_close
            FROM kline_daily
            WHERE ts_code = v_rec.ts_code AND trade_date = v_target_date;

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
                    FALSE,
                    FALSE,
                    'DONE'
                )
                ON CONFLICT (stock_daily_id, n_day) DO UPDATE SET
                    target_date = EXCLUDED.target_date,
                    close_price = EXCLUDED.close_price,
                    pnl_pct = EXCLUDED.pnl_pct,
                    calc_status = 'DONE';
                v_count := v_count + 1;
            END IF;
        END LOOP;
    END LOOP;
    RAISE NOTICE 'T+N price calculation done: % records', v_count;
END;
$$;
