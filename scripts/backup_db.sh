#!/bin/bash
# PostgreSQL 定期备份脚本
# crontab: 0 2 * * * /root/stock_analysis/scripts/backup_db.sh

set -e

# 配置
BACKUP_DIR="/root/stock_analysis/backups"
DB_NAME="stock_analysis"
DB_USER="stock_user"
KEEP_DAYS=30

# 创建备份目录
mkdir -p $BACKUP_DIR

# 生成文件名
DATE=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="$BACKUP_DIR/${DB_NAME}_${DATE}.sql.gz"

# 日志函数
log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') $1"
}

# 执行备份
log "🔄 开始备份数据库: $DB_NAME"

# 使用 pg_dump 备份并压缩
# 注意：需要在 ~/.pgpass 或环境变量中配置密码
if pg_dump -U $DB_USER $DB_NAME | gzip > $BACKUP_FILE; then
    SIZE=$(du -h $BACKUP_FILE | cut -f1)
    log "✅ 备份成功: $BACKUP_FILE ($SIZE)"
else
    log "❌ 备份失败"
    exit 1
fi

# 清理旧备份
log "🗑️  清理 $KEEP_DAYS 天前的旧备份..."
find $BACKUP_DIR -name "${DB_NAME}_*.sql.gz" -mtime +$KEEP_DAYS -delete

# 统计当前备份数量
BACKUP_COUNT=$(ls -1 $BACKUP_DIR/${DB_NAME}_*.sql.gz 2>/dev/null | wc -l)
log "📦 当前备份数量: $BACKUP_COUNT 个"

# 列出最近的备份
log "📋 最近备份:"
ls -lht $BACKUP_DIR/${DB_NAME}_*.sql.gz 2>/dev/null | head -5 | while read line; do
    log "  $line"
done

log "🎉 备份任务完成"
