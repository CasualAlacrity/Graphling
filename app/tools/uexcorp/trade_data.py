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
    has_loading_dock_origin: int
    has_loading_dock_destination: int
