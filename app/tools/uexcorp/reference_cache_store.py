from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import UexReferenceCacheRecord
from tools.uexcorp.reference_cache import UexReferenceCache

# Structural reference data (terminals, commodities, vehicles, etc.) changes with game
# patches, not by the minute — matches the in-memory TTL UEXCorpClient already used.
CACHE_TTL = timedelta(hours=24)


async def load_reference_cache(session: AsyncSession) -> UexReferenceCache | None:
    result = await session.execute(
        select(UexReferenceCacheRecord).order_by(UexReferenceCacheRecord.fetched_at.desc()).limit(1)
    )
    record = result.scalars().first()
    if record is None:
        return None
    if datetime.now(timezone.utc) - record.fetched_at > CACHE_TTL:
        return None
    return UexReferenceCache.model_validate(record.payload)


async def store_reference_cache(session: AsyncSession, cache: UexReferenceCache) -> None:
    # Only one reference cache ever exists at a time — replace rather than accumulate rows.
    await session.execute(delete(UexReferenceCacheRecord))
    session.add(UexReferenceCacheRecord(payload=cache.model_dump(mode="json"), fetched_at=cache.fetched_at))
    await session.commit()
