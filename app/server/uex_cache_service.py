"""Server-side owner of the UEX cache (docs/todo.md Phase 2's cache follow-up — the
client used to hit Postgres directly for this, which silently depended on the client
having its own Postgres access; production Postgres has no published port at all).

Thin SessionLocal-wrapping layer around the same tools/uexcorp/reference_cache_store.py
and tools/uexcorp/price_cache.py functions the client used to call directly — their
read-through-or-fetch logic didn't need to change at all, only who calls them.
"""
from db.session import SessionLocal
from server.config import settings
from tools.uexcorp.client import UEXCorpClient
from tools.uexcorp.price_cache import (
    cached_routes_from_terminal as _cached_routes_from_terminal,
)
from tools.uexcorp.price_cache import (
    fetch_routes_from_terminal as _fetch_routes_from_terminal,
)
from tools.uexcorp.price_cache import (
    get_commodity_price_rows as _get_commodity_price_rows,
)
from tools.uexcorp.price_cache import (
    get_commodity_route_rows as _get_commodity_route_rows,
)
from tools.uexcorp.price_cache import (
    get_terminal_price_rows as _get_terminal_price_rows,
)
from tools.uexcorp.reference_cache import UexReferenceCache
from tools.uexcorp.reference_cache_store import load_reference_cache, store_reference_cache

uex_client = UEXCorpClient(api_key=settings.uexcorp_api_key, bearer_token=settings.uexcorp_bearer_token)


async def get_reference_cache() -> UexReferenceCache:
    async with SessionLocal() as session:
        cached = await load_reference_cache(session)
    if cached is not None:
        return cached
    return await refresh_reference_cache()


async def refresh_reference_cache() -> UexReferenceCache:
    """Unconditional rebuild, bypassing the cache check entirely — for
    server/refresh_static_caches.py, run by hand after a game patch, where a TTL-gated
    read-through isn't what you want (the whole point is not waiting up to 24h for the
    normal miss to happen on its own)."""
    cache = await uex_client.build_uex_cache()

    async with SessionLocal() as session:
        await store_reference_cache(session, cache)
    return cache


async def get_commodity_price_rows(commodity_id: int) -> list[dict]:
    async with SessionLocal() as session:
        return await _get_commodity_price_rows(uex_client, session, commodity_id)


async def get_terminal_price_rows(terminal_id: int) -> list[dict]:
    async with SessionLocal() as session:
        return await _get_terminal_price_rows(uex_client, session, terminal_id)


async def get_commodity_route_rows(commodity_id: int) -> list[dict]:
    async with SessionLocal() as session:
        return await _get_commodity_route_rows(uex_client, session, commodity_id)


async def get_cached_routes_from_terminal(terminal_id: int) -> list[dict] | None:
    async with SessionLocal() as session:
        return await _cached_routes_from_terminal(session, terminal_id)


async def fetch_routes_from_terminal(terminal_id: int) -> list[dict]:
    async with SessionLocal() as session:
        return await _fetch_routes_from_terminal(uex_client, session, terminal_id)
