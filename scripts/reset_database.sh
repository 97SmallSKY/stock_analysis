#!/bin/bash
# ============================================================
# 龙虎榜量化交易系统 v2 - 数据库完全重置脚本
# 文件: scripts/reset_database.sh
# 说明: 删除现有数据库并重新初始化（用于生产环境）
# ============================================================

set -e  # 遇到错误立即退出

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# 从 .env 读取数据库配置
if [ -f .env ]; then
    export $(grep -v '^#' .env | grep -v '^$' | xargs)
else
    echo -e "${RED}错误: .env 文件不存在${NC}"
    exit 1
fi

# 构建数据库连接信息
DB_HOST="${PG_HOST}"
DB_PORT="${PG_PORT}"
DB_NAME="${PG_DATABASE}"
DB_USER="${PG_USER}"
DB_PASS="${PG_PASSWORD}"

echo -e "${BLUE}============================================================${NC}"
echo -e "${BLUE}  龙虎榜量化交易系统 v2 - 数据库完全重置${NC}"
echo -e "${BLUE}============================================================${NC}"
echo ""
echo -e "${YELLOW}⚠️  警告: 此操作将删除数据库 '${DB_NAME}' 及其所有数据！${NC}"
echo -e "${YELLOW}⚠️  目标服务器: ${DB_HOST}:${DB_PORT}${NC}"
echo ""
echo -e "是否继续? (输入 'YES' 确认删除): "
read -r CONFIRM

if [ "$CONFIRM" != "YES" ]; then
    echo -e "${RED}操作已取消${NC}"
    exit 0
fi

echo ""
echo -e "${BLUE}[步骤 1/6] 检查数据库连接...${NC}"
export PGPASSWORD="${DB_PASS}"

# 测试连接（连接到 postgres 默认数据库）
if psql -h "${DB_HOST}" -p "${DB_PORT}" -U "${DB_USER}" -d postgres -c "\l" > /dev/null 2>&1; then
    echo -e "${GREEN}✓ 数据库连接成功${NC}"
else
    echo -e "${RED}✗ 数据库连接失败，请检查配置${NC}"
    exit 1
fi

echo ""
echo -e "${BLUE}[步骤 2/6] 终止所有活动连接...${NC}"
psql -h "${DB_HOST}" -p "${DB_PORT}" -U "${DB_USER}" -d postgres << EOF
SELECT pg_terminate_backend(pid) 
FROM pg_stat_activity 
WHERE datname = '${DB_NAME}' AND pid <> pg_backend_pid();
EOF
echo -e "${GREEN}✓ 活动连接已终止${NC}"

echo ""
echo -e "${BLUE}[步骤 3/6] 删除现有数据库...${NC}"
psql -h "${DB_HOST}" -p "${DB_PORT}" -U "${DB_USER}" -d postgres << EOF
DROP DATABASE IF EXISTS ${DB_NAME};
EOF
echo -e "${GREEN}✓ 数据库 '${DB_NAME}' 已删除${NC}"

echo ""
echo -e "${BLUE}[步骤 4/6] 创建新数据库...${NC}"
psql -h "${DB_HOST}" -p "${DB_PORT}" -U "${DB_USER}" -d postgres << EOF
CREATE DATABASE ${DB_NAME} 
WITH OWNER = ${DB_USER}
     ENCODING = 'UTF8'
     LC_COLLATE = 'zh_CN.UTF-8'
     LC_CTYPE = 'zh_CN.UTF-8'
     TEMPLATE = template0;
EOF
echo -e "${GREEN}✓ 数据库 '${DB_NAME}' 已创建${NC}"

echo ""
echo -e "${BLUE}[步骤 5/6] 启用 TimescaleDB 扩展...${NC}"
psql -h "${DB_HOST}" -p "${DB_PORT}" -U "${DB_USER}" -d "${DB_NAME}" << EOF
CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;
EOF
echo -e "${GREEN}✓ TimescaleDB 扩展已启用${NC}"

echo ""
echo -e "${BLUE}[步骤 6/6] 执行迁移脚本...${NC}"

# 001: 创建表结构
echo -e "  → 执行 001_init_schema.sql"
psql -h "${DB_HOST}" -p "${DB_PORT}" -U "${DB_USER}" -d "${DB_NAME}" -f backend/db/migrations/001_init_schema.sql
echo -e "${GREEN}  ✓ 表结构创建完成${NC}"

# 002: 创建索引
echo -e "  → 执行 002_init_indexes.sql"
psql -h "${DB_HOST}" -p "${DB_PORT}" -U "${DB_USER}" -d "${DB_NAME}" -f backend/db/migrations/002_init_indexes.sql
echo -e "${GREEN}  ✓ 索引创建完成${NC}"

# 003: 创建触发器
echo -e "  → 执行 003_init_triggers.sql"
psql -h "${DB_HOST}" -p "${DB_PORT}" -U "${DB_USER}" -d "${DB_NAME}" -f backend/db/migrations/003_init_triggers.sql
echo -e "${GREEN}  ✓ 触发器创建完成${NC}"

# 004: 插入基础数据
echo -e "  → 执行 004_init_seed_data.sql"
psql -h "${DB_HOST}" -p "${DB_PORT}" -U "${DB_USER}" -d "${DB_NAME}" -f backend/db/migrations/004_init_seed_data.sql
echo -e "${GREEN}  ✓ 基础数据导入完成${NC}"

echo ""
echo -e "${BLUE}============================================================${NC}"
echo -e "${GREEN}  ✓ 数据库重置完成！${NC}"
echo -e "${BLUE}============================================================${NC}"
echo ""
echo -e "${GREEN}统计信息:${NC}"

# 查询表数量
TABLE_COUNT=$(psql -h "${DB_HOST}" -p "${DB_PORT}" -U "${DB_USER}" -d "${DB_NAME}" -t -c "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = 'public';")
echo -e "  - 表数量: ${TABLE_COUNT}"

# 查询索引数量
INDEX_COUNT=$(psql -h "${DB_HOST}" -p "${DB_PORT}" -U "${DB_USER}" -d "${DB_NAME}" -t -c "SELECT COUNT(*) FROM pg_indexes WHERE schemaname = 'public';")
echo -e "  - 索引数量: ${INDEX_COUNT}"

# 查询触发器数量
TRIGGER_COUNT=$(psql -h "${DB_HOST}" -p "${DB_PORT}" -U "${DB_USER}" -d "${DB_NAME}" -t -c "SELECT COUNT(*) FROM pg_trigger WHERE NOT tgisinternal;")
echo -e "  - 触发器数量: ${TRIGGER_COUNT}"

# 查询视图数量
VIEW_COUNT=$(psql -h "${DB_HOST}" -p "${DB_PORT}" -U "${DB_USER}" -d "${DB_NAME}" -t -c "SELECT COUNT(*) FROM information_schema.views WHERE table_schema = 'public';")
echo -e "  - 视图数量: ${VIEW_COUNT}"

echo ""
echo -e "${YELLOW}下一步操作:${NC}"
echo -e "  1. 运行数据采集器导入股票列表和交易日历"
echo -e "  2. 执行首次龙虎榜数据采集"
echo -e "  3. 启动定时调度器"
echo ""

# 清理 PGPASSWORD
unset PGPASSWORD
