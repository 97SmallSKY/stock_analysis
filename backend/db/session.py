"""PostgreSQL 连接 —— 直接用 psycopg2，不挂 ORM。

ingest 层的写入都是批量 upsert，SQLAlchemy ORM 会拖慢节奏；
这里封装一个连接上下文管理器就够了。
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Iterator

import psycopg2
from psycopg2.extensions import connection as PGConnection

from backend.config import get_settings

logger = logging.getLogger(__name__)


def connect() -> PGConnection:
    s = get_settings().db
    return psycopg2.connect(
        host=s.host,
        port=s.port,
        dbname=s.database,
        user=s.user,
        password=s.password,
    )


@contextmanager
def pg_conn() -> Iterator[PGConnection]:
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
