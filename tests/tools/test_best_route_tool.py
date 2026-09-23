"""Covers BestRouteTool's route_token stashing. find_best_route's own runner-up
bookkeeping (runner_up_scu being the runner-up's own reachable load, not the winner's)
is already covered in test_route_ranking.py — this covers the layer above it: that
best_route_tool actually stashes *both* routes as distinct tokens, since that's what
start_trade_run ultimately depends on to commit to "the alternative" by name.

Regression this guards (fixed 2026-09-18, docs/start-route-tool.md): best_route named a
runner-up out loud but only ever stashed the winner, so "do that one instead" had no
token to resolve.
"""
import re
from types import SimpleNamespace

import pytest

from tools import best_route_tool as best_route_tool_module
from tools.best_route_tool import BestRouteTool
from tools.route_ranking import RouteSearch
from tools.starcitizenwiki.client import StarCitizenWikiClient
from tools.trade_run import resolver, route_cache
from tools.trade_run.resolver import AmbiguousRunError
from tools.uexcorp import matching
from tools.uexcorp.client import UEXCorpClient
from tools.uexcorp.matching import DEFAULT_NEAR_DISTANCE, RegionMatch
from tools.uexcorp.reference_cache import TerminalType
from tools.uexcorp.trade_data import UEXTradeRoute


def _route(commodity_name, destination_name, scu):
    return UEXTradeRoute(
        id_commodity=10, commodity_name=commodity_name,
        id_terminal_origin=1, origin_terminal_name="Orison TDD",
        origin_star_system_name="Stanton", origin_planet_name="Crusader",
        id_terminal_destination=2, destination_terminal_name=destination_name,
        destination_star_system_name="Stanton", destination_planet_name="Crusader",
        price_origin=10.0, price_destination=20.0, price_margin=10.0,
        scu_origin=scu, scu_destination=scu, status_origin=2, status_destination=1,
        distance=10.0, is_on_ground_origin=0, is_on_ground_destination=0,
        is_auto_load_origin=1, is_auto_load_destination=1,
        container_sizes_origin=[1, 2, 4, 8, 16, 24, 32], container_sizes_destination=[1, 2, 4, 8, 16, 24, 32],
    )


@pytest.fixture
def tool(monkeypatch):
    # Real client instances (cheap to construct — no network at construction time) with
    # get_uex_cache patched at the class level: pydantic BaseModel instances refuse a
    # plain instance-attribute assignment for a name that isn't a declared field
    # ("object has no field get_uex_cache"), so this has to replace the method on the
    # class, not the instance.
    terminal = SimpleNamespace(id=1, name="Orison TDD", type=TerminalType.COMMODITY)
    vehicle = SimpleNamespace(id=1, name="Railen", is_concept=False, scu=96)
    fake_cache = SimpleNamespace(terminals=[terminal], vehicles=[vehicle], commodities=[])

    async def fake_get_uex_cache(self):
        return fake_cache

    monkeypatch.setattr(UEXCorpClient, "get_uex_cache", fake_get_uex_cache)
    return BestRouteTool(uex_client=UEXCorpClient(api_key="test", bearer_token="test"), scw_client=StarCitizenWikiClient())


async def test_runner_up_gets_its_own_token_and_scu(tool, monkeypatch):
    winner = _route("Copper", "Rod's Fuel 'N Supplies", scu=64)
    runner_up = _route("Scrap", "Rod's Fuel 'N Supplies", scu=32)

    async def fake_find_best_route(*args, **kwargs):
        return RouteSearch(
            best=winner, best_profit=960_000.0, best_rate=1600.0, best_scu=64,
            runner_up=runner_up, runner_up_profit=480_000.0, runner_up_rate=800.0, runner_up_scu=32,
            origins_searched=1, origins_available=1,
        )

    monkeypatch.setattr(best_route_tool_module, "find_best_route", fake_find_best_route)

    message = await tool._arun(origin="Orison TDD", ship="Railen")

    tokens = re.findall(r"route_token=(\w+)", message)
    assert len(tokens) == 2
    assert tokens[0] != tokens[1]

    winner_route, winner_scu, _ = route_cache.get(tokens[0])
    runner_up_route, runner_up_scu, _ = route_cache.get(tokens[1])

    assert winner_route is winner
    assert winner_scu == 64
    assert runner_up_route is runner_up
    assert runner_up_scu == 32  # the runner-up's own load, not the winner's 64


async def test_no_runner_up_token_when_no_runner_up_exists(tool, monkeypatch):
    winner = _route("Copper", "Rod's Fuel 'N Supplies", scu=64)

    async def fake_find_best_route(*args, **kwargs):
        return RouteSearch(
            best=winner, best_profit=960_000.0, best_rate=1600.0, best_scu=64,
            runner_up=None, runner_up_profit=None, runner_up_rate=None, runner_up_scu=None,
            origins_searched=1, origins_available=1,
        )

    monkeypatch.setattr(best_route_tool_module, "find_best_route", fake_find_best_route)

    message = await tool._arun(origin="Orison TDD", ship="Railen")

    assert len(re.findall(r"route_token=(\w+)", message)) == 1


