"""Covers server/wiki_cache_service.py — the Postgres-backed shared cache that replaced
StarCitizenWikiClient's old in-memory-only, per-process caching (docs/todo.md Phase 2's
cache follow-up). Fakes the DB session and the real wiki fetch (fetch_ship_speed_from_wiki/
fetch_locations_from_wiki), the two things this module actually owns wiring together.
"""
from datetime import UTC, datetime

from db.models import WikiCacheKind
from server import wiki_cache_service
from tools.starcitizenwiki.client import StarCitizenWikiClient
from tools.starcitizenwiki.models import LocationPosition, ShipSpeed


class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _FakeSession:
    def __init__(self, execute_value=None):
        self._execute_value = execute_value
        self.committed = False
        self.executed_stmt = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, stmt):
        self.executed_stmt = stmt
        return _FakeResult(self._execute_value)

    async def commit(self):
        self.committed = True


def _make_row(kind, key, payload, fetched_at=None):
    from db.models import WikiCache
    return WikiCache(kind=kind, key=key, payload=payload, fetched_at=fetched_at or datetime.now(UTC))


def _ship_speed(game_name="Railen", scm=100.0, quantum_speed=1000.0, quantum_spool_time=5.0):
    # ShipSpeed's fields use validation_alias=AliasPath(...) to parse the wiki's own
    # nested API shape — model_validate needs that same nested shape, not flat kwargs.
    return ShipSpeed.model_validate({
        "game_name": game_name,
        "speed": {"scm": scm},
        "quantum": {"quantum_speed": quantum_speed, "quantum_spool_time": quantum_spool_time},
    })


async def test_get_ship_speed_returns_a_fresh_cached_value_without_hitting_the_wiki(monkeypatch):
    # Flat field names, matching what model_dump() actually writes (see the service's
    # own comment on why reading it back uses model_construct, not model_validate).
    row = _make_row(
        WikiCacheKind.SHIP_SPEED, "railen",
        {"ship_speed": {"game_name": "Railen", "scm_speed": 100.0, "quantum_speed": 1000.0, "quantum_spool_time": 5.0}},
    )
    monkeypatch.setattr(wiki_cache_service, "SessionLocal", lambda: _FakeSession(execute_value=row))

    async def fail_if_called(self, ship_name):
        raise AssertionError("should not hit the real wiki on a cache hit")

    monkeypatch.setattr(StarCitizenWikiClient, "fetch_ship_speed_from_wiki", fail_if_called)

    result = await wiki_cache_service.get_ship_speed("Railen")

    assert isinstance(result, ShipSpeed)
    assert result.game_name == "Railen"


async def test_get_ship_speed_caches_a_miss_as_none_not_an_empty_row(monkeypatch):
    # A ship genuinely not found on the wiki is itself worth remembering for the TTL
    # window — see the service's own comment on why this isn't left unstored.
    row = _make_row(WikiCacheKind.SHIP_SPEED, "not a real ship", {"ship_speed": None})
    monkeypatch.setattr(wiki_cache_service, "SessionLocal", lambda: _FakeSession(execute_value=row))

    result = await wiki_cache_service.get_ship_speed("not a real ship")

    assert result is None


async def test_get_ship_speed_fetches_and_stores_on_a_miss(monkeypatch):
    session = _FakeSession(execute_value=None)
    monkeypatch.setattr(wiki_cache_service, "SessionLocal", lambda: session)
    fresh = _ship_speed()

    async def fake_fetch(self, ship_name):
        assert ship_name == "Railen"
        return fresh

    monkeypatch.setattr(StarCitizenWikiClient, "fetch_ship_speed_from_wiki", fake_fetch)

    result = await wiki_cache_service.get_ship_speed("Railen")

    assert result is fresh
    assert session.committed


async def test_get_locations_returns_cached_list_without_hitting_the_wiki(monkeypatch):
    row = _make_row(
        WikiCacheKind.LOCATIONS, wiki_cache_service.LOCATIONS_KEY,
        {"locations": [{"name": "Orison", "type": "City", "system": "Stanton", "x": 1.0, "y": 2.0, "z": 3.0}]},
    )
    monkeypatch.setattr(wiki_cache_service, "SessionLocal", lambda: _FakeSession(execute_value=row))

    async def fail_if_called(self):
        raise AssertionError("should not hit the real wiki on a cache hit")

    monkeypatch.setattr(StarCitizenWikiClient, "fetch_locations_from_wiki", fail_if_called)

    result = await wiki_cache_service.get_locations()

    assert result == [LocationPosition(name="Orison", type="City", system="Stanton", x=1.0, y=2.0, z=3.0)]


