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
