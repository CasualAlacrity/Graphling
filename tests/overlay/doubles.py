"""Shared test doubles for the overlay's Qt tests — a small reference cache plus fake
async lookups, built from the real UEX cache/trade-route models so fixture data can't
silently drift out of sync with the schema the app actually uses."""
import uuid
from datetime import UTC, datetime

from db.models import CargoTransferType, LegType, TradeLeg, TradeRun
from tools.uexcorp.reference_cache import (
    CachedCommodity,
    CachedTerminal,
    CachedVehicle,
    TerminalType,
    UexReferenceCache,
)
from tools.uexcorp.trade_data import UEXTradeRoute

TERMINALS = [
    CachedTerminal(
        id=1, name="Orison TDD", type=TerminalType.COMMODITY, star_system_name="Stanton",
        orbit_name="Stanton", moon_name=None, planet_name="Crusader", displayname="Orison TDD",
        nickname="Orison TDD", space_station_name=None, outpost_name=None, city_name="Orison",
    ),
    CachedTerminal(
        id=2, name="Seraphim Station", type=TerminalType.COMMODITY, star_system_name="Stanton",
        orbit_name="Stanton", moon_name=None, planet_name="Crusader", displayname="Seraphim Station",
        nickname="Seraphim Station", space_station_name="Seraphim Station", outpost_name=None, city_name=None,
    ),
    CachedTerminal(
        id=3, name="Baijini Point", type=TerminalType.COMMODITY, star_system_name="Stanton",
        orbit_name="Stanton", moon_name=None, planet_name=None, displayname="Baijini Point",
        nickname="Baijini Point", space_station_name="Baijini Point", outpost_name=None, city_name=None,
    ),
]

COMMODITIES = [
    CachedCommodity(
        id=10, name="Agricium", code="AGRI", id_parent=0, is_raw=False, is_refined=False,
        ids_star_systems=[], ids_planets=[], ids_moons=[], ids_orbits=[], ids_poi=[], is_buyable=1,
    ),
    CachedCommodity(
        id=20, name="Laranite", code="LRNT", id_parent=0, is_raw=False, is_refined=False,
        ids_star_systems=[], ids_planets=[], ids_moons=[], ids_orbits=[], ids_poi=[], is_buyable=1,
    ),
]

VEHICLES = [
    CachedVehicle(id=1, name="Railen", name_full="Railen", scu=96, is_concept=0),
]


def make_fake_cache() -> UexReferenceCache:
    return UexReferenceCache(
        fetched_at=datetime.now(UTC),
        commodities=COMMODITIES, star_systems=[], orbits=[], terminals=TERMINALS, moons=[],
        item_categories=[], items=[], vehicles=VEHICLES, refinery_yields=[], poi=[],
        commodity_statuses=[],
    )


def make_route(
    commodity_id=10, commodity_name="Agricium", origin_id=1, origin_name="Orison TDD",
    destination_id=2, destination_name="Seraphim Station", price_origin=10.0, price_destination=20.0,
    price_margin=15.0, scu_origin=100, scu_destination=100, distance=10.0,
    origin_system="Stanton", origin_planet="Crusader", destination_system="Stanton", destination_planet="Crusader",
) -> UEXTradeRoute:
    return UEXTradeRoute(
        id_commodity=commodity_id, commodity_name=commodity_name,
        id_terminal_origin=origin_id, origin_terminal_name=origin_name,
        origin_star_system_name=origin_system, origin_planet_name=origin_planet,
        id_terminal_destination=destination_id, destination_terminal_name=destination_name,
        destination_star_system_name=destination_system, destination_planet_name=destination_planet,
        price_origin=price_origin, price_destination=price_destination, price_margin=price_margin,
        scu_origin=scu_origin, scu_destination=scu_destination, status_origin=2, status_destination=1,
        distance=distance, is_on_ground_origin=0, is_on_ground_destination=0,
        has_loading_dock_origin=1, has_loading_dock_destination=1,
    )


def make_trade_leg(
    leg_type=LegType.ACQUISITION, cargo_transfer_type=CargoTransferType.MANUAL,
    terminal_id=1, terminal_name="Orison TDD", commodity_name="Agricium", quantity_scu=32, price_per_unit=14,
    **timestamp_overrides,
) -> TradeLeg:
    fields = {
        "id": uuid.uuid4(), "leg_type": leg_type, "terminal_id": terminal_id, "terminal_name": terminal_name,
        "commodity_name": commodity_name, "quantity_scu": quantity_scu, "price_per_unit": price_per_unit,
        "cargo_transfer_type": cargo_transfer_type, "cargo_transfer_fee": 0,
        "created_at": datetime.now(UTC),
        "started_at": None, "reached_at": None, "transaction_completed_at": None,
        "transferred_at": None, "finalized_at": None,
    }
    fields.update(timestamp_overrides)
    return TradeLeg(**fields)


def make_trade_run(ship=None, legs=None, **overrides) -> TradeRun:
    if legs is None:
        legs = [
            make_trade_leg(LegType.ACQUISITION, terminal_id=1, terminal_name="Orison TDD"),
            make_trade_leg(LegType.SALE, terminal_id=2, terminal_name="Seraphim Station"),
        ]
    fields = {"id": uuid.uuid4(), "ship": ship, "created_at": datetime.now(UTC), "finalized_at": None, "legs": legs}
    fields.update(overrides)
    return TradeRun(**fields)