async def test_asks_for_ship_when_no_active_run_and_none_given(tool, monkeypatch):
    async def fake_resolve_run():
        raise ValueError("No active trade runs")

    monkeypatch.setattr(resolver, "resolve_run", fake_resolve_run)

    message = await tool._arun(origin="Orison TDD")

    assert message == "Which ship are you flying?"


async def test_asks_for_ship_when_active_run_is_ambiguous(tool, monkeypatch):
    async def fake_resolve_run():
        raise AmbiguousRunError(candidates=[])

    monkeypatch.setattr(resolver, "resolve_run", fake_resolve_run)

    message = await tool._arun(origin="Orison TDD")

    # Caught the same as "no run" rather than surfacing AmbiguousRunError's own
    # candidate-listing message — best_route has no leg/commodity hint to disambiguate
    # with, so asking the pilot outright is the only useful fallback either way.
    assert message == "Which ship are you flying?"


async def test_uses_active_runs_ship_when_none_given(tool, monkeypatch):
    winner = _route("Copper", "Rod's Fuel 'N Supplies", scu=64)

    async def fake_find_best_route(*args, **kwargs):
        return RouteSearch(
            best=winner, best_profit=960_000.0, best_rate=1600.0, best_scu=64,
            runner_up=None, runner_up_profit=None, runner_up_rate=None, runner_up_scu=None,
            origins_searched=1, origins_available=1,
        )

    async def fake_resolve_run():
        return SimpleNamespace(ship="Railen")

    monkeypatch.setattr(resolver, "resolve_run", fake_resolve_run)
    monkeypatch.setattr(best_route_tool_module, "find_best_route", fake_find_best_route)

    message = await tool._arun(origin="Orison TDD")

    assert "Which ship are you flying?" not in message
    assert "Railen" in message


# --- origin resolution: exact match fails, region fallback ------------------------

async def test_origin_not_found_at_all(tool, monkeypatch):
    def fake_resolve_or_hedge(query, items, label, **kwargs):
        if label == "location":
            return None, "Couldn't find a location matching 'Nowhereville'."
        return matching.resolve_or_hedge(query, items, label, **kwargs)

    monkeypatch.setattr(best_route_tool_module, "resolve_or_hedge", fake_resolve_or_hedge)
    monkeypatch.setattr(best_route_tool_module, "terminals_within", lambda *a, **k: None)

    message = await tool._arun(origin="Nowhereville", ship="Railen")

    # The region fallback also came up empty, so this surfaces the *original*
    # exact-match error, not a second "couldn't find a location" from the fallback.
    assert message == "Couldn't find a location matching 'Nowhereville'."


async def test_origin_falls_back_to_region_when_exact_match_fails(tool, monkeypatch):
    def fake_resolve_or_hedge(query, items, label, **kwargs):
        if label == "location":
            return None, "Didn't catch which location you meant clearly enough — can you say it again?"
        return matching.resolve_or_hedge(query, items, label, **kwargs)

    region_terminal = SimpleNamespace(id=2, name="Admin - Seraphim", type=TerminalType.COMMODITY)
    monkeypatch.setattr(best_route_tool_module, "resolve_or_hedge", fake_resolve_or_hedge)
    monkeypatch.setattr(
        best_route_tool_module, "terminals_within", lambda *a, **k: RegionMatch("Orison", [region_terminal])
    )

    winner = _route("Copper", "Rod's Fuel 'N Supplies", scu=64)

    async def fake_find_best_route(*args, **kwargs):
        assert args[2] == [2]  # searched the region's terminal id, not a single exact one
        return RouteSearch(
            best=winner, best_profit=960_000.0, best_rate=1600.0, best_scu=64,
            runner_up=None, runner_up_profit=None, runner_up_rate=None, runner_up_scu=None,
            origins_searched=1, origins_available=1,
        )

    monkeypatch.setattr(best_route_tool_module, "find_best_route", fake_find_best_route)

    message = await tool._arun(origin="Orison", ship="Railen")

    assert message.startswith("Best in Orison, from ")


# --- origin resolution: 'near' mode ------------------------------------------------

async def test_origin_near_mode_location_not_found(tool, monkeypatch):
    async def fake_terminals_near(*args, **kwargs):
        return None

    monkeypatch.setattr(best_route_tool_module, "terminals_near", fake_terminals_near)

    message = await tool._arun(origin="Deep Space", origin_mode="near", ship="Railen")

    assert message == "Couldn't find a location matching 'Deep Space'."


