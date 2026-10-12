from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import UexCacheKind, UexPriceCache
from tools.uexcorp.client import UEXCorpClient

# How long each kind of cached row stays fresh (docs/llm-gateway.md, "Cache lifetimes by
# how often the data actually changes", agreed 2026-10-09):
# - Commodity prices and routes are player-driven and shift any second. 30 minutes is
#   UEX's own max-age for them, so polling faster mostly gets the same answer back.
#   best_route ranks on these rows and the overlay's volatility icon reads them, so this
#   is real price data, not just "does this pairing exist".
# - Item and vehicle prices only change with game versions (Jeff, 2026-10-09), and orbit
#   distances are static world geometry: 7 days. A patch-day refresh comes from
#   server/refresh_static_caches.py (and the planned game-version poll).
PLAYER_DRIVEN_TTL = timedelta(minutes=30)
PER_PATCH_TTL = timedelta(days=7)
CACHE_TTL = {
    UexCacheKind.COMMODITY: PLAYER_DRIVEN_TTL,
    UexCacheKind.TERMINAL: PLAYER_DRIVEN_TTL,
    UexCacheKind.ROUTE: PLAYER_DRIVEN_TTL,
    UexCacheKind.ROUTE_BY_ORIGIN: PLAYER_DRIVEN_TTL,
    UexCacheKind.ITEM: PER_PATCH_TTL,
    UexCacheKind.VEHICLE_PURCHASE: PER_PATCH_TTL,
    UexCacheKind.VEHICLE_RENTAL: PER_PATCH_TTL,
    UexCacheKind.ORBIT_DISTANCES: PER_PATCH_TTL,
}


async def _get_cached_rows(session: AsyncSession, kind: UexCacheKind, entity_id: int) -> list[dict] | None:
    result = await session.execute(
        select(UexPriceCache).where(UexPriceCache.kind == kind, UexPriceCache.entity_id == entity_id)
    )
    cache_row = result.scalar_one_or_none()
    if cache_row is None:
        return None
    if datetime.now(UTC) - cache_row.fetched_at > CACHE_TTL[kind]:
        return None
    return cache_row.rows


async def _store_rows(session: AsyncSession, kind: UexCacheKind, entity_id: int, rows: list[dict]) -> None:
    stmt = pg_insert(UexPriceCache).values(
        kind=kind,
        entity_id=entity_id,
        rows=rows,
        fetched_at=datetime.now(UTC),
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["kind", "entity_id"],
        set_={"rows": stmt.excluded.rows, "fetched_at": stmt.excluded.fetched_at},
    )
    await session.execute(stmt)
    await session.commit()


async def get_commodity_price_rows(
    client: UEXCorpClient, session: AsyncSession, commodity_id: int
) -> list[dict]:
    cached = await _get_cached_rows(session, UexCacheKind.COMMODITY, commodity_id)
    if cached is not None:
        return cached

    rows = await client.get_commodity_prices(commodity_id)
    await _store_rows(session, UexCacheKind.COMMODITY, commodity_id, rows)
    return rows


async def get_terminal_price_rows(
    client: UEXCorpClient, session: AsyncSession, terminal_id: int
) -> list[dict]:
    cached = await _get_cached_rows(session, UexCacheKind.TERMINAL, terminal_id)
    if cached is not None:
        return cached

    rows = await client.get_terminal_prices(terminal_id)
    await _store_rows(session, UexCacheKind.TERMINAL, terminal_id, rows)
    return rows


async def get_commodity_route_rows(
    client: UEXCorpClient, session: AsyncSession, commodity_id: int
) -> list[dict]:
    # Every route for this commodity, unfiltered by origin/destination — the caller
    # filters locally. Lets a source-only or destination-only search reuse the same
    # cached fetch a commodity-specified search would have made, instead of needing
    # its own separate live call per search.
    cached = await _get_cached_rows(session, UexCacheKind.ROUTE, commodity_id)
    if cached is not None:
        return cached

    rows = await client.get_commodity_routes(commodity_id=commodity_id)
    await _store_rows(session, UexCacheKind.ROUTE, commodity_id, rows)
    return rows


# Split into a read and a fetch, unlike the get_*_rows helpers above, because best_route
# needs to know *which* origins are already warm before deciding what to spend its live
# call budget on. A single get-or-fetch helper can't express "use everything cached, then
# fetch only a few of the rest".
async def cached_routes_from_terminal(session: AsyncSession, terminal_id: int) -> list[dict] | None:
    """Routes out of this terminal if they're cached and fresh, else None. Never calls UEX."""
    return await _get_cached_rows(session, UexCacheKind.ROUTE_BY_ORIGIN, terminal_id)


async def fetch_routes_from_terminal(
    client: UEXCorpClient, session: AsyncSession, terminal_id: int
) -> list[dict]:
    """Live fetch of every route out of this terminal, stored for next time.

    Deliberately unfiltered by commodity even when the caller only wants one: the whole
    set is what gets cached, so a commodity-specific search also warms the general case,
    and the caller filters locally for free.
    """
    rows = await client.get_commodity_routes(origin_terminal_id=terminal_id)
    await _store_rows(session, UexCacheKind.ROUTE_BY_ORIGIN, terminal_id, rows)
    return rows


async def _get_or_fetch(session: AsyncSession, kind: UexCacheKind, entity_id: int, fetch) -> list[dict]:
    cached = await _get_cached_rows(session, kind, entity_id)
    if cached is not None:
        return cached
    rows = await fetch()
    await _store_rows(session, kind, entity_id, rows)
    return rows


async def get_item_price_rows(client: UEXCorpClient, session: AsyncSession, item_id: int) -> list[dict]:
    return await _get_or_fetch(session, UexCacheKind.ITEM, item_id, lambda: client.get_item_prices(item_id))


async def get_vehicle_purchase_price_rows(
    client: UEXCorpClient, session: AsyncSession, vehicle_id: int
) -> list[dict]:
    return await _get_or_fetch(
        session, UexCacheKind.VEHICLE_PURCHASE, vehicle_id, lambda: client.get_vehicle_purchase_prices(vehicle_id),
    )


async def get_vehicle_rental_price_rows(
    client: UEXCorpClient, session: AsyncSession, vehicle_id: int
) -> list[dict]:
    return await _get_or_fetch(
        session, UexCacheKind.VEHICLE_RENTAL, vehicle_id, lambda: client.get_vehicle_rental_prices(vehicle_id),
    )


async def get_orbit_distance_rows(
    client: UEXCorpClient, session: AsyncSession, orbit_id: int, star_system_id: int
) -> list[dict]:
    """Keyed by orbit id alone: UEX orbit ids are unique across star systems, and UEX only
    wants the star system alongside it as a required query parameter."""
    return await _get_or_fetch(
        session, UexCacheKind.ORBIT_DISTANCES, orbit_id,
        lambda: client.get_orbit_distances(orbit_id, star_system_id),
    )
