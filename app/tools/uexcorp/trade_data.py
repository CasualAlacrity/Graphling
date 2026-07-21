from pydantic import BaseModel, Field, field_validator


class UEXTradeData(BaseModel):
    terminal_id: int | None = Field(default=None, validation_alias="id_terminal")
    terminal_name: str
    star_system_name: str | None
    orbit_name: str | None
    moon_name: str | None
    planet_name: str | None
    price_you_pay_to_acquire: float | None = Field(default=None, validation_alias="price_buy")
    price_you_receive_when_selling: float | None = Field(default=None, validation_alias="price_sell")
    status_buy: int | None = None
    status_sell: int | None = None

    @field_validator("price_you_pay_to_acquire", "price_you_receive_when_selling", "status_buy", "status_sell")
    @classmethod
    def zero_means_not_offered(cls, value):
        return None if value == 0 else value


class UEXTradeRoute(BaseModel):
    commodity_id: int = Field(validation_alias="id_commodity")
    commodity_name: str
    origin_terminal_id: int = Field(validation_alias="id_terminal_origin")
    origin_terminal_name: str
    origin_star_system_name: str | None
    origin_planet_name: str | None
    destination_terminal_id: int = Field(validation_alias="id_terminal_destination")
    destination_terminal_name: str
    destination_star_system_name: str | None
    destination_planet_name: str | None
    price_origin: float
    price_destination: float
    price_margin: float
    scu_origin: float
    scu_destination: float
    status_origin: int
    status_destination: int
    distance: float
    is_on_ground_origin: int
    is_on_ground_destination: int
    # Not part of the commodities_routes payload — is_auto_load lives on the terminals
    # endpoint instead (a terminal property, not a per-route one). uex_lookup.search_routes
    # fills these in via a terminal lookup after validating the raw route row.
    is_auto_load_origin: int = 0
    is_auto_load_destination: int = 0
    # CSV strings from UEX, e.g. "1,2,4,8,16,24,32" — confirmed live these have real gaps
    # per terminal (not just "every size up to some max"), so they can't be derived from
    # a simpler terminal-level field and have to come from the route row itself.
    container_sizes_origin: list[int] = Field(default_factory=list)
    container_sizes_destination: list[int] = Field(default_factory=list)

    @field_validator("container_sizes_origin", "container_sizes_destination", mode="before")
    @classmethod
    def _parse_container_sizes(cls, value):
        if not value:
            return []
        if isinstance(value, list):
            return value
        return [int(part) for part in value.split(",") if part]
