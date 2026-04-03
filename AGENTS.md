# AGENTS.md — 龙虎榜交易策略分析系统

> Agent-facing reference. If you are a human reader, see `README.md` instead.
> Detailed business rules and development principles: `skill/总控 Prompt.md`.

---

## ⚠️ P0 — 每次修改后必须 rsync 上传并重启服务

**每完成一轮代码修改，必须执行以下两步，不可省略：**

```bash
# Step 1：rsync 同步改动文件到服务器（远程无 git，不能 git pull）
rsync -avz --exclude='__pycache__' --exclude='*.pyc' \
  pages/xxx.py jd:~/stock_analysis/pages/
# 若改动多个目录，同步整个项目（排除数据/日志/缓存）：
rsync -avz --exclude='__pycache__' --exclude='*.pyc' \
  --exclude='.git' --exclude='.claude/' --exclude='data/' --exclude='logs/' \
  ./ jd:~/stock_analysis/

# Step 2：重启 Streamlit 服务使改动生效
ssh jd "cd ~/stock_analysis && ./stop.sh && sleep 1 && STREAMLIT_PORT=8051 ./start.sh"
```

> 服务器别名: `ssh jd` | 路径: `~/stock_analysis` | Python: `/root/miniconda3/bin/python`
> **线上地址：http://117.72.184.8:8051**

---

## 1. Project Overview

A-share Dragon Tiger List (龙虎榜) quantitative trading analysis system with:
- Multi-factor stock selection (technical + capital + historical win-rate scoring)
- Simulated trading robot with LLM-assisted selection (Phase A/B execution)
- Streamlit dashboard for visualization
- PostgreSQL persistence with full audit trail

**Live URL:** http://117.72.184.8:8051
**Deployment:** Jingdong Cloud VPS (`ssh jd`, project at `~/stock_analysis`)

---

## 2. Repository Structure

```
stock_analysis/
├── app.py                          # Streamlit app entry point
├── AGENTS.md                       # This file
├── README.md                       # Human-readable documentation
│
├── modules/                        # Core algorithm engine (business logic lives here)
│   ├── scorer.py                   # CANONICAL scoring engine — all scores go through here
│   ├── market_data.py              # CANONICAL market data layer (K-line, T+N calendar)
│   ├── analyzer.py                 # Backtest framework (delegates to scorer + market_data)
│   ├── data_fetcher.py             # Dragon Tiger List / limit-up / fund flow data fetch
│   ├── feature_engine.py           # Feature calculation
│   ├── ms_scorer.py                # Momentum scoring (V×0.40 + P×0.35 + B×0.25)
│   └── realtime_fetcher.py         # Real-time quotes (3-tier fallback chain)
│
├── pages/                          # Streamlit UI layer — display only, NO business logic
│   ├── sim_trading.py              # Simulated trading main page + AHS dashboard
│   ├── lhb_signal.py               # Decision-assist signal page
│   ├── mainboard_strategy.py       # Mainboard multi-factor strategy
│   ├── dashboard.py                # Market overview
│   └── ...                         # Other pages (backtest, data_manager, etc.)
│
├── skills/stock_sim_robot/         # Automated trading robot
│   ├── SKILL.md                    # Skill trigger words and cron schedule
│   ├── fetch_signal.py             # Signal scoring (calls scorer)
│   ├── llm_select.py               # LLM stock selection + dynamic position sizing
│   ├── trade_runner.py             # Phase A/B execution core
│   ├── monitor_intraday.py         # Intraday monitoring (4 concurrent threads)
│   ├── sim_trade_db.py             # Trade DB ops + AHS scoring + circuit breaker
│   ├── summary_report.py           # Feishu daily report
│   └── trade_log.py                # Trade logging and audit
│
├── scripts/                        # Scheduled task entry points
│   ├── daily_update.py             # Daily 8-step data update
│   ├── sim_robot_run.py            # Robot entry (phase_a / phase_b)
│   └── backfill_features.py        # Historical feature backfill
│
├── utils/                          # Infrastructure
│   ├── storage.py                  # Storage abstraction (PostgreSQL / Excel auto-switch)
│   ├── retry.py                    # Retry + proxy-aware degradation
│   └── helpers.py
│
├── config/
│   └── settings.py                 # Central config
│
├── docs/                           # Technical documentation
│   ├── database.md                 # PostgreSQL schema reference (authoritative)
│   ├── architecture_plan.md        # Architecture roadmap
│   └── ...
│
└── skill/                          # Claude Code task templates
    └── 总控 Prompt.md              # Full working principles and business rules
```

