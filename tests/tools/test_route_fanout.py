"""Covers search_routes()'s three query shapes (commodity-specified, source-only,
destination-only) and the concurrency bound on its fan-out — the piece that replaced
UEX's commodities_routes endpoint rejecting destination-only queries with a 400."""
import asyncio

import pytest

from overlay import uex_lookup


def _route_row(commodity_id, origin_id, destination_id):
    return {
        "id_commodity": commodity_id, "commodity_name": "Test",
        "id_terminal_origin": origin_id, "origin_terminal_name": "Origin",
        "origin_star_system_name": "Stanton", "origin_planet_name": "Hurston",
        "id_terminal_destination": destination_id, "destination_terminal_name": "Dest",
        "destination_star_system_name": "Stanton", "destination_planet_name": "Hurston",
        "price_origin": 10.0, "price_destination": 20.0, "price_margin": 15.0,
        "scu_origin": 100, "scu_destination": 100, "status_origin": 2, "status_destination": 1,
        "distance": 10,
        "is_on_ground_origin": 0, "is_on_ground_destination": 0,
    }


FAKE_ROUTES_BY_COMMODITY = {
    1: [_route_row(1, 100, 200), _route_row(1, 100, 201)],
    2: [_route_row(2, 100, 202)],
    3: [_route_row(3, 101, 200)],
}

# is_auto_load is a terminal property search_routes looks up separately from the route
# row itself (see uex_lookup._terminal_is_auto_load) — every terminal referenced above
# defaults to autoload-capable; tests that need otherwise override this per-terminal.
TERMINAL_AUTOLOAD = {100: 1, 101: 1, 200: 1, 201: 1, 202: 1}

SEARCH_DEFAULTS = {
    "min_source_code": None,
    "max_destination_code": None,
    "space_only": False,
    "require_autoload": False,
}


@pytest.fixture
def call_log():
    return []


@pytest.fixture(autouse=True)
def fake_route_source(monkeypatch, call_log):
    """Doubles the two DB-touching pieces search_routes() relies on, so this exercises
    the real fan-out/filtering logic without needing Postgres or live UEX."""

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    async def fake_get_commodity_route_rows(client, session, commodity_id):
        call_log.append(commodity_id)
        await asyncio.sleep(0.02)  # simulate network latency
        return FAKE_ROUTES_BY_COMMODITY.get(commodity_id, [])

    async def fake_commodity_ids_at(terminal_id, side):
        field = "id_terminal_origin" if side == "buy" else "id_terminal_destination"
        return {
            commodity_id for commodity_id, rows in FAKE_ROUTES_BY_COMMODITY.items()
            if any(row[field] == terminal_id for row in rows)
        }

    monkeypatch.setattr(uex_lookup, "SessionLocal", lambda: FakeSession())
    monkeypatch.setattr(uex_lookup, "get_commodity_route_rows", fake_get_commodity_route_rows)
    monkeypatch.setattr(uex_lookup, "commodity_ids_at", fake_commodity_ids_at)
    monkeypatch.setattr(uex_lookup, "_terminal_is_auto_load", lambda terminal_id: TERMINAL_AUTOLOAD.get(terminal_id, 0))


async def test_commodity_specified_search_does_a_single_cached_fetch(call_log):
    routes = await uex_lookup.search_routes(
        commodity_id=1, source_terminal_id=None, destination_terminal_id=201, **SEARCH_DEFAULTS,
    )
    assert call_log == [1]
    assert len(routes) == 1
    assert routes[0].destination_terminal_id == 201


async def test_source_only_search_fans_out_across_relevant_commodities(call_log):
    routes = await uex_lookup.search_routes(
        commodity_id=None, source_terminal_id=100, destination_terminal_id=None, **SEARCH_DEFAULTS,
    )
    assert sorted(call_log) == [1, 2]
    assert len(routes) == 3
    assert all(route.origin_terminal_id == 100 for route in routes)


async def test_source_and_destination_together_filters_by_both(call_log):
    # Regression test: source+destination with no commodity picked used to land in the
    # source-only branch and silently ignore destination_terminal_id, returning every
    # destination reachable from that source instead of just the one requested.
    routes = await uex_lookup.search_routes(
        commodity_id=None, source_terminal_id=100, destination_terminal_id=201, **SEARCH_DEFAULTS,
    )
    assert len(routes) == 1
    assert routes[0].origin_terminal_id == 100
    assert routes[0].destination_terminal_id == 201


async def test_destination_only_search_fans_out_and_filters(call_log):
    # The case that used to 400 against the live API — a destination alone isn't a
    # valid commodities_routes query, so search_routes covers it via fan-out instead.
    routes = await uex_lookup.search_routes(
        commodity_id=None, source_terminal_id=None, destination_terminal_id=200, **SEARCH_DEFAULTS,
    )
    assert sorted(call_log) == [1, 3]
    assert len(routes) == 2
    assert all(route.destination_terminal_id == 200 for route in routes)


async def test_require_autoload_does_not_exclude_a_pinned_terminal_lacking_it(call_log, monkeypatch):
    # Regression test: require_autoload used to check both ends unconditionally, so a
    # pinned terminal without autoload (the player explicitly chose it) zeroed out every
    # result regardless of the open end's status. It should only constrain the end the
    # search left open, matching space_only's behavior.
    monkeypatch.setitem(TERMINAL_AUTOLOAD, 201, 0)
    routes = await uex_lookup.search_routes(
        commodity_id=1, source_terminal_id=None, destination_terminal_id=201,
        min_source_code=None, max_destination_code=None, space_only=False, require_autoload=True,
    )
    assert len(routes) == 1
    assert routes[0].destination_terminal_id == 201


async def test_require_autoload_still_filters_the_open_end(call_log, monkeypatch):
    # The origin end is left open here, so it should still be constrained to
    # autoload-capable terminals even though the destination is pinned.
    routes = await uex_lookup.search_routes(
        commodity_id=3, source_terminal_id=None, destination_terminal_id=200,
        min_source_code=None, max_destination_code=None, space_only=False, require_autoload=True,
    )
    assert len(routes) == 1

    monkeypatch.setitem(TERMINAL_AUTOLOAD, 101, 0)
    routes = await uex_lookup.search_routes(
        commodity_id=3, source_terminal_id=None, destination_terminal_id=200,
        min_source_code=None, max_destination_code=None, space_only=False, require_autoload=True,
    )
    assert routes == []


async def test_nothing_set_returns_empty_without_fetching(call_log):
    routes = await uex_lookup.search_routes(
        commodity_id=None, source_terminal_id=None, destination_terminal_id=None, **SEARCH_DEFAULTS,
    )
    assert routes == []
    assert call_log == []


async def test_fanout_concurrency_is_bounded(monkeypatch):
    many_commodities = {
        i: [{"id_commodity": i, "id_terminal_origin": 300, "id_terminal_destination": 400}]
        for i in range(1, 13)
    }
    in_flight = 0
    max_in_flight = 0

    async def tracking_fetch(client, session, commodity_id):
        nonlocal in_flight, max_in_flight
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        await asyncio.sleep(0.02)
        in_flight -= 1
        return many_commodities.get(commodity_id, [])

    monkeypatch.setattr(uex_lookup, "get_commodity_route_rows", tracking_fetch)

    await uex_lookup._fanout_route_rows(list(range(1, 13)))

    # 12 candidate commodities against a limit of 5 should actually saturate the limit,
    # not just stay under it — otherwise the bound could be a no-op.
    assert max_in_flight == uex_lookup.ROUTE_FANOUT_CONCURRENCY
