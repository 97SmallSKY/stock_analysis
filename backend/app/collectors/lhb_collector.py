"""
龙虎榜数据采集器
"""
from typing import Optional
from datetime import date
import pandas as pd
import akshare as ak
from loguru import logger
from sqlalchemy import select, func
from sqlalchemy.dialects.postgresql import insert

from .base import BaseCollector
from backend import LhbStockDaily, LhbSeat


class LhbCollector(BaseCollector):
    """
    龙虎榜采集器
    
    数据来源: AKShare
    - stock_lhb_detail_em(): 龙虎榜每日明细
    - stock_lhb_jgmmtj_em(): 机构买卖汇总
    
    入库表:
    - lhb_stock_daily: 每日股票汇总
    - lhb_seats: 席位明细
    """
    
    async def fetch(self, trade_date: Optional[date] = None) -> pd.DataFrame:
        """
        抓取龙虎榜数据
        
        Args:
            trade_date: 交易日期（可选，默认为当天）
            
        Returns:
            原始龙虎榜 DataFrame
        """
        if trade_date is None:
            trade_date = date.today()
        
        # 格式化日期为 AKShare 需要的格式 YYYYMMDD
        date_str = trade_date.strftime('%Y%m%d')
        
        logger.info(
            f"[LhbCollector] 开始抓取龙虎榜数据",
            extra={"date": date_str}
        )
        
        try:
            # 调用 AKShare API
            df = ak.stock_lhb_detail_em(start_date=date_str, end_date=date_str)
            
            if df.empty:
                logger.warning(
                    f"[LhbCollector] 当日无龙虎榜数据",
                    extra={"date": date_str}
                )
                return pd.DataFrame()
            
            logger.info(
                f"[LhbCollector] 抓取成功",
                extra={
                    "date": date_str,
                    "rows": len(df),
                    "columns": list(df.columns)
                }
            )
            
            return df
            
        except Exception as e:
            logger.error(
                f"[LhbCollector] AKShare 调用失败: {e}",
                extra={"date": date_str}
            )
            raise
    
    async def transform(self, df: pd.DataFrame) -> list[dict]:
        """
        转换龙虎榜数据
        
        Args:
            df: 原始 DataFrame
            
        Returns:
            {
                'stocks': [股票汇总记录],
                'seats': [席位明细记录]
            }
        """
        stocks = []
        seats = []
        
        # 按股票代码分组聚合
        for stock_code, group in df.groupby('代码'):
            if not self._validate_stock_code(stock_code):
                logger.warning(
                    f"[LhbCollector] 无效股票代码: {stock_code}"
                )
                continue
            
            first_row = group.iloc[0]
            
            # === 汇总数据 ===
            # AKShare实际字段: '上榜日', '收盘价', '涨跌幅', '龙虎榜净买额', '龙虎榜买入额', '龙虎榜卖出额', '龙虎榜成交额', '市场总成交额', '净买额占总成交比', '成交额占总成交比', '换手率', '流通市值', '上榜原因'
            stock_record = {
                'trade_date': pd.to_datetime(first_row['上榜日']).date(),
                'stock_code': stock_code,
                'stock_name': first_row['名称'],
                'close_price': float(first_row['收盘价']) if pd.notna(first_row['收盘价']) else None,
                'change_ratio': self._clean_ratio(str(first_row['涨跌幅']) + '%', as_decimal=True),
                'turnover_rate': self._clean_ratio(str(first_row.get('换手率', 0)) + '%', as_decimal=True),
                'reason': first_row.get('上榜原因', ''),
                'board_type': self._extract_board_type(first_row.get('上榜原因', ''))
            }
            
            # 计算买卖金额汇总
            total_buy = float(first_row.get('龙虎榜买入额', 0)) if pd.notna(first_row.get('龙虎榜买入额', None)) else 0.0
            total_sell = float(first_row.get('龙虎榜卖出额', 0)) if pd.notna(first_row.get('龙虎榜卖出额', None)) else 0.0
            net_buy = float(first_row.get('龙虎榜净买额', 0)) if pd.notna(first_row.get('龙虎榜净买额', None)) else 0.0
            total_amount = float(first_row.get('龙虎榜成交额', 0)) if pd.notna(first_row.get('龙虎榜成交额', None)) else 0.0
            market_amount = float(first_row.get('市场总成交额', 0)) if pd.notna(first_row.get('市场总成交额', None)) else 0.0
            net_buy_ratio = self._clean_ratio(str(first_row.get('净买额占总成交比', 0)) + '%', as_decimal=True)
            
            # 席位数（AKShare返回的是汇总数据，无法获取具体席位数，用估算）
            buy_seats_count = 0  # 无法从汇总数据获取
            sell_seats_count = 0
            institution_buy = 0.0
            institution_sell = 0.0
            
            # === 席位明细 ===
            # 注意: AKShare stock_lhb_detail_em() 返回的是股票级别汇总数据
            # 不包含买方/卖方营业部明细，因此无法生成席位记录
            # 如需席位明细，需要调用其他API: stock_lhb_hyyyb_em() 或 stock_lhb_jgmmtj_em()
            pass
            
            # 完善汇总记录
            stock_record.update({
                'total_buy_amount': total_buy,
                'total_sell_amount': total_sell,
                'net_buy_amount': net_buy,
                'net_buy_ratio': net_buy_ratio,
                'total_amount': total_amount,
                'market_amount': market_amount,
                'buy_seats': buy_seats_count,
                'sell_seats': sell_seats_count,
                'institution_buy_amount': institution_buy,
                'institution_sell_amount': institution_sell,
                'institution_net_buy': institution_buy - institution_sell
            })
            
            stocks.append(stock_record)
        
        logger.info(
            f"[LhbCollector] 转换完成",
            extra={
                "stocks_count": len(stocks),
                "seats_count": len(seats)
            }
        )
        
        return {'stocks': stocks, 'seats': seats}
    
    async def save(self, records: dict) -> int:
        """
        保存龙虎榜数据（upsert）
        
        Args:
            records: {'stocks': [...], 'seats': [...]}
            
        Returns:
            保存的总记录数
        """
        stocks = records.get('stocks', [])
        seats = records.get('seats', [])
        
        saved_count = 0
        
        # 保存股票汇总
        if stocks:
            stmt = insert(LhbStockDaily).values(stocks)
            stmt = stmt.on_conflict_do_update(
                constraint='uk_lhb_stock_daily_date_code',
                set_={
                    'stock_name': stmt.excluded.stock_name,
                    'close_price': stmt.excluded.close_price,
                    'change_ratio': stmt.excluded.change_ratio,
                    'turnover_rate': stmt.excluded.turnover_rate,
                    'total_buy_amount': stmt.excluded.total_buy_amount,
                    'total_sell_amount': stmt.excluded.total_sell_amount,
                    'net_buy_amount': stmt.excluded.net_buy_amount,
                    'net_buy_ratio': stmt.excluded.net_buy_ratio,
                    'buy_seats': stmt.excluded.buy_seats,
                    'sell_seats': stmt.excluded.sell_seats,
                    'institution_buy_amount': stmt.excluded.institution_buy_amount,
                    'institution_sell_amount': stmt.excluded.institution_sell_amount,
                    'institution_net_buy': stmt.excluded.institution_net_buy,
                    'reason': stmt.excluded.reason,
                    'board_type': stmt.excluded.board_type,
                    'updated_at': func.now()
                }
            )
            
            await self.db.execute(stmt)
            saved_count += len(stocks)
            
            logger.info(
                f"[LhbCollector] 股票汇总保存完成",
                extra={"count": len(stocks)}
            )
        
        # 保存席位明细
        if seats:
            # 先删除当日旧数据
            if seats:
                trade_date = seats[0]['trade_date']
                await self.db.execute(
                    select(LhbSeat).where(LhbSeat.trade_date == trade_date)
                )
                # 删除操作
                from sqlalchemy import delete
                await self.db.execute(
                    delete(LhbSeat).where(LhbSeat.trade_date == trade_date)
                )
            
            # 插入新数据
            stmt = insert(LhbSeat).values(seats)
            stmt = stmt.on_conflict_do_update(
                constraint='uk_lhb_seats_date_stock_dir_rank',
                set_={
                    'seat_name': stmt.excluded.seat_name,
                    'seat_type': stmt.excluded.seat_type,
                    'amount': stmt.excluded.amount
                }
            )
            
            await self.db.execute(stmt)
            saved_count += len(seats)
            
            logger.info(
                f"[LhbCollector] 席位明细保存完成",
                extra={"count": len(seats)}
            )
        
        await self.db.commit()
        
        return saved_count
    
    def _extract_board_type(self, reason: str) -> str:
        """
        从上榜原因提取板块类型
        
        Args:
            reason: 上榜原因文本
            
        Returns:
            板块类型：main_board, gem, star_market 等
        """
        if not reason:
            return 'unknown'
        
        if '创业板' in reason or 'GEM' in reason.upper():
            return 'gem'
        elif '科创板' in reason or 'STAR' in reason.upper():
            return 'star_market'
        elif '北交所' in reason or 'BSE' in reason.upper():
            return 'bse'
        else:
            return 'main_board'
