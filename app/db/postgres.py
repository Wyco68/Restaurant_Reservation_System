"""PostgreSQL connection (async SQLAlchemy 2.x).

All queries go through SQLAlchemy with parameters. Plain SQL strings are
only used in migrations, and never contain user input.
"""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings

engine = create_async_engine(
    settings.postgres_dsn,
    echo=False,
    pool_size=10,
    max_overflow=5,
    pool_pre_ping=True,  # drop dead connections instead of failing a request
)

SessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency: one session per request.

    Endpoints call commit themselves. On an error the session is rolled
    back.
    """
    async with SessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


async def ping() -> bool:
    """Health check: run SELECT 1."""
    from sqlalchemy import text

    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


async def close() -> None:
    await engine.dispose()
