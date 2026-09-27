"""A small, fixed "world" of UEX/wiki data for the deterministic tool-selection
harness (docs/todo.md Phase 3).

Every fake client in fakes.py serves data from here instead of a real HTTP call —
built from the *same* Pydantic models production code uses (`CachedCommodity`,
`CachedTerminal`, `UexReferenceCache`, ...), so a fixture that's wrong looks exactly
like a real schema mismatch would (a `ValidationError` at import time), not a
harness-only bug that only shows up as a confusing tool-result mismatch later.

Deliberately small and hand-picked, not a dump of real UEX data — just enough
distinct terminals/commodities to make "cheapest to buy" and "best to sell"
genuinely different answers. Grows as more harness cases need more of the world
(e.g. the Railen/Orison phonetic-mismatch seed cases from docs/todo.md's Phase 3
list will need a vehicle and a couple of ambiguously-named locations added here).
"""

from datetime import UTC, datetime

from tools.starcitizenwiki.models import LocationPosition, ShipSpeed
from tools.uexcorp.reference_cache import (
    CachedCommodity,
    CachedTerminal,
    CachedVehicle,
    TerminalType,
    UexReferenceCache,
)

LARANITE = CachedCommodity(
    id=101, name="Laranite", code="LARN", id_parent=0, is_raw=True, is_refined=False,
    ids_star_systems=[1], ids_planets=[], ids_moons=[], ids_orbits=[], ids_poi=[], is_buyable=1,
)

BAIJINI_POINT = CachedTerminal(
    id=201, name="Baijini Point", type=TerminalType.COMMODITY, star_system_name="Pyro",
    orbit_name="Pyro I", moon_name=None, planet_name=None, displayname="Baijini Point",
    nickname="Baijini", space_station_name="Baijini Point", outpost_name=None, city_name=None,
    is_auto_load=0,
)

PORT_TRESSLER = CachedTerminal(
    id=202, name="Port Tressler", type=TerminalType.COMMODITY, star_system_name="Stanton",
    orbit_name="Crusader", moon_name=None, planet_name=None, displayname="Port Tressler",
    nickname="Tressler", space_station_name="Port Tressler", outpost_name=None, city_name=None,
    is_auto_load=0,
)

CUTLASS_BLACK = CachedVehicle(id=301, name="Cutlass Black", name_full="Drake Cutlass Black", scu=46.0, is_concept=0)

REFERENCE_CACHE = UexReferenceCache(
    fetched_at=datetime.now(UTC),
    commodities=[LARANITE],
    star_systems=[],
    orbits=[],
    terminals=[BAIJINI_POINT, PORT_TRESSLER],
    moons=[],
    item_categories=[],
    items=[],
    vehicles=[CUTLASS_BLACK],
    refinery_yields=[],
    poi=[],
    commodity_statuses=[],
)

# Raw dict rows, not UEXTradeData instances — get_commodity_prices returns list[dict]
# straight off UEX's own JSON shape (id_terminal/price_buy/price_sell), and it's the
# *tool* that validates each row into UEXTradeData, not the client. Keeping these as
# plain dicts means the fake is standing in for the HTTP response, not for the
# parsing step — a bug in that parsing still gets exercised for real.
COMMODITY_PRICE_ROWS: dict[int, list[dict]] = {
    LARANITE.id: [
        {
            "id_terminal": BAIJINI_POINT.id, "terminal_name": BAIJINI_POINT.name,
            "star_system_name": "Pyro", "orbit_name": "Pyro I", "moon_name": None, "planet_name": None,
            "price_buy": 3.15, "price_sell": 2.85, "status_buy": 1, "status_sell": 1,
        },
        {
            "id_terminal": PORT_TRESSLER.id, "terminal_name": PORT_TRESSLER.name,
            "star_system_name": "Stanton", "orbit_name": "Crusader", "moon_name": None, "planet_name": None,
            "price_buy": 3.42, "price_sell": 3.10, "status_buy": 1, "status_sell": 1,
        },
    ],
}

# Ships have no in-game resale market (VehiclePurchaseTool's own docstring), so these
# rows only ever carry price_buy — price_sell stays 0/omitted, same as UEX's real payload.
VEHICLE_PURCHASE_ROWS: dict[int, list[dict]] = {
    CUTLASS_BLACK.id: [
        {
            "id_terminal": BAIJINI_POINT.id, "terminal_name": BAIJINI_POINT.name,
            "star_system_name": "Pyro", "orbit_name": "Pyro I", "moon_name": None, "planet_name": None,
            "price_buy": 1_190_000, "price_sell": 0, "status_buy": 1, "status_sell": 0,
        },
        {
            "id_terminal": PORT_TRESSLER.id, "terminal_name": PORT_TRESSLER.name,
            "star_system_name": "Stanton", "orbit_name": "Crusader", "moon_name": None, "planet_name": None,
            "price_buy": 1_225_000, "price_sell": 0, "status_buy": 1, "status_sell": 0,
        },
    ],
}

# Raw dict rows for uex_cache_client's route functions -- matches UEXTradeRoute's
# validation_alias field names (id_commodity/id_terminal_origin/...), same reasoning
# as the price rows above: standing in for the HTTP response, not the parsing step.
# Not yet consumed by a harness case (find_best_route needs orbit-distance and wiki-
# location fakes tied together too, a bigger lift than one route row) -- faked here
# so the seam exists for when that case gets built, not silently skipped.
LARANITE_ROUTE_ROWS: list[dict] = [
    {
        "id_commodity": LARANITE.id, "commodity_name": LARANITE.name,
        "id_terminal_origin": BAIJINI_POINT.id, "origin_terminal_name": BAIJINI_POINT.name,
        "origin_star_system_name": "Pyro", "origin_planet_name": None,
        "id_terminal_destination": PORT_TRESSLER.id, "destination_terminal_name": PORT_TRESSLER.name,
        "destination_star_system_name": "Stanton", "destination_planet_name": None,
        "price_origin": 3.15, "price_destination": 3.10, "price_margin": -0.05,
        "scu_origin": 500.0, "scu_destination": 500.0, "status_origin": 1, "status_destination": 1,
        "distance": 1_000_000.0, "is_on_ground_origin": 0, "is_on_ground_destination": 0,
        "container_sizes_origin": [1, 2, 4, 8, 16, 24, 32], "container_sizes_destination": [1, 2, 4, 8, 16, 24, 32],
    },
]

FREELANCER_SHIP_SPEED = ShipSpeed.model_construct(
    game_name="Freelancer", scm_speed=205.0, quantum_speed=91_200_000.0, quantum_spool_time=6.0,
)

BAIJINI_POINT_LOCATION = LocationPosition(
    name="Baijini Point", type="Anomaly", system="Pyro", x=1_000_000.0, y=2_000_000.0, z=3_000_000.0,
)

PORT_TRESSLER_LOCATION = LocationPosition(
    name="Port Tressler", type="Manmade", system="Stanton", x=4_000_000.0, y=5_000_000.0, z=6_000_000.0,
)

WIKI_LOCATIONS: list[LocationPosition] = [BAIJINI_POINT_LOCATION, PORT_TRESSLER_LOCATION]
