import os
from typing import AsyncGenerator

from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

load_dotenv()


def _async_url(url: str) -> str:
    """asyncpg driver for async SQLAlchemy."""
    return url.replace("postgresql://", "postgresql+asyncpg://")


# Engine + sessionmaker are created ONCE at import and reused for the app's lifetime.
# Creating an engine per request spins up a fresh connection pool each time and
# exhausts Postgres connections under load.
engine = create_async_engine(
    _async_url(os.getenv("DATABASE_URL")),
    pool_pre_ping=True,
)

SessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
