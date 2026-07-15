import enum
from datetime import datetime

from pydantic import BaseModel, Field, field_validator


class TerminalType(str, enum.Enum):
    ITEM = "item"
    COMMODITY = "commodity"
    COMMODITY_RAW = "commodity_raw"
    FUEL = "fuel"
    REFINERY = "refinery"
    VEHICLE_RENT = "vehicle_rent"
    VEHICLE_BUY = "vehicle_buy"


class CachedBase(BaseModel):
    id: int
    name: str


def _parse_id_list(value: str | list[int] | None) -> list[int]:
    if not value:
        return []

    if isinstance(value, list):
        return value

    ids = []
    for part in value.split(","):
        if part:
            ids.append(int(part))
    return ids


class CachedCommodity(CachedBase):
    code: str
    id_parent: int
    is_raw: bool
    is_refined: bool
    ids_star_systems: list[int]
    ids_planets: list[int]
    ids_moons: list[int]
    ids_orbits: list[int]
    ids_poi: list[int]
    is_buyable:int

    @field_validator("ids_star_systems", "ids_planets", "ids_moons", "ids_orbits", "ids_poi", mode="before")
    @classmethod
    def parse_id_lists(cls, value: str | list[int] | None) -> list[int]:
        return _parse_id_list(value)


class CachedStarSystem(CachedBase):
    code: str


class CachedOrbit(CachedBase):
    id_star_system: int
    star_system_name: str | None


class CachedTerminal(CachedBase):
    type: TerminalType
    star_system_name: str | None
    orbit_name: str | None
    moon_name: str | None
    planet_name: str | None
    displayname:str|None
    nickname:str|None


class CachedMoon(CachedBase):
    id_star_system: int
    id_planet: int
    id_orbit: int
    star_system_name: str | None
    orbit_name: str | None
    planet_name: str | None
    code: str


class CachedItem(CachedBase):
    slug: str


class CachedItemCategory(CachedBase):
    section: str


class CachedVehicle(CachedBase):
    name_full:str


class CachedRefineryYield(BaseModel):
    commodity_name: str
    terminal_name: str
    star_system_name: str | None
    orbit_name: str | None
    moon_name: str | None
    planet_name: str | None
    yield_bonus_percent: float = Field(validation_alias="value")


class CachedPoi(CachedBase):
    type: str
    star_system_name: str | None
    orbit_name: str | None
    moon_name: str | None
    planet_name: str | None


class CachedCommodityStatus(BaseModel):
    type: str  # "buy" or "sell" — which side of a transaction this tier applies to
    code: int
    name: str
    name_short: str
    percentage_start: int
    percentage_end: int


class UexReferenceCache(BaseModel):
    fetched_at: datetime
    commodities: list[CachedCommodity]
    star_systems: list[CachedStarSystem]
    orbits: list[CachedOrbit]
    terminals: list[CachedTerminal]
    moons: list[CachedMoon]
    item_categories: list[CachedItemCategory]
    items: list[CachedItem]
    vehicles: list[CachedVehicle]
    refinery_yields: list[CachedRefineryYield]
    poi: list[CachedPoi]
    commodity_statuses: list[CachedCommodityStatus]
