# tests/test_data_fetch.py
"""
数据源获取测试
用真实API测试数据获取，验证降级链路
需要网络连接
"""

import sys
import pytest
import pandas as pd
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.data_gateway.category import DataCategory, AdapterName
from backend.data_gateway.manager import DataSourceManager


# ================================================================
# Fixture
# ================================================================

@pytest.fixture(scope="module")
def manager(data_source_config):
    """初始化DataSourceManager(整个模块共享)"""
    mgr = DataSourceManager()
    mgr.initialize(data_source_config)
    yield mgr


@pytest.fixture(scope="module")
def akshare_only():
    """只初始化AKShare(免费无token)"""
    mgr = DataSourceManager()
    mgr.initialize({
        "akshare": {"rate_limit": 20},
        "sina": {"rate_limit": 10},
        "crawler": {"rate_limit": 10},
    })
    yield mgr


# ================================================================
# 1. AKShare 适配器直接测试
# ================================================================

class TestAKShareAdapter:
    """AKShare 适配器(免费，可直接测试)"""

    def test_akshare_init(self):
        from backend.data_gateway.adapters.akshare_adapter import AKShareAdapter
        adapter = AKShareAdapter(config={"rate_limit": 20})
        result = adapter.initialize()
        assert result is True
        assert adapter._initialized is True

    def test_akshare_health_check(self):
        from backend.data_gateway.adapters.akshare_adapter import AKShareAdapter
        adapter = AKShareAdapter()
        adapter.initialize()
        assert adapter.health_check() is True

    def test_get_daily_kline(self):
        from backend.data_gateway.adapters.akshare_adapter import AKShareAdapter
        adapter = AKShareAdapter()
        adapter.initialize()

        df = adapter.get_daily_kline(
            ts_code="000001.SZ",
            start_date="20240101",
            end_date="20240131"
        )

        assert df is not None
        assert isinstance(df, pd.DataFrame)
        assert len(df) > 0
        assert "ts_code" in df.columns
        assert "trade_date" in df.columns
        assert "close" in df.columns
        assert "pct_change" in df.columns
        # 验证百分比已转小数
        assert df["pct_change"].abs().max() < 1.0

    def test_get_stock_info(self):
        from backend.data_gateway.adapters.akshare_adapter import AKShareAdapter
        adapter = AKShareAdapter()
        adapter.initialize()

        df = adapter.get_stock_info()

        assert df is not None
        assert len(df) > 4000  # A股至少4000+
        assert "ts_code" in df.columns
        assert "name" in df.columns

    def test_get_realtime_quote(self):
        from backend.data_gateway.adapters.akshare_adapter import AKShareAdapter
        adapter = AKShareAdapter()
        adapter.initialize()

        df = adapter.get_realtime_quote(
            ts_codes=["000001.SZ", "600000.SH", "300750.SZ"]
        )

        # 非交易时间可能返回None或空
        if df is not None and not df.empty:
            assert "ts_code" in df.columns
            assert "current_price" in df.columns
            assert len(df) <= 3

    def test_get_lhb_detail(self):
        from backend.data_gateway.adapters.akshare_adapter import AKShareAdapter
        adapter = AKShareAdapter()
        adapter.initialize()

        # 用一个确定有龙虎榜数据的日期
        df = adapter.get_lhb_detail(trade_date="20240115")

        if df is not None and not df.empty:
            assert "ts_code" in df.columns
            assert "trade_date" in df.columns
            assert "l_net" in df.columns or "l_buy" in df.columns

    def test_get_market_sentiment(self):
        from backend.data_gateway.adapters.akshare_adapter import AKShareAdapter
        adapter = AKShareAdapter()
        adapter.initialize()

        df = adapter.get_market_sentiment(trade_date="20240115")

        if df is not None and not df.empty:
            assert "trade_date" in df.columns
            assert "sentiment_score" in df.columns
            score = float(df["sentiment_score"].iloc[0])
            assert 0 <= score <= 100

    def test_code_conversion(self):
        """代码转换测试"""
        from backend.data_gateway.adapters.akshare_adapter import AKShareAdapter

        assert AKShareAdapter._to_ts_code("000001") == "000001.SZ"
        assert AKShareAdapter._to_ts_code("600000") == "600000.SH"
        assert AKShareAdapter._to_ts_code("300750") == "300750.SZ"
        assert AKShareAdapter._to_ts_code("688001") == "688001.SH"