---

## 3. Environment Setup

### Python & Dependencies
```bash
# Python 3.13.12 via miniconda (remote server)
/root/miniconda3/bin/python --version

# Install dependencies
pip install -r requirements.txt
```

### Required `.env` Variables

```env
# Storage mode: "postgres" (production) or "excel" (offline dev)
STORAGE_MODE=postgres

# PostgreSQL — production (VPS)
PG_HOST=127.0.0.1
PG_PORT=5432
PG_DATABASE=stock_analysis
PG_USER=stock_user
PG_PASSWORD=<see .env file>
PG_TABLE_PREFIX=

# LLM provider (OpenAI-compatible)
OPENAI_API_KEY=<or LLM_API_KEY>

# Feishu webhook for daily report
FEISHU_WEBHOOK=<url>
```

### Runtime Behavior Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `SKIP_CIRCUIT_BREAKER` | unset | Set to `1` to bypass circuit breaker (current run only) |
| `MONITOR_MAX_WORKERS` | `4` | Intraday monitor concurrency threads |
| `MONITOR_PRICELOG_MIN_CHANGE_PCT` | `0.2` | Min price change % to write price_log |
| `MAX_OPEN_POSITIONS` | `2` | Max new positions per Phase A run |
| `STORAGE_MODE` | `excel` (local) / `postgres` (server) | Storage backend |

### Local vs Remote Development

| Context | Connection | Python path |
|---------|-----------|-------------|
| Remote (production) | `ssh jd && cd ~/stock_analysis` | `/root/miniconda3/bin/python` |
| Local dev | `localhost:5432` | system python |

**Do not run git operations on the remote server** (risk of polluting company repos).

---

## 4. Common Commands

```bash
# ── Streamlit ──────────────────────────────────────────────────────────────
streamlit run app.py --server.port 8051

# Restart Streamlit (remote)
pkill -f "streamlit run"
nohup /root/miniconda3/bin/python -m streamlit run app.py \
  --server.port 8051 --server.address 0.0.0.0 \
  --server.headless true --browser.gatherUsageStats false \
  >> logs/streamlit.log 2>&1 &

# ── Data Updates ───────────────────────────────────────────────────────────
# Full daily update (8 steps, including volume ratio backfill)
/root/miniconda3/bin/python scripts/daily_update.py

# Only T+1 price fill (step 3)
/root/miniconda3/bin/python scripts/daily_update.py --step 3

# Historical feature backfill
/root/miniconda3/bin/python scripts/backfill_features.py

# ── Sim Robot ──────────────────────────────────────────────────────────────
# Phase A: post-market settlement + stock selection
/root/miniconda3/bin/python scripts/sim_robot_run.py phase_a

# Phase B: fill T+1 opening buy price
/root/miniconda3/bin/python scripts/sim_robot_run.py phase_b

# Intraday monitor (runs in foreground, normally triggered by cron)
/root/miniconda3/bin/python skills/stock_sim_robot/monitor_intraday.py

# ── Database ───────────────────────────────────────────────────────────────
# Connect to PostgreSQL (via Docker on VPS)
docker exec -it videofetcher-db psql -U stock_user -d stock_analysis

# Table stats
/root/miniconda3/bin/python -c "
import sys; sys.path.insert(0, '.')
from utils.storage import storage
print(storage.get_table_stats().to_string(index=False))
"

# Migrate local Excel → PostgreSQL
/root/miniconda3/bin/python utils/migrate.py --dry-run  # preview
/root/miniconda3/bin/python utils/migrate.py             # execute

# ── Logs ───────────────────────────────────────────────────────────────────
tail -f logs/streamlit.log
tail -f logs/daily_update.log
tail -f logs/sim_robot.log
tail -f logs/monitor_intraday.log

# ── Syntax Check (do this after every code change) ─────────────────────────
/root/miniconda3/bin/python -m py_compile <file_path>
```

