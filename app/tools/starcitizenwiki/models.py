from pydantic import AliasPath, BaseModel, Field


class ShipSpeed(BaseModel):
    """Deliberately narrow — the star-citizen.wiki vehicle response has ~80 fields, but the
    travel-time calculation only needs these. Pulled straight out of the nested
    speed/quantum/fuel objects the API actually returns, via AliasPath, rather than
    modeling the full response shape.

    Everything past quantum_spool_time is optional, with None meaning "not known" —
    travel_time falls back to the older, coarser model for whichever piece is missing.
    Optional for two reasons beyond ships the wiki simply lacks data for: cached
    payloads written before these fields existed (server/wiki_cache_service.py
    model_construct()s them, which fills in defaults rather than failing), and a client
    talking to a server that hasn't been redeployed with them yet.

    Units, confirmed against the wiki's own numbers rather than assumed:
    - quantum_fuel_capacity is SCU and quantum_range is meters — the Bolon's
      0.00654 SCU/Gm requirement times the Railen's 1345.6 Gm range is exactly its
      8.8 SCU tank, so fuel per Gm is just capacity / range.
    - hydrogen_capacity is SCU. hydrogen_usage_* (burn at full thrust, per second) is
      NOT confirmed — see travel_time.HYDROGEN_USAGE_UNIT_SCU.
    - The quantum_stage_* / quantum_engage_speed fields aren't on the vehicle response
      at all — they're the installed quantum drive's own stats, fetched as a separate
      item and merged in by StarCitizenWikiClient.fetch_ship_speed_from_wiki.
    """

    game_name: str
    scm_speed: float = Field(validation_alias=AliasPath("speed", "scm"))
    quantum_speed: float = Field(validation_alias=AliasPath("quantum", "quantum_speed"))
    quantum_spool_time: float = Field(validation_alias=AliasPath("quantum", "quantum_spool_time"))
    quantum_fuel_capacity: float | None = Field(
        default=None, validation_alias=AliasPath("quantum", "quantum_fuel_capacity"),
    )
    quantum_range: float | None = Field(default=None, validation_alias=AliasPath("quantum", "quantum_range"))
    max_speed: float | None = Field(default=None, validation_alias=AliasPath("speed", "max"))
    zero_to_max: float | None = Field(default=None, validation_alias=AliasPath("speed", "zero_to_max"))
    max_to_zero: float | None = Field(default=None, validation_alias=AliasPath("speed", "max_to_zero"))
    hydrogen_capacity: float | None = Field(default=None, validation_alias=AliasPath("fuel", "capacity"))
    hydrogen_usage_main: float | None = Field(default=None, validation_alias=AliasPath("fuel", "usage", "main"))
    hydrogen_usage_retro: float | None = Field(default=None, validation_alias=AliasPath("fuel", "usage", "retro"))
    quantum_engage_speed: float | None = None
    quantum_stage_one_accel: float | None = None
    quantum_stage_two_accel: float | None = None


class LocationPosition(BaseModel):
    """A named in-game location (planet, moon, station, city, outpost, POI) with real 3D
    coordinates — from the wiki's locations/positions endpoint, not the versioned v2 API.
    Coordinates are in meters, confirmed live against two legs of a real route (33.82 Gm
    and 90.23 Gm, exact to the decimal) from the wiki's own route-planner tool. See
    tools.travel_time._location_distance_m for the conversion and how an earlier,
    less-verified reading landed on kilometers instead."""

    name: str
    type: str
    system: str
    x: float
    y: float
    z: float
    # Meters from the location at which quantum travel drops the pilot out — from the
    # wiki's v2 locations endpoint (game data's quantum_travel.arrival_radius), joined
    # in by uuid since the positions endpoint doesn't carry it. Checked against what
    # pilots actually see: stations 24 km, cities/outposts 20 km, Levski 60 km. None
    # when the v2 data has no entry (or no quantum_travel block) for this location.
    arrival_radius: float | None = None
