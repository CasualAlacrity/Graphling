from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import UexCacheKind, UexPriceCache
from tools.uexcorp.client import UexCallBudgetExhausted, UEXCorpClient

# How long each kind of cached row stays fresh (docs/llm-gateway.md, "Cache lifetimes by
# how often the data actually changes", agreed 2026-10-09):
# - Commodity prices and routes are player-driven and shift any second. 30 minutes is
#   UEX's own max-age for them, so polling faster mostly gets the same answer back.
#   best_route ranks on these rows and the overlay's volatility icon reads them, so this
#   is real price data, not just "does this pairing exist".
# - Item and vehicle prices only change with game versions (Jeff, 2026-10-09), and orbit
#   distances are static world geometry: 7 days. A patch-day refresh comes from
#   server/refresh_static_caches.py (and the planned game-version poll).
# - Routes by origin are the exception to the 30 minutes: the server refreshes every trade
#   terminal's routes hourly (server/uex_route_prefetch.py), which is what UEX's 7,000
#   calls a day affords for 161 terminals, so they have to outlive one interval plus the
#   run itself. The trade-off, agreed 2026-10-16: best_route ranks on prices up to ~75
#   minutes old, in exchange for searching every terminal instead of 8 more per search.
PLAYER_DRIVEN_TTL = timedelta(minutes=30)
PREFETCHED_ROUTE_TTL = timedelta(minutes=75)
PER_PATCH_TTL = timedelta(days=7)
CACHE_TTL = {
    UexCacheKind.COMMODITY: PLAYER_DRIVEN_TTL,
    UexCacheKind.TERMINAL: PLAYER_DRIVEN_TTL,
    UexCacheKind.ROUTE: PLAYER_DRIVEN_TTL,
    UexCacheKind.ROUTE_BY_ORIGIN: PREFETCHED_ROUTE_TTL,
    UexCacheKind.ITEM: PER_PATCH_TTL,
    UexCacheKind.VEHICLE_PURCHASE: PER_PATCH_TTL,
    UexCacheKind.VEHICLE_RENTAL: PER_PATCH_TTL,
    UexCacheKind.ORBIT_DISTANCES: PER_PATCH_TTL,
}


async def _get_cached_rows(
    session: AsyncSession, kind: UexCacheKind, entity_id: int, any_age: bool = False
) -> list[dict] | None:
    """The cached rows if fresh, else None. any_age=True returns them however old --
    only for when UEX can't be asked today (UexCallBudgetExhausted)."""
    result = await session.execute(
        select(UexPriceCache).where(UexPriceCache.kind == kind, UexPriceCache.entity_id == entity_id)
    )
    cache_row = result.scalar_one_or_none()
    if cache_row is None:
        return None
    if not any_age and datetime.now(UTC) - cache_row.fetched_at > CACHE_TTL[kind]:
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
    return await _get_or_fetch(
        session, UexCacheKind.COMMODITY, commodity_id, lambda: client.get_commodity_prices(commodity_id),
    )


async def get_terminal_price_rows(
    client: UEXCorpClient, session: AsyncSession, terminal_id: int
) -> list[dict]:
    return await _get_or_fetch(
        session, UexCacheKind.TERMINAL, terminal_id, lambda: client.get_terminal_prices(terminal_id),
    )


async def get_commodity_route_rows(
    client: UEXCorpClient, session: AsyncSession, commodity_id: int
) -> list[dict]:
    # Every route for this commodity, unfiltered by origin/destination — the caller
    # filters locally. Lets a source-only or destination-only search reuse the same
    # cached fetch a commodity-specified search would have made, instead of needing
    # its own separate live call per search.
    return await _get_or_fetch(
        session, UexCacheKind.ROUTE, commodity_id, lambda: client.get_commodity_routes(commodity_id=commodity_id),
    )