# ================================================================
# 2. 新浪适配器测试
# ================================================================

class TestSinaAdapter:
    """新浪适配器(免费)"""

    def test_sina_init(self):
        from backend.data_gateway.adapters.sina_adapter import SinaAdapter
        adapter = SinaAdapter()
        result = adapter.initialize()
        assert result is True

    def test_sina_health_check(self):
        from backend.data_gateway.adapters.sina_adapter import SinaAdapter
        adapter = SinaAdapter()
        adapter.initialize()
        assert adapter.health_check() is True

    def test_get_realtime_quote(self):
        from backend.data_gateway.adapters.sina_adapter import SinaAdapter
        adapter = SinaAdapter()
        adapter.initialize()

        df = adapter.get_realtime_quote(
            ts_codes=["000001.SZ", "600000.SH"]
        )

        if df is not None and not df.empty:
            assert "ts_code" in df.columns
            assert "current_price" in df.columns
            assert "name" in df.columns
            # 验证代码格式正确
            for code in df["ts_code"]:
                assert "." in code  # 000001.SZ格式

    def test_sina_code_conversion(self):
        from backend.data_gateway.adapters.sina_adapter import SinaAdapter

        assert SinaAdapter._to_sina_code("000001.SZ") == "sz000001"
        assert SinaAdapter._to_sina_code("600000.SH") == "sh600000"
        assert SinaAdapter._from_sina_code("sz000001") == "000001.SZ"
        assert SinaAdapter._from_sina_code("sh600000") == "600000.SH"


# ================================================================
# 3. 东方财富爬虫适配器测试
# ================================================================

class TestCrawlerAdapter:
    """东方财富爬虫适配器"""

    def test_crawler_init(self):
        from backend.data_gateway.adapters.crawler_adapter import CrawlerAdapter
        adapter = CrawlerAdapter()
        result = adapter.initialize()
        assert result is True

    def test_get_daily_kline(self):
        from backend.data_gateway.adapters.crawler_adapter import CrawlerAdapter
        adapter = CrawlerAdapter()
        adapter.initialize()

        df = adapter.get_daily_kline(
            ts_code="000001.SZ",
            start_date="20240101",
            end_date="20240131"
        )

        assert df is not None
        assert len(df) > 0
        assert "close" in df.columns
        assert "pct_change" in df.columns

    def test_get_lhb_detail(self):
        from backend.data_gateway.adapters.crawler_adapter import CrawlerAdapter
        adapter = CrawlerAdapter()
        adapter.initialize()

        df = adapter.get_lhb_detail(trade_date="20240115")

        if df is not None and not df.empty:
            assert "ts_code" in df.columns
            assert "reason" in df.columns

    def test_get_lhb_seats(self):
        from backend.data_gateway.adapters.crawler_adapter import CrawlerAdapter
        adapter = CrawlerAdapter()
        adapter.initialize()

        df = adapter.get_lhb_seats(trade_date="20240115")

        if df is not None and not df.empty:
            assert "broker_name" in df.columns
            assert "direction" in df.columns


# ================================================================
# 4. TuShare 适配器测试(需要token)
# ================================================================

