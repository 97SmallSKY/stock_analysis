"""
数据采集器基类
"""
import asyncio
from abc import ABC, abstractmethod
from typing import Optional, Any
from datetime import date
from loguru import logger
import pandas as pd
from sqlalchemy.ext.asyncio import AsyncSession


class BaseCollector(ABC):
    """
    数据采集器抽象基类
    
    所有采集器必须实现三个核心方法：
    - fetch(): 从外部 API 获取原始数据
    - transform(): 数据清洗和字段映射
    - save(): 数据入库（upsert）
    """
    
    # 重试配置
    MAX_RETRIES = 3
    RETRY_BACKOFF = [1, 3, 10]  # 秒，指数退避
    
    def __init__(
        self,
        db_session: AsyncSession,
        redis_client: Optional[Any] = None,
        config: Optional[dict] = None
    ):
        """
        初始化采集器
        
        Args:
            db_session: 数据库异步会话
            redis_client: Redis 客户端（可选）
            config: 配置字典（可选）
        """
        self.db = db_session
        self.redis = redis_client
        self.config = config or {}
        self.collector_name = self.__class__.__name__
    
    @abstractmethod
    async def fetch(self, trade_date: Optional[date] = None) -> pd.DataFrame:
        """
        从外部 API 获取原始数据
        
        Args:
            trade_date: 交易日期（可选，默认为当天）
            
        Returns:
            原始数据 DataFrame
            
        Raises:
            Exception: 数据获取失败时抛出异常
        """
        pass
    
    @abstractmethod
    async def transform(self, df: pd.DataFrame) -> list[dict]:
        """
        数据清洗和字段映射
        
        Args:
            df: 原始数据 DataFrame
            
        Returns:
            清洗后的记录列表
        """
        pass
    
    @abstractmethod
    async def save(self, records: list[dict]) -> int:
        """
        数据入库（upsert）
        
        Args:
            records: 待保存的记录列表
            
        Returns:
            成功保存的记录数
        """
        pass
    
    async def run(
        self,
        trade_date: Optional[date] = None,
        dry_run: bool = False
    ) -> dict:
        """
        执行完整采集流程：fetch → transform → save（含重试）
        
        Args:
            trade_date: 交易日期（可选）
            dry_run: 是否为演练模式（不入库）
            
        Returns:
            执行结果字典 {success: bool, records_count: int, error: str}
        """
        for attempt in range(self.MAX_RETRIES):
            try:
                logger.info(
                    f"[{self.collector_name}] 开始采集",
                    extra={
                        "trade_date": trade_date,
                        "attempt": attempt + 1,
                        "max_retries": self.MAX_RETRIES
                    }
                )
                
                # 步骤 1: 抓取数据
                df = await self.fetch(trade_date)
                
                if df.empty:
                    logger.warning(
                        f"[{self.collector_name}] 抓取结果为空",
                        extra={"trade_date": trade_date}
                    )
                    return {
                        "success": True,
                        "records_count": 0,
                        "message": "抓取结果为空（可能为非交易日）"
                    }
                
                logger.info(
                    f"[{self.collector_name}] 抓取完成",
                    extra={
                        "rows": len(df),
                        "columns": list(df.columns)
                    }
                )
                
                # 步骤 2: 数据转换
                records = await self.transform(df)
                
                logger.info(
                    f"[{self.collector_name}] 转换完成",
                    extra={"records_count": len(records)}
                )
                
                if dry_run:
                    logger.info(
                        f"[{self.collector_name}] 演练模式，跳过保存",
                        extra={"records_count": len(records)}
                    )
                    return {
                        "success": True,
                        "records_count": len(records),
                        "message": "演练模式（未保存）"
                    }
                
                # 步骤 3: 保存数据
                saved_count = await self.save(records)
                
                logger.info(
                    f"[{self.collector_name}] 保存成功",
                    extra={
                        "saved_count": saved_count,
                        "trade_date": trade_date
                    }
                )
                
                return {
                    "success": True,
                    "records_count": saved_count,
                    "message": "采集成功"
                }
                
            except Exception as e:
                error_msg = f"{type(e).__name__}: {str(e)}"
                
                logger.error(
                    f"[{self.collector_name}] 采集失败（尝试 {attempt + 1}/{self.MAX_RETRIES}）",
                    extra={
                        "error": error_msg,
                        "trade_date": trade_date
                    }
                )
                
                # 记录错误到数据库
                await self._record_error(trade_date, error_msg, attempt + 1)
                
                # 最后一次尝试失败
                if attempt >= self.MAX_RETRIES - 1:
                    logger.error(
                        f"[{self.collector_name}] 所有重试失败",
                        extra={
                            "error": error_msg,
                            "trade_date": trade_date
                        }
                    )
                    return {
                        "success": False,
                        "records_count": 0,
                        "error": error_msg
                    }
                
                # 等待后重试
                backoff_time = self.RETRY_BACKOFF[attempt]
                logger.info(
                    f"[{self.collector_name}] 等待 {backoff_time}秒 后重试..."
                )
                await asyncio.sleep(backoff_time)
        
        # 不应该到达这里
        return {
            "success": False,
            "records_count": 0,
            "error": "未知错误"
        }
    
    async def _record_error(
        self,
        trade_date: Optional[date],
        error_message: str,
        retry_count: int
    ):
        """
        记录采集错误到数据库
        
        Args:
            trade_date: 交易日期
            error_message: 错误信息
            retry_count: 重试次数
        """
        try:
            from backend.db.models import CollectorErrorLog
            
            error_log = CollectorErrorLog(
                collector_name=self.collector_name,
                error_type="collection_failed",
                error_message=error_message,
                trade_date=trade_date,
                retry_count=retry_count,
                resolved=False
            )
            
            self.db.add(error_log)
            await self.db.commit()
            
        except Exception as e:
            logger.error(
                f"[{self.collector_name}] 记录错误日志失败: {e}"
            )
    
    def _validate_stock_code(self, code: str) -> bool:
        """
        验证股票代码格式
        
        Args:
            code: 股票代码
            
        Returns:
            是否有效
        """
        if not code or len(code) != 6:
            return False
        return code.isdigit()
    
    def _clean_amount(self, value: Any, unit_multiplier: float = 10000) -> float:
        """
        清洗金额字段
        
        Args:
            value: 原始值
            unit_multiplier: 单位乘数（万元转元需要 10000）
            
        Returns:
            清洗后的金额（元）
        """
        try:
            if pd.isna(value):
                return 0.0
            return float(value) * unit_multiplier
        except (ValueError, TypeError):
            return 0.0
    
    def _clean_ratio(self, value: Any, as_decimal: bool = True) -> float:
        """
        清洗比率字段
        
        Args:
            value: 原始值（可能是百分比字符串或数值）
            as_decimal: 是否转为小数形式（True: 5% → 0.05, False: 保持 5）
            
        Returns:
            清洗后的比率
        """
        try:
            if pd.isna(value):
                return 0.0
            
            # 移除百分号
            if isinstance(value, str):
                value = value.replace('%', '').strip()
            
            ratio = float(value)
            
            # 如果要转为小数形式且值看起来是百分比
            if as_decimal and abs(ratio) > 1:
                ratio = ratio / 100
            
            return ratio
            
        except (ValueError, TypeError):
            return 0.0
