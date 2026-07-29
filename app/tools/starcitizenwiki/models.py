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
