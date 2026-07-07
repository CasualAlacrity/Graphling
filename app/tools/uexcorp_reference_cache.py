from datetime import datetime

from pydantic import BaseModel


class CachedCommodity(BaseModel):
    id: int
    name: str
    code: str


class CachedStarSystem(BaseModel):
    id: int
    name: str
    code: str


class CachedOrbit(BaseModel):
    id: int
    id_star_system: int
    name: str
    star_system_name: str | None


class CachedTerminal(BaseModel):
    id: int
    name: str
    type: str
    star_system_name: str | None
    orbit_name: str | None
    moon_name: str | None
    planet_name: str | None


class UexReferenceCache(BaseModel):
    fetched_at: datetime
    commodities: list[CachedCommodity]
    star_systems: list[CachedStarSystem]
    orbits: list[CachedOrbit]
    terminals: list[CachedTerminal]
