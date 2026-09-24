"""Manual maintenance script — force-refreshes the UEX reference cache and the wiki
cache, bypassing their normal TTL-gated read-through. Run by hand after a Star Citizen
patch, where waiting for the normal 24h-TTL miss to happen on its own means pilots could
see pre-patch ship stats, terminal data, or vehicle info for up to a day.

Deliberately does NOT touch the UEX price/route cache (UexPriceCache) — that one has a
30-minute TTL already matched to UEX's own price-data freshness window, so it self-heals
long before a manual refresh would matter, and force-refreshing it means hundreds of
throttled live UEX calls (one per cached commodity/terminal) for a problem that mostly
isn't one. See docs/todo.md's Phase 2 cache section for the full reasoning.

Usage (from the server container, e.g. `docker compose exec server`):
    python -m server.refresh_static_caches
"""
import asyncio

from server import uex_cache_service, wiki_cache_service

# Bounds how many ship-speed lookups run concurrently — same spirit as
# overlay/uex_lookup.py's ROUTE_FANOUT_CONCURRENCY, so a large cached ship list doesn't
# burst-hammer the wiki API even though it's never documented a rate limit.
SHIP_SPEED_CONCURRENCY = 5


async def _refresh_ship_speeds() -> int:
    ship_names = await wiki_cache_service.cached_ship_speed_keys()
    semaphore = asyncio.Semaphore(SHIP_SPEED_CONCURRENCY)

    async def refresh_one(ship_name: str) -> None:
        async with semaphore:
            await wiki_cache_service.refresh_ship_speed(ship_name)

    await asyncio.gather(*(refresh_one(name) for name in ship_names))
    return len(ship_names)


async def main() -> None:
    print("Rebuilding the UEX reference cache...", flush=True)
    reference_cache = await uex_cache_service.refresh_reference_cache()
    print(
        f"  -> {len(reference_cache.commodities)} commodities, {len(reference_cache.terminals)} terminals, "
        f"{len(reference_cache.vehicles)} vehicles",
        flush=True,
    )

    print("Refreshing cached wiki ship speeds...", flush=True)
    ship_count = await _refresh_ship_speeds()
    print(f"  -> {ship_count} ships refreshed", flush=True)

    print("Refreshing wiki locations...", flush=True)
    locations = await wiki_cache_service.refresh_locations()
    print(f"  -> {len(locations)} locations", flush=True)

    print("Done.", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
