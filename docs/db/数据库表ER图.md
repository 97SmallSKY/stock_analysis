
| 类别 | 数量 | 明细 |
|------|------|------|
| **枚举类型** | 11 | robot_status, trade_direction, trade_phase, exec_status, ledger_type, sim_trade_status, backtest_status, seat_direction, circuit_state, audit_action, data_category |
| **基础配置表** | 9 | stock_info, trade_calendar, app_config, strategies, strategy_params, datasource_config, datasource_circuit_log, collector_error_log, audit_logs |
| **市场数据表** | 8 | kline_daily, lhb_stock_daily, lhb_seats, lhb_tn_price, lhb_agg_stats, lhb_institution_stat, market_snapshots, market_sentiment |
| **交易相关表** | 7 | robots, robot_trades, trade_executions, cash_ledger, daily_nav, sim_trades, sim_trade_snapshots |
| **事件日志表** | 2 | trade_events, selection_logs |
| **回测表** | 2 | backtest_tasks, backtest_results |
| **视图** | 5 | v_robot_positions, v_robot_overview, v_lhb_signals, v_broker_profile, v_daily_pnl_analysis |
| **函数** | 7 | fn_calc_trade_cost, fn_calc_robot_nav, fn_next_trade_date, fn_is_trade_date, fn_trade_days_between, fn_data_integrity_check, fn_system_stats |
| **存储过程** | 8 | sp_calc_tn_prices, sp_refresh_lhb_agg_stats, sp_daily_settlement, sp_cleanup_old_data, sp_calc_ahs_score, sp_close_expired_sim_trades, sp_check_sim_trade_triggers, sp_generate_sim_snapshots |
| **触发器** | 14 | 1个交易统计 + 1个资金校验 + 12个auto_updated_at |
```angular2html

┌─────────────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│                                           交 易 相 关 表 (7张)                                                 │
│                                                                                                                 │
│  ┌─────────────────┐       ┌──────────────────┐       ┌───────────────────┐       ┌──────────────────┐         │
│  │    strategies    │       │     robots       │       │   robot_trades    │       │ trade_executions │         │
│  │─────────────────│       │──────────────────│       │───────────────────│       │──────────────────│         │
│  │ id (PK)         │◄──┐   │ id (PK)          │       │ id (PK)           │       │ id (PK)          │         │
│  │ name            │   │   │ name             │       │ robot_id (FK)─────│──┐    │ trade_id (FK)────│──┐      │
│  │ category        │   │   │ strategy_id (FK)─│──┘    │ ts_code (FK)──────│──│─┐  │ exec_price       │  │      │
│  │ entry_rules     │   │   │ initial_capital  │       │ direction         │  │ │  │ exec_shares      │  │      │
│  │ exit_rules      │   │   │ current_capital  │       │ signal_score      │  │ │  │ commission       │  │      │
│  │ risk_rules      │   │   │ cash             │       │ entry_price       │  │ │  │ slippage         │  │      │
│  │ version         │   │   │ status           │       │ exit_price        │  │ │  │ status           │  │      │
│  │ is_active       │   │   │ max_positions    │       │ shares            │  │ │  │ executed_at      │  │      │
│  └────────┬────────┘   │   │ risk_config(JSON)│       │ phase             │  │ │  └──────────────────┘  │      │
│           │            │   │ ahs_score        │       │ pnl_amount        │  │ │           ▲             │      │
│           │            │   │ created_at       │       │ pnl_pct           │  │ │           │             │      │
│           │            │   └──────┬───────────┘       │ trade_time        │  │ │           │             │      │
│           │            │          │                    └──────┬────────────┘  │ │           │             │      │
│           │            │          │                           │               │ │           └─────────────┘      │
│           │            │          │         ┌─────────────────┘               │ │                                │
│           │            │          │         │                                 │ │                                │
│           │            │          │         │              ┌──────────────────┘ │                                │
│           │            │          ▼         ▼              │                    │                                │
│           │            │   ┌──────────────────┐           │                    │                                │
│           │            │   │   cash_ledger    │           │                    │                                │
│           │            │   │──────────────────│           │                    │                                │
│           │            │   │ id (PK)          │           │                    │                                │
│           │            │   │ robot_id (FK)────│───────────│────────────────────│─── robots.id                   │
│           │            │   │ trade_id (FK)────│───────────│──── robot_trades.id│                                │
│           │            │   │ direction        │           │                    │                                │
│           │            │   │ amount           │           │                    │                                │
│           │            │   │ balance_after    │           │                    │                                │
│           │            │   │ reason           │           │                    │                                │
│           │            │   │ created_at       │           │                    │                                │
│           │            │   └──────────────────┘           │                    │                                │
│           │            │                                  │                    │                                │
│           │            │   ┌──────────────────┐           │                    │                                │
│           │            │   │    daily_nav     │           │                    │                                │
│           │            │   │──────────────────│           │                    │                                │
│           │            │   │ id (PK)          │           │                    │                                │
│           │            │   │ robot_id (FK)────│───────────│────────────────────│─── robots.id                   │
│           │            │   │ trade_date       │           │                    │                                │
│           │            │   │ net_value        │           │                    │                                │
│           │            │   │ total_assets     │           │                    │                                │
│           │            │   │ cash             │           │                    │                                │
│           │            │   │ position_value   │           │                    │                                │
│           │            │   │ pnl_daily_pct    │           │                    │                                │
│           │            │   │ drawdown         │           │                    │                                │
│           │            │   │ max_drawdown     │           │                    │                                │
│           │            │   └──────────────────┘           │                    │                                │
│           │            │                                  │                    │                                │
│           │            │   ┌──────────────────┐    ┌──────┴──────────────┐     │                                │
│           │            │   │  sim_trades      │    │sim_trade_snapshots │     │                                │
│           │            │   │──────────────────│    │─────────────────────│     │                                │
│           │            │   │ id (PK)          │◄───│ sim_trade_id (FK)  │     │                                │
│           │            │   │ robot_id (FK)────│────│                    │     │                                │
│           │            │   │ ts_code (FK)─────│──┐ │ snapshot_date      │     │                                │
│           │            │   │ entry_date       │  │ │ current_price      │     │                                │
│           │            │   │ entry_price      │  │ │ unrealized_pnl     │     │                                │
│           │            │   │ target_price     │  │ │ unrealized_pnl_pct │     │                                │
│           │            │   │ stop_loss_price  │  │ │ hold_days          │     │                                │
│           │            │   │ status           │  │ │ created_at         │     │                                │
│           │            │   │ closed_at        │  │ └─────────────────────┘     │                                │
│           │            │   │ realized_pnl     │  │                             │                                │
│           │            │   └──────────────────┘  │                             │                                │
│           │            │                         │                             │                                │
└───────────┼────────────┼─────────────────────────┼─────────────────────────────┼────────────────────────────────┘
            │            │                         │                             │
            │            │                         │                             │
┌───────────┼────────────┼─────────────────────────┼─────────────────────────────┼────────────────────────────────┐
│           │            │        市 场 数 据 表 (8张)                            │                                │
│           │            │                         │                             │                                │
│           │            │                         ▼                             │                                │
│           │            │   ┌──────────────────────────┐                        │                                │
│           │            │   │      stock_info          │                        │                                │
│           │            │   │──────────────────────────│                        │                                │
│           │            │   │ ts_code (PK)             │◄───────────────────────┘                                │
│           │            │   │ symbol                   │◄──── (被 robot_trades / sim_trades 等引用)              │
│           │            │   │ name                     │                                                         │
│           │            │   │ industry                 │         ┌──────────────────────┐                        │
│           │            │   │ market                   │◄────────│    kline_daily       │                        │
│           │            │   │ list_date                │         │──────────────────────│                        │
│           │            │   │ is_active                │         │ id (PK)              │                        │
│           │            │   └──────────┬───────────────┘         │ ts_code (FK)─────────│──── stock_info.ts_code │
│           │            │              │                         │ trade_date           │                        │
│           │            │              │                         │ open                 │                        │
│           │            │              │                         │ high                 │                        │
│           │            │              │                         │ low                  │                        │
│           │            │              │                         │ close                │                        │
│           │            │              │                         │ volume               │                        │
│           │            │              │                         │ amount               │                        │
│           │            │              │                         │ turnover_rate        │                        │
│           │            │              │                         │ pct_change           │                        │
│           │            │              │                         └──────────────────────┘                        │
│           │            │              │                                                                         │
│           │            │              │   ┌─────────────────────────────────────────────────────────────┐       │
│           │            │              │   │                 龙 虎 榜 数 据 组                            │       │
│           │            │              │   │                                                              │       │
│           │            │              │   │  ┌─────────────────────┐      ┌────────────────────────┐    │       │
│           │            │              ├──►│  │  lhb_stock_daily   │      │     lhb_seats          │    │       │
│           │            │              │   │  │─────────────────────│      │────────────────────────│    │       │
│           │            │              │   │  │ id (PK)            │◄─────│ stock_daily_id (FK)    │    │       │
│           │            │              │   │  │ ts_code (FK)───────│──┐   │ id (PK)                │    │       │
│           │            │              │   │  │ trade_date         │  │   │ ts_code                │    │       │
│           │            │              │   │  │ name               │  │   │ trade_date             │    │       │
│           │            │              │   │  │ close_price        │  │   │ broker_name            │    │       │
│           │            │              │   │  │ pct_change         │  │   │ buy_amount             │    │       │
│           │            │              │   │  │ turnover_rate      │  │   │ sell_amount            │    │       │
│           │            │              │   │  │ l_buy              │  │   │ net_amount             │    │       │
│           │            │              │   │  │ l_sell             │  │   │ direction              │    │       │
│           │            │              │   │  │ l_net              │  │   │ rank                   │    │       │
│           │            │              │   │  │ reason             │  │   └────────────────────────┘    │       │
│           │            │              │   │  │ score              │  │                                  │       │
│           │            │              │   │  │ score_detail(JSON) │  │   ┌────────────────────────┐    │       │
│           │            │              │   │  └─────────┬──────────┘  │   │    lhb_tn_price       │    │       │
│           │            │              │   │            │             │   │────────────────────────│    │       │
│           │            │              │   │            │             │   │ id (PK)                │    │       │
│           │            │              │   │            ├─────────────│──►│ stock_daily_id (FK)    │    │       │
│           │            │              │   │            │             │   │ ts_code                │    │       │
│           │            │              │   │            │             │   │ trade_date             │    │       │
│           │            │              │   │            │             │   │ n_day                  │    │       │
│           │            │              │   │            │             │   │ close_price            │    │       │
│           │            │              │   │            │             │   │ pnl_pct                │    │       │
│           │            │              │   │            │             │   │ high_pct               │    │       │
│           │            │              │   │            │             │   │ low_pct                │    │       │
│           │            │              │   │            │             │   │ is_limit_up            │    │       │
│           │            │              │   │            │             │   └────────────────────────┘    │       │
│           │            │              │   │            │             │                                  │       │
│           │            │              │   │            │             │   ┌────────────────────────┐    │       │
│           │            │              │   │            │             │   │   lhb_agg_stats       │    │       │
│           │            │              │   │            │             │   │────────────────────────│    │       │
│           │            │              │   │            │             │   │ id (PK)                │    │       │
│           │            │              │   │            └─────────────│──►│ ts_code (FK)           │    │       │
│           │            │              │   │                          │   │ stat_period            │    │       │
│           │            │              │   │                          │   │ lhb_count              │    │       │
│           │            │              │   │                          │   │ avg_score              │    │       │
│           │            │              │   │                          │   │ win_rate_1d            │    │       │
│           │            │              │   │                          │   │ win_rate_3d            │    │       │
│           │            │              │   │                          │   │ avg_pnl_1d             │    │       │
│           │            │              │   │                          │   │ avg_pnl_3d             │    │       │
│           │            │              │   │                          │   │ avg_l_net              │    │       │
│           │            │              │   │                          │   │ updated_at             │    │       │
│           │            │              │   │                          │   └────────────────────────┘    │       │
│           │            │              │   │                          │                                  │       │
│           │            │              │   │


```