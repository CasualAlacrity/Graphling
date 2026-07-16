import asyncio
import os

from db.session import SessionLocal, engine
from tools.uexcorp.client import UEXCorpClient
from tools.uexcorp.price_cache import get_commodity_price_rows, get_terminal_price_rows
from tools.uexcorp.reference_cache import TerminalType
from tools.uexcorp.trade_data import UEXTradeRoute

uex_client = UEXCorpClient(
    api_key=os.getenv("UEXCORP_API_KEY"),
    bearer_token=os.getenv("UEXCORP_BEARER_TOKEN"),
)


async def _load_uex_cache():
    cache = await uex_client.get_uex_cache()
    # get_uex_cache() now reads/writes the reference cache table. This call runs in its
    # own throwaway asyncio.run() loop, same reasoning as search_routes' dispose.
    await engine.dispose()
    return cache


uex_cache = asyncio.run(_load_uex_cache())
ship_names = [v.name_full for v in uex_cache.vehicles if v.scu >= 1]
commodity_names = [c.name for c in uex_cache.commodities if c.is_buyable == 1]
terminal_names = [t.nickname for t in uex_cache.terminals if t.type == TerminalType.COMMODITY]


def find_terminal(name):
    for terminal in uex_cache.terminals:
        if terminal.nickname == name:
            return terminal
    return None


def find_commodity(name):
    for commodity in uex_cache.commodities:
        if commodity.name == name:
            return commodity
    return None


def find_vehicle(name):
    for vehicle in uex_cache.vehicles:
        if vehicle.name_full == name:
            return vehicle
    return None


def inventory_code_for(status_name, status_type):
    if not status_name or status_name == "Select...":
        return None
    for status in uex_cache.commodity_statuses:
        if status.type == status_type and status.name == status_name:
            return status.code
    return None


def is_space_terminal(terminal):
    # A terminal requires an atmospheric landing (is_on_ground) exactly when it's a
    # city/outpost, not a space station — verified against live route data (0 mismatches
    # across 106 terminals / 2141 routes sampled). Reference-cache-only, so this works
    # before a route search ever runs.
    return terminal is not None and terminal.space_station_name is not None


def terminal_breadcrumb(terminal):
    if terminal is None:
        return ""
    parts = [
        terminal.star_system_name,
        terminal.planet_name,
        terminal.moon_name,
        terminal.space_station_name,
        terminal.outpost_name,
        terminal.city_name,
    ]
    return ">".join(part for part in parts if part)


# "buy" = stock available to purchase at a terminal; "sell" = how saturated a
# terminal's demand already is. Same tiers, different top label.
SOURCE_INVENTORY_LEVELS = [
    status.name for status in uex_cache.commodity_statuses if status.type == "buy"
]

DESTINATION_INVENTORY_LEVELS = [
    status.name for status in uex_cache.commodity_statuses if status.type == "sell"
]


async def commodity_ids_at(terminal_id, side):
    async with SessionLocal() as session:
        rows = await get_terminal_price_rows(uex_client, session, terminal_id)
    field_name = "price_buy" if side == "buy" else "price_sell"
    return {row["id_commodity"] for row in rows if row.get(field_name)}


async def terminal_ids_for(commodity_id, side):
    async with SessionLocal() as session:
        rows = await get_commodity_price_rows(uex_client, session, commodity_id)
    field_name = "price_buy" if side == "buy" else "price_sell"
    return {row["id_terminal"] for row in rows if row.get(field_name)}


async def search_routes(
    commodity_id,
    source_terminal_id,
    destination_terminal_id,
    min_source_code,
    max_destination_code,
    space_only,
    require_autoload,
) -> list[UEXTradeRoute]:
    raw_routes = await uex_client.get_commodity_routes(
        commodity_id=commodity_id,
        origin_terminal_id=source_terminal_id,
        destination_terminal_id=destination_terminal_id,
    )
    routes = [UEXTradeRoute.model_validate(row) for row in raw_routes]

    # Space Only should only constrain an end the search left open — a pinned terminal
    # (the player explicitly chose it) shouldn't get excluded by its own ground status.
    # e.g. source=TDD Orison (a ground landing zone) + Space Only should still return
    # its space-station destinations, not zero results because Orison itself is ground.
    constrain_origin_to_space = space_only and source_terminal_id is None
    constrain_destination_to_space = space_only and destination_terminal_id is None

    return [
        route for route in routes
        if (min_source_code is None or route.status_origin >= min_source_code)
        and (max_destination_code is None or route.status_destination <= max_destination_code)
        and (not constrain_origin_to_space or route.is_on_ground_origin == 0)
        and (not constrain_destination_to_space or route.is_on_ground_destination == 0)
        and (not require_autoload or (route.has_loading_dock_origin == 1 and route.has_loading_dock_destination == 1))
    ]
