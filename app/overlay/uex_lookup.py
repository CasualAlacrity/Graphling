import asyncio
import os

from db.session import SessionLocal, engine
from tools.uexcorp.client import UEXCorpClient
from tools.uexcorp.matching import find_commodity_by_id as _find_commodity_by_id
from tools.uexcorp.price_cache import get_commodity_price_rows, get_commodity_route_rows, get_terminal_price_rows
from tools.uexcorp.reference_cache import TerminalType
from tools.uexcorp.trade_data import UEXTradeRoute

uex_client = UEXCorpClient(
    api_key=os.getenv("UEXCORP_API_KEY"),
    bearer_token=os.getenv("UEXCORP_BEARER_TOKEN"),
)

# Populated by init() — every function below that touches these assumes init() has
# already run. Left as plain module attributes (not a class) so `uex_lookup.X` access
# from other modules keeps working unchanged; the point of this split is only to make
# importing this module free of DB/network side effects, not to change its shape.
uex_cache = None
ship_names = []
commodity_names = []
terminal_names = []
SOURCE_INVENTORY_LEVELS = []
DESTINATION_INVENTORY_LEVELS = []
_star_system_codes = {}


async def _load_uex_cache():
    cache = await uex_client.get_uex_cache()
    # get_uex_cache() now reads/writes the reference cache table. This call runs in its
    # own throwaway asyncio.run() loop, same reasoning as search_routes' dispose.
    await engine.dispose()
    return cache


def init():
    """Loads the UEX reference cache and derives every lookup table this module
    exposes. Must be called once, before anything else here is used — kept explicit
    rather than a module-level side effect so importing uex_lookup (directly, or via
    filter_panel/results_panel) is cheap and doesn't require Postgres/UEX to be
    reachable, which matters for tests and for tooling that only needs the pure
    functions below."""
    global uex_cache, ship_names, commodity_names, terminal_names
    global SOURCE_INVENTORY_LEVELS, DESTINATION_INVENTORY_LEVELS, _star_system_codes

    uex_cache = asyncio.run(_load_uex_cache())
    ship_names = [v.name_full for v in uex_cache.vehicles if v.scu >= 1 and v.is_concept == 0]
    commodity_names = [c.name for c in uex_cache.commodities if c.is_buyable == 1]
    terminal_names = [t.nickname for t in uex_cache.terminals if t.type == TerminalType.COMMODITY]
    # "buy" = stock available to purchase at a terminal; "sell" = how saturated a
    # terminal's demand already is. Same tiers, different top label.
    SOURCE_INVENTORY_LEVELS = [status.name for status in uex_cache.commodity_statuses if status.type == "buy"]
    DESTINATION_INVENTORY_LEVELS = [status.name for status in uex_cache.commodity_statuses if status.type == "sell"]
    _star_system_codes = {system.name: system.code for system in uex_cache.star_systems}


def route_breadcrumb(system_name, planet_name, terminal_id, fallback_terminal_name):
    # Only the system gets abbreviated to its official UEX code (e.g. "Stanton" -> "ST")
    # — planet stays full. Terminal uses its reference-cache nickname (short, human —
    # "Everus Harbor") rather than the routes endpoint's own terminal_name, which is the
    # full official name ("Admin - Everus Harbor") and far too long for a list row.
    terminal = find_terminal_by_id(terminal_id)
    terminal_name = terminal.nickname if terminal else fallback_terminal_name
    parts = [_star_system_codes.get(system_name, system_name), planet_name, terminal_name]
    return "/".join(part for part in parts if part)


def terminal_place_name(terminal):
    # The Travel dialog needs the place a terminal actually sits at (what you'd type into
    # Mobiglass nav — "Orison"), not the terminal's own name ("Orison TDD"). Falls back to
    # the terminal's nickname for the rare terminal with none of the three set (e.g. a
    # free-floating station) rather than showing nothing.
    if terminal is None:
        return None
    if terminal.city_name:
        return terminal.city_name, "City"
    if terminal.outpost_name:
        return terminal.outpost_name, "Outpost"
    if terminal.space_station_name:
        return terminal.space_station_name, "Station"
    return (terminal.nickname, "Terminal") if terminal.nickname else None


def commodity_code_for(commodity_id, fallback_name):
    # The routes endpoint doesn't include a commodity code (confirmed live — only
    # commodity_name/commodity_slug), so this needs the reference-cache commodity,
    # unlike terminal_code which the routes endpoint does provide directly.
    commodity = _find_commodity_by_id(uex_cache, commodity_id)
    return commodity.code if commodity else fallback_name


