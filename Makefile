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

PYTHON ?= python
PIP    ?= pip

# 默认日期范围 —— 覆盖方式：make ingest-kline FROM=... TO=...
FROM ?= 20200101
TO   ?= $(shell date +%Y%m%d)
CODES ?= all

.PHONY: help install test lint clean \
        ingest-stock-info ingest-calendar ingest-kline ingest-lhb ingest-sentiment ingest-all \
        deploy restart healthcheck

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

ingest-sentiment: ## 抓取市场情绪 market_sentiment
	$(PYTHON) -m backend.cli ingest sentiment --from $(FROM) --to $(TO)

ingest-all: ingest-stock-info ingest-calendar ingest-kline ingest-lhb ingest-sentiment ## 一键全量灌库

# ---------- 评分 ----------
score: ## 批量计算龙虎榜评分 (FROM/TO 可选)
	$(PYTHON) -m backend.cli score --from $(FROM) --to $(TO)

score-empty: ## 仅对 score=0 的行评分(断点续跑)
	$(PYTHON) -m backend.cli score --from $(FROM) --to $(TO) --only-empty

# ---------- 部署（jd 服务器） ----------
deploy: ## ssh jd 拉代码 + 重启服务
	ssh jd "cd ~/stock_analysis && git pull && bash scripts/deploy.sh"

restart: ## 重启 api/worker/scheduler
	ssh jd "bash ~/stock_analysis/scripts/restart.sh"

healthcheck: ## 健康检查
	ssh jd "bash ~/stock_analysis/scripts/healthcheck.sh"
