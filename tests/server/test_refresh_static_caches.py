"""Covers server/refresh_static_caches.py's own orchestration logic — that it calls the
right refresh_* functions and respects the ship-speed concurrency bound. The refresh_*
functions themselves already have their own tests in test_uex_cache_service.py/
test_wiki_cache_service.py; this only checks the script wires them together correctly.
"""
import asyncio
from datetime import UTC, datetime

from server import refresh_static_caches, uex_cache_service, wiki_cache_service


async def test_refresh_ship_speeds_calls_refresh_for_every_cached_name(monkeypatch):
    monkeypatch.setattr(wiki_cache_service, "cached_ship_speed_keys", _returning(["railen", "caterpillar", "cutlass"]))

    calls = []

    async def fake_refresh(ship_name):
        calls.append(ship_name)

    monkeypatch.setattr(wiki_cache_service, "refresh_ship_speed", fake_refresh)

    count = await refresh_static_caches._refresh_ship_speeds()

    assert count == 3
    assert sorted(calls) == ["caterpillar", "cutlass", "railen"]


async def test_refresh_ship_speeds_respects_the_concurrency_bound(monkeypatch):
    names = [f"ship-{i}" for i in range(20)]
    monkeypatch.setattr(wiki_cache_service, "cached_ship_speed_keys", _returning(names))

    in_flight = 0
    max_in_flight = 0

    async def fake_refresh(ship_name):
        nonlocal in_flight, max_in_flight
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        await asyncio.sleep(0.01)
        in_flight -= 1

    monkeypatch.setattr(wiki_cache_service, "refresh_ship_speed", fake_refresh)

    await refresh_static_caches._refresh_ship_speeds()

    assert max_in_flight == refresh_static_caches.SHIP_SPEED_CONCURRENCY


async def test_main_calls_every_refresh_in_order(monkeypatch):
    calls = []

    async def fake_refresh_reference_cache():
        calls.append("reference")
        from tools.uexcorp.reference_cache import UexReferenceCache
        return UexReferenceCache(
            fetched_at=datetime.now(UTC), commodities=[], star_systems=[], orbits=[], terminals=[], moons=[],
            item_categories=[], items=[], vehicles=[], refinery_yields=[], poi=[], commodity_statuses=[],
        )

    async def fake_refresh_ship_speeds():
        calls.append("ship_speeds")
        return 0

    async def fake_refresh_locations():
        calls.append("locations")
        return []

    monkeypatch.setattr(uex_cache_service, "refresh_reference_cache", fake_refresh_reference_cache)
    monkeypatch.setattr(refresh_static_caches, "_refresh_ship_speeds", fake_refresh_ship_speeds)
    monkeypatch.setattr(wiki_cache_service, "refresh_locations", fake_refresh_locations)

    await refresh_static_caches.main()

    assert calls == ["reference", "ship_speeds", "locations"]


def _returning(value):
    async def _inner(*args, **kwargs):
        return value
    return _inner