class TestTuShareAdapter:
    """TuShare 适配器(需要TUSHARE_TOKEN环境变量)"""

    @pytest.fixture(autouse=True)
    def skip_without_token(self, data_source_config):
        token = data_source_config.get("tushare", {}).get("token", "")
        if not token:
            pytest.skip("TUSHARE_TOKEN 未配置，跳过TuShare测试")

    def test_tushare_init(self, data_source_config):
        from backend.data_gateway.adapters.tushare_adapter import TuShareAdapter
        adapter = TuShareAdapter(config=data_source_config["tushare"])
        result = adapter.initialize()
        assert result is True

    def test_get_daily_kline(self, data_source_config):
        from backend.data_gateway.adapters.tushare_adapter import TuShareAdapter
        adapter = TuShareAdapter(config=data_source_config["tushare"])
        adapter.initialize()

        df = adapter.get_daily_kline(
            ts_code="000001.SZ",
            start_date="20240101",
            end_date="20240131"
        )

        assert df is not None
        assert len(df) > 0
        assert "close" in df.columns

    def test_get_trade_calendar(self, data_source_config):
        from backend.data_gateway.adapters.tushare_adapter import TuShareAdapter
        adapter = TuShareAdapter(config=data_source_config["tushare"])
        adapter.initialize()

        df = adapter.get_trade_calendar(
            exchange="SSE",
            start_date="20240101",
            end_date="20240131"
        )

        assert df is not None
        assert len(df) == 31  # 1月有31天
        assert "is_open" in df.columns

    def test_get_stock_info(self, data_source_config):
        from backend.data_gateway.adapters.tushare_adapter import TuShareAdapter
        adapter = TuShareAdapter(config=data_source_config["tushare"])
        adapter.initialize()

        df = adapter.get_stock_info()
        assert df is not None
        assert len(df) > 4000


# ================================================================
# 5. 路由降级链测试
# ================================================================

class TestRouterFallback:
    """路由器降级链测试"""

    def test_kline_fallback_chain(self, akshare_only):
        """K线: AKShare失败 → Sina → Crawler"""
        df = akshare_only.get_daily_kline(
            ts_code="000001.SZ",
            start_date="20240101",
            end_date="20240110"
        )

        assert df is not None
        assert len(df) > 0
        assert "_source" in df.columns
        print(f"K线数据来源: {df['_source'].iloc[0]}, {len(df)}行")

    def test_lhb_fallback_chain(self, akshare_only):
        """龙虎榜降级链"""
        df = akshare_only.get_lhb_detail(trade_date="20240115")

        if df is not None:
            assert "_source" in df.columns
            print(f"龙虎榜数据来源: {df['_source'].iloc[0]}, {len(df)}行")

    def test_realtime_quote_chain(self, akshare_only):
        """实时行情降级链"""
        df = akshare_only.get_realtime_quote(
            ts_codes=["000001.SZ", "600000.SH"]
        )

        if df is not None and not df.empty:
            assert "_source" in df.columns
            print(f"行情来源: {df['_source'].iloc[0]}, {len(df)}行")

    def test_sentiment_chain(self, akshare_only):
        """市场情绪降级链"""
        df = akshare_only.get_market_sentiment(trade_date="20240115")

        if df is not None:
            assert "_source" in df.columns
            print(f"情绪来源: {df['_source'].iloc[0]}")


# ================================================================
# 6. Manager 集成测试
# ================================================================

class TestManagerIntegration:
    """DataSourceManager 集成测试"""

    def test_manager_initialize(self, akshare_only):
        status = akshare_only.get_status()
        assert "adapters" in status
        assert "priority_chains" in status
        # 至少有一个适配器初始化成功
        active = sum(1 for a in status["adapters"].values() if a["initialized"])
        assert active >= 1

    def test_health_check_all(self, akshare_only):
        results = akshare_only.health_check_all()
        assert isinstance(results, dict)
        # 至少akshare应该健康
        assert results.get("akshare", False) is True

    def test_get_status_structure(self, akshare_only):
        status = akshare_only.get_status()

        # 验证结构
        assert "adapters" in status
        assert "priority_chains" in status

        # 验证优先级链
        chains = status["priority_chains"]
        assert "DAILY_KLINE" in chains
        assert "LHB_DETAIL" in chains
        assert "REALTIME_QUOTE" in chains

    def test_circuit_breaker_status(self, akshare_only):
        status = akshare_only.get_status()
        for adapter_name, adapter_status in status["adapters"].items():
            cb = adapter_status["circuit_breaker"]
            assert "state" in cb
            assert "failure_count" in cb
            assert cb["state"] in ["CLOSED", "OPEN", "HALF_OPEN"]