---

## 5. Key Modules & Entry Points

### Canonical Scoring Engine — `modules/scorer.py`
**All scoring goes through here. Do not create parallel scoring implementations.**

```python
from modules.scorer import score_tech, score_fund, score_history

# Technical score (max 40pts): MACD, RSI, MA, volume; lianban_count adds up to +10
score_tech(kline_df, trade_date, lianban_count=0)

# Capital score (max 35pts): net buy strength, seat analysis, anti-churning detection
score_fund(lhb_row, seat_df, fund_flow)

# Historical win-rate score (max 25pts)
score_history(hist_dict)
```

### Canonical Market Data Layer — `modules/market_data.py`
**Only source for K-line data and trading calendar. Do not use iloc[1] or natural day+1.**

```python
from modules.market_data import get_kline, get_t2_prices, get_nth_trading_day

get_kline(code, trade_date, lookback=30)      # cached K-line
get_t2_prices(code, trade_date)               # T+2 price with retry
get_nth_trading_day(base_date, n)             # trading calendar
```

### Storage Abstraction — `utils/storage.py`
**All DB access goes through `storage`. Do not bypass with raw SQL unless using `storage._get_engine()`.**

```python
from utils.storage import storage

storage.read("lhb_top10")                          # full table read
storage.write(df, "lhb_top10", mode="append")      # date-dedup then overwrite
storage.query_by_date("lhb_top10_prices", "20260326")
storage.query("sim_trades", filters={"status": "open"})
```

### Sim Robot Execution — `skills/stock_sim_robot/trade_runner.py`
- `run_phase_a()` — Post-market: settle holdings + select new positions
- `run_phase_b()` — Morning: fill T+1 opening price for yesterday's selections

### Account Health Score + Circuit Breaker — `skills/stock_sim_robot/sim_trade_db.py`
- `calc_account_health_score()` — AHS 0~100
- `check_circuit_breaker()` — returns `(triggers: list[dict], summary: dict | None)`
- `get_performance_stats()` — recent win rate, avg PnL, consecutive losses

---

## 6. Database Schema

Database: `stock_analysis` (PostgreSQL, isolated from other services).
Full schema: `docs/database.md`.

### Key Tables

| Table | Purpose |
|-------|---------|
| `lhb_top10` | Daily Dragon Tiger List Top50 by net buy amount (`流通市值` unit = **万元**) |
| `lhb_top10_prices` | T+1/T+2/T+5/T+10 prices for lhb_top10 stocks |
| `lhb_detail` | Daily listing brief with volume and listing reason |
| `lhb_seat_detail` | Seat-level buy/sell details (includes `解读` field for churn detection) |
| `lhb_jigou` | Institutional seat aggregation |
| `market_sentiment` | Daily market sentiment indicators |
| `sim_trades` | Simulated trade records (T+1 buy, tiered sell, stop-loss, AHS, batch sell) |
| `sim_summary` | Weekly trade summary |
| `sim_trade_events` | Trade lifecycle events log |
| `sim_audit_log` | Audit trail (do not delete writes to this table) |
| `sim_selection_log` | Stock selection reasoning records |

### Critical Field Notes
- `lhb_top10.流通市值` — unit is **万元** (10,000 CNY), NOT 元. Misuse = 100x error.
- `trade_date` — always `YYYYMMDD` string format (join key across tables).
- Tables are auto-created on first write via SQLAlchemy `to_sql`.

---

## 7. Core Business Rules (Non-Negotiable)

These rules cannot be removed or bypassed. See `skill/总控 Prompt.md` for full context.

