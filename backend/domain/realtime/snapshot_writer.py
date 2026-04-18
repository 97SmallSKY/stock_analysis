"""W8-2 market_snapshots 批量入库

设计:
  - 输入 CrawlerAdapter.get_intraday_snapshot() 返回的 DataFrame 或
    list[dict], 转成 execute_values 批量写 market_snapshots.
  - 幂等: PRIMARY KEY (ts, ts_code), 冲突 ON CONFLICT DO UPDATE 取新值,
    便于补抓 (最新一次写入覆盖同秒同股).
  - source 列默认 'crawler', 可由调用方覆盖.
  - snapshot_type 默认 'REALTIME', 盘后 close_snapshot 时传 'CLOSE'.
  - 一次可容纳数千行, 10s 周期 300 ~ 1000 码完全够用.
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Iterable, Optional, Sequence, Union

import pandas as pd

logger = logging.getLogger(__name__)

# market_snapshots 字段顺序 (execute_values template 与此一致)
COLUMNS: tuple[str, ...] = (
    "ts_code", "ts", "trade_date", "snapshot_type",
    "price", "pct_change", "change",
    "volume", "amount",
    "open", "high", "low", "prev_close",
    "bid1", "bid1_vol", "ask1", "ask1_vol",
    "source",
)


def _row_from_dict(
    d: dict, *, snapshot_type: str, source: str,
) -> Optional[tuple]:
    ts_code = d.get("ts_code")
    ts = d.get("ts")
    if not ts_code or ts is None:
        return None
    # ts_code 统一大写
    ts_code = str(ts_code).upper()
    # ts 必须是 datetime (带 tz 最好, 无 tz Postgres 会按 server tz 视作 TIMESTAMPTZ)
    if isinstance(ts, str):
        try:
            ts = datetime.fromisoformat(ts)
        except ValueError:
            return None
    if not isinstance(ts, datetime):
        return None
    trade_date: date = ts.date()

    def _g(k):
        v = d.get(k)
        if v is None:
            return None
        # pandas NaN
        try:
            if pd.isna(v):
                return None
        except (TypeError, ValueError):
            pass
        return v

    return (
        ts_code, ts, trade_date, snapshot_type,
        _g("price"), _g("pct_change"), _g("change"),
        _g("volume"), _g("amount"),
        _g("open"), _g("high"), _g("low"), _g("prev_close"),
        _g("bid1"), _g("bid1_vol"), _g("ask1"), _g("ask1_vol"),
        source,
    )


def _iter_dict_rows(
    records: Union[pd.DataFrame, Iterable[dict]],
) -> Iterable[dict]:
    if isinstance(records, pd.DataFrame):
        if records.empty:
            return
        for _, r in records.iterrows():
            yield r.to_dict()
    else:
        yield from records


def build_rows(
    records: Union[pd.DataFrame, Iterable[dict]],
    *,
    snapshot_type: str = "REALTIME",
    source: str = "crawler",
) -> list[tuple]:
    """将 DataFrame / list[dict] 规范化为待插入元组.

    丢弃 ts_code 或 ts 缺失/无法解析的行.
    """
    out: list[tuple] = []
    for d in _iter_dict_rows(records):
        row = _row_from_dict(d, snapshot_type=snapshot_type, source=source)
        if row is not None:
            out.append(row)
    return out


# 构造 execute_values 用的 SQL. 外部单测用它校验正确性.
INSERT_SQL = f"""
INSERT INTO market_snapshots ({', '.join(COLUMNS)})
VALUES %s
ON CONFLICT (ts, ts_code) DO UPDATE SET
    price        = EXCLUDED.price,
    pct_change   = EXCLUDED.pct_change,
    change       = EXCLUDED.change,
    volume       = EXCLUDED.volume,
    amount       = EXCLUDED.amount,
    open         = EXCLUDED.open,
    high         = EXCLUDED.high,
    low          = EXCLUDED.low,
    prev_close   = EXCLUDED.prev_close,
    bid1         = EXCLUDED.bid1,
    bid1_vol     = EXCLUDED.bid1_vol,
    ask1         = EXCLUDED.ask1,
    ask1_vol     = EXCLUDED.ask1_vol,
    source       = EXCLUDED.source
"""


def insert_snapshots(
    pg_conn_factory,
    records: Union[pd.DataFrame, Iterable[dict]],
    *,
    snapshot_type: str = "REALTIME",
    source: str = "crawler",
    page_size: int = 500,
) -> int:
    """批量插入. pg_conn_factory 是上下文管理器 (默认 backend.db.session.pg_conn).

    返回成功入库的行数 (= rows 长度, 冲突 upsert 也算).
    """
    rows = build_rows(records, snapshot_type=snapshot_type, source=source)
    if not rows:
        return 0
    # psycopg2 execute_values 用闭包, 不强依赖 psycopg2 才装上
    from psycopg2.extras import execute_values
    with pg_conn_factory() as conn:
        with conn.cursor() as cur:
            execute_values(cur, INSERT_SQL, rows, page_size=page_size)
        conn.commit()
    return len(rows)
