import logging
from typing import Optional

import pandas as pd

from ..base_adapter import BaseDataAdapter
from ..category import AdapterName

logger = logging.getLogger(__name__)


class TuShareAdapter(BaseDataAdapter):
    """
    TuShare Pro 数据源适配器

    支持: DAILY_KLINE, FINANCIAL_DATA, STOCK_INFO, TRADE_CALENDAR, INDEX_DAILY
    文档: https://tushare.pro/document/2
    """

    def __init__(self, config: Optional[dict] = None):
        super().__init__(
            name=AdapterName.TUSHARE,
            config=config or {},
            rate_limit=config.get("rate_limit", 200) if config else 200,
            timeout=config.get("timeout", 30) if config else 30,
        )
        self._pro = None

    def _do_initialize(self):
        """初始化TuShare连接"""
        import tushare as ts

        token = self.config.get("token", "")
        if not token:
            raise ValueError("TuShare token 未配置")

        ts.set_token(token)
        self._pro = ts.pro_api()

        # 验证token有效性
        df = self._pro.trade_cal(exchange="SSE", start_date="20240101", end_date="20240102")
        if df is None or df.empty:
            raise ConnectionError("TuShare token 验证失败")

        logger.info(f"[TuShare] 初始化成功, token: {token[:8]}***")

    def health_check(self) -> bool:
        """健康检查"""
        try:
            if self._pro is None:
                return False
            df = self._pro.trade_cal(
                exchange="SSE",
                start_date="20240101",
                end_date="20240102"
            )
            return df is not None and not df.empty
        except Exception:
            return False

    # ================================================================
    # 日K线
    # ================================================================

    def get_daily_kline(
            self, ts_code: str, start_date: str, end_date: str
    ) -> Optional[pd.DataFrame]:
        """
        获取日K线数据

        Args:
            ts_code: 股票代码, 如 "000001.SZ"
            start_date: 开始日期, 如 "20240101"
            end_date: 结束日期, 如 "20241231"

        Returns:
            DataFrame with columns:
                ts_code, trade_date, open, high, low, close,
                pre_close, pct_chg, vol, amount
        """

        def _fetch():
            # 基础行情
            df_basic = self._pro.daily(
                ts_code=ts_code,
                start_date=start_date,
                end_date=end_date
            )

            if df_basic is None or df_basic.empty:
                return df_basic

            # 每日指标(换手率/市值/PE等)
            df_indicator = self._pro.daily_basic(
                ts_code=ts_code,
                start_date=start_date,
                end_date=end_date,
                fields=(
                    "ts_code,trade_date,turnover_rate,turnover_rate_f,"
                    "volume_ratio,pe_ttm,pb,total_mv,circ_mv"
                )
            )

            # 合并
            if df_indicator is not None and not df_indicator.empty:
                df = pd.merge(
                    df_basic, df_indicator,
                    on=["ts_code", "trade_date"],
                    how="left"
                )
            else:
                df = df_basic

            # 统一列名
            df = df.rename(columns={
                "pct_chg": "pct_change",
                "change": "price_change",
            })

            # 百分比转小数 (TuShare返回的是百分比形式)
            for col in ["pct_change", "turnover_rate", "turnover_rate_f"]:
                if col in df.columns:
                    df[col] = df[col] / 100.0

            return df

        return self._safe_request(_fetch)

    # ================================================================
    # 股票基础信息
    # ================================================================

    def get_stock_info(self) -> Optional[pd.DataFrame]:
        """获取全部股票基础信息"""

        def _fetch():
            # 获取上市状态: L-上市 D-退市 P-暂停上市
            df_l = self._pro.stock_basic(
                exchange="", list_status="L",
                fields=(
                    "ts_code,symbol,name,area,industry,market,"
                    "list_date,is_hs,exchange,curr_type"
                )
            )
            df_d = self._pro.stock_basic(
                exchange="", list_status="D",
                fields=(
                    "ts_code,symbol,name,area,industry,market,"
                    "list_date,delist_date,is_hs,exchange,curr_type"
                )
            )

            frames = [f for f in [df_l, df_d] if f is not None and not f.empty]
            if not frames:
                return None

            df = pd.concat(frames, ignore_index=True)
            df["is_active"] = df["ts_code"].isin(
                df_l["ts_code"] if df_l is not None else []
            )

            return df

        return self._safe_request(_fetch)

    # ================================================================
    # 交易日历
    # ================================================================

    def get_trade_calendar(
            self, exchange: str = "SSE", start_date: str = "20200101", end_date: str = "20261231"
    ) -> Optional[pd.DataFrame]:
        """获取交易日历"""

        def _fetch():
            df = self._pro.trade_cal(
                exchange=exchange,
                start_date=start_date,
                end_date=end_date,
                fields="exchange,cal_date,is_open,pretrade_date"
            )
            if df is not None and not df.empty:
                df["is_open"] = df["is_open"].astype(bool)
            return df

        return self._safe_request(_fetch)

    # ================================================================
    # 指数日线
    # ================================================================

    def get_index_daily(
            self, ts_code: str, start_date: str, end_date: str
    ) -> Optional[pd.DataFrame]:
        """获取指数日线数据"""

        def _fetch():
            df = self._pro.index_daily(
                ts_code=ts_code,
                start_date=start_date,
                end_date=end_date
            )
            if df is not None and not df.empty:
                df["pct_change"] = df["pct_chg"] / 100.0
            return df

        return self._safe_request(_fetch)

    # ================================================================
    # 财务数据
    # ================================================================

    def get_financial_data(
            self, ts_code: str, period: str = ""
    ) -> Optional[pd.DataFrame]:
        """获取财务数据(利润表)"""

        def _fetch():
            df = self._pro.income(
                ts_code=ts_code,
                period=period,
                fields=(
                    "ts_code,ann_date,f_ann_date,end_date,"
                    "revenue,operate_profit,total_profit,n_income,"
                    "basic_eps,diluted_eps"
                )
            )
            return df

        return self._safe_request(_fetch)