async def test_get_locations_fetches_and_stores_on_a_miss(monkeypatch):
    session = _FakeSession(execute_value=None)
    monkeypatch.setattr(wiki_cache_service, "SessionLocal", lambda: session)
    fresh = [LocationPosition(name="Orison", type="City", system="Stanton", x=1.0, y=2.0, z=3.0)]

    async def fake_fetch(self):
        return fresh

    monkeypatch.setattr(StarCitizenWikiClient, "fetch_locations_from_wiki", fake_fetch)

    result = await wiki_cache_service.get_locations()

    assert result == fresh
    assert session.committed


async def test_stale_cache_row_is_treated_as_a_miss(monkeypatch):
    from datetime import timedelta

    stale_row = _make_row(
        WikiCacheKind.SHIP_SPEED, "railen", {"ship_speed": None},
        fetched_at=datetime.now(UTC) - timedelta(hours=25),
    )
    session = _FakeSession(execute_value=stale_row)
    monkeypatch.setattr(wiki_cache_service, "SessionLocal", lambda: session)
    fresh = _ship_speed()

    async def fake_fetch(self, ship_name):
        return fresh

    monkeypatch.setattr(StarCitizenWikiClient, "fetch_ship_speed_from_wiki", fake_fetch)

    result = await wiki_cache_service.get_ship_speed("Railen")

    assert result is fresh


# refresh_* — the unconditional fetch-and-store path server/refresh_static_caches.py
# uses after a patch, which must bypass the cache check entirely (unlike get_ship_speed/
# get_locations, a fresh existing row must not short-circuit these).

async def test_refresh_ship_speed_refetches_even_with_a_fresh_cached_row(monkeypatch):
    fresh_row = _make_row(WikiCacheKind.SHIP_SPEED, "railen", {"ship_speed": None})  # would be a hit if checked
    session = _FakeSession(execute_value=fresh_row)
    monkeypatch.setattr(wiki_cache_service, "SessionLocal", lambda: session)
    fresh = _ship_speed()

    fetch_calls = []

    async def fake_fetch(self, ship_name):
        fetch_calls.append(ship_name)
        return fresh

    monkeypatch.setattr(StarCitizenWikiClient, "fetch_ship_speed_from_wiki", fake_fetch)

    result = await wiki_cache_service.refresh_ship_speed("Railen")

    assert result is fresh
    assert fetch_calls == ["Railen"]
    assert session.committed


async def test_refresh_locations_refetches_even_with_a_fresh_cached_row(monkeypatch):
    fresh_row = _make_row(
        WikiCacheKind.LOCATIONS, wiki_cache_service.LOCATIONS_KEY, {"locations": []},
    )  # would be a hit if checked
    session = _FakeSession(execute_value=fresh_row)
    monkeypatch.setattr(wiki_cache_service, "SessionLocal", lambda: session)
    fresh = [LocationPosition(name="Orison", type="City", system="Stanton", x=1.0, y=2.0, z=3.0)]

    fetch_calls = []

    async def fake_fetch(self):
        fetch_calls.append(1)
        return fresh

    monkeypatch.setattr(StarCitizenWikiClient, "fetch_locations_from_wiki", fake_fetch)

    result = await wiki_cache_service.refresh_locations()

    assert result == fresh
    assert fetch_calls == [1]
    assert session.committed


async def test_cached_ship_speed_keys_returns_only_ship_speed_kind_keys(monkeypatch):
    class _FakeScalarsResult:
        def __init__(self, values):
            self._values = values

        def scalars(self):
            return self

        def all(self):
            return self._values

    class _KeysSession(_FakeSession):
        async def execute(self, stmt):
            self.executed_stmt = stmt
            return _FakeScalarsResult(["railen", "caterpillar"])

    session = _KeysSession()
    monkeypatch.setattr(wiki_cache_service, "SessionLocal", lambda: session)

    result = await wiki_cache_service.cached_ship_speed_keys()

    assert result == ["railen", "caterpillar"]
