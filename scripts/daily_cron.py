"""
每日调度主入口

crontab 配置：
0  9  * * 1-5  cd ~/stock_analysis && /root/miniconda3/bin/python scripts/daily_cron.py dividend
35 9  * * 1-5  cd ~/stock_analysis && /root/miniconda3/bin/python scripts/daily_cron.py phase_b
45 18 * * 1-5  cd ~/stock_analysis && /root/miniconda3/bin/python scripts/daily_cron.py phase_a
5  15 * * 1-5  cd ~/stock_analysis && /root/miniconda3/bin/python scripts/daily_cron.py settle
0  2  * * *    cd ~/stock_analysis && /root/miniconda3/bin/python scripts/daily_cron.py backup
0  3  * * 0    cd ~/stock_analysis && /root/miniconda3/bin/python scripts/daily_cron.py cleanup
"""
import sys
import logging
import argparse
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

# 配置日志
log_dir = ROOT / 'logs'
log_dir.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[
        logging.FileHandler(log_dir / f'cron_{datetime.now().strftime("%Y%m%d")}.log'),
        logging.StreamHandler(),
    ]
)
logger = logging.getLogger('daily_cron')


def main():
    parser = argparse.ArgumentParser(description='每日调度任务')
    parser.add_argument('command', choices=[
        'dividend', 'phase_b', 'phase_a', 'settle', 'eliminate', 'backup', 'cleanup'
    ], help='执行的命令')
    parser.add_argument('trade_date', nargs='?', default=None, help='交易日期（可选）')
    parser.add_argument('--dry-run', action='store_true', help='模拟运行（仅用于 cleanup）')
    
    args = parser.parse_args()
    
    trade_date = args.trade_date or datetime.now().strftime('%Y%m%d')
    
    logger.info("=" * 60)
    logger.info(f"🚀 daily_cron: {args.command} | {trade_date}")
    logger.info("=" * 60)
    
    try:
        if args.command == 'dividend':
            # 除权分红处理（09:00）
            from engine.live.dividend_handler import run_dividend_handler
            run_dividend_handler(trade_date)
        
        elif args.command == 'phase_b':
            # Phase B 次日开盘买入（09:35）
            from skills.stock_sim_robot.trade_runner import run_phase_b
            run_phase_b(trade_date)

        elif args.command == 'phase_a':
            # Phase A 盘后选股（18:45）
            from skills.stock_sim_robot.trade_runner import run_phase_a
            run_phase_a(trade_date)
        
        elif args.command == 'settle':
            # 每日结算（15:05）
            from engine.live.daily_settlement import DailySettlement
            DailySettlement().run(trade_date)
            
            # 淘汰检查
            from engine.live.eliminator import run_elimination
            run_elimination(trade_date)
        
        elif args.command == 'eliminate':
            # 单独执行淘汰检查
            from engine.live.eliminator import run_elimination
            run_elimination(trade_date)
        
        elif args.command == 'backup':
            # 数据库备份（02:00）
            import subprocess
            backup_script = ROOT / 'scripts' / 'backup_db.sh'
            if backup_script.exists():
                subprocess.run(['bash', str(backup_script)], check=True)
            else:
                logger.warning("备份脚本不存在")
        
        elif args.command == 'cleanup':
            # 数据清理（每周日凌晨 03:00）
            from scripts.data_cleaner import cleanup, vacuum_database
            cleanup(
                dry_run=args.dry_run,
            )
            if not args.dry_run:
                vacuum_database()
        
        logger.info("=" * 60)
        logger.info(f"✅ {args.command} 执行完成")
        logger.info("=" * 60)
    
    except Exception as e:
        logger.error("=" * 60)
        logger.error(f"❌ {args.command} 执行失败: {e}")
        logger.error("=" * 60)
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
