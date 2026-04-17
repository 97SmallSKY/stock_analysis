"""
数据采集器单元测试
"""
import pytest
import asyncio
from datetime import date
from unittest.mock import AsyncMock, Mock, patch
import pandas as pd

from backend.app.collectors.base import BaseCollector
from backend.app.collectors.lhb_collector import LhbCollector
from backend.app.collectors.market_collector import MarketCollector


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def mock_db_session():
    """Mock 数据库会话"""
    session = AsyncMock()
    session.add = Mock()
    session.commit = AsyncMock()
    session.execute = AsyncMock()
    return session


@pytest.fixture
def sample_lhb_data():
    """示例龙虎榜数据"""
    return pd.DataFrame([
        {
            '交易日期': '2026-04-17',
            '代码': '000001',
            '名称': '平安银行',
            '收盘价': 12.5,
            '涨跌幅': 5.2,
            '换手率': 3.5,
            '上榜原因': '日涨幅偏离值达7%',
            '买方营业部名称': '机构专用',
            '买入金额': 5000000,
            '卖方营业部名称': '某某证券营业部',
            '卖出金额': 3000000
        },
        {
            '交易日期': '2026-04-17',
            '代码': '000001',
            '名称': '平安银行',
            '收盘价': 12.5,
            '涨跌幅': 5.2,
            '换手率': 3.5,
            '上榜原因': '日涨幅偏离值达7%',
            '买方营业部名称': '其他营业部',
            '买入金额': 2000000,
            '卖方营业部名称': '机构专用',
            '卖出金额': 4000000
        }
    ])


@pytest.fixture
def sample_market_data():
    """示例市场情绪数据"""
    return pd.DataFrame([
        {'代码': '000001', '涨跌幅': 10.0, '换手率': 5.0, '成交额': 1000000000},
        {'代码': '000002', '涨跌幅': 5.0, '换手率': 3.0, '成交额': 500000000},
        {'代码': '000003', '涨跌幅': -5.0, '换手率': 2.0, '成交额': 300000000},
        {'代码': '000004', '涨跌幅': -10.0, '换手率': 4.0, '成交额': 800000000},
    ])


# ============================================================
# BaseCollector 测试
# ============================================================

class TestBaseCollector:
    """BaseCollector 基类测试"""
    
    @pytest.mark.asyncio
    async def test_validate_stock_code(self, mock_db_session):
        """测试股票代码验证"""
        collector = LhbCollector(mock_db_session)
        
        # 有效代码
        assert collector._validate_stock_code('000001') == True
        assert collector._validate_stock_code('600519') == True
        
        # 无效代码
        assert collector._validate_stock_code('00001') == False  # 太短
        assert collector._validate_stock_code('0000001') == False  # 太长
        assert collector._validate_stock_code('00000A') == False  # 包含字母
        assert collector._validate_stock_code(None) == False
        assert collector._validate_stock_code('') == False
    
    def test_clean_amount(self, mock_db_session):
        """测试金额清洗"""
        collector = LhbCollector(mock_db_session)
        
        assert collector._clean_amount(100, 10000) == 1000000.0  # 100万元
        assert collector._clean_amount(0) == 0.0
        assert collector._clean_amount(None) == 0.0
        assert collector._clean_amount('invalid') == 0.0
    
    def test_clean_ratio(self, mock_db_session):
        """测试比率清洗"""
        collector = LhbCollector(mock_db_session)
        
        # 百分比字符串
        assert collector._clean_ratio('5.2%', as_decimal=True) == pytest.approx(0.052)
        assert collector._clean_ratio('5.2%', as_decimal=False) == pytest.approx(5.2)
        
        # 数值
        assert collector._clean_ratio(5.2, as_decimal=True) == pytest.approx(0.052)
        assert collector._clean_ratio(0.052, as_decimal=True) == pytest.approx(0.052)
        
        # 异常值
        assert collector._clean_ratio(None) == pytest.approx(0.0)
        assert collector._clean_ratio('invalid') == pytest.approx(0.0)


# ============================================================
# LhbCollector 测试
# ============================================================

