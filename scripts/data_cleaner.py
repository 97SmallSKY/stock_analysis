"""
历史数据清理（节省磁盘空间）

清理策略：
- market_snapshots: 保留最近90天，更早的只保留日级别（最后一条）
- monitor_events: 保留最近180天
- elimination_records: 永久保留
"""
import sys
import logging
from pathlib import Path
from datetime import datetime, timedelta

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))

from utils.storage import storage
from sqlalchemy import text

logger = logging.getLogger(__name__)


def cleanup(
    keep_days_snapshots: int = 90,
    keep_days_events: int = 180,
    dry_run: bool = False
):
    """
    执行数据清理
    
    Args:
        keep_days_snapshots: 快照保留天数
        keep_days_events: 事件保留天数
        dry_run: 只统计不删除
    """
    engine = storage._get_engine()
    now = datetime.now()
    
    logger.info("=" * 50)
    logger.info("🧹 数据清理开始")
    logger.info("=" * 50)
    
    with engine.begin() as conn:
        # === 清理行情快照 ===
        cutoff_snapshots = (now - timedelta(days=keep_days_snapshots)).strftime('%Y%m%d')
        
        # 先统计要删除的数量
        count_before = conn.execute(text('''
            SELECT COUNT(*) FROM market_snapshots WHERE trade_date < :cutoff
        '''), {'cutoff': cutoff_snapshots}).scalar()
        
        logger.info(f"\n📊 行情快照:")
        logger.info(f"  - 保留最近 {keep_days_snapshots} 天")
        logger.info(f"  - 清理截止日期: {cutoff_snapshots}")
        logger.info(f"  - 待清理记录数: {count_before}")
        
        if not dry_run and count_before > 0:
            # 保留每天的最后一条快照，删除其余的
            deleted = conn.execute(text('''
                DELETE FROM market_snapshots
                WHERE trade_date < :cutoff
                  AND id NOT IN (
                      SELECT DISTINCT ON (stock_code, trade_date) id
                      FROM market_snapshots
                      WHERE trade_date < :cutoff
                      ORDER BY stock_code, trade_date, snapshot_time DESC
                  )
            '''), {'cutoff': cutoff_snapshots})
            
            logger.info(f"  ✅ 已删除 {deleted.rowcount} 条记录")
        
        # === 清理监控事件 ===
        cutoff_events = (now - timedelta(days=keep_days_events)).strftime('%Y%m%d')
        
        count_before_evt = conn.execute(text('''
            SELECT COUNT(*) FROM monitor_events WHERE trade_date < :cutoff
        '''), {'cutoff': cutoff_events}).scalar()
        
        logger.info(f"\n📋 监控事件:")
        logger.info(f"  - 保留最近 {keep_days_events} 天")
        logger.info(f"  - 清理截止日期: {cutoff_events}")
        logger.info(f"  - 待清理记录数: {count_before_evt}")
        
        if not dry_run and count_before_evt > 0:
            deleted = conn.execute(text('''
                DELETE FROM monitor_events WHERE trade_date < :cutoff
            '''), {'cutoff': cutoff_events})
            
            logger.info(f"  ✅ 已删除 {deleted.rowcount} 条记录")
        
        # === 清理淘汰记录（永久保留，只做统计） ===
        elimination_count = conn.execute(text('''
            SELECT COUNT(*) FROM elimination_records
        ''')).scalar()
        
        logger.info(f"\n🏆 淘汰记录:")
        logger.info(f"  - 永久保留: {elimination_count} 条")
    
    # === 统计表大小 ===
    logger.info("\n📈 表大小统计:")
    
    with engine.connect() as conn:
        for table in ['market_snapshots', 'monitor_events', 'elimination_records', 'dividend_records']:
            try:
                count = conn.execute(text(f'SELECT COUNT(*) FROM {table}')).scalar()
                logger.info(f"  - {table}: {count:,} 条")
            except Exception:
                logger.info(f"  - {table}: 表不存在")
    
    logger.info("\n" + "=" * 50)
    if dry_run:
        logger.info("🔍 模拟运行完成（未实际删除）")
    else:
        logger.info("✅ 数据清理完成")
    logger.info("=" * 50)


def vacuum_database():
    """执行 VACUUM 回收磁盘空间"""
    engine = storage._get_engine()
    
    logger.info("🧹 执行 VACUUM...")
    
    # PostgreSQL VACUUM 需要在 autocommit 模式下执行
    conn = engine.connect()
    conn.execution_options(isolation_level="AUTOCOMMIT")
    conn.execute(text('VACUUM ANALYZE'))
    conn.close()
    
    logger.info("✅ VACUUM 完成")


if __name__ == '__main__':
    import argparse
    
    parser = argparse.ArgumentParser(description='历史数据清理')
    parser.add_argument('--snapshots-days', type=int, default=90, help='快照保留天数')
    parser.add_argument('--events-days', type=int, default=180, help='事件保留天数')
    parser.add_argument('--dry-run', action='store_true', help='模拟运行')
    parser.add_argument('--vacuum', action='store_true', help='执行 VACUUM')
    
    args = parser.parse_args()
    
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(message)s'
    )
    
    cleanup(
        keep_days_snapshots=args.snapshots_days,
        keep_days_events=args.events_days,
        dry_run=args.dry_run,
    )
    
    if args.vacuum and not args.dry_run:
        vacuum_database()
