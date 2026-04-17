"""
SQLAlchemy ORM 模型基类和 Mixin
"""
from datetime import datetime
from sqlalchemy import Column, DateTime, BigInteger
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class TimestampMixin:
    """审计字段 Mixin"""
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=datetime.now
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=datetime.now,
        onupdate=datetime.now
    )
    deleted_at = Column(
        DateTime(timezone=True),
        nullable=True
    )


class IDMixin:
    """主键 Mixin"""
    id = Column(
        BigInteger,
        primary_key=True,
        autoincrement=True
    )
