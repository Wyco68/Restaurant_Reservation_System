"""PostgreSQL connection setup (async SQLAlchemy 2.x).

All access goes through the ORM / Core. Raw SQL string concatenation is
forbidden by course policy (team_project.pdf p.3) because it is an SQL
injection vector. Where literal SQL is unavoidable (extensions, the EXCLUDE
constraint), it is a fixed statement in a migration with no interpolated
user input.
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
    """FastAPI dependency. One session per request.

    The session is rolled back and closed automatically. Endpoints commit
    explicitly so the transaction boundary is visible in the code.
    """
    async with SessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


async def ping() -> bool:
    """Health check: can we actually round-trip a query?"""
    from sqlalchemy import text

    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


async def close() -> None:
    await engine.dispose()
