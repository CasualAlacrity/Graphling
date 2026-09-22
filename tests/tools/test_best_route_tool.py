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
from tools.trade_run import route_cache
from tools.uexcorp.client import UEXCorpClient
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
