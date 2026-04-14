"""
Tushare 数据源实现

优先级：1（最高）
限制：120积分，仅支持日K线
"""
import logging
from typing import Optional
import pandas as pd

from .base import BaseDataSource, FetchResult

logger = logging.getLogger(__name__)


class TushareDataSource(BaseDataSource):
    """Tushare 数据源"""
    
    NAME = "tushare"
    PRIORITY = 1  # 最高优先级
    
    def __init__(self, token: Optional[str] = None):
        """
        初始化 Tushare
        
        Args:
            token: Tushare API token（可从环境变量 TS_TOKEN 获取）
        """
        self.token = token
        self._pro = None
        self._api_limit_reached = False
    
    @property
    def pro(self):
        """延迟初始化 Tushare pro 接口"""
        if self._pro is None:
            try:
                import tushare as ts
                if self.token:
                    ts.set_token(self.token)
                self._pro = ts.pro_api()
                logger.info("✅ Tushare 初始化成功")
            except ImportError:
                logger.warning("❌ Tushare 未安装，请执行: pip install tushare")
                raise
            except Exception as e:
                logger.error(f"❌ Tushare 初始化失败: {e}")
                raise
        return self._pro
    
    def get_daily_kline(
        self, 
        stock_code: str, 
        start_date: str, 
        end_date: str
    ) -> FetchResult:
        """
        获取日K线数据
        
        Tushare 120积分限制：
        - 每分钟最多 200 次请求
        - 单次返回最大 5000 行
        """
        try:
            # 转换股票代码格式：600001 → 600001.SH
            ts_code = self._convert_code(stock_code)
            
            df = self.pro.daily(
                ts_code=ts_code,
                start_date=start_date,
                end_date=end_date
            )
            
            if df is None or df.empty:
                return self._make_error(f"Tushare 返回空数据: {stock_code}")
            
            # 标准化列名
            df = df.rename(columns={
                'ts_code': 'code',
                'trade_date': 'trade_date',
                'vol': 'volume',
            })
            
            # 按日期升序排列
            df = df.sort_values('trade_date').reset_index(drop=True)
            
            logger.info(f"📊 Tushare 获取 {stock_code} 日K线: {len(df)} 条")
            return self._make_result(df, is_realtime=False)
            
        except Exception as e:
            error_msg = str(e)
            # 检测是否达到积分限制
            if '积分' in error_msg or '权限' in error_msg or 'exceed' in error_msg.lower():
                self._api_limit_reached = True
                logger.warning(f"⚠️ Tushare API 限制: {error_msg}")
            return self._make_error(f"Tushare 错误: {error_msg}")
    
    def get_realtime_quote(self, stock_code: str) -> FetchResult:
        """
        获取实时行情
        
        注意：Tushare 120积分不支持实时行情
        """
        return self._make_error("Tushare 120积分不支持实时行情，请使用其他数据源")
    
    def is_available(self) -> bool:
        """检查 Tushare 是否可用"""
        if self._api_limit_reached:
            return False
        try:
            # 尝试获取最近一个交易日的数据作为连通性测试
            self.pro.trade_cal(exchange='SSE', is_open='1', limit=1)
            return True
        except Exception:
            return False
    
    def _convert_code(self, stock_code: str) -> str:
        """
        转换股票代码格式
        
        Examples:
            600001 → 600001.SH
            000001 → 000001.SZ
        """
        code = stock_code.strip()
        if code.startswith(('6', '688')):
            return f"{code}.SH"
        else:
            return f"{code}.SZ"
