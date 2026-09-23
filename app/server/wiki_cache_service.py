"""Server-side owner of the wiki cache (WikiCache table) — the shared-caching
counterpart to uex_cache_service.py, for star-citizen.wiki data (ship speeds, named
locations). Previously this lived only in StarCitizenWikiClient's own in-memory,
per-process cache with no Postgres involvement and no sharing across pilots at all;
this gives it the same shared shape the UEX cache already had.
"""
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from db.models import WikiCache, WikiCacheKind
from db.session import SessionLocal
from tools.starcitizenwiki.client import StarCitizenWikiClient
from tools.starcitizenwiki.models import LocationPosition, ShipSpeed

# Ship stats and named locations only change on game patches, not within a session —
# same reasoning/TTL as the UEX reference cache (tools/uexcorp/reference_cache_store.py).
CACHE_TTL = timedelta(hours=24)

# The one row LOCATIONS ever needs — it's a singleton dataset, not keyed by anything,
# but reuses WikiCache's generic kind/key shape rather than a separate table for one row.
LOCATIONS_KEY = "_all"

wiki_client = StarCitizenWikiClient()


async def _get_cached_payload(kind: WikiCacheKind, key: str) -> dict | None:
    async with SessionLocal() as session:
        result = await session.execute(select(WikiCache).where(WikiCache.kind == kind, WikiCache.key == key))
        row = result.scalar_one_or_none()
    if row is None:
        return None
    if datetime.now(UTC) - row.fetched_at > CACHE_TTL:
        return None
    return row.payload


async def _store_payload(kind: WikiCacheKind, key: str, payload: dict) -> None:
    stmt = pg_insert(WikiCache).values(kind=kind, key=key, payload=payload, fetched_at=datetime.now(UTC))
    stmt = stmt.on_conflict_do_update(
        index_elements=["kind", "key"],
        set_={"payload": stmt.excluded.payload, "fetched_at": stmt.excluded.fetched_at},
    )
    async with SessionLocal() as session:
        await session.execute(stmt)
        await session.commit()


async def get_ship_speed(ship_name: str) -> ShipSpeed | None:
    key = ship_name.lower()
    cached = await _get_cached_payload(WikiCacheKind.SHIP_SPEED, key)
    if cached is not None:
        # Always a {"ship_speed": ...} wrapper, never the bare value — payload is a
        # NOT NULL JSONB column, and a bare Python None here would bind as SQL NULL
        # rather than a JSON null, which nullable=False rejects. Wrapping sidesteps
        # that entirely rather than relying on a SQLAlchemy JSON-null sentinel.
        value = cached["ship_speed"]
        # model_construct, not model_validate: ShipSpeed's fields use validation_alias=
        # AliasPath("speed", "scm") etc. to parse the wiki's own nested API response
        # shape, but model_dump() (what we stored) always writes the flat field names —
        # model_validate() on that flat dict looks for the nested shape and always fails.
        # model_construct() skips validation and just assigns, which is fine here since
        # the payload came from a real ShipSpeed's own model_dump(), not outside input.
        return ShipSpeed.model_construct(**value) if value is not None else None

    ship_speed = await wiki_client.fetch_ship_speed_from_wiki(ship_name)
    # Cached even when None (ship not found) — a miss is itself worth remembering for
    # the TTL window rather than re-querying the wiki on every subsequent lookup, same
    # reasoning UexPriceCache already applies to an empty result.
    await _store_payload(
        WikiCacheKind.SHIP_SPEED, key, {"ship_speed": ship_speed.model_dump(mode="json") if ship_speed else None},
    )
    return ship_speed


async def get_locations() -> list[LocationPosition]:
    cached = await _get_cached_payload(WikiCacheKind.LOCATIONS, LOCATIONS_KEY)
    if cached is not None:
        return [LocationPosition.model_validate(row) for row in cached["locations"]]

    locations = await wiki_client.fetch_locations_from_wiki()
    await _store_payload(
        WikiCacheKind.LOCATIONS, LOCATIONS_KEY, {"locations": [loc.model_dump(mode="json") for loc in locations]},
    )
    return locations
