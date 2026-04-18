"""W8-2 订阅池维护

订阅池 = 盘中需要抓 10s 快照的 ts_code 集合. 来源三类:

    LHB_RECENT  近 T-20 上过龙虎榜的 ts_code, 每天收盘后刷新一次
                (ingest/scheduler 的日终 job 调 refresh_lhb_recent)
    ZT_TODAY    今日涨停池 (stock_zt_pool_em), 盘中每分钟刷一次
                (W8-3 的 APScheduler job 调 refresh_zt_today)
    MANUAL      人工 pin 的股票, 前端 /data-sources 扩展页加减

对应 DB 表 subscription_pool (db/init/03_W8_market_snapshots.sql).
读接口 get_active_codes() 返回 (ts_code, reasons) 的聚合列表, 供 W8-3 的
snapshot job 使用.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

logger = logging.getLogger(__name__)

# 过期策略默认: LHB 保留 20 个交易日 ~ 30 个自然日 ≈ 30d;
#             ZT 仅当天有效, 新 ZT 刷进时旧 ZT 一并清.
DEFAULT_LHB_TTL = timedelta(days=30)


def _now_utc() -> datetime:
    return datetime.now(tz=timezone.utc)


def upsert(
    pg_conn_factory,
    ts_codes: Iterable[str],
    reason: str,
    *,
    expires_at: Optional[datetime] = None,
) -> int:
    """将一批 ts_code 以指定 reason 写入 subscription_pool.

    用 ON CONFLICT DO UPDATE 续期 expires_at.
    """
    codes = [str(c).upper() for c in ts_codes if c]
    if not codes:
        return 0
    rows = [(c, reason, expires_at) for c in codes]
    from psycopg2.extras import execute_values
    sql = """
        INSERT INTO subscription_pool (ts_code, reason, expires_at)
        VALUES %s
        ON CONFLICT (ts_code, reason) DO UPDATE SET
            expires_at = EXCLUDED.expires_at,
            added_at   = NOW()
    """
    with pg_conn_factory() as conn, conn.cursor() as cur:
        execute_values(cur, sql, rows)
        conn.commit()
    return len(rows)


def replace_by_reason(
    pg_conn_factory,
    ts_codes: Iterable[str],
    reason: str,
    *,
    expires_at: Optional[datetime] = None,
) -> int:
    """用新集合完全替换某 reason 下的订阅.

    用于 ZT_TODAY 这种每分钟刷新全量的场景.
    """
    codes = [str(c).upper() for c in ts_codes if c]
    from psycopg2.extras import execute_values
    with pg_conn_factory() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM subscription_pool WHERE reason = %s", (reason,))
        if codes:
            execute_values(
                cur,
                """INSERT INTO subscription_pool (ts_code, reason, expires_at)
                   VALUES %s""",
                [(c, reason, expires_at) for c in codes],
            )
        conn.commit()
    return len(codes)


def remove(pg_conn_factory, ts_code: str, reason: Optional[str] = None) -> int:
    with pg_conn_factory() as conn, conn.cursor() as cur:
        if reason is None:
            cur.execute(
                "DELETE FROM subscription_pool WHERE ts_code = %s",
                (ts_code.upper(),),
            )
        else:
            cur.execute(
                "DELETE FROM subscription_pool WHERE ts_code = %s AND reason = %s",
                (ts_code.upper(), reason),
            )
        conn.commit()
        return cur.rowcount


def cleanup_expired(pg_conn_factory) -> int:
    """清理已过期订阅. 等价 CALL sp_snapshot_cleanup_subs()."""
    with pg_conn_factory() as conn, conn.cursor() as cur:
        cur.execute(
            """DELETE FROM subscription_pool
               WHERE expires_at IS NOT NULL AND expires_at < NOW()"""
        )
        conn.commit()
        return cur.rowcount


def get_active_codes(pg_conn_factory) -> list[str]:
    """返回当前有效订阅 ts_code 去重列表.

    有效条件: expires_at IS NULL OR expires_at > NOW().
    """
    with pg_conn_factory() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT DISTINCT ts_code
               FROM subscription_pool
               WHERE expires_at IS NULL OR expires_at > NOW()
               ORDER BY ts_code"""
        )
        return [r[0] for r in cur.fetchall()]


def get_detail(pg_conn_factory) -> list[dict]:
    """返回所有订阅明细 (ts_code, reason, expires_at)."""
    with pg_conn_factory() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT ts_code, reason, added_at, expires_at
               FROM subscription_pool
               ORDER BY ts_code, reason"""
        )
        cols = [c.name for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


# ------------------------------------------------------------------
# 方便调用的 refresh helpers: 外部 scheduler 调
# ------------------------------------------------------------------


def refresh_lhb_recent(
    pg_conn_factory,
    ts_codes: Iterable[str],
    ttl: timedelta = DEFAULT_LHB_TTL,
) -> int:
    """日终调用: 把近 T-20 LHB ts_code 写成 reason=LHB_RECENT, ttl=30d."""
    return upsert(
        pg_conn_factory, ts_codes, "LHB_RECENT",
        expires_at=_now_utc() + ttl,
    )


def refresh_zt_today(
    pg_conn_factory,
    ts_codes: Iterable[str],
) -> int:
    """盘中每分钟调用: 用今日涨停池全量替换 ZT_TODAY.

    expires_at = 今日 16:00 UTC+8 (= 08:00 UTC).
    """
    now = _now_utc().astimezone(timezone(timedelta(hours=8)))
    end_of_session = now.replace(hour=16, minute=0, second=0, microsecond=0)
    return replace_by_reason(
        pg_conn_factory, ts_codes, "ZT_TODAY",
        expires_at=end_of_session,
    )
