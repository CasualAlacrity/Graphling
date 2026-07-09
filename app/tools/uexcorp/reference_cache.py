from datetime import datetime

from pydantic import BaseModel, Field


class CachedBase(BaseModel):
    id: int
    name: str


class CachedCommodity(CachedBase):
    code: str
    id_parent: int
    is_raw: bool
    is_refined: bool


class CachedStarSystem(CachedBase):
    code: str


class CachedOrbit(CachedBase):
    id_star_system: int
    star_system_name: str | None


class CachedTerminal(CachedBase):
    type: str
    star_system_name: str | None
    orbit_name: str | None
    moon_name: str | None
    planet_name: str | None


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
    pass


class CachedRefineryYield(BaseModel):
    commodity_name: str
    terminal_name: str
    star_system_name: str | None
    orbit_name: str | None
    moon_name: str | None
    planet_name: str | None
    yield_bonus_percent: float = Field(validation_alias="value")


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