# Split into a read and a fetch, unlike the get_*_rows helpers above, because best_route
# needs to know *which* origins are already warm before deciding what to spend its live
# call budget on. A single get-or-fetch helper can't express "use everything cached, then
# fetch only a few of the rest".
async def cached_routes_from_terminal(session: AsyncSession, terminal_id: int) -> list[dict] | None:
    """Routes out of this terminal if they're cached and fresh, else None. Never calls UEX."""
    return await _get_cached_rows(session, UexCacheKind.ROUTE_BY_ORIGIN, terminal_id)


async def cached_routes_from_terminals(session: AsyncSession, terminal_ids: list[int]) -> dict[int, list[dict]]:
    """cached_routes_from_terminal for many origins in one query: terminal id -> rows,
    for the fresh ones only (an absent id is a miss). A region search asks about every
    trade terminal in it, up to 117 in Stanton, and one request each made the lookup
    alone take tens of seconds."""
    result = await session.execute(
        select(UexPriceCache).where(
            UexPriceCache.kind == UexCacheKind.ROUTE_BY_ORIGIN, UexPriceCache.entity_id.in_(terminal_ids)
        )
    )
    now = datetime.now(UTC)
    fresh: dict[int, list[dict]] = {}
    for cache_row in result.scalars():
        if now - cache_row.fetched_at <= CACHE_TTL[UexCacheKind.ROUTE_BY_ORIGIN]:
            fresh[cache_row.entity_id] = cache_row.rows
    return fresh


async def all_cached_origin_routes(session: AsyncSession) -> dict[int, list[dict]]:
    """Every origin's fresh prefetched routes in one query: terminal id -> rows. Never
    calls UEX."""
    result = await session.execute(select(UexPriceCache).where(UexPriceCache.kind == UexCacheKind.ROUTE_BY_ORIGIN))
    now = datetime.now(UTC)
    fresh: dict[int, list[dict]] = {}
    for cache_row in result.scalars():
        if now - cache_row.fetched_at <= CACHE_TTL[UexCacheKind.ROUTE_BY_ORIGIN]:
            fresh[cache_row.entity_id] = cache_row.rows
    return fresh


async def route_origin_fetched_at(session: AsyncSession) -> dict[int, datetime]:
    """When each origin terminal's routes were last fetched, for the prefetch to skip
    ones that are still recent (e.g. right after a restart)."""
    result = await session.execute(
        select(UexPriceCache.entity_id, UexPriceCache.fetched_at).where(
            UexPriceCache.kind == UexCacheKind.ROUTE_BY_ORIGIN
        )
    )
    fetched_at: dict[int, datetime] = {}
    for entity_id, when in result.all():
        fetched_at[entity_id] = when
    return fetched_at


async def fetch_routes_from_terminal(
    client: UEXCorpClient, session: AsyncSession, terminal_id: int
) -> list[dict]:
    """Live fetch of every route out of this terminal, stored for next time.

    Deliberately unfiltered by commodity even when the caller only wants one: the whole
    set is what gets cached, so a commodity-specific search also warms the general case,
    and the caller filters locally for free.

    With today's UEX calls used up, the last rows fetched for this terminal come back
    instead, however old; with none, UexCallBudgetExhausted propagates.
    """
    try:
        rows = await client.get_commodity_routes(origin_terminal_id=terminal_id)
    except UexCallBudgetExhausted:
        stale = await _get_cached_rows(session, UexCacheKind.ROUTE_BY_ORIGIN, terminal_id, any_age=True)
        if stale is None:
            raise
        return stale
    await _store_rows(session, UexCacheKind.ROUTE_BY_ORIGIN, terminal_id, rows)
    return rows


async def _get_or_fetch(session: AsyncSession, kind: UexCacheKind, entity_id: int, fetch) -> list[dict]:
    """Fresh cached rows, else a live fetch that's stored for next time. With today's UEX
    calls used up, stale rows beat no answer; with none cached, the refusal propagates
    (the server answers 503)."""
    cached = await _get_cached_rows(session, kind, entity_id)
    if cached is not None:
        return cached
    try:
        rows = await fetch()
    except UexCallBudgetExhausted:
        stale = await _get_cached_rows(session, kind, entity_id, any_age=True)
        if stale is None:
            raise
        return stale
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
