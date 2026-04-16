# ============================================================
# 龙虎榜量化交易系统 v2 - 项目管理命令
# 用法: make <command>
# ============================================================

# 项目配置
PROJECT_NAME    := lhb-quant
COMPOSE_FILE    := docker-compose.yml
COMPOSE_CMD     := docker compose
BACKEND_PORT    := 8000
FRONTEND_PORT   := 3000
POSTGRES_PORT   := 5432
REDIS_PORT      := 6379

# 颜色
GREEN  := \033[0;32m
YELLOW := \033[1;33m
RED    := \033[0;31m
BLUE   := \033[0;34m
NC     := \033[0m

# 默认目标
.DEFAULT_GOAL := help

# ============================================================
# 帮助信息
# ============================================================

.PHONY: help
help: ## 显示帮助信息
	@echo ""
	@echo "$(BLUE)══════════════════════════════════════════$(NC)"
	@echo "$(BLUE)   龙虎榜量化交易系统 v2 - 管理命令$(NC)"
	@echo "$(BLUE)══════════════════════════════════════════$(NC)"
	@echo ""
	@echo "$(GREEN)启动/停止:$(NC)"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  $(YELLOW)make %-18s$(NC) %s\n", $$1, $$2}'
	@echo ""

# ============================================================
# 一键操作
# ============================================================

.PHONY: start
start: ## 一键启动全部服务
	@echo "$(GREEN)[启动] 开始启动全部服务...$(NC)"
	@$(MAKE) check-deps --no-print-directory
	@$(MAKE) check-env --no-print-directory
	@$(MAKE) check-ports --no-print-directory
	@$(MAKE) docker-up --no-print-directory
	@$(MAKE) wait-infra --no-print-directory
	@$(MAKE) db-init --no-print-directory
	@$(MAKE) app-start --no-print-directory
	@$(MAKE) health --no-print-directory
	@echo ""
	@echo "$(GREEN)════════════════════════════════════════$(NC)"
	@echo "$(GREEN)  全部服务启动完成!$(NC)"
	@echo "$(GREEN)  后端:  http://localhost:$(BACKEND_PORT)$(NC)"
	@echo "$(GREEN)  前端:  http://localhost:$(FRONTEND_PORT)$(NC)"
	@echo "$(GREEN)  API:   http://localhost:$(BACKEND_PORT)/docs$(NC)"
	@echo "$(GREEN)════════════════════════════════════════$(NC)"

.PHONY: stop
stop: ## 一键停止全部服务
	@echo "$(YELLOW)[停止] 开始停止全部服务...$(NC)"
	@$(MAKE) app-stop --no-print-directory
	@$(MAKE) docker-down --no-print-directory
	@echo "$(GREEN)[完成] 全部服务已停止$(NC)"

.PHONY: restart
restart: ## 一键重启全部服务
	@echo "$(YELLOW)[重启] 开始重启全部服务...$(NC)"
	@$(MAKE) stop --no-print-directory
	@sleep 2
	@$(MAKE) start --no-print-directory

.PHONY: status
status: ## 查看全部服务状态
	@echo "$(BLUE)[状态] 服务状态概览$(NC)"
	@echo ""
	@echo "$(BLUE)── Docker 容器 ──$(NC)"
	@$(COMPOSE_CMD) -f $(COMPOSE_FILE) ps 2>/dev/null || echo "  Docker Compose 未启动"
	@echo ""
	@echo "$(BLUE)── 端口占用 ──$(NC)"
	@printf "  PostgreSQL (:$(POSTGRES_PORT))  "; \
		lsof -i :$(POSTGRES_PORT) -sTCP:LISTEN > /dev/null 2>&1 && echo "$(GREEN)运行中$(NC)" || echo "$(RED)未运行$(NC)"
	@printf "  Redis      (:$(REDIS_PORT))  "; \
		lsof -i :$(REDIS_PORT) -sTCP:LISTEN > /dev/null 2>&1 && echo "$(GREEN)运行中$(NC)" || echo "$(RED)未运行$(NC)"
	@printf "  Backend    (:$(BACKEND_PORT))  "; \
		lsof -i :$(BACKEND_PORT) -sTCP:LISTEN > /dev/null 2>&1 && echo "$(GREEN)运行中$(NC)" || echo "$(RED)未运行$(NC)"
	@printf "  Frontend   (:$(FRONTEND_PORT))  "; \
		lsof -i :$(FRONTEND_PORT) -sTCP:LISTEN > /dev/null 2>&1 && echo "$(GREEN)运行中$(NC)" || echo "$(RED)未运行$(NC)"
	@echo ""
	@echo "$(BLUE)── 健康检查 ──$(NC)"
	@curl -sf http://localhost:$(BACKEND_PORT)/api/v1/system/health 2>/dev/null | \
		python3 -m json.tool 2>/dev/null || echo "  后端未响应"

# ============================================================
# 环境检查
# ============================================================

.PHONY: check-deps
check-deps: ## 检查系统依赖
	@echo "$(BLUE)[检查] 系统依赖$(NC)"
	@# Docker
	@printf "  Docker          "; \
		command -v docker > /dev/null 2>&1 && \
		echo "$(GREEN)✓ $$(docker --version | grep -oP '\d+\.\d+\.\d+')$(NC)" || \
		(echo "$(RED)✗ 未安装$(NC)" && exit 1)
	@# Docker Compose
	@printf "  Docker Compose  "; \
		$(COMPOSE_CMD) version > /dev/null 2>&1 && \
		echo "$(GREEN)✓ $$($(COMPOSE_CMD) version --short 2>/dev/null)$(NC)" || \
		(echo "$(RED)✗ 未安装$(NC)" && exit 1)
	@# Docker 是否运行
	@printf "  Docker Daemon   "; \
		docker info > /dev/null 2>&1 && \
		echo "$(GREEN)✓ 运行中$(NC)" || \
		(echo "$(RED)✗ 未运行$(NC)" && exit 1)
	@# Git
	@printf "  Git             "; \
		command -v git > /dev/null 2>&1 && \
		echo "$(GREEN)✓$(NC)" || echo "$(YELLOW)✗ 可选$(NC)"
	@# lsof
	@printf "  lsof            "; \
		command -v lsof > /dev/null 2>&1 && \
		echo "$(GREEN)✓$(NC)" || echo "$(YELLOW)✗ 可选$(NC)"

.PHONY: check-env
check-env: ## 检查环境配置文件
	@echo "$(BLUE)[检查] 环境配置$(NC)"
	@if [ -f .env ]; then \
		echo "  .env 文件 $(GREEN)✓$(NC)"; \
		for key in POSTGRES_PASSWORD REDIS_URL; do \
			val=$$(grep "^$$key=" .env | cut -d'=' -f2-); \
			if [ -z "$$val" ] || [ "$$val" = '""' ]; then \
				echo "  $(YELLOW)⚠ $$key 未配置$(NC)"; \
			fi; \
		done; \
	else \
		echo "  $(YELLOW).env 不存在，从模板创建...$(NC)"; \
		cp .env.example .env 2>/dev/null || \
			(echo "$(RED).env.example 也不存在$(NC)" && exit 1); \
		echo "  $(YELLOW)请编辑 .env 填写实际配置后重新启动$(NC)"; \
		exit 1; \
	fi

.PHONY: check-ports
check-ports: ## 检查端口冲突
	@echo "$(BLUE)[检查] 端口占用$(NC)"
	@conflict=0; \
	for port in $(POSTGRES_PORT) $(REDIS_PORT) $(BACKEND_PORT) $(FRONTEND_PORT); do \
		if lsof -i :$$port -sTCP:LISTEN > /dev/null 2>&1; then \
			pid=$$(lsof -i :$$port -sTCP:LISTEN -t 2>/dev/null | head -1); \
			pname=$$(ps -p $$pid -o comm= 2>/dev/null); \
			echo "  $(YELLOW)⚠ 端口 $$port 被占用 (PID:$$pid $$pname)$(NC)"; \
			conflict=1; \
		else \
			echo "  端口 $$port $(GREEN)空闲$(NC)"; \
		fi; \
	done; \
	if [ $$conflict -eq 1 ]; then \
		echo ""; \
		echo "  $(YELLOW)检测到端口冲突，可执行以下操作:$(NC)"; \
		echo "  $(YELLOW)  make kill-ports  释放所有占用端口$(NC)"; \
		echo "  $(YELLOW)  make stop        先停止旧服务$(NC)"; \
	fi

# ============================================================
# Docker 容器管理
# ============================================================

.PHONY: docker-up
docker-up: ## 启动 Docker 容器
	@echo "$(BLUE)[Docker] 启动容器$(NC)"
	@$(COMPOSE_CMD) -f $(COMPOSE_FILE) up -d
	@echo "  容器启动完成 $(GREEN)✓$(NC)"

.PHONY: docker-down
docker-down: ## 停止 Docker 容器
	@echo "$(BLUE)[Docker] 停止容器$(NC)"
	@$(COMPOSE_CMD) -f $(COMPOSE_FILE) down
	@echo "  容器停止完成 $(GREEN)✓$(NC)"

.PHONY: docker-rebuild
docker-rebuild: ## 重新构建并启动容器
	@echo "$(BLUE)[Docker] 重新构建$(NC)"
	@$(COMPOSE_CMD) -f $(COMPOSE_FILE) down
	@$(COMPOSE_CMD) -f $(COMPOSE_FILE) build --no-cache
	@$(COMPOSE_CMD) -f $(COMPOSE_FILE) up -d
	@echo "  重新构建完成 $(GREEN)✓$(NC)"

.PHONY: docker-logs
docker-logs: ## 查看 Docker 容器日志
	@$(COMPOSE_CMD) -f $(COMPOSE_FILE) logs -f --tail=100

.PHONY: wait-infra
wait-infra: ## 等待基础设施就绪
	@echo "$(BLUE)[等待] 基础设施就绪$(NC)"
	@# 等待 PostgreSQL
	@printf "  PostgreSQL "; \
	for i in $$(seq 1 30); do \
		if $(COMPOSE_CMD) -f $(COMPOSE_FILE) exec -T postgres pg_isready -U postgres > /dev/null 2>&1; then \
			echo "$(GREEN)✓$(NC)"; \
			break; \
		fi; \
		printf "."; \
		sleep 1; \
		if [ $$i -eq 30 ]; then echo " $(RED)超时$(NC)"; exit 1; fi; \
	done
	@# 等待 Redis
	@printf "  Redis      "; \
	for i in $$(seq 1 15); do \
		if $(COMPOSE_CMD) -f $(COMPOSE_FILE) exec -T redis redis-cli ping 2>/dev/null | grep -q PONG; then \
			echo "$(GREEN)✓$(NC)"; \
			break; \
		fi; \
		printf "."; \
		sleep 1; \
		if [ $$i -eq 15 ]; then echo " $(RED)超时$(NC)"; exit 1; fi; \
	done

# ============================================================
# 数据库管理
# ============================================================

.PHONY: db-init
db-init: ## 初始化数据库（首次或迁移）
	@echo "$(BLUE)[数据库] 检查初始化状态$(NC)"
	@# 检查是否已初始化（通过检查 strategies 表是否存在）
	@if $(COMPOSE_CMD) -f $(COMPOSE_FILE) exec -T postgres \
		psql -U postgres -d lhb_quant -c "SELECT 1 FROM strategies LIMIT 1" > /dev/null 2>&1; then \
		echo "  数据库已初始化 $(GREEN)✓$(NC)"; \
		echo "  执行数据库迁移..."; \
		$(MAKE) db-migrate --no-print-directory; \
	else \
		echo "  首次初始化数据库..."; \
		$(MAKE) db-create --no-print-directory; \
		$(MAKE) db-migrate --no-print-directory; \
		$(MAKE) db-seed --no-print-directory; \
	fi

.PHONY: db-create
db-create: ## 创建数据库
	@echo "$(BLUE)[数据库] 创建数据库$(NC)"
	@$(COMPOSE_CMD) -f $(COMPOSE_FILE) exec -T postgres \
		psql -U postgres -c "CREATE DATABASE lhb_quant" 2>/dev/null || true
	@$(COMPOSE_CMD) -f $(COMPOSE_FILE) exec -T postgres \
		psql -U postgres -d lhb_quant -c "CREATE EXTENSION IF NOT EXISTS timescaledb" 2>/dev/null
	@echo "  数据库创建完成 $(GREEN)✓$(NC)"

.PHONY: db-migrate
db-migrate: ## 执行数据库迁移
	@echo "$(BLUE)[数据库] 执行迁移$(NC)"
	@$(COMPOSE_CMD) -f $(COMPOSE_FILE) exec -T backend \
		alembic upgrade head
	@echo "  迁移完成 $(GREEN)✓$(NC)"

.PHONY: db-seed
db-seed: ## 导入初始数据
	@echo "$(BLUE)[数据库] 导入初始数据$(NC)"
	@$(COMPOSE_CMD) -f $(COMPOSE_FILE) exec -T backend \
		python scripts/seed_data.py
	@echo "  初始数据导入完成 $(GREEN)✓$(NC)"

.PHONY: db-reset
db-reset: ## 重置数据库（危险：清除所有数据）
	@echo "$(RED)⚠ 即将删除并重建数据库，所有数据将丢失!$(NC)"
	@read -p "确认继续? 输入 YES: " confirm; \
	if [ "$$confirm" = "YES" ]; then \
		$(COMPOSE_CMD) -f $(COMPOSE_FILE) exec -T postgres \
			psql -U postgres -c "DROP DATABASE IF EXISTS lhb_quant"; \
		$(MAKE) db-create --no-print-directory; \
		$(MAKE) db-migrate --no-print-directory; \
		$(MAKE) db-seed --no-print-directory; \
		echo "$(GREEN)数据库重置完成$(NC)"; \
	else \
		echo "已取消"; \
	fi

.PHONY: db-backup
db-backup: ## 备份数据库
	@echo "$(BLUE)[数据库] 备份$(NC)"
	@mkdir -p backups
	@timestamp=$$(date +%Y%m%d_%H%M%S); \
	$(COMPOSE_CMD) -f $(COMPOSE_FILE) exec -T postgres \
		pg_dump -U postgres -d lhb_quant --format=custom \
		> "backups/lhb_quant_$$timestamp.dump"; \
	echo "  备份完成: backups/lhb_quant_$$timestamp.dump $(GREEN)✓$(NC)"

.PHONY: db-restore
db-restore: ## 恢复数据库（用法: make db-restore FILE=backups/xxx.dump）
	@if [ -z "$(FILE)" ]; then \
		echo "$(RED)请指定备份文件: make db-restore FILE=backups/xxx.dump$(NC)"; \
		echo "可用备份:"; \
		ls -la backups/*.dump 2>/dev/null || echo "  无备份文件"; \
		exit 1; \
	fi
	@echo "$(YELLOW)即将从 $(FILE) 恢复数据库$(NC)"
	@read -p "确认继续? [y/N]: " confirm; \
	if [ "$$confirm" = "y" ]; then \
		cat "$(FILE)" | $(COMPOSE_CMD) -f $(COMPOSE_FILE) exec -T postgres \
			pg_restore -U postgres -d lhb_quant --clean --if-exists; \
		echo "$(GREEN)恢复完成$(NC)"; \
	fi

.PHONY: db-shell
db-shell: ## 进入数据库命令行
	@$(COMPOSE_CMD) -f $(COMPOSE_FILE) exec postgres \
		psql -U postgres -d lhb_quant

# ============================================================
# 应用管理
# ============================================================

.PHONY: app-start
app-start: ## 启动应用服务（后端 + 前端 + Celery）
	@echo "$(BLUE)[应用] 启动服务$(NC)"
	@# 启动后端
	@$(MAKE) backend-start --no-print-directory
	@# 启动 Celery Worker
	@$(MAKE) celery-start --no-print-directory
	@# 启动 Celery Beat
	@$(MAKE) beat-start --no-print-directory
	@# 启动前端
	@$(MAKE) frontend-start --no-print-directory

.PHONY: app-stop
app-stop: ## 停止应用服务
	@echo "$(BLUE)[应用] 停止服务$(NC)"
	@$(MAKE) frontend-stop --no-print-directory
	@$(MAKE) beat-stop --no-print-directory
	@$(MAKE) celery-stop --no-print-directory
	@$(MAKE) backend-stop --no-print-directory

.PHONY: backend-start
backend-start: ## 启动后端
	@echo "  启动后端..."
	@if lsof -i :$(BACKEND_PORT) -sTCP:LISTEN > /dev/null 2>&1; then \
		echo "  $(YELLOW)后端已在运行，先停止...$(NC)"; \
		$(MAKE) backend-stop --no-print-directory; \
		sleep 1; \
	fi
	@cd backend && \
		nohup python -m uvicorn app.main:app \
			--host 0.0.0.0 \
			--port $(BACKEND_PORT) \
			--reload \
			> ../logs/backend.log 2>&1 & \
		echo $$! > ../pids/backend.pid
	@sleep 2
	@if lsof -i :$(BACKEND_PORT) -sTCP:LISTEN > /dev/null 2>&1; then \
		echo "  后端 :$(BACKEND_PORT) $(GREEN)✓$(NC)"; \
	else \
		echo "  后端启动失败 $(RED)✗$(NC)"; \
		echo "  查看日志: make logs-backend"; \
		exit 1; \
	fi

.PHONY: backend-stop
backend-stop: ## 停止后端
	@echo "  停止后端..."
	@if [ -f pids/backend.pid ]; then \
		pid=$$(cat pids/backend.pid); \
		if kill -0 $$pid 2>/dev/null; then \
			kill $$pid; \
			sleep 1; \
			kill -0 $$pid 2>/dev/null && kill -9 $$pid; \
		fi; \
		rm -f pids/backend.pid; \
	fi
	@# 兜底：按端口清理
	@lsof -i :$(BACKEND_PORT) -sTCP:LISTEN -t 2>/dev/null | xargs -r kill 2>/dev/null || true
	@echo "  后端已停止 $(GREEN)✓$(NC)"

.PHONY: celery-start
celery-start: ## 启动 Celery Worker
	@echo "  启动 Celery Worker..."
	@if [ -f pids/celery_worker.pid ] && kill -0 $$(cat pids/celery_worker.pid) 2>/dev/null; then \
		echo "  $(YELLOW)Celery Worker 已在运行，先停止...$(NC)"; \
		$(MAKE) celery-stop --no-print-directory; \
		sleep 1; \
	fi
	@cd backend && \
		nohup celery -A app.tasks.celery_app worker \
			--loglevel=info \
			--concurrency=4 \
			-Q trading,monitor,data,settlement \
			--pidfile=../pids/celery_worker.pid \
			> ../logs/celery_worker.log 2>&1 &
	@sleep 3
	@if [ -f pids/celery_worker.pid ] && kill -0 $$(cat pids/celery_worker.pid) 2>/dev/null; then \
		echo "  Celery Worker $(GREEN)✓$(NC)"; \
	else \
		echo "  Celery Worker 启动失败 $(RED)✗$(NC)"; \
		exit 1; \
	fi

.PHONY: celery-stop
celery-stop: ## 停止 Celery Worker
	@echo "  停止 Celery Worker..."
	@if [ -f pids/celery_worker.pid ]; then \
		pid=$$(cat pids/celery_worker.pid); \
		if kill -0 $$pid 2>/dev/null; then \
			kill -TERM $$pid; \
			for i in $$(seq 1 10); do \
				kill -0 $$pid 2>/dev/null || break; \
				sleep 1; \
			done; \
			kill -0 $$pid 2>/dev/null && kill -9 $$pid; \
		fi; \
		rm -f pids/celery_worker.pid; \
	fi
	@echo "  Celery Worker 已停止 $(GREEN)✓$(NC)"

.PHONY: beat-start
beat-start: ## 启动 Celery Beat 调度器
	@echo "  启动 Celery Beat..."
	@if [ -f pids/celery_beat.pid ] && kill -0 $$(cat pids/celery_beat.pid) 2>/dev/null; then \
		echo "  $(YELLOW)Celery Beat 已在运行，先停止...$(NC)"; \
		$(MAKE) beat-stop --no-print-directory; \
		sleep 1; \
	fi
	@cd backend && \
		nohup celery -A app.tasks.celery_app beat \
			--loglevel=info \
			--pidfile=../pids/celery_beat.pid \
			--schedule=../data/celerybeat-schedule \
			> ../logs/celery_beat.log 2>&1 &
	@sleep 2
	@if [ -f pids/celery_beat.pid ] && kill -0 $$(cat pids/celery_beat.pid) 2>/dev/null; then \
		echo "  Celery Beat $(GREEN)✓$(NC)"; \
	else \
		echo "  Celery Beat 启动失败 $(RED)✗$(NC)"; \
		exit 1; \
	fi

.PHONY: beat-stop
beat-stop: ## 停止 Celery Beat
	@echo "  停止 Celery Beat..."
	@if [ -f pids/celery_beat.pid ]; then \
		pid=$$(cat pids/celery_beat.pid); \
		if kill -0 $$pid 2>/dev/null; then \
			kill $$pid; \
			sleep 1; \
			kill -0 $$pid 2>/dev/null && kill -9 $$pid; \
		fi; \
		rm -f pids/celery_beat.pid; \
	fi
	@echo "  Celery Beat 已停止 $(GREEN)✓$(NC)"

.PHONY: frontend-start
frontend-start: ## 启动前端
	@echo "  启动前端..."
	@if lsof -i :$(FRONTEND_PORT) -sTCP:LISTEN > /dev/null 2>&1; then \
		echo "  $(YELLOW)前端已在运行，先停止...$(NC)"; \
		$(MAKE) frontend-stop --no-print-directory; \
		sleep 1; \
	fi
	@cd frontend && \
		nohup npm run dev -- --port $(FRONTEND_PORT) \
			> ../logs/frontend.log 2>&1 & \
		echo $$! > ../pids/frontend.pid
	@sleep 3
	@if lsof -i :$(FRONTEND_PORT) -sTCP:LISTEN > /dev/null 2>&1; then \
		echo "  前端 :$(FRONTEND_PORT) $(GREEN)✓$(NC)"; \
	else \
		echo "  前端启动失败 $(RED)✗$(NC)"; \
		exit 1; \
	fi

.PHONY: frontend-stop
frontend-stop: ## 停止前端
	@echo "  停止前端..."
	@if [ -f pids/frontend.pid ]; then \
		pid=$$(cat pids/frontend.pid); \
		if kill -0 $$pid 2>/dev/null; then \
			kill $$pid; \
			sleep 1; \
			kill -0 $$pid 2>/dev/null && kill -9 $$pid; \
		fi; \
		rm -f pids/frontend.pid; \
	fi
	@lsof -i :$(FRONTEND_PORT) -sTCP:LISTEN -t 2>/dev/null | xargs -r kill 2>/dev/null || true
	@echo "  前端已停止 $(GREEN)✓$(NC)"

# ============================================================
# 健康检查
# ============================================================

.PHONY: health
health: ## 健康检查
	@echo "$(BLUE)[健康检查]$(NC)"
	@pass=0; fail=0; \
	\
	printf "  PostgreSQL   "; \
	if $(COMPOSE_CMD) -f $(COMPOSE_FILE) exec -T postgres pg_isready -U postgres > /dev/null 2>&1; then \
		echo "$(GREEN)✓$(NC)"; pass=$$((pass+1)); \
	else \
		echo "$(RED)✗$(NC)"; fail=$$((fail+1)); \
	fi; \
	\
	printf "  Redis        "; \
	if $(COMPOSE_CMD) -f $(COMPOSE_FILE) exec -T redis redis-cli ping 2>/dev/null | grep -q PONG; then \
		echo "$(GREEN)✓$(NC)"; pass=$$((pass+1)); \
	else \
		echo "$(RED)✗$(NC)"; fail=$$((fail+1)); \
	fi; \
	\
	printf "  Backend API  "; \
	if curl -sf http://localhost:$(BACKEND_PORT)/api/v1/system/health > /dev/null 2>&1; then \
		echo "$(GREEN)✓$(NC)"; pass=$$((pass+1)); \
	else \
		echo "$(RED)✗$(NC)"; fail=$$((fail+1)); \
	fi; \
	\
	printf "  Frontend     "; \
	if curl -sf http://localhost:$(FRONTEND_PORT) > /dev/null 2>&1; then \
		echo "$(GREEN)✓$(NC)"; pass=$$((pass+1)); \
	else \
		echo "$(RED)✗$(NC)"; fail=$$((fail+1)); \
	fi; \
	\
	printf "  Celery       "; \
	if [ -f pids/celery_worker.pid ] && kill -0 $$(cat pids/celery_worker.pid) 2>/dev/null; then \
		echo "$(GREEN)✓$(NC)"; pass=$$((pass+1)); \
	else \
		echo "$(RED)✗$(NC)"; fail=$$((fail+1)); \
	fi; \
	\
	printf "  Celery Beat  "; \
	if [ -f pids/celery_beat.pid ] && kill -0 $$(cat pids/celery_beat.pid) 2>/dev/null; then \
		echo "$(GREEN)✓$(NC)"; pass=$$((pass+1)); \
	else \
		echo "$(RED)✗$(NC)"; fail=$$((fail+1)); \
	fi; \
	\
	echo ""; \
	echo "  结果: $(GREEN)$$pass 通过$(NC) / $(RED)$$fail 失败$(NC)"; \
	if [ $$fail -gt 0 ]; then exit 1; fi

# ============================================================
# 日志查看
# ============================================================

.PHONY: logs
logs: ## 查看全部日志（实时）
	@tail -f logs/*.log

.PHONY: logs-backend
logs-backend: ## 查看后端日志
	@tail -f logs/backend.log

.PHONY: logs-celery
logs-celery: ## 查看 Celery 日志
	@tail -f logs/celery_worker.log

.PHONY: logs-beat
logs-beat: ## 查看 Celery Beat 日志
	@tail -f logs/celery_beat.log

.PHONY: logs-frontend
logs-frontend: ## 查看前端日志
	@tail -f logs/frontend.log

.PHONY: logs-docker
logs-docker: ## 查看 Docker 容器日志
	@$(COMPOSE_CMD) -f $(COMPOSE_FILE) logs -f --tail=100

.PHONY: logs-postgres
logs-postgres: ## 查看 PostgreSQL 日志
	@$(COMPOSE_CMD) -f $(COMPOSE_FILE) logs -f postgres

.PHONY: logs-redis
logs-redis: ## 查看 Redis 日志
	@$(COMPOSE_CMD) -f $(COMPOSE_FILE) logs -f redis

# ============================================================
# 端口管理
# ============================================================

.PHONY: kill-ports
kill-ports: ## 强制释放所有占用端口
	@echo "$(YELLOW)[端口] 释放占用端口$(NC)"
	@for port in $(POSTGRES_PORT) $(REDIS_PORT) $(BACKEND_PORT) $(FRONTEND_PORT); do \
		pids=$$(lsof -i :$$port -sTCP:LISTEN -t 2>/dev/null); \
		if [ -n "$$pids" ]; then \
			echo "  端口 $$port: 终止进程 $$pids"; \
			echo "$$pids" | xargs kill -9 2>/dev/null; \
		else \
			echo "  端口 $$port: 空闲"; \
		fi; \
	done
	@echo "  端口释放完成 $(GREEN)✓$(NC)"

.PHONY: kill-port
kill-port: ## 释放指定端口（用法: make kill-port PORT=8000）
	@if [ -z "$(PORT)" ]; then \
		echo "$(RED)请指定端口: make kill-port PORT=8000$(NC)"; \
		exit 1; \
	fi
	@pids=$$(lsof -i :$(PORT) -sTCP:LISTEN -t 2>/dev/null); \
	if [ -n "$$pids" ]; then \
		echo "  终止端口 $(PORT) 上的进程: $$pids"; \
		echo "$$pids" | xargs kill -9 2>/dev/null; \
		echo "  $(GREEN)✓$(NC)"; \
	else \
		echo "  端口 $(PORT) 未被占用"; \
	fi

# ============================================================
# 开发工具
# ============================================================

.PHONY: dev
dev: ## 开发模式启动（仅基础设施 + 后端热重载）
	@echo "$(GREEN)[开发模式] 启动$(NC)"
	@$(MAKE) check-deps --no-print-directory
	@$(MAKE) check-env --no-print-directory
	@$(MAKE) docker-up --no-print-directory
	@$(MAKE) wait-infra --no-print-directory
	@$(MAKE) db-init --no-print-directory
	@echo "$(GREEN)基础设施就绪，手动启动后端:$(NC)"
	@echo "  cd backend && uvicorn app.main:app --reload --port $(BACKEND_PORT)"

.PHONY: test
test: ## 运行测试
	@echo "$(BLUE)[测试]$(NC)"
	@cd backend && python -m pytest tests/ -v --tb=short

.PHONY: test-cov
test-cov: ## 运行测试（含覆盖率）
	@cd backend && python -m pytest tests/ -v --cov=app --cov-report=html --cov-report=term

.PHONY: lint
lint: ## 代码检查
	@echo "$(BLUE)[Lint] 后端$(NC)"
	@cd backend && python -m ruff check app/
	@echo "$(BLUE)[Lint] 前端$(NC)"
	@cd frontend && npm run lint

.PHONY: format
format: ## 代码格式化
	@echo "$(BLUE)[Format] 后端$(NC)"
	@cd backend && python -m ruff format app/
	@echo "$(BLUE)[Format] 前端$(NC)"
	@cd frontend && npm run format

# ============================================================
# 数据操作
# ============================================================

.PHONY: data-sync
data-sync: ## 手动触发数据同步
	@echo "$(BLUE)[数据] 触发同步$(NC)"
	@curl -sf -X POST http://localhost:$(BACKEND_PORT)/api/v1/actions/sync \
		-H "Content-Type: application/json" \
		-d '{"categories": ["lhb_daily", "kline", "sentiment"]}' | \
		python3 -m json.tool
	@echo ""

.PHONY: data-backfill
data-backfill: ## 数据回填（用法: make data-backfill START=2025-01-01 END=2025-12-31）
	@if [ -z "$(START)" ] || [ -z "$(END)" ]; then \
		echo "$(RED)用法: make data-backfill START=2025-01-01 END=2025-12-31$(NC)"; \
		exit 1; \
	fi
	@echo "$(BLUE)[数据] 回填 $(START) ~ $(END)$(NC)"
	@curl -sf -X POST http://localhost:$(BACKEND_PORT)/api/v1/actions/backfill \
		-H "Content-Type: application/json" \
		-d "{\"category\": \"kline\", \"start_date\": \"$(START)\", \"end_date\": \"$(END)\"}" | \
		python3 -m json.tool

# ============================================================
# 清理
# ============================================================

.PHONY: clean
clean: ## 清理日志和临时文件
	@echo "$(BLUE)[清理]$(NC)"
	@rm -f logs/*.log
	@rm -f pids/*.pid
	@rm -f data/celerybeat-schedule
	@echo "  清理完成 $(GREEN)✓$(NC)"

.PHONY: clean-all
clean-all: stop clean ## 停止全部服务并清理
	@echo "$(BLUE)[深度清理]$(NC)"
	@$(COMPOSE_CMD) -f $(COMPOSE_FILE) down -v --remove-orphans
	@docker system prune -f
	@echo "  深度清理完成 $(GREEN)✓$(NC)"

# ============================================================
# 初始化目录
# ============================================================

$(shell mkdir -p logs pids backups data)