# ================================================================
# 7. 数据质量验证
# ================================================================

class TestDataQuality:
    """验证返回数据的质量"""

    def test_kline_data_quality(self, akshare_only):
        df = akshare_only.get_daily_kline(
            ts_code="000001.SZ",
            start_date="20240101",
            end_date="20240131"
        )

        if df is None:
            pytest.skip("无法获取K线数据")

        # 基本字段不为空
        assert df["close"].notna().all()
        assert df["trade_date"].notna().all()

        # 价格合理性
        assert (df["close"] > 0).all()
        assert (df["high"] >= df["low"]).all()
        assert (df["high"] >= df["close"]).all()
        assert (df["low"] <= df["close"]).all()

        # 成交量非负
        if "vol" in df.columns:
            assert (df["vol"] >= 0).all()

        # 日期格式正确(YYYYMMDD)
        for d in df["trade_date"]:
            assert len(str(d)) == 8

        # 涨跌幅范围合理(小数形式，±20%以内)
        if "pct_change" in df.columns:
            valid_pct = df["pct_change"].dropna()
            assert (valid_pct.abs() < 0.21).all(), f"涨跌幅超出范围: {valid_pct.abs().max()}"

    def test_kline_date_order(self, akshare_only):
        """K线日期有序"""
        df = akshare_only.get_daily_kline(
            ts_code="600000.SH",
            start_date="20240101",
            end_date="20240331"
        )

        if df is None:
            pytest.skip("无法获取K线数据")

        dates = df["trade_date"].tolist()
        dates_sorted = sorted(dates)
        # 可能升序或降序，取决于数据源
        assert dates == dates_sorted or dates == dates_sorted[::-1]

    def test_kline_no_duplicates(self, akshare_only):
        """K线无重复日期"""
        df = akshare_only.get_daily_kline(
            ts_code="000001.SZ",
            start_date="20240101",
            end_date="20240331"
        )

        if df is None:
            pytest.skip("无法获取K线数据")

        assert df["trade_date"].duplicated().sum() == 0

    def test_lhb_data_quality(self, akshare_only):
        """龙虎榜数据质量"""
        df = akshare_only.get_lhb_detail(trade_date="20240115")

        if df is None or df.empty:
            pytest.skip("无法获取龙虎榜数据")

        # ts_code 格式正确
        for code in df["ts_code"]:
            assert "." in str(code), f"代码格式错误: {code}"
            parts = str(code).split(".")
            assert len(parts[0]) == 6
            assert parts[1] in ("SH", "SZ", "BJ")

        # 买卖额非负
        for col in ["l_buy", "l_sell"]:
            if col in df.columns:
                valid = pd.to_numeric(df[col], errors="coerce").dropna()
                assert (valid >= 0).all(), f"{col} 存在负值"

    def test_sentiment_score_range(self, akshare_only):
        """情绪评分范围0-100"""
        df = akshare_only.get_market_sentiment(trade_date="20240115")

        if df is None or df.empty:
            pytest.skip("无法获取情绪数据")

        score = float(df["sentiment_score"].iloc[0])
        assert 0 <= score <= 100, f"情绪评分超出范围: {score}"

        level = df["sentiment_level"].iloc[0]
        valid_levels = ["ICE_COLD", "COLD", "NEUTRAL", "WARM", "HOT", "EXTREME"]
        assert level in valid_levels, f"无效的情绪级别: {level}"


# ================================================================
# 8. 熔断降级测试
# ================================================================

