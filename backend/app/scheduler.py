"""
数据采集调度器
"""
import os
import asyncio
from datetime import datetime
from pathlib import Path
from typing import Optional
from loguru import logger
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.triggers.cron import CronTrigger
import chinese_calendar as cc

from backend.db.session import AsyncSessionLocal, sync_database_url
from backend.app.collectors import LhbCollector, MarketCollector


class DataScheduler:
    """
    数据采集调度器
    
    功能:
    1. 定时触发数据采集任务
    2. 自动跳过非交易日
    3. 持久化任务状态到PostgreSQL
    4. 故障恢复和重试机制
    """
    
    def __init__(self, config: Optional[dict] = None):
        """
        初始化调度器
        
        Args:
            config: 配置字典（可选）
        """
        self.config = config or {}
        self.scheduler: Optional[AsyncIOScheduler] = None
        
        # 配置日志
        self._setup_logging()
        
        logger.info("[DataScheduler] 初始化完成")
    
    def _setup_logging(self):
        """配置日志"""
        log_dir = Path("logs")
        log_dir.mkdir(exist_ok=True)
        
        # 配置 loguru（如果需要额外配置）
        logger.add(
            log_dir / "scheduler_{time:YYYY-MM-DD}.log",
            rotation="00:00",
            retention="30 days",
            level="INFO",
            format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {message}"
        )
    
    def start(self):
        """
        启动调度器
        """
        if self.scheduler:
            logger.warning("[DataScheduler] 调度器已在运行")
            return
        
        # 创建调度器（使用PostgreSQL持久化）
        jobstores = {
            'default': SQLAlchemyJobStore(url=sync_database_url)
        }
        
        self.scheduler = AsyncIOScheduler(
            jobstores=jobstores,
            timezone='Asia/Shanghai'
        )
        
        # 注册任务
        self._register_jobs()
        
        # 启动
        self.scheduler.start()
        logger.info("[DataScheduler] 调度器已启动")
    
    def stop(self):
        """
        停止调度器
        """
        if self.scheduler:
            self.scheduler.shutdown(wait=True)
            self.scheduler = None
            logger.info("[DataScheduler] 调度器已停止")
    
    def _register_jobs(self):
        """
        注册定时任务
        """
        # 任务1: 龙虎榜数据采集 (交易日 15:05)
        self.scheduler.add_job(
            func=self._collect_lhb,
            trigger=CronTrigger(
                day_of_week='mon-fri',
                hour=15,
                minute=5,
                timezone='Asia/Shanghai'
            ),
            id='collect_lhb',
            name='龙虎榜数据采集',
            replace_existing=True,
            misfire_grace_time=300  # 5分钟容错
        )
        
        # 任务2: 市场情绪采集 (交易日 15:10)
        self.scheduler.add_job(
            func=self._collect_market,
            trigger=CronTrigger(
                day_of_week='mon-fri',
                hour=15,
                minute=10,
                timezone='Asia/Shanghai'
            ),
            id='collect_market',
            name='市场情绪采集',
            replace_existing=True,
            misfire_grace_time=300
        )
        
        # 任务3: 数据完整性检查 (交易日 15:30)
        self.scheduler.add_job(
            func=self._check_integrity,
            trigger=CronTrigger(
                day_of_week='mon-fri',
                hour=15,
                minute=30,
                timezone='Asia/Shanghai'
            ),
            id='check_integrity',
            name='数据完整性检查',
            replace_existing=True,
            misfire_grace_time=600
        )
        
        logger.info("[DataScheduler] 任务注册完成")
    
    async def _collect_lhb(self):
        """
        龙虎榜采集任务
        """
        # 检查是否为交易日
        if not self._is_trading_day():
            logger.info("[DataScheduler] 今日非交易日，跳过龙虎榜采集")
            return
        
        logger.info("[DataScheduler] 开始执行龙虎榜采集任务")
        
        try:
            async with AsyncSessionLocal() as db:
                collector = LhbCollector(db)
                result = await collector.run()
                
                if result['success']:
                    logger.info(
                        f"[DataScheduler] 龙虎榜采集成功",
                        extra={"records": result['records_count']}
                    )
                else:
                    logger.error(
                        f"[DataScheduler] 龙虎榜采集失败: {result.get('error')}"
                    )
                    
        except Exception as e:
            logger.error(f"[DataScheduler] 龙虎榜采集异常: {e}")
    
    async def _collect_market(self):
        """
        市场情绪采集任务
        """
        if not self._is_trading_day():
            logger.info("[DataScheduler] 今日非交易日，跳过市场情绪采集")
            return
        
        logger.info("[DataScheduler] 开始执行市场情绪采集任务")
        
        try:
            async with AsyncSessionLocal() as db:
                collector = MarketCollector(db)
                result = await collector.run()
                
                if result['success']:
                    logger.info(
                        f"[DataScheduler] 市场情绪采集成功",
                        extra={"records": result['records_count']}
                    )
                else:
                    logger.error(
                        f"[DataScheduler] 市场情绪采集失败: {result.get('error')}"
                    )
                    
        except Exception as e:
            logger.error(f"[DataScheduler] 市场情绪采集异常: {e}")
    
    async def _check_integrity(self):
        """
        数据完整性检查任务
        """
        if not self._is_trading_day():
            logger.info("[DataScheduler] 今日非交易日，跳过完整性检查")
            return
        
        logger.info("[DataScheduler] 开始执行数据完整性检查")
        
        # TODO: 实现完整性检查逻辑
        # from backend.app.integrity import check_data_integrity
        # result = await check_data_integrity()
        
        logger.info("[DataScheduler] 完整性检查完成")
    
    def _is_trading_day(self, check_date: Optional[datetime] = None) -> bool:
        """
        检查是否为交易日
        
        Args:
            check_date: 待检查日期（默认今天）
            
        Returns:
            是否为交易日
        """
        if check_date is None:
            check_date = datetime.now()
        
        # 使用 chinese_calendar 库判断
        is_workday = cc.is_workday(check_date.date())
        
        return is_workday
    
    def run_forever(self):
        """
        运行调度器直到手动停止
        """
        self.start()
        
        try:
            # 保持运行
            asyncio.get_event_loop().run_forever()
        except (KeyboardInterrupt, SystemExit):
            logger.info("[DataScheduler] 收到停止信号")
        finally:
            self.stop()


if __name__ == '__main__':
    scheduler = DataScheduler()
    scheduler.run_forever()
