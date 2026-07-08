from pydantic import BaseModel, Field


class LocationArgs(BaseModel):
    star_system: str | None = Field(
        default=None,
        description="The star system to narrow the search to, e.g. 'Stanton' or 'Pyro'. "
                    "Leave unset if the user didn't mention one."
    )
    orbit: str | None = Field(
        default=None,
        description="The planet to narrow the search to, e.g. 'microTech', 'Hurston'. "
                    "This is what players usually mean when they say 'planet'."
    )
    terminal: str | None = Field(
        default=None,
        description="An exact trading terminal/location name, e.g. 'Area18', 'Port Tressler', "
                    "'Ambitious Dream Refueling'. Only set this if the user named a specific location."
    )
    moon: str | None = Field(
        default=None,
        description="The moon to narrow the search to, e.g. 'Yela', 'Daymar'. "
                    "This is what players usually mean when they say 'moon'. "
                    "The player may mistakenly refer to these celestial bodies as 'planets'."
    )
    near: str | None = Field(
        default=None,
        description="A reference planet, moon, or trading terminal/station to measure distance from, "
                    "e.g. 'Yela', 'Crusader', 'CRU-L1'. Only set this when the user gives a distance "
                    "constraint (e.g. 'within 30gm of Crusader') — pair it with max_distance, since "
                    "near by itself doesn't filter anything."
    )
    max_distance: float | None = Field(
        default=None,
        description="The maximum distance in Gigameters (gm) a result may be from 'near'. Only set "
                    "this together with near — it has no effect on its own."
    )
