"""
新浪数据源实现

优先级：3（最低，用于降级）
特点：实时行情快速稳定，但不支持历史K线
"""
import logging
import re
from typing import Optional
import requests

from .base import BaseDataSource, FetchResult

logger = logging.getLogger(__name__)


class SinaDataSource(BaseDataSource):
    """新浪数据源"""
    
    NAME = "sina"
    PRIORITY = 3  # 最低优先级，用于降级
    
    # 新浪实时行情接口
    QUOTE_URL = "https://hq.sinajs.cn/list={}"
    
    def __init__(self):
        """初始化新浪数据源"""
        self._session = requests.Session()
        self._session.headers.update({
            'Referer': 'https://finance.sina.com.cn',
            'User-Agent': 'Mozilla/5.0'
        })
    
    def get_daily_kline(
        self, 
        stock_code: str, 
        start_date: str, 
        end_date: str
    ) -> FetchResult:
        """
        获取日K线数据
        
        注意：新浪接口不支持历史K线，此方法直接返回错误
        """
        return self._make_error("新浪数据源不支持历史K线，请使用 Tushare 或 AKShare")
    
    def get_realtime_quote(self, stock_code: str) -> FetchResult:
        """
        获取实时行情
        
        新浪实时行情接口响应快速稳定
        """
        try:
            # 转换股票代码格式
            sina_code = self._convert_code(stock_code)
            
            url = self.QUOTE_URL.format(sina_code)
            resp = self._session.get(url, timeout=5)
            resp.encoding = 'gbk'
            
            if resp.status_code != 200:
                return self._make_error(f"新浪接口 HTTP {resp.status_code}")
            
            # 解析返回数据
            # 格式: var hq_str_sh600001="包钢股份,2.05,2.04,2.06,..."
            match = re.search(r'="([^"]*)"', resp.text)
            if not match:
                return self._make_error(f"新浪返回格式异常: {stock_code}")
            
            parts = match.group(1).split(',')
            if len(parts) < 32:
                return self._make_error(f"新浪数据字段不足: {stock_code}")
            
            quote = {
                'code': stock_code,
                'name': parts[0],
                'open': float(parts[1]),
                'pre_close': float(parts[2]),
                'price': float(parts[3]),
                'high': float(parts[4]),
                'low': float(parts[5]),
                'volume': float(parts[8]),
                'amount': float(parts[9]),
                'bid_price': float(parts[10]),  # 买一价
                'ask_price': float(parts[20]),  # 卖一价
                'change_pct': 0.0,  # 需要计算
            }
            
            # 计算涨跌幅
            if quote['pre_close'] > 0:
                quote['change_pct'] = (quote['price'] - quote['pre_close']) / quote['pre_close'] * 100
            
            return self._make_result(quote, is_realtime=True)
            
        except Exception as e:
            return self._make_error(f"新浪接口错误: {e}")
    
    def is_available(self) -> bool:
        """检查新浪是否可用"""
        try:
            # 测试获取上证指数
            resp = self._session.get(self.QUOTE_URL.format("sh000001"), timeout=3)
            return resp.status_code == 200 and "var hq_str" in resp.text
        except Exception:
            return False
    
    def _convert_code(self, stock_code: str) -> str:
        """
        转换股票代码为新浪格式
        
        Examples:
            600001 → sh600001
            000001 → sz000001
        """
        code = stock_code.strip()
        if code.startswith(('6', '688')):
            return f"sh{code}"
        else:
            return f"sz{code}"
