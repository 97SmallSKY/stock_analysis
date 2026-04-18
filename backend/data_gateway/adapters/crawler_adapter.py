# backend/data_gateway/adapters/crawler_adapter.py

import logging
import time
from typing import Optional

import pandas as pd

from ..base_adapter import BaseDataAdapter
from ..category import AdapterName

logger = logging.getLogger(__name__)


class CrawlerAdapter(BaseDataAdapter):
    """
    东方财富爬虫适配器(兜底数据源)

    支持: LHB_DETAIL, LHB_BROKER, DAILY_KLINE
    """

    BASE_URL = "https://datacenter-web.eastmoney.com/api"

    def __init__(self, config: Optional[dict] = None):
        super().__init__(
            name=AdapterName.CRAWLER,
            config=config or {},
            rate_limit=config.get("rate_limit", 20) if config else 20,
            timeout=config.get("timeout", 30) if config else 30,
        )
        self._session = None

    def _do_initialize(self):
        import requests
        self._session = requests.Session()
        # 绕过 SG 代理，直连国内源
        self._session.trust_env = False
        self._session.proxies = {}
        self._session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Referer": "https://data.eastmoney.com/",
            "Accept": "application/json",
        })
        # 验证连通性
        resp = self._session.get(
            "https://data.eastmoney.com/",
            timeout=10
        )
        if resp.status_code != 200:
            raise ConnectionError(f"东方财富连接失败: HTTP {resp.status_code}")

    def health_check(self) -> bool:
        try:
            if self._session is None:
                return False
            resp = self._session.get(
                "https://data.eastmoney.com/",
                timeout=5
            )
            return resp.status_code == 200
        except Exception:
            return False

    # ================================================================
    # 龙虎榜明细
    # ================================================================

    def get_lhb_detail(self, trade_date: str) -> Optional[pd.DataFrame]:
        """从东方财富爬取龙虎榜明细"""

        def _fetch():
            date_fmt = f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:]}"

            url = (
                f"{self.BASE_URL}/data/v1/get?"
                f"reportName=RPT_DAILYBILLBOARD_DETAILSNEW"
                f"&columns=ALL"
                f"&filter=(TRADE_DATE%3D%27{date_fmt}%27)"
                f"&pageNumber=1&pageSize=500"
                f"&sortTypes=-1&sortColumns=SECURITY_CODE"
                f"&source=WEB&client=WEB"
            )

            resp = self._session.get(url, timeout=self.timeout)
            data = resp.json()

            if not data or data.get("code") != 0:
                logger.warning(f"[Crawler] 东方财富接口返回异常: {data}")
                return None

            result = data.get("result", {})
            items = result.get("data", [])

            if not items:
                return None

            df = pd.DataFrame(items)

            # 映射列名
            column_map = {
                "SECURITY_CODE": "symbol",
                "SECURITY_NAME_ABBR": "name",
                "TRADE_DATE": "trade_date",
                "CLOSE_PRICE": "close_price",
                "CHANGE_RATE": "pct_change",
                "TURNOVERRATE": "turnover_rate",
                "BILLBOARD_NET_AMT": "l_net",
                "BILLBOARD_BUY_AMT": "l_buy",
                "BILLBOARD_SELL_AMT": "l_sell",
                "BILLBOARD_DEAL_AMT": "l_amount",
                "ACCUM_AMOUNT": "amount",
                "DEAL_NET_RATIO": "net_pct",
                "EXPLANATION": "reason",
                "FREE_MARKET_CAP": "float_market_cap",
            }
            df = df.rename(columns={k: v for k, v in column_map.items() if k in df.columns})

            # 标准化
            if "symbol" in df.columns:
                df["ts_code"] = df["symbol"].apply(self._to_ts_code)

            df["trade_date"] = trade_date

            # 百分比转小数
            for col in ["pct_change", "turnover_rate", "net_pct"]:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors="coerce") / 100.0

            # 金额: 东方财富返回单位是元，转万
            for col in ["l_net", "l_buy", "l_sell", "l_amount", "amount"]:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors="coerce") / 10000.0

            return df

        return self._safe_request(_fetch)

    # ================================================================
    # 龙虎榜营业部席位
    # ================================================================

    def get_lhb_seats(self, trade_date: str) -> Optional[pd.DataFrame]:
        """从东方财富爬取龙虎榜营业部席位"""

        def _fetch():
            date_fmt = f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:]}"

            url = (
                f"{self.BASE_URL}/data/v1/get?"
                f"reportName=RPT_BILLBOARD_DAILYDETAILSBUY"
                f"&columns=ALL"
                f"&filter=(TRADE_DATE%3D%27{date_fmt}%27)"
                f"&pageNumber=1&pageSize=1000"
                f"&sortTypes=-1&sortColumns=BUY"
                f"&source=WEB&client=WEB"
            )

            resp = self._session.get(url, timeout=self.timeout)
            data = resp.json()

            if not data or data.get("code") != 0:
                return None

            buy_items = data.get("result", {}).get("data", [])

            # 卖出席位
            url_sell = url.replace(
                "RPT_BILLBOARD_DAILYDETAILSBUY",
                "RPT_BILLBOARD_DAILYDETAILSSELL"
            ).replace("sortColumns=BUY", "sortColumns=SELL")

            resp_sell = self._session.get(url_sell, timeout=self.timeout)
            data_sell = resp_sell.json()
            sell_items = data_sell.get("result", {}).get("data", []) if data_sell else []

            all_items = []

            # 解析买入席位
            for item in buy_items:
                all_items.append({
                    "ts_code": self._to_ts_code(str(item.get("SECURITY_CODE", ""))),
                    "trade_date": trade_date,
                    "broker_name": item.get("OPERATEDEPT_NAME", ""),
                    "buy_amount": (item.get("BUY", 0) or 0) / 10000,          # 元→万
                    "sell_amount": (item.get("SELL", 0) or 0) / 10000,
                    "net_amount": (item.get("NET", 0) or 0) / 10000,
                    "direction": "BUY",
                    "rank": item.get("RANK", 0),
                    "broker_type": "INSTITUTION" if "机构" in str(item.get("OPERATEDEPT_NAME", "")) else "UNKNOWN",
                    "is_institution": "机构" in str(item.get("OPERATEDEPT_NAME", "")),
                })

            # 解析卖出席位
            for item in sell_items:
                all_items.append({
                    "ts_code": self._to_ts_code(str(item.get("SECURITY_CODE", ""))),
                    "trade_date": trade_date,
                    "broker_name": item.get("OPERATEDEPT_NAME", ""),
                    "buy_amount": (item.get("BUY", 0) or 0) / 10000,
                    "sell_amount": (item.get("SELL", 0) or 0) / 10000,
                    "net_amount": (item.get("NET", 0) or 0) / 10000,
                    "direction": "SELL",
                    "rank": item.get("RANK", 0),
                    "broker_type": "INSTITUTION" if "机构" in str(item.get("OPERATEDEPT_NAME", "")) else "UNKNOWN",
                    "is_institution": "机构" in str(item.get("OPERATEDEPT_NAME", "")),
                })

            if not all_items:
                return None

            return pd.DataFrame(all_items)

        return self._safe_request(_fetch)

    # ================================================================
    # 日K线(兜底)
    # ================================================================

    def get_daily_kline(
        self, ts_code: str, start_date: str, end_date: str
    ) -> Optional[pd.DataFrame]:
        """从东方财富爬取日K线"""

        def _fetch():
            symbol = ts_code.split(".")[0]
            # 东方财富代码: 深圳0/3开头加前缀0, 上海6开头加前缀1
            if symbol.startswith(("6", "9")):
                secid = f"1.{symbol}"
            else:
                secid = f"0.{symbol}"

            url = (
                f"https://push2his.eastmoney.com/api/qt/stock/kline/get?"
                f"secid={secid}"
                f"&fields1=f1,f2,f3,f4,f5,f6"
                f"&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"
                f"&klt=101"       # 日K
                f"&fqt=1"         # 前复权
                f"&beg={start_date}&end={end_date}"
            )

            resp = self._session.get(url, timeout=self.timeout)
            data = resp.json()

            klines = data.get("data", {}).get("klines", [])
            if not klines:
                return None

            rows = []
            for line in klines:
                # 格式: 日期,开,收,高,低,成交量,成交额,振幅,涨跌幅,涨跌额,换手率
                fields = line.split(",")
                if len(fields) < 11:
                    continue
                rows.append({
                    "ts_code": ts_code,
                    "trade_date": fields[0].replace("-", ""),
                    "open": float(fields[1]),
                    "close": float(fields[2]),
                    "high": float(fields[3]),
                    "low": float(fields[4]),
                    "vol": float(fields[5]),
                    "amount": float(fields[6]),
                    "amplitude": float(fields[7]) / 100,
                    "pct_change": float(fields[8]) / 100,
                    "price_change": float(fields[9]),
                    "turnover_rate": float(fields[10]) / 100,
                })

            return pd.DataFrame(rows)

        return self._safe_request(_fetch)

    # ================================================================
    # 盘中快照 (W8)
    # ================================================================

    #
    # 东方财富 push2 多股批量接口:
    #   https://push2.eastmoney.com/api/qt/ulist.np/get?secids=0.000001,1.600000&fields=...
    #
    # 字段定义 (东财 push2 f-code):
    #   f1   市场 (0=深, 1=沪, 116=港, 105=美)
    #   f2   最新价 (×100, 保留2位 -> /100)
    #   f3   涨跌幅 (×100 -> /100, 单位 %)
    #   f4   涨跌额 (×100)
    #   f5   成交量 (手)
    #   f6   成交额 (元)
    #   f15  最高 (×100)
    #   f16  最低 (×100)
    #   f17  开盘 (×100)
    #   f18  昨收 (×100)
    #   f12  代码
    #   f14  名称
    #
    # 5 档买卖 (单股接口 stock/get, 这里批量先不取 5档, 统一留 None)
    #
    _SNAPSHOT_FIELDS = "f1,f2,f3,f4,f5,f6,f12,f14,f15,f16,f17,f18"

    @staticmethod
    def _ts_code_to_secid(ts_code: str) -> Optional[str]:
        """'600000.SH' -> '1.600000', '000001.SZ' -> '0.000001'."""
        ts = str(ts_code or "").strip().upper()
        if "." not in ts:
            return None
        code, mkt = ts.split(".", 1)
        if mkt == "SH":
            return f"1.{code}"
        if mkt == "SZ":
            return f"0.{code}"
        if mkt == "BJ":
            return f"0.{code}"  # 北交所也走 0
        return None

    @staticmethod
    def _scale(v, factor: float = 100.0) -> Optional[float]:
        """东财 ×100 整数 -> float. '-' 或 None 返回 None."""
        if v in (None, "-", ""):
            return None
        try:
            return float(v) / factor
        except (TypeError, ValueError):
            return None

    def get_intraday_snapshot(
        self, ts_codes: list
    ) -> Optional[pd.DataFrame]:
        """批量拉盘中快照 (东财 push2 ulist.np).

        返回列: ts_code, ts, name, price, pct_change, change,
                volume, amount, open, high, low, prev_close.
                5 档买卖为 None (批量接口不提供, 如需请单股另取).
        """
        if not ts_codes:
            return pd.DataFrame()

        # ts_code -> secid, 顺带保留原始代码用于结果回填
        secids: list[str] = []
        valid: list[str] = []
        for tc in ts_codes:
            sec = self._ts_code_to_secid(tc)
            if sec is None:
                logger.debug(f"[crawler.snapshot] 无效 ts_code: {tc!r}")
                continue
            secids.append(sec)
            valid.append(tc.upper())
        if not secids:
            return pd.DataFrame()

        def _fetch() -> pd.DataFrame:
            import datetime as _dt
            url = "https://push2.eastmoney.com/api/qt/ulist.np/get"
            params = {
                "secids": ",".join(secids),
                "fields": self._SNAPSHOT_FIELDS,
                "ut": "bd1d9ddb04089700cf9c27f6f7426281",
                "fltt": "2",
                "invt": "2",
            }
            resp = self._session.get(url, params=params, timeout=self.timeout)
            resp.raise_for_status()
            js = resp.json() or {}
            data = (js.get("data") or {}).get("diff") or []
            now = _dt.datetime.now()
            rows = []
            for it in data:
                code = it.get("f12")
                f1 = it.get("f1")
                ts_code = None
                if code:
                    # f1: 0=深, 1=沪
                    if f1 == 1:
                        ts_code = f"{code}.SH"
                    elif f1 == 0:
                        ts_code = f"{code}.SZ"
                    else:
                        ts_code = f"{code}.SZ"
                rows.append({
                    "ts_code": ts_code,
                    "ts": now,
                    "name": it.get("f14"),
                    "price": self._scale(it.get("f2")),
                    "pct_change": self._scale(it.get("f3")),
                    "change": self._scale(it.get("f4")),
                    "volume": int(it["f5"]) if it.get("f5") not in (None, "-", "") else None,
                    "amount": float(it["f6"]) if it.get("f6") not in (None, "-", "") else None,
                    "high": self._scale(it.get("f15")),
                    "low": self._scale(it.get("f16")),
                    "open": self._scale(it.get("f17")),
                    "prev_close": self._scale(it.get("f18")),
                })
            return pd.DataFrame(rows)

        return self._safe_request(_fetch, max_retries=1)

    # ================================================================
    # 工具方法
    # ================================================================

    @staticmethod
    def _to_ts_code(symbol: str) -> str:
        """000001 → 000001.SZ"""
        symbol = str(symbol).strip().zfill(6)
        if symbol.startswith(("6", "9")):
            return f"{symbol}.SH"
        elif symbol.startswith(("0", "2", "3")):
            return f"{symbol}.SZ"
        return f"{symbol}.SZ"
