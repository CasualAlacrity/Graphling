"""Installs fake, deterministic stand-ins for every external-data seam a tool can
reach, backed by world.py's fixed data.

Same idiom the rest of the suite already uses for these exact targets — class-level
patching for `UEXCorpClient` (a Pydantic `BaseModel`; plain instance-attribute
assignment doesn't stick on one of those, a gotcha this codebase has already hit more
than once), module-level patching for `uex_cache_client`/`wiki_cache_client`/
`ledger_client`. This just packages that idiom as one reusable context manager
instead of repeating the patch calls in every harness case.

`ledger_client` (trade-run state) isn't patched yet — deferred until a harness case
actually needs one, per the harness plan (docs/todo.md Phase 3).
"""

from contextlib import ExitStack, contextmanager
from unittest.mock import patch

import uex_cache_client
import wiki_cache_client
from harness import world
from tools.starcitizenwiki.models import LocationPosition, ShipSpeed
from tools.uexcorp.client import UEXCorpClient
from tools.uexcorp.reference_cache import UexReferenceCache


@contextmanager
def install_fakes():
    async def fake_get_uex_cache(self) -> UexReferenceCache:
        return world.REFERENCE_CACHE

    async def fake_get_commodity_prices(self, commodity_id: int) -> list[dict]:
        return world.COMMODITY_PRICE_ROWS.get(commodity_id, [])

    async def fake_get_vehicle_purchase_prices(self, vehicle_id: int) -> list[dict]:
        return world.VEHICLE_PURCHASE_ROWS.get(vehicle_id, [])

    async def fake_get_reference_cache() -> UexReferenceCache:
        return world.REFERENCE_CACHE

    async def fake_cached_routes_from_terminal(terminal_id: int) -> list[dict] | None:
        if terminal_id == world.BAIJINI_POINT.id:
            return world.LARANITE_ROUTE_ROWS
        return None

    async def fake_fetch_routes_from_terminal(terminal_id: int) -> list[dict]:
        if terminal_id == world.BAIJINI_POINT.id:
            return world.LARANITE_ROUTE_ROWS
        return []

    async def fake_get_ship_speed(ship_name: str) -> ShipSpeed | None:
        if ship_name.lower() == world.FREELANCER_SHIP_SPEED.game_name.lower():
            return world.FREELANCER_SHIP_SPEED
        return None

    async def fake_get_locations() -> list[LocationPosition]:
        return world.WIKI_LOCATIONS

    with ExitStack() as stack:
        stack.enter_context(patch.object(UEXCorpClient, "get_uex_cache", fake_get_uex_cache))
        stack.enter_context(patch.object(UEXCorpClient, "get_commodity_prices", fake_get_commodity_prices))
        stack.enter_context(
            patch.object(UEXCorpClient, "get_vehicle_purchase_prices", fake_get_vehicle_purchase_prices)
        )
        stack.enter_context(patch.object(uex_cache_client, "get_reference_cache", fake_get_reference_cache))
        stack.enter_context(
            patch.object(uex_cache_client, "cached_routes_from_terminal", fake_cached_routes_from_terminal)
        )
        stack.enter_context(
            patch.object(uex_cache_client, "fetch_routes_from_terminal", fake_fetch_routes_from_terminal)
        )
        stack.enter_context(patch.object(wiki_cache_client, "get_ship_speed", fake_get_ship_speed))
        stack.enter_context(patch.object(wiki_cache_client, "get_locations", fake_get_locations))
        yield