async def test_origin_near_mode_no_terminals_in_range(tool, monkeypatch):
    async def fake_terminals_near(*args, **kwargs):
        return []

    monkeypatch.setattr(best_route_tool_module, "terminals_near", fake_terminals_near)

    message = await tool._arun(origin="Deep Space", origin_mode="near", ship="Railen")

    assert message == f"No terminals found within {DEFAULT_NEAR_DISTANCE} Gm of Deep Space."


# --- origin resolution: 'within' mode -----------------------------------------------

async def test_origin_within_mode_location_not_found(tool, monkeypatch):
    monkeypatch.setattr(best_route_tool_module, "terminals_within", lambda *a, **k: None)

    message = await tool._arun(origin="Nowhere System", origin_mode="within", ship="Railen")

    assert message == "Couldn't find a location matching 'Nowhere System'."


async def test_origin_within_mode_no_terminals(tool, monkeypatch):
    monkeypatch.setattr(best_route_tool_module, "terminals_within", lambda *a, **k: RegionMatch("Pyro", []))

    message = await tool._arun(origin="Pyro", origin_mode="within", ship="Railen")

    assert message == "No terminals found in Pyro."


# --- destination_region resolution --------------------------------------------------

async def test_destination_region_not_found(tool, monkeypatch):
    monkeypatch.setattr(best_route_tool_module, "terminals_within", lambda *a, **k: None)

    message = await tool._arun(origin="Orison TDD", ship="Railen", destination_region="Nowhere")

    assert message == "Couldn't find a location matching 'Nowhere'."


async def test_destination_region_has_no_terminals(tool, monkeypatch):
    monkeypatch.setattr(best_route_tool_module, "terminals_within", lambda *a, **k: RegionMatch("Pyro", []))

    message = await tool._arun(origin="Orison TDD", ship="Railen", destination_region="Pyro")

    assert message == "No terminals found in Pyro."


# --- commodity resolution ------------------------------------------------------------

def _cache_with_commodity(commodity):
    return SimpleNamespace(
        terminals=[SimpleNamespace(id=1, name="Orison TDD", type=TerminalType.COMMODITY)],
        vehicles=[SimpleNamespace(id=1, name="Railen", is_concept=False, scu=96)],
        commodities=[commodity],
    )


async def test_commodity_not_found(tool, monkeypatch):
    cache = _cache_with_commodity(SimpleNamespace(name="Copper", id=5, is_buyable=True))

    async def fake_get_uex_cache(self):
        return cache

    monkeypatch.setattr(UEXCorpClient, "get_uex_cache", fake_get_uex_cache)

    message = await tool._arun(origin="Orison TDD", ship="Railen", commodity="Wormhole Juice")

    assert message == "Couldn't find a commodity matching 'Wormhole Juice'."


async def test_commodity_not_buyable_is_rejected(tool, monkeypatch):
    # Matched against the full catalog on purpose (see resolve_or_hedge's own comment) —
    # this commodity is minable/salvage-only, so it resolves fine but has no buy price.
    cache = _cache_with_commodity(SimpleNamespace(name="Scrap Metal", id=7, is_buyable=False))

    async def fake_get_uex_cache(self):
        return cache

    monkeypatch.setattr(UEXCorpClient, "get_uex_cache", fake_get_uex_cache)

    message = await tool._arun(origin="Orison TDD", ship="Railen", commodity="Scrap Metal")

    assert message == (
        "Scrap Metal isn't sold at any terminal — it has to be mined or salvaged, so "
        "there's no buy-and-haul route for it. I can help with where to sell it once "
        "you've got some."
    )


# --- no usable route found ------------------------------------------------------------

async def test_no_route_found_without_qualifiers(tool, monkeypatch):
    async def fake_find_best_route(*args, **kwargs):
        return None

    monkeypatch.setattr(best_route_tool_module, "find_best_route", fake_find_best_route)

    message = await tool._arun(origin="Orison TDD", ship="Railen")

    assert message == "No usable in-system route turned up from Orison TDD."


async def test_no_route_found_lists_active_qualifiers(tool, monkeypatch):
    destination_terminal = SimpleNamespace(id=9, name="Some Station", type=TerminalType.COMMODITY)
    monkeypatch.setattr(
        best_route_tool_module, "terminals_within", lambda *a, **k: RegionMatch("Crusader", [destination_terminal])
    )

    async def fake_find_best_route(*args, **kwargs):
        return None

    monkeypatch.setattr(best_route_tool_module, "find_best_route", fake_find_best_route)

    message = await tool._arun(
        origin="Orison TDD", ship="Railen", destination_region="Crusader",
        exclude_ground_stations=True, require_autoload=True,
    )

    assert message == (
        "No usable in-system route turned up from Orison TDD "
        "(excluding ground stations, auto-load only, staying within Crusader)."
    )