class TestCircuitBreakerIntegration:
    """熔断器在实际请求中的行为"""

    def test_adapter_has_circuit_breaker(self):
        from backend.data_gateway.adapters.akshare_adapter import AKShareAdapter
        adapter = AKShareAdapter()
        adapter.initialize()

        assert adapter.circuit_breaker is not None
        assert adapter.circuit_breaker.state.value == "CLOSED"

    def test_circuit_breaker_after_success(self, akshare_only):
        """成功请求后熔断器保持CLOSED"""
        df = akshare_only.get_daily_kline(
            ts_code="000001.SZ",
            start_date="20240101",
            end_date="20240110"
        )

        status = akshare_only.get_status()
        for name, adapter_status in status["adapters"].items():
            if adapter_status["initialized"]:
                cb = adapter_status["circuit_breaker"]
                assert cb["state"] == "CLOSED"

    def test_invalid_request_doesnt_crash(self, akshare_only):
        """无效请求不会导致崩溃"""
        # 无效股票代码
        df = akshare_only.get_daily_kline(
            ts_code="INVALID.XX",
            start_date="20240101",
            end_date="20240110"
        )
        # 应该返回None，不抛异常
        assert df is None or isinstance(df, pd.DataFrame)

    def test_future_date_returns_empty(self, akshare_only):
        """未来日期返回空"""
        df = akshare_only.get_daily_kline(
            ts_code="000001.SZ",
            start_date="20991201",
            end_date="20991231"
        )
        assert df is None or df.empty

    def test_force_circuit_open_and_fallback(self, akshare_only):
        """手动熔断一个适配器，验证降级到下一个"""
        # 熔断 akshare
        akshare_only.force_open_circuit("akshare")

        # 请求应该降级到 sina 或 crawler
        df = akshare_only.get_daily_kline(
            ts_code="000001.SZ",
            start_date="20240101",
            end_date="20240110"
        )

        if df is not None:
            assert df["_source"].iloc[0] != "akshare"
            print(f"降级到: {df['_source'].iloc[0]}")

        # 恢复
        akshare_only.force_close_circuit("akshare")


# ================================================================
# 9. 数据写入PG测试
# ================================================================