### Trading Mechanism
**T+1 Buy Mechanism**
- T-day: Analyze Dragon Tiger signals. Do NOT buy.
- T+1-day opening auction: Execute buy based on gap:
  - ≥+10% (locked limit-up) → abandon
  - >+7% → abandon (too risky)
  - +2%~+7% → buy at 50%~70% of planned position
  - -1%~+2% (flat open) → best entry, full planned position
  - 1%~3% gap-down → can buy if market-wide; cut to 50% if stock-specific
  - >3% gap-down → signal may be invalid, abandon
- If T+1 is suspended (null open price or zero volume) → signal void

**T+2 Sell Mechanism (tiered profit-taking)**
- +3% → sell 1st tranche (1/3 position)
- +5% → sell 2nd tranche (1/3 position)
- +8% → close all
- -5% stop-loss: execute immediately intraday, no hesitation, hard rule
- Max holding: 3 trading days (forced close at market)

**Transaction Costs (must be deducted in all backtests)**
- Buy commission: 0.025% + buy slippage: 0.10%
- Sell commission: 0.025% + stamp duty: 0.05% (sell only) + sell slippage: 0.05%
- **Total round-trip cost: ~0.25%**
- Use `analyzer.simulate_with_stoploss(roundtrip_cost=0.0025)`

### Account Health Score (AHS)
Scoring (base 50pts): last-10 win rate (±25) + avg PnL (±15) + consecutive loss penalty (max -20) + today PnL correction (±10)

| AHS Range | Mode | Position Multiplier |
|-----------|------|-------------------|
| ≥75 | Strong | ×1.2 |
| 60~74 | Normal | ×1.0 |
| 50~59 | Weak | ×0.75 |
| 30~49 | Recovery | ×0.5 |
| <30 | Critical | ×0.1 |

### Circuit Breaker Rules
Implemented in `sim_trade_db.check_circuit_breaker()`. **Do not skip this check in `run_phase_a()`.**

| Trigger | Action |
|---------|--------|
| Single-day loss > -3% | Stop all new positions today |
| Consecutive losses ≥ 3 | Skip today's opening |
| Weekly loss > -5% | Stop for remainder of week |
| Monthly loss > -8% | Stop for remainder of month |
| Recent 10-trade max drawdown > -15% | Full liquidation, halt strategy |

Manual override: `SKIP_CIRCUIT_BREAKER=1` (current run only, auto-expires next day).

---

## 8. Prohibited Actions

| # | Prohibited | Reason |
|---|-----------|--------|
| X1 | `df.iloc[1]` or natural day +1 for T+1 price | Suspended/holiday days produce lookahead bias |
| X2 | Omit transaction costs in backtest | Inflated gross returns, bad decisions |
| X3 | Remove or bypass stop-loss logic | Unlimited downside risk |
| X4 | `import as` top-level module inside a `render()` function body | UnboundLocalError scope bug |
| X5 | Use `include_groups=False` in `groupby().apply()` | Drops grouping column, downstream KeyError |
| X6 | Use `lhb_top10.流通市值` without confirming unit = 万元 | 100x magnitude error |
| X7 | Assume `check_circuit_breaker()` returns a list | Returns `tuple[list[dict], dict | None]` — must unpack |
| X8 | Run git operations in the openclaw repo directory | Contaminates company open-source repo |
| X9 | Output code without first presenting a plan and waiting for confirmation | Bypasses plan-first workflow |

---

## 9. Coding Conventions

1. **Minimal changes** — do not refactor unrelated modules.
2. **Pages are display-only** — business logic belongs in `modules/` or `utils/`.
3. **Scoring is centralized** — all score functions call `modules/scorer.py`. Never create parallel implementations.
4. **Market data is centralized** — always use `modules/market_data.py` for K-line and trading calendar.
5. **Storage is abstracted** — use `utils/storage.py`. Do not write raw SQL unless via `storage._get_engine()`.
6. Logging: use `loguru` with emoji conventions matching existing code.
7. AKShare/network calls: wrap with `utils/retry.safe_akshare_call()`.
8. DataFrames: validate field existence before access; use `pd.to_numeric(errors="coerce")` for numeric fields.
9. Dates: handle `YYYYMMDD` / `datetime` / `date` string formats uniformly.
10. New factors must have a fallback (return 0 score if field missing, do not break flow).
11. Both Excel and PostgreSQL storage modes must remain compatible.
12. Python 3.13 compatibility required.
13. DB schema changes: write documentation first, then code (`docs/database.md`).

