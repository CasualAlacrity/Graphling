from datetime import datetime, timedelta, timezone

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
    if datetime.now(timezone.utc) - cache_row.fetched_at > CACHE_TTL:
        return None
    return cache_row.rows


async def _store_rows(session: AsyncSession, kind: UexCacheKind, entity_id: int, rows: list[dict]) -> None:
    stmt = pg_insert(UexPriceCache).values(
        kind=kind,
        entity_id=entity_id,
        rows=rows,
        fetched_at=datetime.now(timezone.utc),
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
