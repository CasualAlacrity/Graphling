import os
from collections.abc import AsyncGenerator

from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

load_dotenv()


def _async_url(url: str) -> str:
    """asyncpg driver for async SQLAlchemy."""
    return url.replace("postgresql://", "postgresql+asyncpg://")


# Engine + sessionmaker are created ONCE at import and reused for the app's lifetime, but
# NullPool means no underlying connection is ever kept alive between checkouts. Required
# here (not just a perf choice): this app runs three independent event loops against this
# same engine — Chainlit's loop, the overlay's qasync loop, and the voice loop's own
# asyncio.run() in a dedicated background thread (overlay_app.py) — and an asyncpg
# connection is bound to whichever loop created it. A pooled connection checked out from a
# different loop than the one that opened it fails with "attached to a different loop."
# NullPool sidesteps this entirely by never handing out a connection older than the
# current checkout.
engine = create_async_engine(
    _async_url(os.getenv("TRADE_DB_URL")),
    poolclass=NullPool,
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
