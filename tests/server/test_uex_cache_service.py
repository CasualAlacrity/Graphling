"""Covers server/uex_cache_service.py — the thin SessionLocal-wrapping layer around
tools/uexcorp/reference_cache_store.py and tools/uexcorp/price_cache.py, now the only
place either of those gets called from directly (docs/todo.md Phase 2's cache
follow-up). The read-through-or-fetch logic itself already has its own tests where it's
defined; this just checks the wrapping actually calls through with the right args.
"""
from datetime import UTC, datetime

from server import uex_cache_service
from tools.uexcorp.client import UEXCorpClient


class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalars(self):
        return self

    def first(self):
        return self._value

    def scalar_one_or_none(self):
        return self._value


class _FakeSession:
    def __init__(self, execute_value=None):
        self._execute_value = execute_value
        self.committed = False
        self.added = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, stmt):
        return _FakeResult(self._execute_value)

    def add(self, obj):
        self.added = obj

    async def commit(self):
        self.committed = True

    async def delete(self, obj):
        pass


async def test_get_reference_cache_returns_a_fresh_postgres_row_without_hitting_uex(monkeypatch):
    from db.models import UexReferenceCacheRecord

    record = UexReferenceCacheRecord(
        payload={
            "fetched_at": datetime.now(UTC).isoformat(), "commodities": [], "star_systems": [], "orbits": [],
            "terminals": [], "moons": [], "item_categories": [], "items": [], "vehicles": [],
            "refinery_yields": [], "poi": [], "commodity_statuses": [],
        },
        fetched_at=datetime.now(UTC),
    )
    monkeypatch.setattr(uex_cache_service, "SessionLocal", lambda: _FakeSession(execute_value=record))

    async def fail_if_called():
        raise AssertionError("build_uex_cache should not run on a cache hit")

    monkeypatch.setattr(UEXCorpClient, "build_uex_cache", lambda self: fail_if_called())

    result = await uex_cache_service.get_reference_cache()

    assert result.fetched_at is not None


async def test_get_reference_cache_builds_and_stores_on_a_miss(monkeypatch):
    from tools.uexcorp.reference_cache import UexReferenceCache

    fresh = UexReferenceCache(
        fetched_at=datetime.now(UTC), commodities=[], star_systems=[], orbits=[], terminals=[], moons=[],
        item_categories=[], items=[], vehicles=[], refinery_yields=[], poi=[], commodity_statuses=[],
    )
    session = _FakeSession(execute_value=None)  # no cached row
    monkeypatch.setattr(uex_cache_service, "SessionLocal", lambda: session)

    async def fake_build():
        return fresh

    monkeypatch.setattr(UEXCorpClient, "build_uex_cache", lambda self: fake_build())

    result = await uex_cache_service.get_reference_cache()

    assert result is fresh
    assert session.committed


async def test_refresh_reference_cache_rebuilds_unconditionally_even_with_a_fresh_row(monkeypatch):
    # The whole point of refresh_ (server/refresh_static_caches.py, run after a patch)
    # is to skip the cache check entirely — unlike get_reference_cache, a fresh existing
    # row must not short-circuit this.
    from tools.uexcorp.reference_cache import UexReferenceCache

    fresh = UexReferenceCache(
        fetched_at=datetime.now(UTC), commodities=[], star_systems=[], orbits=[], terminals=[], moons=[],
        item_categories=[], items=[], vehicles=[], refinery_yields=[], poi=[], commodity_statuses=[],
    )
    session = _FakeSession(execute_value="a cache row would go here, but this must be ignored")
    monkeypatch.setattr(uex_cache_service, "SessionLocal", lambda: session)

    build_calls = []

    async def fake_build():
        build_calls.append(1)
        return fresh

    monkeypatch.setattr(UEXCorpClient, "build_uex_cache", lambda self: fake_build())

    result = await uex_cache_service.refresh_reference_cache()

    assert result is fresh
    assert build_calls == [1]
    assert session.committed


async def test_get_commodity_price_rows_delegates_with_the_service_uex_client(monkeypatch):
    calls = []

    async def fake_get_commodity_price_rows(client, session, commodity_id):
        calls.append((client, commodity_id))
        return [{"id_commodity": commodity_id}]

    monkeypatch.setattr(uex_cache_service, "SessionLocal", lambda: _FakeSession())
    monkeypatch.setattr(uex_cache_service, "_get_commodity_price_rows", fake_get_commodity_price_rows)

    result = await uex_cache_service.get_commodity_price_rows(42)

    assert result == [{"id_commodity": 42}]
    assert calls[0][0] is uex_cache_service.uex_client
    assert calls[0][1] == 42


async def test_get_cached_routes_from_terminal_never_fetches(monkeypatch):
    async def fake_cached(session, terminal_id):
        return None

    monkeypatch.setattr(uex_cache_service, "SessionLocal", lambda: _FakeSession())
    monkeypatch.setattr(uex_cache_service, "_cached_routes_from_terminal", fake_cached)

    result = await uex_cache_service.get_cached_routes_from_terminal(7)

    assert result is None
