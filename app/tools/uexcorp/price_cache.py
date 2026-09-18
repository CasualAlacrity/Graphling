from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import UexCacheKind, UexPriceCache
from tools.uexcorp.client import UEXCorpClient

# Matches commodities_prices' own documented cache TTL (+30 minutes) — this cache
# only ever answers "does this terminal/commodity pairing exist at all," never the
# live price/stock fields, so it doesn't need to track UEX's data any more tightly
# than UEX itself refreshes it.
CACHE_TTL = timedelta(minutes=30)


async def _get_cached_rows(session: AsyncSession, kind: UexCacheKind, entity_id: int) -> list[dict] | None:
    result = await session.execute(
        select(UexPriceCache).where(UexPriceCache.kind == kind, UexPriceCache.entity_id == entity_id)
    )
    cache_row = result.scalar_one_or_none()
    if cache_row is None:
        return None
    if datetime.now(UTC) - cache_row.fetched_at > CACHE_TTL:
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
