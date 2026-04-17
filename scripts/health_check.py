#!/usr/bin/env python
"""
健康检查脚本

检查系统各个组件的健康状态
"""
import sys
import time
import asyncio
from datetime import datetime
from typing import Dict, List, Tuple
from loguru import logger

from backend import AsyncSessionLocal, engine
from backend import app_scheduler


class HealthChecker:
    """系统健康检查器"""
    
    def __init__(self):
        self.results: Dict[str, Tuple[bool, str]] = {}
        self.warnings: List[str] = []
        self.errors: List[str] = []
    
    def check_database_connection(self) -> Tuple[bool, str]:
        """检查数据库连接"""
        try:
            start_time = time.time()
            
            # 测试连接
            import psycopg2
            from backend import settings
            
            conn = psycopg2.connect(
                host=settings.PG_HOST,
                port=settings.PG_PORT,
                database=settings.PG_DATABASE,
                user=settings.PG_USER,
                password=settings.PG_PASSWORD
            )
            
            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            cursor.fetchone()
            
            latency = (time.time() - start_time) * 1000
            
            conn.close()
            
            if latency < 100:
                return True, f"正常 (延迟: {latency:.0f}ms)"
            elif latency < 500:
                return True, f"较慢 (延迟: {latency:.0f}ms)"
            else:
                return False, f"过慢 (延迟: {latency:.0f}ms)"
                
        except Exception as e:
            return False, f"连接失败: {e}"
    
    async def check_scheduler_status(self) -> Tuple[bool, str]:
        """检查调度器状态"""
        try:
            if app_scheduler.scheduler is None:
                return False, "未初始化"
            
            if not app_scheduler.scheduler.running:
                return False, "未运行"
            
            jobs = app_scheduler.scheduler.get_jobs()
            active_jobs = [j for j in jobs if j.next_run_time is not None]
            
            return True, f"运行中 ({len(active_jobs)} 个任务)"
            
        except Exception as e:
            return False, f"检查失败: {e}"
    
    async def check_recent_collection(self) -> Tuple[bool, str]:
        """检查最近采集"""
        try:
            async with AsyncSessionLocal() as db:
                from sqlalchemy import select, func
                from backend import LhbStockDaily
                
                # 查询最近采集日期
                stmt = select(func.max(LhbStockDaily.trade_date))
                result = await db.execute(stmt)
                latest_date = result.scalar()
                
                if latest_date is None:
                    return False, "无采集数据"
                
                today = datetime.now().date()
                days_diff = (today - latest_date).days
                
                if days_diff == 0:
                    return True, f"今天 ({latest_date})"
                elif days_diff == 1:
                    return True, f"昨天 ({latest_date})"
                elif days_diff <= 3:
                    return True, f"{days_diff} 天前 ({latest_date})"
                else:
                    return False, f"过期 ({days_diff} 天前)"
                    
        except Exception as e:
            return False, f"检查失败: {e}"
    
    def check_error_logs(self) -> Tuple[bool, str]:
        """检查错误日志"""
        try:
            import os
            from pathlib import Path
            
            log_dir = Path("logs")
            if not log_dir.exists():
                return True, "无日志目录"
            
            recent_errors = 0
            current_time = time.time()
            
            for log_file in log_dir.glob("*.log"):
                if log_file.stat().st_mtime < current_time - 86400:  # 24小时内
                    continue
                
                with open(log_file, 'r', encoding='utf-8') as f:
                    content = f.read()
                    recent_errors += content.count("ERROR")
            
            if recent_errors == 0:
                return True, "无错误"
            elif recent_errors < 10:
                return True, f"少量错误 ({recent_errors} 个)"
            else:
                return False, f"大量错误 ({recent_errors} 个)"
                
        except Exception as e:
            return False, f"检查失败: {e}"
    
    def check_disk_usage(self) -> Tuple[bool, str]:
        """检查磁盘使用"""
        try:
            import shutil
            
            total, used, free = shutil.disk_usage("/")
            usage_percent = (used / total) * 100
            
            if usage_percent < 70:
                return True, f"{usage_percent:.1f}% / {total // (1024**3)}GB"
            elif usage_percent < 90:
                return True, f"较高 {usage_percent:.1f}%"
            else:
                return False, f"危险 {usage_percent:.1f}%"
                
        except Exception as e:
            return False, f"检查失败: {e}"
    
    def check_memory_usage(self) -> Tuple[bool, str]:
        """检查内存使用"""
        try:
            import psutil
            
            memory = psutil.virtual_memory()
            usage_percent = memory.percent
            
            if usage_percent < 70:
                return True, f"{usage_percent:.1f}% / {memory.total // (1024**3)}GB"
            elif usage_percent < 90:
                return True, f"较高 {usage_percent:.1f}%"
            else:
                return False, f"危险 {usage_percent:.1f}%"
                
        except Exception as e:
            return False, f"检查失败: {e}"
    
    async def run_all_checks(self):
        """运行所有检查"""
        logger.info("开始系统健康检查...")
        
        # 同步检查
        self.results['数据库连接'] = self.check_database_connection()
        self.results['错误日志'] = self.check_error_logs()
        self.results['磁盘使用'] = self.check_disk_usage()
        self.results['内存使用'] = self.check_memory_usage()
        
        # 异步检查
        self.results['调度器状态'] = await self.check_scheduler_status()
        self.results['最近采集'] = await self.check_recent_collection()
        
        return self.results
    
    def print_report(self):
        """打印报告"""
        print("\n" + "=" * 60)
        print("  系统健康检查报告")
        print("=" * 60)
        print()
        
        all_healthy = True
        
        for check_name, (healthy, message) in self.results.items():
            status = "✓" if healthy else "✗"
            print(f"[{status}] {check_name:20} {message}")
            
            if not healthy:
                all_healthy = False
        
        print()
        print("-" * 60)
        
        if all_healthy:
            print("健康状态: ✓ 正常")
        else:
            print("健康状态: ✗ 异常 - 请检查上述失败项")
        
        print("=" * 60)
        
        return all_healthy


async def main():
    checker = HealthChecker()
    results = await checker.run_all_checks()
    all_healthy = checker.print_report()
    
    # 如果有失败项，退出码为1
    sys.exit(0 if all_healthy else 1)


if __name__ == '__main__':
    asyncio.run(main())
