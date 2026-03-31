# Stock Sim Robot Skill

## Overview

This skill provides automated stock trading simulation (模拟炒股) using data from the Dragon Tiger List (龙虎榜) analysis system.

## Trigger Keywords

- 模拟炒股
- sim_robot
- 今日信号
- 本周战报
- 持仓查看
- 模拟交易

## Commands

| Command | Script | Description |
|---------|--------|-------------|
| 模拟炒股 今日信号 | `python scripts/sim_robot_run.py signals` | View today's Top10 mainboard signals |
| 模拟炒股 持仓 | `python scripts/sim_robot_run.py query positions` | View current open positions |
| 模拟炒股 本周战报 | `python scripts/sim_robot_run.py query weekly` | Weekly P&L summary |
| 模拟炒股 历史 | `python scripts/sim_robot_run.py query history` | Recent 10 trades |

## Cron Jobs

| Schedule | Command | Description |
|----------|---------|-------------|
| 每周一至周五 18:45 | `python scripts/sim_robot_run.py phase_a` | 盘后：结算持仓 + 今日选股 |
| 每周一至周五 09:35 | `python scripts/sim_robot_run.py phase_b` | 开盘后：回填买入价 |

## Strategy Logic

1. **T Day (today)**: LLM selects the best mainboard stock from 龙虎榜 Top10
2. **T+1 Day**: Simulate buying at opening price (集合竞价)
3. **T+2 Day**: Simulate selling at listing price (挂牌卖出)
4. Stop-loss at -5% if T+2 close price < buy_price × 0.95

## Database Tables (stock_analysis library)

- `sim_trades` — Each simulated trade record
- `sim_summary` — Weekly P&L summary

## Setup

```bash
cd stock_analysis

# 1. Initialize tables
python scripts/sim_robot_run.py init

# 2. Test signal fetch
python scripts/sim_robot_run.py signals

# 3. Manual trigger phase_a (for testing)
python scripts/sim_robot_run.py phase_a
```

## Environment Variables

```env
# Required (existing stock_analysis config)
PG_HOST=117.72.184.8  # or localhost
PG_DATABASE=stock_analysis
PG_USER=stock_user
PG_PASSWORD=...

# Optional (for LLM-based selection)
OPENAI_API_KEY=your_api_key
OPENAI_BASE_URL=https://api.openai.com/v1  # or custom endpoint
LLM_MODEL=gpt-4o-mini
```