---

## 10. Testing & Verification

There is no formal test framework. Use `test.py` for point verification.

**After scoring logic changes:**
- Compare backtest results before/after on the same date range.
- Check `modules/analyzer.simulate_with_stoploss()` output includes `return_rate` (net) and `return_rate_gross` (gross).

**After database changes:**
- Validate in a test PostgreSQL instance before production.
- Verify existing page queries still work (especially `pages/sim_trading.py`, `pages/lhb_signal.py`).

**After robot changes:**
- Syntax check: `/root/miniconda3/bin/python -m py_compile <file>`
- Check cron logs: `~/.cron_logs/` or `logs/sim_robot.log`
- End-to-end chain: Phase A → Phase B → Monitor → `sim_trades` / `sim_trade_events` / `sim_audit_log` / pages consistent.

**Verification order:**
1. `py_compile` — no syntax errors
2. SQL query — data written correctly
3. Page UI — displays expected results
4. Regression — adjacent modules not broken

---

## 11. Automated Tasks (Cron)

All cron jobs run on the remote VPS. Logs in `~/stock_analysis/logs/`.

| Schedule | Task | Script |
|----------|------|--------|
| `*/5 * * * *` | Streamlit keepalive | `scripts/start_stock.sh` |
| `55 14 workdays` | Pre-close sentiment refresh | `scripts/daily_update.py` |
| `5 9 workdays` | T+1 price fill (phase_b equivalent) | `scripts/daily_update.py --step 3` |
| `*/3 9-15 workdays` | Intraday monitor (4-thread, pushes Feishu alerts) | `skills/stock_sim_robot/monitor_intraday.py` |
| `40 18 workdays` | Full data update (8 steps, ~5min) | `scripts/daily_update.py` |
| `50 18 workdays` | Phase A: settle + select new positions | `scripts/sim_robot_run.py phase_a` |
| `@reboot` | Auto-start after server restart (2min delay) | `scripts/start_stock.sh` |

---

## 12. For Agents: How to Work

### Workflow (Four Steps)
1. **Understand** — Analyze: goal, affected files, DB schema impact, backtest/robot/page impact, old-data compatibility
2. **Propose** — Output: affected file list, change rationale, risk points, migration/backfill needs, minimal change path
3. **Code** — Only after confirmation: diff or full function replacement, SQL migrations, change notes
4. **Verify** — Always include: `py_compile` check, SQL query validation, page function test, regression impact check

### Key Constraints
- Propose plan before writing any code (rule X9).
- All scoring changes go through `modules/scorer.py` only.
- All trading calendar / T+N lookups go through `modules/market_data.py` only.
- Do not run git on the remote server.
- When in doubt about business rules, ask — do not assume T-day definitions, buy price source, or cost basis.

### Quick Reference: Where Things Live

| Question | Answer |
|----------|--------|
| Where is scoring logic? | `modules/scorer.py` |
| Where is K-line / T+N calendar? | `modules/market_data.py` |
| Where is AHS scoring? | `sim_trade_db.calc_account_health_score()` |
| Where is circuit breaker? | `sim_trade_db.check_circuit_breaker()` |
| Where is T+1 buy execution? | `skills/stock_sim_robot/trade_runner.run_phase_a()` |
| Where is backtest? | `modules/analyzer.simulate_with_stoploss()` |
| Where is DB schema? | `docs/database.md` |
| Where are full business rules? | `skill/总控 Prompt.md` |
| Where are known issues? | `docs/robot_issues.md` |
| Where is loss review log? | `docs/loss_lessons.md` |