class TestLhbCollector:
    """龙虎榜采集器测试"""
    
    @pytest.mark.asyncio
    async def test_fetch_success(self, mock_db_session, sample_lhb_data):
        """测试成功抓取数据"""
        collector = LhbCollector(mock_db_session)
        
        # Mock AKShare API
        with patch('akshare.stock_lhb_detail_em', return_value=sample_lhb_data):
            df = await collector.fetch(date(2026, 4, 17))
            
            assert not df.empty
            assert len(df) == 2
            assert '代码' in df.columns
    
    @pytest.mark.asyncio
    async def test_fetch_empty(self, mock_db_session):
        """测试抓取空数据"""
        collector = LhbCollector(mock_db_session)
        
        # Mock 返回空 DataFrame
        with patch('akshare.stock_lhb_detail_em', return_value=pd.DataFrame()):
            df = await collector.fetch(date(2026, 4, 17))
            assert df.empty
    
    @pytest.mark.asyncio
    async def test_transform(self, mock_db_session, sample_lhb_data):
        """测试数据转换"""
        collector = LhbCollector(mock_db_session)
        
        result = await collector.transform(sample_lhb_data)
        
        assert 'stocks' in result
        assert 'seats' in result
        
        stocks = result['stocks']
        seats = result['seats']
        
        # 应该聚合为1只股票
        assert len(stocks) == 1
        stock = stocks[0]
        
        assert stock['stock_code'] == '000001'
        assert stock['stock_name'] == '平安银行'
        assert stock['close_price'] == 12.5
        
        # 检查买卖金额汇总
        assert stock['total_buy_amount'] == 7000000  # 500万 + 200万
        assert stock['total_sell_amount'] == 7000000  # 300万 + 400万
        assert stock['net_buy_amount'] == 0  # 平衡
        
        # 检查机构识别
        assert stock['institution_buy_amount'] == 5000000
        assert stock['institution_sell_amount'] == 4000000
        
        # 应该有4个席位记录（2买2卖）
        assert len(seats) == 4
    
    @pytest.mark.asyncio
    async def test_run_dry_mode(self, mock_db_session, sample_lhb_data):
        """测试演练模式"""
        collector = LhbCollector(mock_db_session)
        
        with patch('akshare.stock_lhb_detail_em', return_value=sample_lhb_data):
            result = await collector.run(dry_run=True)
            
            assert result['success'] == True
            assert result['records_count'] > 0
            assert '演练模式' in result['message']
            
            # 演练模式不应该调用 commit
            mock_db_session.commit.assert_not_called()


# ============================================================
# MarketCollector 测试
# ============================================================

class TestMarketCollector:
    """市场情绪采集器测试"""
    
    @pytest.mark.asyncio
    async def test_transform(self, mock_db_session, sample_market_data):
        """测试市场情绪计算"""
        collector = MarketCollector(mock_db_session)
        
        records = await collector.transform(sample_market_data)
        
        assert len(records) == 1
        record = records[0]
        
        # 检查统计数字
        assert record['limit_up_count'] == 1  # 一个10%
        assert record['limit_down_count'] == 1  # 一个-10%
        assert record['rise_count'] == 2  # 两个正
        assert record['fall_count'] == 2  # 两个负
        
        # 检查情绪评分（应该在0-100之间）
        assert 0 <= record['sentiment_score'] <= 100
        
        # 检查市场阶段
        assert record['market_stage'] in ['hot', 'normal', 'cold', 'crisis']
    
    @pytest.mark.asyncio
    async def test_sentiment_score_hot_market(self, mock_db_session):
        """测试热市情绪评分"""
        collector = MarketCollector(mock_db_session)
        
        # 模拟热市：大量涨停
        hot_data = pd.DataFrame([
            {'涨跌幅': 10.0} for _ in range(100)
        ] + [
            {'涨跌幅': 5.0} for _ in range(100)
        ])
        
        records = await collector.transform(hot_data)
        record = records[0]
        
        # 热市情绪评分应该很高
        assert record['sentiment_score'] > 70
        assert record['market_stage'] == 'hot'
    
    @pytest.mark.asyncio
    async def test_sentiment_score_crisis_market(self, mock_db_session):
        """测试危机情绪评分"""
        collector = MarketCollector(mock_db_session)
        
        # 模拟危机：大量跌停
        crisis_data = pd.DataFrame([
            {'涨跌幅': -10.0} for _ in range(100)
        ] + [
            {'涨跌幅': -5.0} for _ in range(100)
        ])
        
        records = await collector.transform(crisis_data)
        record = records[0]
        
        # 危机情绪评分应该很低
        assert record['sentiment_score'] < 30
        assert record['market_stage'] == 'crisis'


# ============================================================
# 集成测试
# ============================================================

class TestCollectorIntegration:
    """采集器集成测试"""
    
    @pytest.mark.asyncio
    async def test_retry_mechanism(self, mock_db_session):
        """测试重试机制"""
        collector = LhbCollector(mock_db_session)
        
        # Mock 前两次失败，第三次成功
        call_count = 0
        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise Exception("API 调用失败")
            return pd.DataFrame()  # 空数据表示成功
        
        with patch('akshare.stock_lhb_detail_em', side_effect=side_effect):
            result = await collector.run()
            
            # 应该重试成功
            assert result['success'] == True
            assert call_count == 3  # 重试了2次
    
    @pytest.mark.asyncio
    async def test_max_retries_exceeded(self, mock_db_session):
        """测试超过最大重试次数"""
        collector = LhbCollector(mock_db_session)
        
        # Mock 一直失败
        with patch('akshare.stock_lhb_detail_em', side_effect=Exception("持续失败")):
            result = await collector.run()
            
            # 应该最终失败
            assert result['success'] == False
            assert 'error' in result


# ============================================================
# 运行测试
# ============================================================

if __name__ == '__main__':
    pytest.main([__file__, '-v', '--asyncio-mode=auto'])
