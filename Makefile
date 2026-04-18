# ============================================================
# 龙虎榜量化交易系统 — Makefile
# ============================================================
# 常用：
#   make install                                    # 安装依赖
#   make test                                       # 跑单测
#   make ingest-stock-info                          # 股票清单
#   make ingest-calendar                            # 交易日历
#   make ingest-kline FROM=20200101 TO=20260417     # 全市场日K
#   make ingest-kline CODES=000001.SZ FROM=20200101 # 单只
#   make ingest-lhb FROM=20240101 TO=20260417       # 龙虎榜
#   make ingest-sentiment FROM=... TO=...           # 市场情绪
#   make ingest-all                                 # 一键全量
#   make deploy / make restart / make healthcheck
# ============================================================

SHELL := /bin/bash

PYTHON ?= python
PIP    ?= pip

# 默认日期范围 —— 覆盖方式：make ingest-kline FROM=... TO=...
FROM ?= 20200101
TO   ?= $(shell date +%Y%m%d)
CODES ?= all

# ---- 服务端口（stop/reset 按端口兜底 kill） ----
API_PORT      ?= 8000
VITE_PORT     ?= 5173
PG_PORT       ?= 5432
REDIS_PORT    ?= 6379
# 覆盖方式： make stop PORTS="8000 5173"
PORTS ?= $(API_PORT) $(VITE_PORT)

SSH_HOST ?= jd

.PHONY: help install test lint clean \
        ingest-stock-info ingest-calendar ingest-kline ingest-lhb ingest-sentiment ingest-all \
        score score-empty \
        dev-api dev-fe stop reset status ps logs \
        remote-stop remote-reset remote-status remote-logs \
        deploy restart healthcheck smoke

help:
	@grep -E '^[a-zA-Z_-]+:.*?##' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?##"}{printf "  \033[36m%-22s\033[0m %s\n",$$1,$$2}'

# ---------- 环境 ----------
install: ## 安装 Python 依赖
	$(PIP) install -r requirements.txt

test: ## 运行 pytest
	$(PYTHON) -m pytest -q

lint: ## 代码检查（ruff 可选）
	@ruff check backend tests || echo "ruff 未安装，跳过"

clean: ## 清理缓存
	rm -rf .pytest_cache .mypy_cache .ruff_cache
	find . -name "__pycache__" -type d -exec rm -rf {} +

# ---------- 数据抓取 ----------
ingest-stock-info: ## 刷新 stock_info (股票基础信息)
	$(PYTHON) -m backend.cli ingest stock-info

ingest-calendar: ## 刷新 trade_calendar (交易日历)
	$(PYTHON) -m backend.cli ingest calendar --from $(FROM) --to $(TO)

ingest-kline: ## 抓取历史日K线到 kline_daily (FROM/TO/CODES 可选)
	$(PYTHON) -m backend.cli ingest kline --from $(FROM) --to $(TO) --codes $(CODES)

ingest-lhb: ## 抓取龙虎榜 lhb_stock_daily (FROM/TO 可选)
	$(PYTHON) -m backend.cli ingest lhb --from $(FROM) --to $(TO)

ingest-lhb-seats: ## 仅回填 lhb_seats(主表已齐时用;FROM/TO 可选)
	$(PYTHON) -m backend.cli ingest lhb-seats --from $(FROM) --to $(TO)

ingest-sentiment: ## 抓取市场情绪 market_sentiment
	$(PYTHON) -m backend.cli ingest sentiment --from $(FROM) --to $(TO)

ingest-all: ingest-stock-info ingest-calendar ingest-kline ingest-lhb ingest-sentiment ## 一键全量灌库

# ---------- 评分 ----------
score: ## 批量计算龙虎榜评分 (FROM/TO 可选)
	$(PYTHON) -m backend.cli score --from $(FROM) --to $(TO)

score-empty: ## 仅对 score=0 的行评分(断点续跑)
	$(PYTHON) -m backend.cli score --from $(FROM) --to $(TO) --only-empty

