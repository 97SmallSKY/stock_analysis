"""新浪财经数据源适配器"""

import logging
import re
from typing import Optional

import pandas as pd
import requests

from ..base_adapter import BaseDataAdapter
from ..category import AdapterName

logger = logging.getLogger(__name__)


class SinaAdapter(BaseDataAdapter):
    """
    新浪财经数据源适配器 (免费)

    支持: REALTIME_QUOTE, DAILY_KLINE, LHB_DETAIL
    说明: 内部 requests.Session 显式禁用环境代理，避免走境外代理访问国内源。
    """

    def __init__(self, config: Optional[dict] = None):
        super().__init__(
            name=AdapterName.SINA,
            config=config or {},
            rate_limit=config.get("rate_limit", 30) if config else 30,
            timeout=config.get("timeout", 20) if config else 20,
        )
        self._session: Optional[requests.Session] = None

    def _do_initialize(self):
        """新浪接口无需鉴权，验证连通性"""
        self._session = self._build_session()
        resp = self._session.get(
            "https://hq.sinajs.cn/list=sh000001",
            timeout=10,
        )
        if resp.status_code != 200:
            raise ConnectionError(f"新浪接口连接失败: HTTP {resp.status_code}")

    def health_check(self) -> bool:
        try:
            if self._session is None:
                self._session = self._build_session()
            resp = self._session.get(
                "https://hq.sinajs.cn/list=sh000001",
                timeout=5,
            )
            return resp.status_code == 200 and len(resp.text) > 10
        except Exception:
            return False

    @staticmethod
    def _build_session() -> requests.Session:
        """构造国内源专用 session：禁用环境代理"""
        s = requests.Session()
        s.trust_env = False
        s.proxies = {}
        s.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Referer": "https://finance.sina.com.cn",
        })
        return s

    # ================================================================
    # 实时行情
    # ================================================================

    def get_realtime_quote(self, ts_codes: list) -> Optional[pd.DataFrame]:
        """批量获取实时行情"""

        def _fetch():
            if not ts_codes:
                return None

            sina_codes = [self._to_sina_code(c) for c in ts_codes]
            batch_size = 50
            all_rows = []

            for i in range(0, len(sina_codes), batch_size):
                batch = sina_codes[i:i + batch_size]
                url = f"https://hq.sinajs.cn/list={','.join(batch)}"

                resp = self._session.get(url, timeout=self.timeout)
                if resp.status_code != 200:
                    continue

                for line in resp.text.strip().split("\n"):
                    match = re.match(r'var hq_str_(\w+)="(.+)";', line)
                    if not match:
                        continue
                    sina_code = match.group(1)
                    fields = match.group(2).split(",")
                    if len(fields) < 32:
                        continue
                    row = self._parse_sina_quote(sina_code, fields)
                    if row:
                        all_rows.append(row)

            return pd.DataFrame(all_rows) if all_rows else None

        return self._safe_request(_fetch)

    def _parse_sina_quote(self, sina_code: str, fields: list) -> Optional[dict]:
        """
        字段顺序:
        0:名称 1:今开 2:昨收 3:当前价 4:最高 5:最低
        6:买一价 7:卖一价 8:成交量(股) 9:成交额(元)
        30:日期 31:时间
        """
        try:
            ts_code = self._from_sina_code(sina_code)
            pre_close = float(fields[2]) if fields[2] else 0.0
            current = float(fields[3]) if fields[3] else None
            return {
                "ts_code": ts_code,
                "name": fields[0],
                "open": float(fields[1]) if fields[1] else None,
                "pre_close": pre_close if pre_close else None,
                "current_price": current,
                "high": float(fields[4]) if fields[4] else None,
                "low": float(fields[5]) if fields[5] else None,
                "bid_price": float(fields[6]) if fields[6] else None,
                "ask_price": float(fields[7]) if fields[7] else None,
                "vol": float(fields[8]) / 100 if fields[8] else None,
                "amount": float(fields[9]) if fields[9] else None,
                "date": fields[30] if len(fields) > 30 else None,
                "time": fields[31] if len(fields) > 31 else None,
                "pct_change": round((current - pre_close) / pre_close, 4)
                if current is not None and pre_close > 0 else None,
            }
        except (ValueError, IndexError, ZeroDivisionError):
            return None

    # ================================================================
    # 日K线 (备用源，走 AKShare 的新浪接口)
    # ================================================================

    def get_daily_kline(
        self, ts_code: str, start_date: str, end_date: str
    ) -> Optional[pd.DataFrame]:

        def _fetch():
            import akshare as ak

            symbol = self._to_sina_code(ts_code)
            df = ak.stock_zh_a_daily(
                symbol=symbol,
                start_date=f"{start_date[:4]}-{start_date[4:6]}-{start_date[6:]}",
                end_date=f"{end_date[:4]}-{end_date[4:6]}-{end_date[6:]}",
                adjust="qfq",
            )
            if df is None or df.empty:
                return None

            df = df.rename(columns={"date": "trade_date"})
            df["ts_code"] = ts_code
            df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.strftime("%Y%m%d")
            if "close" in df.columns:
                df["pct_change"] = df["close"].pct_change()
            return df

        return self._safe_request(_fetch)

    # ================================================================
    # 龙虎榜明细
    # ================================================================

    def get_lhb_detail(self, trade_date: str) -> Optional[pd.DataFrame]:

        def _fetch():
            date_fmt = f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:]}"
            url = (
                f"https://vip.stock.finance.sina.com.cn/q/go.php/vLHBData/kind/ggtj/"
                f"date/{date_fmt}/order/fld_lhb_jmr/page/1.phtml"
            )
            resp = self._session.get(url, timeout=self.timeout)
            if resp.status_code != 200:
                return None

            dfs = pd.read_html(resp.text, encoding="gb2312")
            if not dfs:
                return None

            df = dfs[0]
            column_map = {
                "代码": "ts_code",
                "名称": "name",
                "收盘价": "close_price",
                "涨跌幅": "pct_change",
                "龙虎榜买入额(万)": "l_buy",
                "龙虎榜卖出额(万)": "l_sell",
                "龙虎榜净买额(万)": "l_net",
            }
            df = df.rename(columns={k: v for k, v in column_map.items() if k in df.columns})
            df["trade_date"] = trade_date
            if "ts_code" in df.columns:
                df["ts_code"] = df["ts_code"].astype(str).apply(self._to_ts_code_from_pure)
            return df

        return self._safe_request(_fetch)

    # ================================================================
    # 工具方法
    # ================================================================

    @staticmethod
    def _to_sina_code(ts_code: str) -> str:
        """000001.SZ → sz000001"""
        parts = ts_code.split(".")
        if len(parts) != 2:
            return ts_code
        code, exchange = parts
        prefix = "sh" if exchange.upper() in ("SH", "SSE") else "sz"
        return f"{prefix}{code}"

    @staticmethod
    def _from_sina_code(sina_code: str) -> str:
        """sz000001 → 000001.SZ"""
        if sina_code.startswith("sh"):
            return f"{sina_code[2:]}.SH"
        if sina_code.startswith("sz"):
            return f"{sina_code[2:]}.SZ"
        return sina_code

    @staticmethod
    def _to_ts_code_from_pure(symbol: str) -> str:
        """000001 → 000001.SZ"""
        symbol = str(symbol).strip().zfill(6)
        if symbol.startswith(("6", "9")):
            return f"{symbol}.SH"
        if symbol.startswith(("0", "2", "3")):
            return f"{symbol}.SZ"
        return f"{symbol}.SZ"
