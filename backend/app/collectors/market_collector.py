"""
市场情绪采集器
"""
from typing import Optional
from datetime import date
import pandas as pd
import akshare as ak
from loguru import logger
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy import func

from .base import BaseCollector
from backend import MarketSentiment


class MarketCollector(BaseCollector):
    """
    市场情绪采集器
    
    数据来源: AKShare
    - stock_zh_a_spot_em(): 实时行情
    
    入库表: market_sentiment
    """
    
    async def fetch(self, trade_date: Optional[date] = None) -> pd.DataFrame:
        """
        抓取市场情绪数据
        
        Returns:
            包含所有A股当日涨跌情况的DataFrame
        """
        if trade_date is None:
            trade_date = date.today()
        
        logger.info(
            f"[MarketCollector] 开始抓取市场情绪数据",
            extra={"date": trade_date}
        )
        
        try:
            # 获取A股实时行情
            df = ak.stock_zh_a_spot_em()
            
            if df.empty:
                logger.warning("[MarketCollector] 实时行情数据为空")
                return pd.DataFrame()
            
            logger.info(
                f"[MarketCollector] 抓取成功",
                extra={"rows": len(df)}
            )
            
            return df
            
        except Exception as e:
            logger.error(f"[MarketCollector] AKShare 调用失败: {e}")
            raise
    
    async def transform(self, df: pd.DataFrame) -> list[dict]:
        """
        转换市场情绪数据
        
        计算指标:
        - 涨停/跌停数量
        - 上涨/下跌/平盘数量
        - 总成交额
        - 涨跌停比例
        - 平均换手率
        - 情绪评分
        """
        if df.empty:
            return []
        
        # 提取涨跌幅列（字段名可能因AKShare版本而异）
        change_col = '涨跌幅' if '涨跌幅' in df.columns else '涨跌百分比'
        
        # 统计各类型数量
        limit_up_count = len(df[df[change_col] >= 9.9])
        limit_down_count = len(df[df[change_col] <= -9.9])
        rise_count = len(df[df[change_col] > 0])
        fall_count = len(df[df[change_col] < 0])
        flat_count = len(df[df[change_col] == 0])
        
        # 总成交额（亿元）
        amount_col = '成交额' if '成交额' in df.columns else '成交量'
        total_amount = df[amount_col].sum() if amount_col in df.columns else 0
        
        # 计算比例
        total_stocks = len(df)
        limit_up_ratio = limit_up_count / max(total_stocks, 1)
        limit_down_ratio = limit_down_count / max(total_stocks, 1)
        
        # 平均换手率
        turnover_col = '换手率' if '换手率' in df.columns else None
        avg_turnover = df[turnover_col].mean() if turnover_col and turnover_col in df.columns else 0
        
        # 情绪评分 (0-100)
        # 简化公式: 50 + (涨停数 - 跌停数) / 总股数 * 50 + (上涨数 - 下跌数) / 总股数 * 20
        sentiment_score = 50 + (limit_up_count - limit_down_count) / max(total_stocks, 1) * 50
        sentiment_score += (rise_count - fall_count) / max(total_stocks, 1) * 20
        sentiment_score = max(0, min(100, sentiment_score))  # 限制在0-100
        
        # 市场阶段判断
        if sentiment_score >= 70:
            market_stage = 'hot'
        elif sentiment_score >= 40:
            market_stage = 'normal'
        elif sentiment_score >= 20:
            market_stage = 'cold'
        else:
            market_stage = 'crisis'
        
        record = {
            'trade_date': date.today(),
            'limit_up_count': limit_up_count,
            'limit_down_count': limit_down_count,
            'rise_count': rise_count,
            'fall_count': fall_count,
            'flat_count': flat_count,
            'total_amount': float(total_amount),
            'limit_up_ratio': limit_up_ratio,
            'limit_down_ratio': limit_down_ratio,
            'avg_turnover': float(avg_turnover) if avg_turnover else 0.0,
            'sentiment_score': sentiment_score,
            'market_stage': market_stage
        }
        
        logger.info(
            f"[MarketCollector] 情绪计算完成",
            extra={
                "sentiment_score": sentiment_score,
                "market_stage": market_stage,
                "limit_up": limit_up_count,
                "limit_down": limit_down_count
            }
        )
        
        return [record]
    
    async def save(self, records: list[dict]) -> int:
        """
        保存市场情绪数据（upsert）
        """
        if not records:
            return 0
        
        stmt = insert(MarketSentiment).values(records)
        stmt = stmt.on_conflict_do_update(
            index_elements=['trade_date'],
            set_={
                'limit_up_count': stmt.excluded.limit_up_count,
                'limit_down_count': stmt.excluded.limit_down_count,
                'rise_count': stmt.excluded.rise_count,
                'fall_count': stmt.excluded.fall_count,
                'flat_count': stmt.excluded.flat_count,
                'total_amount': stmt.excluded.total_amount,
                'limit_up_ratio': stmt.excluded.limit_up_ratio,
                'limit_down_ratio': stmt.excluded.limit_down_ratio,
                'avg_turnover': stmt.excluded.avg_turnover,
                'sentiment_score': stmt.excluded.sentiment_score,
                'market_stage': stmt.excluded.market_stage
            }
        )
        
        await self.db.execute(stmt)
        await self.db.commit()
        
        logger.info(
            f"[MarketCollector] 保存成功",
            extra={"count": len(records)}
        )
        
        return len(records)