# ---------- 本地 dev（前后端） ----------
dev-api: ## 本地启动 FastAPI (默认 :8000)
	uvicorn backend.api.main:app --host 0.0.0.0 --port $(API_PORT) --reload

dev-fe: ## 本地启动前端 vite (默认 :5173)
	cd frontend && npm run dev -- --port $(VITE_PORT)

# ---------- 本地 stop/reset（按端口强杀，不依赖 pm/systemctl） ----------
# 用法：
#   make stop                     # 杀 API_PORT + VITE_PORT
#   make stop PORTS="8000"        # 只杀 8000
#   make reset                    # = stop + 清 pids/ + 清 __pycache__
stop: ## 按端口强制 kill（PORTS 变量可覆盖）
	@for p in $(PORTS); do \
	  pids=$$(lsof -ti tcp:$$p 2>/dev/null); \
	  if [ -n "$$pids" ]; then \
	    echo "[stop] port $$p -> kill $$pids"; \
	    kill -9 $$pids || true; \
	  else \
	    echo "[stop] port $$p : no process"; \
	  fi; \
	done

reset: stop ## stop + 清 pids/ + 清 __pycache__（不动 DB）
	-rm -rf pids/*.pid 2>/dev/null
	find . -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null; true
	@echo "[reset] done"

status: ## 显示 API_PORT/VITE_PORT 占用
	@for p in $(PORTS); do \
	  owner=$$(lsof -iTCP:$$p -sTCP:LISTEN -Pn 2>/dev/null | awk 'NR>1{print $$1"(pid="$$2")"}' | paste -sd, -); \
	  printf "port %-5s : %s\n" "$$p" "$${owner:--}"; \
	done

ps: status ## 同 status 的别名

logs: ## tail 本地 logs/
	@tail -n 200 -f logs/*.log 2>/dev/null || echo "logs/ 为空"

# ---------- 远端 jd 服务器（先按 service 再按端口兜底） ----------
remote-status: ## jd 上服务与端口状态
	ssh $(SSH_HOST) 'systemctl --no-pager status stock_analysis_api | head -n 10; echo ---; ss -ltnp | grep -E ":($(API_PORT))\b" || echo "no listener on $(API_PORT)"'

remote-stop: ## jd 上停服务（先 systemctl，再按端口兜底 kill）
	ssh $(SSH_HOST) 'sudo systemctl stop stock_analysis_api || true; \
	  for p in $(API_PORT); do \
	    pids=$$(ss -ltnp 2>/dev/null | awk -v P=$$p "/:("P")\\b/ {print}" | grep -oE "pid=[0-9]+" | cut -d= -f2); \
	    [ -n "$$pids" ] && echo "[remote-stop] port $$p kill $$pids" && sudo kill -9 $$pids || echo "[remote-stop] $$p clean"; \
	  done'

remote-reset: remote-stop ## jd 上 stop + 重启 api
	ssh $(SSH_HOST) 'sudo systemctl start stock_analysis_api && sleep 2 && systemctl --no-pager status stock_analysis_api | head -n 5'

remote-logs: ## jd 上 journalctl 实时日志
	ssh $(SSH_HOST) 'journalctl -u stock_analysis_api -n 200 -f'

# ---------- 部署（jd 服务器） ----------
deploy: ## ssh jd 拉代码 + 重启服务
	ssh $(SSH_HOST) "cd ~/stock_analysis && git pull && bash scripts/deploy.sh"

restart: ## 重启 api/worker/scheduler
	ssh $(SSH_HOST) "bash ~/stock_analysis/scripts/restart.sh"

healthcheck: ## 健康检查
	ssh $(SSH_HOST) "bash ~/stock_analysis/scripts/healthcheck.sh"

smoke: ## 远端快速冒烟（dashboard/lhb/weekly 能否 200）
	@ssh $(SSH_HOST) bash -s < scripts/smoke_http.sh $(API_PORT)