def commodity_code_for_name(commodity_name):
    # Ledger rows only ever stored the commodity's display name (TradeLeg has no
    # commodity_id column, unlike UEXTradeRoute) — this is commodity_code_for's
    # counterpart for that case, same reference-cache lookup keyed by name instead.
    commodity = find_commodity(commodity_name)
    return commodity.code if commodity else commodity_name


def find_terminal(name):
    for terminal in uex_cache.terminals:
        if terminal.nickname == name:
            return terminal
    return None


def find_terminal_by_id(terminal_id):
    for terminal in uex_cache.terminals:
        if terminal.id == terminal_id:
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


async def commodity_volatility(commodity_id):
    # One call covers every terminal that trades this commodity (origin and destination
    # both, for every route sharing this commodity) — cached for 30min in the same
    # UexPriceCache table commodity_ids_at/terminal_ids_for already use, so this doesn't
    # add new fetch cost beyond what filtering already pays.
    async with SessionLocal() as session:
        rows = await get_commodity_price_rows(uex_client, session, commodity_id)

    volatility_by_terminal = {}
    for row in rows:
        price_buy = row.get("price_buy")
        volatility_price_buy = row.get("volatility_price_buy")
        price_sell = row.get("price_sell")
        volatility_price_sell = row.get("volatility_price_sell")
        # UEX returns 0 (not null) for volatility_price_* when a terminal/commodity pair
        # doesn't have enough price history yet to compute one — a real, common case
        # (61% of active listings sampled), not a genuine zero-volatility reading. Treat
        # it as missing data, same as a truly absent value, rather than "perfectly stable".
        volatility_by_terminal[row["id_terminal"]] = {
            "buy_cv": volatility_price_buy / price_buy if price_buy and volatility_price_buy else None,
            "sell_cv": volatility_price_sell / price_sell if price_sell and volatility_price_sell else None,
        }
    return volatility_by_terminal


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


# UEX's commodities_routes endpoint requires id_commodity or id_terminal_origin
# (verified live — destination alone 400s with "missing_one_required_inputs"). A
# destination-only search fans out across every commodity sold there instead — bounded
# to this many concurrent live fetches so one search can't burst-hammer the API; each
# fetch is cached afterward (get_commodity_route_rows), so repeat/overlapping searches
# get progressively cheaper instead of re-paying the same cost every time. Same
# reasoning as Arkanis's own background sync (they batch 10 at a time); this is a bit
# more conservative since it runs reactively per user action, not as a one-off job.
ROUTE_FANOUT_CONCURRENCY = 5


async def _fanout_route_rows(commodity_ids) -> list[dict]:
    semaphore = asyncio.Semaphore(ROUTE_FANOUT_CONCURRENCY)

    async def fetch(commodity_id):
        async with semaphore:
            async with SessionLocal() as session:
                return await get_commodity_route_rows(uex_client, session, commodity_id)

    results = await asyncio.gather(*(fetch(commodity_id) for commodity_id in commodity_ids))
    return [row for rows in results for row in rows]


async def search_routes(
    commodity_id,
    source_terminal_id,
    destination_terminal_id,
    min_source_code,
    max_destination_code,
    space_only,
    require_autoload,
) -> list[UEXTradeRoute]:
    if commodity_id is not None:
        # The full route set for a commodity is cached as one unit regardless of origin/
        # destination — reused here instead of asking UEX to filter server-side, so a
        # repeat search that only changes source/destination (very common while narrowing
        # filters) is a cache hit instead of a fresh live call.
        async with SessionLocal() as session:
            raw_routes = await get_commodity_route_rows(uex_client, session, commodity_id)
        if source_terminal_id is not None:
            raw_routes = [row for row in raw_routes if row.get("id_terminal_origin") == source_terminal_id]
        if destination_terminal_id is not None:
            raw_routes = [row for row in raw_routes if row.get("id_terminal_destination") == destination_terminal_id]
    elif source_terminal_id is not None:
        commodity_ids = await commodity_ids_at(source_terminal_id, "buy")
        raw_routes = await _fanout_route_rows(commodity_ids)
        raw_routes = [row for row in raw_routes if row.get("id_terminal_origin") == source_terminal_id]
        # source+destination-with-no-commodity lands here (source is checked first) —
        # without this, destination_terminal_id was silently ignored whenever a source
        # was also set, returning every destination reachable from that source instead
        # of just the one the pilot picked.
        if destination_terminal_id is not None:
            raw_routes = [row for row in raw_routes if row.get("id_terminal_destination") == destination_terminal_id]
    elif destination_terminal_id is not None:
        commodity_ids = await commodity_ids_at(destination_terminal_id, "sell")
        raw_routes = await _fanout_route_rows(commodity_ids)
        raw_routes = [row for row in raw_routes if row.get("id_terminal_destination") == destination_terminal_id]
    else:
        raw_routes = []

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
