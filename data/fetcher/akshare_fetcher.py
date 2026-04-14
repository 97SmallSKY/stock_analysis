"""
AKShare 数据源实现

优先级：2（次优先）
特点：免费开源，支持日K线和实时行情
"""
import logging
from typing import Optional
import pandas as pd

from .base import BaseDataSource, FetchResult

logger = logging.getLogger(__name__)


class AKShareDataSource(BaseDataSource):
    """AKShare 数据源"""
    
    NAME = "akshare"
    PRIORITY = 2  # 次优先级
    
    def __init__(self):
        """初始化 AKShare"""
        self._ak = None
    
    @property
    def ak(self):
        """延迟加载 akshare 模块"""
        if self._ak is None:
            try:
                import akshare as ak
                self._ak = ak
                logger.info("✅ AKShare 初始化成功")
            except ImportError:
                logger.warning("❌ AKShare 未安装，请执行: pip install akshare")
                raise
        return self._ak
    
    def get_daily_kline(
        self, 
        stock_code: str, 
        start_date: str, 
        end_date: str
    ) -> FetchResult:
        """
        获取日K线数据
        
        使用 akshare 的 stock_zh_a_hist 接口
        """
        try:
            # 转换日期格式：20240101 → 2024-01-01
            start_dt = f"{start_date[:4]}-{start_date[4:6]}-{start_date[6:]}"
            end_dt = f"{end_date[:4]}-{end_date[4:6]}-{end_date[6:]}"
            
            df = self.ak.stock_zh_a_hist(
                symbol=stock_code,
                period="daily",
                start_date=start_dt,
                end_date=end_dt,
                adjust="qfq"  # 前复权
            )
            
            if df is None or df.empty:
                return self._make_error(f"AKShare 返回空数据: {stock_code}")
            
            # 标准化列名
            df = df.rename(columns={
                '日期': 'trade_date',
                '开盘': 'open',
                '最高': 'high',
                '最低': 'low',
                '收盘': 'close',
                '成交量': 'volume',
                '成交额': 'amount',
                '振幅': 'amplitude',
                '涨跌幅': 'pct_change',
                '涨跌额': 'change',
                '换手率': 'turnover',
            })
            
            # 转换日期格式为 YYYYMMDD
            df['trade_date'] = pd.to_datetime(df['trade_date']).dt.strftime('%Y%m%d')
            
            # 添加股票代码列
            df['code'] = stock_code
            
            # 按日期升序排列
            df = df.sort_values('trade_date').reset_index(drop=True)
            
            logger.info(f"📊 AKShare 获取 {stock_code} 日K线: {len(df)} 条")
            return self._make_result(df, is_realtime=False)
            
        except Exception as e:
            return self._make_error(f"AKShare 错误: {e}")
    
    def get_realtime_quote(self, stock_code: str) -> FetchResult:
        """
        获取实时行情
        
        使用 akshare 的 stock_zh_a_spot_em 接口
        """
        try:
            # 获取 A 股实时行情
            df = self.ak.stock_zh_a_spot_em()
            
            # 筛选指定股票
            row = df[df['代码'] == stock_code]
            
            if row.empty:
                return self._make_error(f"AKShare 未找到股票: {stock_code}")
            
            r = row.iloc[0]
            
            quote = {
                'code': stock_code,
                'name': r.get('名称', ''),
                'price': float(r.get('最新价', 0)),
                'open': float(r.get('今开', 0)),
                'high': float(r.get('最高', 0)),
                'low': float(r.get('最低', 0)),
                'pre_close': float(r.get('昨收', 0)),
                'volume': float(r.get('成交量', 0)),
                'amount': float(r.get('成交额', 0)),
                'bid_price': float(r.get('买一', 0)),
                'ask_price': float(r.get('卖一', 0)),
                'change_pct': float(r.get('涨跌幅', 0)),
            }
            
            return self._make_result(quote, is_realtime=True)
            
        except Exception as e:
            return self._make_error(f"AKShare 实时行情错误: {e}")
    
    def is_available(self) -> bool:
        """检查 AKShare 是否可用"""
        try:
            # 尝试获取 A 股列表作为连通性测试
            self.ak.stock_zh_a_spot_em()
            return True
        except Exception:
            return False
