from datetime import datetime, timedelta, timezone

from pydantic import ValidationError
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
    try:
        return UexReferenceCache.model_validate(record.payload)
    except ValidationError:
        # A row written under an older CachedTerminal/CachedCommodity/etc. shape (e.g. a
        # field added since) can't validate against the current schema. Treat that as a
        # miss rather than crashing the overlay — the caller will rebuild from UEX and
        # overwrite this row with a fresh, current-schema payload.
        return None


async def store_reference_cache(session: AsyncSession, cache: UexReferenceCache) -> None:
    # Only one reference cache ever exists at a time — replace rather than accumulate rows.
    await session.execute(delete(UexReferenceCacheRecord))
    session.add(UexReferenceCacheRecord(payload=cache.model_dump(mode="json"), fetched_at=cache.fetched_at))
    await session.commit()