class TestDataToDB:
    """获取数据后写入PG"""

    def test_fetch_and_insert_kline(self, akshare_only, tx_cursor):
        """获取K线数据并写入数据库"""
        df = akshare_only.get_daily_kline(
            ts_code="000001.SZ",
            start_date="20240102",
            end_date="20240105"
        )

        if df is None or df.empty:
            pytest.skip("无法获取K线数据")

        # 先插入stock_info
        tx_cursor.execute("""
            INSERT INTO stock_info (ts_code, symbol, name, industry, market, is_active)
            VALUES ('000001.SZ', '000001', '平安银行', '银行', '主板', TRUE)
            ON CONFLICT (ts_code) DO NOTHING
        """)

        # 写入K线
        insert_count = 0
        for _, row in df.iterrows():
            try:
                tx_cursor.execute("""
                    INSERT INTO kline_daily (ts_code, trade_date, open, high, low, close, vol, amount, pct_change, turnover_rate)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    row.get("ts_code", "000001.SZ"),
                    row.get("trade_date"),
                    row.get("open"),
                    row.get("high"),
                    row.get("low"),
                    row.get("close"),
                    row.get("vol"),
                    row.get("amount"),
                    row.get("pct_change"),
                    row.get("turnover_rate"),
                ))
                insert_count += 1
            except Exception as e:
                print(f"插入失败: {e}")

        assert insert_count > 0
        print(f"成功插入 {insert_count} 条K线数据")

        # 验证写入
        tx_cursor.execute("""
            SELECT COUNT(*) as cnt FROM kline_daily WHERE ts_code = '000001.SZ'
        """)
        assert tx_cursor.fetchone()["cnt"] == insert_count

    def test_fetch_and_insert_lhb(self, akshare_only, tx_cursor):
        """获取龙虎榜并写入数据库"""
        df = akshare_only.get_lhb_detail(trade_date="20240115")

        if df is None or df.empty:
            pytest.skip("无法获取龙虎榜数据")

        # 取前3条测试
        df_sample = df.head(3)
        insert_count = 0

        for _, row in df_sample.iterrows():
            ts_code = row.get("ts_code", "")
            if not ts_code:
                continue

            # 确保stock_info存在
            tx_cursor.execute("""
                INSERT INTO stock_info (ts_code, symbol, name, market, is_active)
                VALUES (%s, %s, %s, '主板', TRUE)
                ON CONFLICT (ts_code) DO NOTHING
            """, (ts_code, ts_code.split(".")[0], row.get("name", "未知")))

            try:
                tx_cursor.execute("""
                    INSERT INTO lhb_stock_daily (
                        ts_code, trade_date, name, close_price, pct_change,
                        l_buy, l_sell, l_net, reason
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    ts_code,
                    row.get("trade_date", "20240115"),
                    row.get("name"),
                    row.get("close_price"),
                    row.get("pct_change"),
                    row.get("l_buy"),
                    row.get("l_sell"),
                    row.get("l_net"),
                    row.get("reason"),
                ))
                insert_count += 1
            except Exception as e:
                print(f"插入失败: {e}")

        assert insert_count > 0
        print(f"成功插入 {insert_count} 条龙虎榜数据")

    def test_fetch_and_insert_sentiment(self, akshare_only, tx_cursor):
        """获取市场情绪并写入数据库"""
        df = akshare_only.get_market_sentiment(trade_date="20240115")

        if df is None or df.empty:
            pytest.skip("无法获取情绪数据")

        row = df.iloc[0]
        tx_cursor.execute("""
            INSERT INTO market_sentiment (
                trade_date, limit_up_count, limit_down_count,
                cont_limit_up, max_cont_board,
                up_down_ratio, avg_pct_change,
                sentiment_score, sentiment_level
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """, (
            row.get("trade_date", "20240115"),
            row.get("limit_up_count", 0),
            row.get("limit_down_count", 0),
            row.get("cont_limit_up", 0),
            row.get("max_cont_board", 0),
            row.get("up_down_ratio"),
            row.get("avg_pct_change"),
            row.get("sentiment_score"),
            row.get("sentiment_level"),
        ))
        result = tx_cursor.fetchone()
        assert result["id"] is not None
        print(f"情绪数据写入成功, 评分: {row.get('sentiment_score')}")


# ================================================================
# 10. 性能基准测试
# ================================================================

class TestPerformance:
    """基础性能测试"""

    def test_kline_fetch_time(self, akshare_only):
        """K线获取耗时"""
        import time

        start = time.time()
        df = akshare_only.get_daily_kline(
            ts_code="000001.SZ",
            start_date="20240101",
            end_date="20240331"
        )
        elapsed = time.time() - start

        if df is not None:
            print(f"K线获取: {len(df)}行, 耗时: {elapsed:.2f}s")
        assert elapsed < 30, f"K线获取超时: {elapsed:.2f}s"

    def test_batch_kline_fetch(self, akshare_only):
        """批量获取多只股票K线"""
        import time

        codes = ["000001.SZ", "600000.SH", "300750.SZ"]
        start = time.time()
        results = {}

        for code in codes:
            df = akshare_only.get_daily_kline(
                ts_code=code,
                start_date="20240101",
                end_date="20240131"
            )
            if df is not None:
                results[code] = len(df)

        elapsed = time.time() - start
        print(f"批量K线: {results}, 总耗时: {elapsed:.2f}s")
        assert elapsed < 60, f"批量获取超时: {elapsed:.2f}s"

    def test_lhb_fetch_time(self, akshare_only):
        """龙虎榜获取耗时"""
        import time

        start = time.time()
        df = akshare_only.get_lhb_detail(trade_date="20240115")
        elapsed = time.time() - start

        if df is not None:
            print(f"龙虎榜获取: {len(df)}行, 耗时: {elapsed:.2f}s")
        assert elapsed < 30, f"龙虎榜获取超时: {elapsed:.2f}s"
