# backend/data_gateway/adapters/akshare_adapter.py

import logging
from typing import Optional, List

import pandas as pd

from ..base_adapter import BaseDataAdapter
from ..category import AdapterName

logger = logging.getLogger(__name__)


class AKShareAdapter(BaseDataAdapter):
    """
    AKShare 数据源适配器(免费无需token)

    支持: DAILY_KLINE, LHB_DETAIL, LHB_BROKER, REALTIME_QUOTE,
          MARKET_SENTIMENT, STOCK_INFO
    文档: https://akshare.akfamily.xyz/
    """

    def __init__(self, config: Optional[dict] = None):
        super().__init__(
            name=AdapterName.AKSHARE,
            config=config or {},
            rate_limit=config.get("rate_limit", 60) if config else 60,
            timeout=config.get("timeout", 30) if config else 30,
        )

    def _do_initialize(self):
        """AKShare无需鉴权，只验证import"""
        import akshare as ak
        # 简单验证
        df = ak.tool_trade_date_hist_sina()
        if df is None or df.empty:
            raise ConnectionError("AKShare 连接异常")

    def health_check(self) -> bool:
        try:
            import akshare as ak
            df = ak.tool_trade_date_hist_sina()
            return df is not None and not df.empty
        except Exception:
            return False

    # ================================================================
    # 日K线
    # ================================================================

    def get_daily_kline(
            self, ts_code: str, start_date: str, end_date: str
    ) -> Optional[pd.DataFrame]:
        """获取日K线"""

        def _fetch():
            import akshare as ak

            # ts_code转换: 000001.SZ → sz000001 / 600000.SH → sh600000
            symbol = self._convert_code(ts_code)

            df = ak.stock_zh_a_hist(
                symbol=symbol.replace("sz", "").replace("sh", ""),
                period="daily",
                start_date=start_date[:4] + "-" + start_date[4:6] + "-" + start_date[6:],
                end_date=end_date[:4] + "-" + end_date[4:6] + "-" + end_date[6:],
                adjust="qfq"  # 前复权
            )

            if df is None or df.empty:
                return None

            # 统一列名
            df = df.rename(columns={
                "日期": "trade_date",
                "开盘": "open",
                "收盘": "close",
                "最高": "high",
                "最低": "low",
                "成交量": "vol",
                "成交额": "amount",
                "振幅": "amplitude",
                "涨跌幅": "pct_change",
                "涨跌额": "price_change",
                "换手率": "turnover_rate",
            })

            df["ts_code"] = ts_code
            df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.strftime("%Y%m%d")

            # 百分比转小数
            for col in ["pct_change", "turnover_rate", "amplitude"]:
                if col in df.columns:
                    df[col] = df[col] / 100.0

            return df

        return self._safe_request(_fetch)

    # ================================================================
    # 龙虎榜明细
    # ================================================================

    def get_lhb_detail(self, trade_date: str) -> Optional[pd.DataFrame]:
        """
        获取龙虎榜每日明细

        Args:
            trade_date: 日期, 如 "20240115"
        """

        def _fetch():
            import akshare as ak

            # 格式化日期: 20240115 → 2024-01-15
            date_fmt = f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:]}"

            df = ak.stock_lhb_detail_em(date=date_fmt)

            if df is None or df.empty:
                return None

            # 统一列名
            df = df.rename(columns={
                "代码": "ts_code",
                # backend/data_gateway/adapters/akshare_adapter.py (续)

                "名称": "name",
                "收盘价": "close_price",
                "涨跌幅": "pct_change",
                "龙虎榜净买额": "l_net",
                "龙虎榜买入额": "l_buy",
                "龙虎榜卖出额": "l_sell",
                "龙虎榜成交额": "l_amount",
                "市场总成交额": "amount",
                "净买额占总成交比": "net_pct",
                "成交额占总成交比": "amount_pct",
                "换手率": "turnover_rate",
                "流通市值": "float_market_cap",
                "上榜原因": "reason",
            })

            df["trade_date"] = trade_date

            # 股票代码标准化: 000001 → 000001.SZ
            if "ts_code" in df.columns:
                df["ts_code"] = df["ts_code"].apply(self._to_ts_code)

            # 百分比转小数
            for col in ["pct_change", "turnover_rate", "net_pct", "amount_pct"]:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors="coerce") / 100.0

            # 金额单位统一为万
            for col in ["l_net", "l_buy", "l_sell", "l_amount", "amount", "float_market_cap"]:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors="coerce")

            return df

        return self._safe_request(_fetch)

    # ================================================================
    # 龙虎榜营业部席位
    # ================================================================

    def get_lhb_seats(self, trade_date: str) -> Optional[pd.DataFrame]:
        """获取龙虎榜营业部买卖席位"""

        def _fetch():
            import akshare as ak

            date_fmt = f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:]}"

            all_seats = []

            # 买入席位
            try:
                df_buy = ak.stock_lhb_stock_detail_em(
                    symbol="近一月", date=date_fmt
                )
                if df_buy is not None and not df_buy.empty:
                    # 根据实际返回的列做适配
                    all_seats.append(self._parse_seat_data(df_buy, trade_date))
            except Exception as e:
                logger.warning(f"[AKShare] 获取买入席位失败: {e}")

            # 也可以按个股获取详细席位
            # 先获取当日龙虎榜股票列表
            try:
                lhb_stocks = ak.stock_lhb_detail_em(date=date_fmt)
                if lhb_stocks is not None and not lhb_stocks.empty:
                    for _, row in lhb_stocks.iterrows():
                        try:
                            code = str(row.get("代码", ""))
                            if not code:
                                continue

                            df_detail = ak.stock_lhb_stock_statistic_em(
                                symbol=code
                            )
                            if df_detail is not None and not df_detail.empty:
                                df_detail["ts_code"] = self._to_ts_code(code)
                                df_detail["trade_date"] = trade_date
                                all_seats.append(df_detail)
                        except Exception:
                            continue
            except Exception as e:
                logger.warning(f"[AKShare] 获取个股席位失败: {e}")

            if not all_seats:
                return None

            df = pd.concat(all_seats, ignore_index=True)
            return self._normalize_seat_columns(df)

        return self._safe_request(_fetch)

    # ================================================================
    # 实时行情
    # ================================================================

    def get_realtime_quote(self, ts_codes: list) -> Optional[pd.DataFrame]:
        """获取实时行情快照"""

        def _fetch():
            import akshare as ak

            df = ak.stock_zh_a_spot_em()

            if df is None or df.empty:
                return None

            # 统一列名
            df = df.rename(columns={
                "代码": "symbol",
                "名称": "name",
                "最新价": "current_price",
                "涨跌幅": "pct_change",
                "涨跌额": "price_change",
                "成交量": "vol",
                "成交额": "amount",
                "振幅": "amplitude",
                "最高": "high",
                "最低": "low",
                "今开": "open",
                "昨收": "pre_close",
                "换手率": "turnover_rate",
                "市盈率-动态": "pe_ttm",
                "市净率": "pb",
                "总市值": "total_mv",
                "流通市值": "circ_mv",
            })

            # 添加 ts_code
            df["ts_code"] = df["symbol"].apply(self._to_ts_code)

            # 筛选目标股票
            if ts_codes:
                df = df[df["ts_code"].isin(ts_codes)]

            # 百分比转小数
            for col in ["pct_change", "turnover_rate", "amplitude"]:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors="coerce") / 100.0

            return df

        return self._safe_request(_fetch)

    # ================================================================
    # 市场情绪
    # ================================================================

    def get_market_sentiment(self, trade_date: str) -> Optional[pd.DataFrame]:
        """获取市场情绪数据(涨跌停统计等)"""

        def _fetch():
            import akshare as ak

            result = {}

            # 1. 涨跌停统计
            try:
                df_limit = ak.stock_zt_pool_em(date=trade_date)
                result["limit_up_count"] = len(df_limit) if df_limit is not None else 0
            except Exception:
                result["limit_up_count"] = 0

            try:
                df_dt = ak.stock_zt_pool_dtgc_em(date=trade_date)
                result["limit_down_count"] = len(df_dt) if df_dt is not None else 0
            except Exception:
                result["limit_down_count"] = 0

            # 2. 连板统计
            try:
                df_lb = ak.stock_zt_pool_zbgc_em(date=trade_date)
                if df_lb is not None and not df_lb.empty:
                    if "连板数" in df_lb.columns:
                        result["cont_limit_up"] = len(df_lb)
                        result["max_cont_board"] = int(df_lb["连板数"].max())
                        result["board_2"] = len(df_lb[df_lb["连板数"] == 2])
                        result["board_3"] = len(df_lb[df_lb["连板数"] == 3])
                        result["board_4"] = len(df_lb[df_lb["连板数"] == 4])
                        result["board_5_plus"] = len(df_lb[df_lb["连板数"] >= 5])
            except Exception:
                pass

            # 3. 全市场涨跌分布
            try:
                df_spot = ak.stock_zh_a_spot_em()
                if df_spot is not None and not df_spot.empty:
                    pct_col = "涨跌幅"
                    if pct_col in df_spot.columns:
                        pct = pd.to_numeric(df_spot[pct_col], errors="coerce")
                        result["up_count"] = int((pct > 0).sum())
                        result["down_count"] = int((pct < 0).sum())
                        result["flat_count"] = int((pct == 0).sum())
                        result["avg_pct_change"] = round(float(pct.mean()) / 100, 4)
                        result["median_pct_change"] = round(float(pct.median()) / 100, 4)
                        result["strong_stock_count"] = int((pct > 5).sum())
                        result["weak_stock_count"] = int((pct < -5).sum())

                        up = result.get("up_count", 0)
                        down = result.get("down_count", 1)
                        result["up_down_ratio"] = round(up / max(down, 1), 4)
            except Exception:
                pass

            # 转为DataFrame
            result["trade_date"] = trade_date
            df = pd.DataFrame([result])

            # 计算综合情绪评分 (0-100)
            df["sentiment_score"] = self._calc_sentiment_score(result)
            df["sentiment_level"] = df["sentiment_score"].apply(self._score_to_level)

            return df

        return self._safe_request(_fetch)

    # ================================================================
    # 股票基础信息
    # ================================================================

    def get_stock_info(self) -> Optional[pd.DataFrame]:
        """获取股票基础信息列表"""

        def _fetch():
            import akshare as ak

            df = ak.stock_info_a_code_name()

            if df is None or df.empty:
                return None

            df = df.rename(columns={
                "code": "symbol",
                "name": "name",
            })
            df["ts_code"] = df["symbol"].apply(self._to_ts_code)
            df["is_active"] = True

            return df

        return self._safe_request(_fetch)

    # ================================================================
    # 交易日历
    # ================================================================

    def get_trade_calendar(
        self,
        exchange: str = "SSE",
        start_date: str = "20200101",
        end_date: str = "20261231",
    ) -> Optional[pd.DataFrame]:
        """交易日历。

        AKShare 的 `tool_trade_date_hist_sina()` 返回 A 股所有历史交易日
        (single column DataFrame: trade_date)。
        我们在此基础上:
          1. 按 [start_date, end_date] 过滤
          2. 扩展为每天一行,标记 is_open,并补 pretrade_date
          3. 输出字段: exchange / cal_date / is_open / pretrade_date
        A 股两所交易日同步,故 exchange 参数仅做标签用。
        """

        def _fetch():
            import akshare as ak

            raw = ak.tool_trade_date_hist_sina()
            if raw is None or raw.empty:
                return None

            # AKShare 返回列名固定为 trade_date
            td = pd.to_datetime(raw["trade_date"]).dt.date
            open_set = set(td)

            start = pd.to_datetime(start_date).date()
            end = pd.to_datetime(end_date).date()

            rows = []
            prev_open = None
            cur = start
            while cur <= end:
                is_open = cur in open_set
                rows.append({
                    "exchange": exchange,
                    "cal_date": cur,
                    "is_open": is_open,
                    "pretrade_date": prev_open,
                })
                if is_open:
                    prev_open = cur
                cur += pd.Timedelta(days=1)

            return pd.DataFrame(rows)

        return self._safe_request(_fetch)

    # ================================================================
    # 工具方法
    # ================================================================

    @staticmethod
    def _convert_code(ts_code: str) -> str:
        """000001.SZ → sz000001"""
        parts = ts_code.split(".")
        if len(parts) != 2:
            return ts_code
        code, exchange = parts
        prefix = "sh" if exchange.upper() in ("SH", "SSE") else "sz"
        return f"{prefix}{code}"

    @staticmethod
    def _to_ts_code(symbol: str) -> str:
        """
        纯代码转ts_code格式
        000001 → 000001.SZ
        600000 → 600000.SH
        300001 → 300001.SZ
        688001 → 688001.SH
        """
        symbol = str(symbol).strip()
        # 去掉可能的前缀
        symbol = symbol.replace("sz", "").replace("sh", "").replace("SZ", "").replace("SH", "")

        if symbol.startswith(("6", "9")):
            return f"{symbol}.SH"
        elif symbol.startswith(("0", "2", "3")):
            return f"{symbol}.SZ"
        elif symbol.startswith("4") or symbol.startswith("8"):
            return f"{symbol}.BJ"
        else:
            return f"{symbol}.SZ"

    def _parse_seat_data(self, df: pd.DataFrame, trade_date: str) -> pd.DataFrame:
        """解析席位数据，统一格式"""
        df["trade_date"] = trade_date
        return df

    def _normalize_seat_columns(self, df: pd.DataFrame) -> pd.DataFrame:
        """标准化席位列名"""
        rename_map = {
            "营业部名称": "broker_name",
            "买入额": "buy_amount",
            "卖出额": "sell_amount",
            "净额": "net_amount",
            "买入额(万)": "buy_amount",
            "卖出额(万)": "sell_amount",
            "净买入额(万)": "net_amount",
        }
        df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns})

        # 金额转数值
        for col in ["buy_amount", "sell_amount", "net_amount"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

        return df

    @staticmethod
    def _calc_sentiment_score(data: dict) -> float:
        """计算综合情绪评分 0-100"""
        score = 50.0  # 基准分

        # 涨跌比贡献 (±20)
        ratio = data.get("up_down_ratio", 1.0)
        if ratio > 2.0:
            score += 20
        elif ratio > 1.5:
            score += 15
        elif ratio > 1.0:
            score += 5
        elif ratio > 0.5:
            score -= 10
        else:
            score -= 20

        # 涨停数贡献 (±15)
        limit_up = data.get("limit_up_count", 0)
        if limit_up > 80:
            score += 15
        elif limit_up > 50:
            score += 10
        elif limit_up > 20:
            score += 5
        elif limit_up < 10:
            score -= 10

        # 跌停数贡献 (±10)
        limit_down = data.get("limit_down_count", 0)
        if limit_down > 30:
            score -= 10
        elif limit_down > 15:
            score -= 5

        # 连板高度贡献 (±5)
        max_board = data.get("max_cont_board", 0)
        if max_board >= 5:
            score += 5
        elif max_board <= 2:
            score -= 5

        return max(0, min(100, round(score, 2)))

    @staticmethod
    def _score_to_level(score: float) -> str:
        """评分转情绪级别"""
        if score >= 85:
            return "EXTREME"
        elif score >= 70:
            return "HOT"
        elif score >= 55:
            return "WARM"
        elif score >= 40:
            return "NEUTRAL"
        elif score >= 25:
            return "COLD"
        else:
            return "ICE_COLD"

