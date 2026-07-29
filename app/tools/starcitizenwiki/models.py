from pydantic import AliasPath, BaseModel, Field


class ShipSpeed(BaseModel):
    """Deliberately narrow — the star-citizen.wiki vehicle response has ~80 fields, but the
    travel-time calculation only needs these three. Pulled straight out of the nested
    speed/quantum objects the API actually returns, via AliasPath, rather than modeling
    the full response shape."""

    game_name: str
    scm_speed: float = Field(validation_alias=AliasPath("speed", "scm"))
    quantum_speed: float = Field(validation_alias=AliasPath("quantum", "quantum_speed"))
    quantum_spool_time: float = Field(validation_alias=AliasPath("quantum", "quantum_spool_time"))


class LocationPosition(BaseModel):
    """A named in-game location (planet, moon, station, city, outpost, POI) with real 3D
    coordinates — from the wiki's locations/positions endpoint, not the versioned v2 API.
    Coordinates are in kilometers, confirmed live: computing Euclidean distance between
    Seraphim Station and Orison and dividing by 1,000,000 matched the wiki's own
    route-planner tool's displayed "0.8 Gm" almost exactly."""

    name: str
    type: str
    system: str
    x: float
    y: float
    z: float
