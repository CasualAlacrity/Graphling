"""Covers StartTradeRunTool's route_token -> run creation path and the round-trip
through route_cache it depends on. This is the first cross-turn dependency in the tool
surface: best_route stashes a token, and a later turn's start_trade_run must resolve
*that exact* one — the regression this guards is a model paraphrasing or dropping the
token instead of copying it, or the plumbing losing data along the way (quantity
override, zero-stock guard). See docs/start-route-tool.md "Tests still to add".

The autoload-mapping regression this file used to cover directly now lives in
tests/server/test_ledger_service.py — that logic (is_auto_load_origin/destination ->
CargoTransferType) moved to server/ledger_service.py, and ledger_client.create_run_
from_route here is just an HTTP passthrough with nothing of its own to get wrong.
"""
import ledger_client
from tools.trade_run import route_cache
from tools.trade_run.start_trade_run_tool import StartTradeRunTool
from tools.uexcorp.trade_data import UEXTradeRoute


def _route(**overrides):
    fields = dict(
        id_commodity=10, commodity_name="Agricium",
        id_terminal_origin=1, origin_terminal_name="Orison TDD",
        origin_star_system_name="Stanton", origin_planet_name="Crusader",
        id_terminal_destination=2, destination_terminal_name="Seraphim Station",
        destination_star_system_name="Stanton", destination_planet_name="Crusader",
        price_origin=10.0, price_destination=20.0, price_margin=10.0,
        scu_origin=100, scu_destination=100, status_origin=2, status_destination=1,
        distance=10.0, is_on_ground_origin=0, is_on_ground_destination=0,
        is_auto_load_origin=1, is_auto_load_destination=0,
        container_sizes_origin=[1, 2, 4, 8, 16, 24, 32], container_sizes_destination=[1, 2, 4, 8, 16, 24, 32],
    )
    fields.update(overrides)
    return UEXTradeRoute(**fields)


def _fake_create_run_from_route(monkeypatch):
    calls = []

    async def fake(route, quantity_scu, ship):
        calls.append((route, quantity_scu, ship))
        return None  # _start's message is built from route/args, not the created run

    monkeypatch.setattr(ledger_client, "create_run_from_route", fake)
    return calls


async def test_happy_path_passes_the_resolved_route_and_quantity_through(monkeypatch):
    route = _route()
    token = route_cache.stash(route, 40, "Railen")
    calls = _fake_create_run_from_route(monkeypatch)

    tool = StartTradeRunTool()
    message = await tool._arun(route_token=token)

    assert calls == [(route, 40, "Railen")]
    assert "Run started" in message


async def test_unknown_token_returns_not_available_message_and_creates_no_run(monkeypatch):
    calls = _fake_create_run_from_route(monkeypatch)

    tool = StartTradeRunTool()
    message = await tool._arun(route_token="not-a-real-token")

    assert "isn't available anymore" in message
    assert calls == []


async def test_quantity_override_replaces_cached_scu_hint(monkeypatch):
    route = _route()
    token = route_cache.stash(route, 40, "Railen")
    calls = _fake_create_run_from_route(monkeypatch)

    tool = StartTradeRunTool()
    await tool._arun(route_token=token, quantity_scu=15)

    assert calls[0][1] == 15


async def test_cached_scu_hint_used_when_quantity_not_overridden(monkeypatch):
    route = _route()
    token = route_cache.stash(route, 40, "Railen")
    calls = _fake_create_run_from_route(monkeypatch)

    tool = StartTradeRunTool()
    await tool._arun(route_token=token)

    assert calls[0][1] == 40


async def test_zero_scu_guard_blocks_run_creation(monkeypatch):
    route = _route()
    token = route_cache.stash(route, 0, "Railen")
    calls = _fake_create_run_from_route(monkeypatch)

    tool = StartTradeRunTool()
    message = await tool._arun(route_token=token)

    assert "can't fill any cargo" in message
    assert calls == []


def test_route_cache_stash_get_round_trip():
    route = _route()

    token = route_cache.stash(route, 40, "Railen")
    cached_route, scu, vehicle_name = route_cache.get(token)

    assert cached_route is route
    assert scu == 40
    assert vehicle_name == "Railen"


def test_route_cache_get_returns_none_for_unknown_token():
    assert route_cache.get("not-a-real-token") is None


def test_route_cache_get_does_not_pop_on_read():
    # A failed commit (e.g. start_trade_run hitting an unrelated error after lookup)
    # shouldn't force the pilot to search again for the same route.
    route = _route()
    token = route_cache.stash(route, 40, "Railen")

    route_cache.get(token)

    assert route_cache.get(token) is not None
