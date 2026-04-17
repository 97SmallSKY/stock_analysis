"""
数据库连接和Session工厂
"""
from sqlalchemy.ext.asyncio import (
    create_async_engine,
    AsyncSession,
    async_sessionmaker
)
from backend import settings

# ============================================================
# 异步引擎配置
# ============================================================
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DEBUG,
    pool_size=20,
    max_overflow=10,
    pool_pre_ping=True,
    pool_recycle=3600,  # 1小时回收连接
)

# ============================================================
# 异步 Session 工厂
# ============================================================
AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


# ============================================================
# FastAPI 依赖注入
# ============================================================
async def get_db():
    """
    FastAPI 依赖注入函数
    
    Usage:
        @app.get("/api/robots")
        async def get_robots(db: AsyncSession = Depends(get_db)):
            result = await db.execute(select(Robot))
            return result.scalars().all()
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


# ============================================================
# 同步引擎（用于 APScheduler JobStore）
# ============================================================
from sqlalchemy import create_engine

# 使用同步 URL
sync_database_url = settings.SYNC_DATABASE_URL

sync_engine = create_engine(
    sync_database_url,
    echo=settings.DEBUG,
    pool_size=10,
    max_overflow=5,
    pool_pre_ping=True,
    pool_recycle=3600,
)
